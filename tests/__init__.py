"""Backend test package."""

# 测试进程一 import 本包就隔离运行时数据落点，避免回归用例把测试会话与假图写进
# 前端可见的 sessions/ 与 data/dish_assets.json。产品行为不受影响，详见模块注释。
from ._runtime_isolation import install as _install_runtime_isolation

_install_runtime_isolation()
