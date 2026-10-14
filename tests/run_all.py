# tests/run_all.py
# 统一运行所有后端测试（零依赖，仅用内置 unittest）
import os
import sys
import unittest

# 把项目根目录（tests/ 的上一级）加入搜索路径，确保 `import agent.*` 等新模块成功
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 受限环境对 %LOCALAPPDATA% 下的 app.log 打不开写句柄（PermissionError），
# api.main_app 在 import 时 configure_logging() 直接崩 → 4 个测试模块导入失败。
# 统一把日志指到工作区；调用方已有显式配置时不覆盖。
os.environ.setdefault("CHEF_LOG_DIR", os.path.join(ROOT, ".tmp-logs"))
os.makedirs(os.environ["CHEF_LOG_DIR"], exist_ok=True)

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.discover(
        os.path.join(ROOT, "tests"),
        pattern="test_*.py",
        top_level_dir=ROOT,
    )
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    # 失败则非零退出，便于 CI / 批处理判断
    sys.exit(0 if result.wasSuccessful() else 1)
