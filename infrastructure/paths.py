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
    这里用 ``mkdir`` + ``os.access`` 判断，不落临时文件——本项目环境对 ``unlink``
    有特殊处理（会被改写成重命名），探针文件反而会留垃圾。
    """
    _load_dotenv_early()
    candidates: list[Path] = []

    override = (os.getenv("CHEF_LOG_DIR") or "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(_platform_log_dir())
    candidates.append(DATA_DIR)

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            if os.access(candidate, os.W_OK):
                return candidate
        except Exception:
            continue

    return DATA_DIR


LOG_DIR = _resolve_log_dir()
