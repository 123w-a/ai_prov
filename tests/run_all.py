# tests/run_all.py
# 统一运行所有后端测试（零依赖，仅用内置 unittest）
import os
import sys
import unittest

# 把项目根目录（tests/ 的上一级）加入搜索路径，确保 `import agent_graph` 等成功
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.discover(os.path.join(ROOT, "tests"), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    # 失败则非零退出，便于 CI / 批处理判断
    sys.exit(0 if result.wasSuccessful() else 1)
