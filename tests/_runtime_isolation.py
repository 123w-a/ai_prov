"""测试期运行时数据隔离：把可写运行时文件的落点重定向到临时目录。

为什么需要：部分回归用例（例如 ``tests/agent/test_image_async.py`` 里的真实
``TestClient`` 路径）会走产品代码的**真实落库分支**：

- ``storage.sessions.append_message`` 直接把 ``__pending__`` 记录写进项目根
  ``sessions/``，于是 ``*-stream-test`` 这类测试会话出现在前端会话侧栏；
- 后台补图线程经 ``storage.dish_assets.update_dish_asset_image`` 把测试假图
  （``https://images.test/...``）写进真实 ``data/dish_assets.json``，再被真实
  链路复用，卡片就渲染成打不开的图。

做法：测试进程 import ``tests`` 包时，把那些"只写运行时状态"的文件路径统一
改指一个临时目录。产品代码的默认路径与行为完全不变，只是测试进程的写入落到
tmp，不会污染前端可见的会话列表与菜品资产库。
"""

from __future__ import annotations

import importlib.abc
import sys
import tempfile
from pathlib import Path


_ROOT = Path(tempfile.mkdtemp(prefix="xiaoshan-tests-"))


def _build_targets() -> dict[str, dict[str, object]]:
    data = _ROOT / "data"
    sessions = _ROOT / "sessions"
    data.mkdir(parents=True, exist_ok=True)
    sessions.mkdir(parents=True, exist_ok=True)
    return {
        # 会话历史（前端侧栏的数据源）
        "storage.sessions": {"SESSIONS_DIR": sessions},
        "services.image_retry_queue": {"SESSIONS_DIR": sessions},
        # Agent 断点库（storage.checkpoints 惰性读 DB_PATH，patch 后自动重连）
        "storage.checkpoints": {"DB_PATH": data / "checkpoint.db"},
        # 跨会话菜品资产库（卡片配图的数据源）
        "storage.dish_assets": {"ASSETS_PATH": data / "dish_assets.json"},
        # 画像/口味/反馈等运行时状态
        "storage.taste": {"_LOG": data / "taste_signals.json"},
        "storage.feedback": {"_LOG": data / "answer_feedback.json"},
        "storage.memory_candidates": {
            "_DATA_DIR": data,
            "_CANDIDATES_PATH": data / "memory_candidates.json",
        },
        "agent.graph": {"_PROFILE_PATH": data / "profile.json"},
        "main": {"_PROFILE_PATH": data / "profile.json"},
        "api.routes.preferences_route": {
            "_DATA_DIR": data,
            "_PREFS_PATH": data / "preferences.txt",
            "_PROFILE_PATH": data / "profile.json",
        },
        # 饮食记录与周报
        "api.routes.reports_route": {
            "_MEALS": data / "meals.json",
            "_FEEDBACK_LOG": data / "feedback.json",
            "_SUMMARY_CACHE": data / "weekly_summary.json",
            "_PANTRY_LOG": data / "pantry_log.json",
        },
        "api.routes.fridge_route": {
            "_FILE": data / "fridge.json",
            "_PANTRY_LOG": data / "pantry_log.json",
        },
    }


_TARGETS = _build_targets()


def _apply(module_name: str) -> None:
    """把已导入目标模块的路径常量改指临时目录（缺属性就跳过）。"""
    attrs = _TARGETS.get(module_name)
    module = sys.modules.get(module_name)
    if not attrs or module is None:
        return
    for name, value in attrs.items():
        if hasattr(module, name):
            setattr(module, name, value)


class _IsolationFinder(importlib.abc.MetaPathFinder):
    """目标模块 import 完成后立刻重定向，无需在测试包里提前 import 重模块。"""

    def __init__(self, inner, targets):
        self._inner = list(inner)
        self._targets = targets

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in self._targets:
            return None
        spec = None
        for finder in self._inner:
            find_spec = getattr(finder, "find_spec", None)
            if find_spec is None:
                continue
            spec = find_spec(fullname, path, target)
            if spec is not None:
                break
        if spec is None or spec.loader is None:
            return None
        original_exec = spec.loader.exec_module

        def exec_module(module, _name=fullname, _exec=original_exec):
            _exec(module)
            _apply(_name)

        spec.loader.exec_module = exec_module
        return spec


_INSTALLED = False


def install() -> None:
    """幂等安装：先补一次已导入模块，再挂 import 钩子覆盖后续导入。"""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    for name in _TARGETS:
        _apply(name)
    sys.meta_path.insert(0, _IsolationFinder(sys.meta_path, _TARGETS))
