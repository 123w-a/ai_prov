# run.py：后端启动入口。改完包结构后用这个文件启动。
# 重要：端口保持 8010，与前端默认 API 地址 (http://127.0.0.1:8010) 一致，否则前端连不上。
#
# 痛点解决：以前重启后端会撞上 "[Errno 10048] 每个套接字地址只允许使用一次"，
# 因为上一次的 uvicorn 进程还赖在 8010 端口上。现在启动前会自动做三步：
#   1. 探测端口是否被占用；
#   2. 占用者是不是"上一版的自己"（请求 /api/ 健康接口确认）；
#   3. 是自己人就自动优雅关闭 + 兜底强杀，不是自己人就提示、绝不误杀别人的程序。
import os
import socket
import subprocess
import sys
import time
import urllib.request

import uvicorn

from api.main_app import app
from agent import turn_decision as _turn_decision_module
from infrastructure.paths import LOG_DIR

HOST = "127.0.0.1"  # 只监听本机，避免暴露到局域网
PORT = 8010  # 与前端 DEFAULT_API_URL 保持一致，别随便改
RELOAD = os.getenv("API_RELOAD", "0") == "1"  # 改代码自动重启，开发时可 set API_RELOAD=1
WORKERS = 1  # 会话/JSON 存储使用进程内锁，必须固定单 worker。


def configure_utf8_stdio():
    """让 Windows 后台重定向日志与 IDE 的 UTF-8 读取保持一致。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


configure_utf8_stdio()
print(
    "[startup] loaded "
    f"api={__import__('api.main_app', fromlist=['app']).__file__} "
    f"turn_decision={_turn_decision_module.__file__}",
    flush=True,
)
# 日志在项目外，启动时打印一次，省得每次都要去翻代码找路径
print(f"[startup] logs -> {LOG_DIR}", flush=True)


def port_in_use(host=HOST, port=PORT):
    """探测端口是否已被监听：连得上就说明有人占着。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)  # 本机连接，超过 0.5 秒基本就是没人听
        return sock.connect_ex((host, port)) == 0


def is_our_backend(host=HOST, port=PORT, attempts=3, timeout=4):
    """判断占用端口的是不是本项目上一次启动的后端。

    探活优先走 `/api/health/live`（不依赖知识库预热，秒回 {"status":"ok"}），
    旧版本回落到 `/api/` 的"服务正常"。**多次重试**是这里的关键：旧进程还在
    KB 预热窗口（约 60~90 秒）里时，单次短超时探针连不上，会把自家后端误判成
    "外人"，于是新版直接 sys.exit(1) —— 用户继续打在旧代码上，正是"改了没生效"
    的经典成因。

    HTTP 全程不通时，再退一步按**进程身份**认定：可执行文件位于本项目 .venv 下、
    且命令行里带 run.py，满足才算自己人。仍然保守：拿不准的一律不杀。
    """
    for attempt in range(attempts):
        for path, expect in (("/api/health/live", "ok"), ("/api/", "服务正常")):
            try:
                with urllib.request.urlopen(
                    f"http://{host}:{port}{path}", timeout=timeout
                ) as resp:
                    body = resp.read().decode("utf-8", errors="ignore")
                if expect in body:
                    return True
            except Exception:
                pass  # 预热期/非本项目服务都会走到这，交给重试和兜底
        if attempt < attempts - 1:
            time.sleep(1)

    return _looks_like_our_backend_pids(find_pids(port))


def find_pids(port=PORT):
    """找出占用该端口的进程 PID。优先用 psutil，缺失时降级解析 netstat。"""
    pids = set()
    try:
        import psutil

        for conn in psutil.net_connections(kind="inet"):
            # 只认处于 LISTEN 状态、端口匹配的连接，TIME_WAIT 之类的不算占用
            if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                if conn.pid:
                    pids.add(conn.pid)
    except Exception:
        # 兜底：没装 psutil 或权限不足时，解析 netstat -ano 的最后一列 PID
        try:
            out = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, errors="ignore"
            ).stdout
            for line in out.splitlines():
                if f":{port} " in line and "LISTENING" in line.upper():
                    pids.add(int(line.split()[-1]))
        except Exception:
            pass
    pids.discard(os.getpid())  # 别把自己杀了
    return pids


