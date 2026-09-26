"""项目运行时数据路径的唯一入口。

分层重构后，代码模块散落在多个包中，但运行数据仍统一放在项目根目录的
``data/`` 与 ``sessions/``。业务模块只从这里取路径，避免同一数据出现多份副本。
"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
SESSIONS_DIR = PROJECT_ROOT / "sessions"
