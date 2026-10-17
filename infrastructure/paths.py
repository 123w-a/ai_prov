"""项目运行时数据路径的唯一入口。

分层重构后，代码模块散落在多个包中，但**业务数据**仍统一放在项目根目录的
``data/`` 与 ``sessions/``。业务模块只从这里取路径，避免同一数据出现多份副本。

**日志是唯一的例外**：日志属于纯观测产物（只写不回读）、体积会持续增长，
放在仓库里既脏工作区、又容易在 ``git status`` / 打包时被顺手带出去。
因此日志默认写到**项目之外的用户级目录**（见 ``LOG_DIR``）：

- Windows：``%LOCALAPPDATA%\\XiaoShanGuanJia\\logs``
- macOS：``~/Library/Logs/XiaoShanGuanJia``
- Linux：``$XDG_STATE_HOME/xiaoshan-guanjia/logs``

需要改位置（例如放到 D 盘）时，在 ``.env`` 里设 ``CHEF_LOG_DIR`` 即可，
不用改代码。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
SESSIONS_DIR = PROJECT_ROOT / "sessions"
# Agent 断点库（LangGraph SqliteSaver）。放在 resources/ 下，与业务数据分开；
# 由 storage/checkpoints.py 统一做删除与孤儿清理。
RESOURCES_DIR = PROJECT_ROOT / "resources"
CHECKPOINT_DB = RESOURCES_DIR / "checkpoint.db"

APP_SLUG = "XiaoShanGuanJia"


def _platform_log_dir() -> Path:
    """按操作系统惯例给出用户级日志目录。"""
    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        if base:
            return Path(base) / APP_SLUG / "logs"
        return Path.home() / ("." + APP_SLUG.lower()) / "logs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / APP_SLUG
    state = os.getenv("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / APP_SLUG.lower() / "logs"


def _load_dotenv_early() -> None:
    """让 ``CHEF_LOG_DIR`` 能被 ``.env`` 配置到。

    坑：本项目 ``.env`` 是在 ``infrastructure/configs.py`` 里 ``load_dotenv()`` 的，
    而本模块（paths）可能比它更早被导入 —— 那时 ``.env`` 还没进 os.environ，
    读 ``CHEF_LOG_DIR`` 会得到空值，用户配了也不生效。
    这里显式指定项目内 ``.env`` 提前加载一次：``load_dotenv`` 默认 **不覆盖**已有的
    真实环境变量，所以不会跟外部设置打架；没装 python-dotenv 时静默跳过。
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except Exception:
        pass


def _resolve_log_dir() -> Path:
    """日志目录优先级：``CHEF_LOG_DIR`` > 系统惯例目录 > 项目内 ``data/`` 兜底。

    兜底分支的意义：换机器、HOME 只读、目录被安全软件锁住时，宁可日志写回项目内，
    也不能让「写日志」把主链路带崩（本项目的日志约定是失败一律静默）。

    判据是 ``_is_writable``（真写一次），**不是** ``os.access``——理由见那个函数。
    （2026-10-15 更正：本段原先写的是"用 ``mkdir`` + ``os.access`` 判断，不落临时
    文件"，那正是要被修掉的做法本身；探针文件写完即删，最坏只留一个删不掉的空
    文件，代价远小于"把一个写不进的目录判成可写"。）

    实测差异值得记一笔：同一台机器上，受限沙箱里运行的服务进程判
    ``%LOCALAPPDATA%`` 不可写，而带 full-access 的交互式 shell 手动写却成功。
    所以"我手工试过能写"**不能**推翻服务进程里的判定——两边问的不是同一个问题。
    """
    _load_dotenv_early()
    candidates: list[Path] = []

    override = (os.getenv("CHEF_LOG_DIR") or "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(_platform_log_dir())
    candidates.append(DATA_DIR)

    for candidate in candidates:
        if _is_writable(candidate):
            return candidate

    return DATA_DIR


def _is_writable(directory: Path) -> bool:
    """真的往里写一个文件，而不是问 ``os.access``。

    2026-10-14 踩坑：``os.access(..., os.W_OK)`` 在 Windows 上**不看 ACL**，
    只回答"这个路径存在吗"——沙箱或安全软件只拦写入时会照样返回 True，于是
    这里选中一个实际写不进去的目录，直到 ``RotatingFileHandler`` 构造时才抛
    ``PermissionError``，把启动链路带崩。判定可写只有一个可靠办法：写一次。

    探针文件用固定名字、写完即删；删不掉也只留一个空文件（本项目环境把 unlink
    改写成重命名，宁可留一格垃圾，也不留一次误判）。
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False
    probe = directory / ".write-probe"
    try:
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("")
    except Exception:
        return False
    try:
        probe.unlink()
    except Exception:
        pass
    return True


LOG_DIR = _resolve_log_dir()