def _looks_like_our_backend_pids(pids):
    """HTTP 探不通时，按进程身份兜底判断占用者是不是本项目的 run.py。

    认定条件（满足其一即可，但都要求命令行里带 run.py）：
      1. 可执行文件位于本项目 .venv 下；或
      2. 命令行里出现本项目 .venv 路径。
    第 2 条是必须的：本机 .venv/Scripts/python.exe 实际是 anaconda 基础解释器的
    别名，psutil 读到的 exe() 是 `D:\\Dev\\anaconda3\\python.exe`，完全不含 `.venv`；
    只看 exe() 会永远认不出自家后端。仍然保守：拿不准的一律不杀。
    """
    if not pids:
        return False
    try:
        import psutil
    except Exception:
        return False  # 拿不到进程信息就不猜，保守放行给用户判断

    root = os.path.dirname(os.path.abspath(__file__))
    venv_dir = os.path.join(root, ".venv").lower()
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            exe = (proc.exe() or "").lower()
            cmd = " ".join(proc.cmdline() or []).lower()
            if "run.py" not in cmd:
                continue
            if venv_dir in exe or venv_dir in cmd:
                return True
        except Exception:
            continue  # 权限不足/进程已退出，跳过
    return False


def kill_pid(pid):
    """先 terminate 优雅关闭，赖着不走再 kill 强杀；两者都失败则用 taskkill 兜底。"""
    try:
        import psutil

        proc = psutil.Process(pid)
        proc.terminate()  # 给 uvicorn 机会释放端口和数据库连接
        try:
            proc.wait(timeout=3)
        except psutil.TimeoutExpired:
            proc.kill()  # 3 秒还不退就强杀
        return True
    except Exception:
        pass
    try:
        # 最后兜底：Windows 原生 taskkill /F 强制结束
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            text=True,
            errors="ignore",
        )
        return True
    except Exception:
        return False


def free_port(host=HOST, port=PORT):
    """启动前清场：端口空闲直接放行；被自己人占则自动接管；被外人占则中止并提示。"""
    if not port_in_use(host, port):
        return True  # 端口干净，正常启动

    ours = is_our_backend(host, port)
    pids = find_pids(port)

    if not ours:
        # 不确定是谁的服务，绝不乱杀，交给用户判断
        print(f"[WARN] 端口 {port} 被占用，但它不像是本项目的后端（健康接口无响应）。")
        print(f"    占用进程 PID：{sorted(pids) or '未知'}")
        print(f"    请手动处理后重试，或修改 run.py 里的 PORT（记得同步改 app.py 的 DEFAULT_API_URL）。")
        return False

    print(f"[INFO] 检测到旧的后端仍在 {host}:{port} 运行，正在自动关闭：PID {sorted(pids)}")
    for pid in pids:
        kill_pid(pid)

    # Windows 释放端口有延迟，轮询等待，最多等 5 秒
    for _ in range(10):
        if not port_in_use(host, port):
            print("[OK] 旧进程已退出，端口已释放。")
            return True
        time.sleep(0.5)

    print(f"[ERROR] 端口 {port} 仍未释放，请手动执行：taskkill /PID {sorted(pids)} /F")
    return False


def enforce_single_worker():
    """拒绝外部误设的多 worker，避免本地 JSON 并发写坏。"""
    configured = os.getenv("WEB_CONCURRENCY") or os.getenv("UVICORN_WORKERS")
    if configured and configured.strip() not in {"1", str(WORKERS)}:
        print(
            "[ERROR] 本项目使用进程内 JSON 锁，只支持单 worker；"
            f"检测到 WORKERS={configured}。请取消 WEB_CONCURRENCY/UVICORN_WORKERS 后重试。"
        )
        return False
    return True


if __name__ == "__main__":
    if not enforce_single_worker():
        sys.exit(1)
    if not free_port():
        sys.exit(1)  # 清场失败就别硬启，避免刷一屏 10048 报错

    if RELOAD:
        # reload 模式必须传 "模块:变量" 字符串，uvicorn 才能在子进程里重新导入
        uvicorn.run("api.main_app:app", host=HOST, port=PORT, reload=True, workers=WORKERS)
    else:
        uvicorn.run(app, host=HOST, port=PORT, workers=WORKERS)
