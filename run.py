# run.py：后端启动入口。改完包结构后用这个文件启动。
# 重要：端口保持 8010，与前端 app.py 的 DEFAULT_API_URL (http://127.0.0.1:8010) 一致，否则前端连不上。
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

HOST = "127.0.0.1"  # 只监听本机，避免暴露到局域网
PORT = 8010  # 与前端 DEFAULT_API_URL 保持一致，别随便改
RELOAD = os.getenv("API_RELOAD", "0") == "1"  # 改代码自动重启，开发时可 set API_RELOAD=1


def port_in_use(host=HOST, port=PORT):
    """探测端口是否已被监听：连得上就说明有人占着。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)  # 本机连接，超过 0.5 秒基本就是没人听
        return sock.connect_ex((host, port)) == 0


def is_our_backend(host=HOST, port=PORT):
    """请求健康接口，判断占用者是不是本项目上一次启动的后端。

    只有确认是"自己人"才允许自动杀掉，防止误伤用户电脑上别的服务。
    """
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/", timeout=2) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        return "服务正常" in body  # main_app.py 里健康接口固定返回这句
    except Exception:
        return False  # 连不上/返回异常，一律当作"不是自己人"，保守处理


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
        print(f"⚠️  端口 {port} 被占用，但它不像是本项目的后端（健康接口无响应）。")
        print(f"    占用进程 PID：{sorted(pids) or '未知'}")
        print(f"    请手动处理后重试，或修改 run.py 里的 PORT（记得同步改 app.py 的 DEFAULT_API_URL）。")
        return False

    print(f"🔄 检测到旧的后端仍在 {host}:{port} 运行，正在自动关闭：PID {sorted(pids)}")
    for pid in pids:
        kill_pid(pid)

    # Windows 释放端口有延迟，轮询等待，最多等 5 秒
    for _ in range(10):
        if not port_in_use(host, port):
            print("✅ 旧进程已退出，端口已释放。")
            return True
        time.sleep(0.5)

    print(f"❌ 端口 {port} 仍未释放，请手动执行：taskkill /PID {sorted(pids)} /F")
    return False


if __name__ == "__main__":
    if not free_port():
        sys.exit(1)  # 清场失败就别硬启，避免刷一屏 10048 报错

    if RELOAD:
        # reload 模式必须传 "模块:变量" 字符串，uvicorn 才能在子进程里重新导入
        uvicorn.run("api.main_app:app", host=HOST, port=PORT, reload=True)
    else:
        uvicorn.run(app, host=HOST, port=PORT)
