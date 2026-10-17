"""断点库维护脚本：清理孤儿线程并回收文件空间。

后台启动时也会跑同一套清理（见 api/main_app.py 的 _start_checkpoint_prune），
这个脚本用于手动查看/执行，尤其在长时间只增不减之后一次性瘦身。

用法（在项目根目录执行）：

    .venv/Scripts/python.exe scripts/prune_checkpoints.py           # 只看，不删
    .venv/Scripts/python.exe scripts/prune_checkpoints.py --apply   # 真删 + VACUUM

「孤儿」的定义：checkpoint.db 里 thread_id 在 sessions/ 下找不到同名 .json。
删掉会话后遗留的线程、以及 failover 派生的 <sid>-fo<时间戳> 线程都算在内。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from infrastructure.paths import CHECKPOINT_DB  # noqa: E402
from storage import checkpoints  # noqa: E402
from storage.sessions import SESSIONS_DIR  # noqa: E402


def _human_mb(size: int) -> str:
    return f"{size / 1024 / 1024:.2f} MB"


def main() -> int:
    parser = argparse.ArgumentParser(description="清理 checkpoint.db 里的孤儿断点线程")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正执行删除与 VACUUM；不带该参数时只报告不修改",
    )
    args = parser.parse_args()

    db_path = Path(CHECKPOINT_DB)
    if not db_path.exists():
        print(f"断点库不存在，无需清理：{db_path}")
        return 0

    sessions_dir = Path(SESSIONS_DIR)
    if not sessions_dir.exists():
        print(f"sessions 目录不存在，已中止（避免误判为『会话都没了』）：{sessions_dir}")
        return 1

    active = {p.stem for p in sessions_dir.glob("*.json")}
    before = db_path.stat().st_size
    threads = checkpoints.all_thread_ids()
    orphans = [t for t in threads if checkpoints.base_session_id(t) not in active]

    print(f"断点库：{db_path}（{_human_mb(before)}）")
    print(f"活跃会话：{len(active)} 个；断点线程：{len(threads)} 条；孤儿线程：{len(orphans)} 条")

    if not orphans:
        print("没有孤儿线程，无需清理。")
        return 0

    if not args.apply:
        for thread_id in sorted(orphans)[:20]:
            print(f"  - {thread_id}")
        if len(orphans) > 20:
            print(f"  ...（其余 {len(orphans) - 20} 条）")
        print("以上为预计删除项；加 --apply 才真正执行。")
        return 0

    deleted = checkpoints.delete_threads(orphans)
    checkpoints.vacuum()
    after = db_path.stat().st_size if db_path.exists() else 0
    print(f"已删除 {deleted} 条孤儿线程；文件 {_human_mb(before)} -> {_human_mb(after)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
