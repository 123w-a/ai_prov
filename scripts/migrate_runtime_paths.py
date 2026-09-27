"""把分层重构期间散落到各包目录的运行数据合并回项目根目录。

脚本只做“合并/补齐”，不删除源文件。执行前会复制一份原始目录到
``.tmpdir/runtime_paths_migration/``，JSONL 与日志文件通过偏移量续接，
重复执行不会重复追加已有内容。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from infrastructure.paths import DATA_DIR, LOG_DIR, PROJECT_ROOT, SESSIONS_DIR
from storage.dish_assets import _normalize_name
from storage.utils import atomic_write_json


STATE_FILE = PROJECT_ROOT / ".tmpdir" / "runtime_paths_migration" / "state.json"
SOURCE_DIRS = (
    DATA_DIR,
    SESSIONS_DIR,
    PROJECT_ROOT / "storage" / "data",
    PROJECT_ROOT / "storage" / "sessions",
    PROJECT_ROOT / "agent" / "data",
    PROJECT_ROOT / "domain" / "data",
    PROJECT_ROOT / "infrastructure" / "data",
)
# 注意：日志类目标已从 DATA_DIR 改为 LOG_DIR —— 日志现在写在项目外的日志目录，
# 若仍往 DATA_DIR 续接，会把历史日志重新拉回仓库里。
JSONL_MERGES = (
    (PROJECT_ROOT / "agent" / "data" / "agent_trace.jsonl", LOG_DIR / "agent_trace.jsonl"),
    (PROJECT_ROOT / "agent" / "data" / "usage.jsonl", LOG_DIR / "usage.jsonl"),
    (PROJECT_ROOT / "infrastructure" / "data" / "app.log", LOG_DIR / "app.log"),
    (
        PROJECT_ROOT / "domain" / "data" / "allergen_unresolved.log",
        LOG_DIR / "allergen_unresolved.log",
    ),
)


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _backup_sources(run_dir: Path) -> None:
    for index, source in enumerate(dict.fromkeys(SOURCE_DIRS)):
        if not source.is_dir():
            continue
        target = run_dir / f"{index:02d}_{source.relative_to(PROJECT_ROOT).as_posix().replace('/', '_')}"
        shutil.copytree(
            source,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__"),
        )


def _merge_dish_assets() -> int:
    sources = (
        DATA_DIR / "dish_assets.json",
        PROJECT_ROOT / "storage" / "data" / "dish_assets.json",
    )
    merged: dict[str, dict] = {}
    for source in sources:
        raw = _load_json(source, {})
        if not isinstance(raw, dict):
            continue
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            normalized = _normalize_name(value.get("name") or key)
            if not normalized:
                continue
            current = merged.get(normalized)
            merged[normalized] = _merge_asset(current, value)
    if not merged:
        return 0
    target = DATA_DIR / "dish_assets.json"
    if _load_json(target, {}) == merged:
        return 0
    atomic_write_json(target, merged)
    return len(merged)


def _merge_asset(current: dict | None, incoming: dict) -> dict:
    if not current:
        return dict(incoming)
    current_newer = _as_float(current.get("updated_at")) >= _as_float(incoming.get("updated_at"))
    newer = current if current_newer else incoming
    older = incoming if current_newer else current
    merged = dict(older)
    merged.update(newer)
    for key in ("image_url", "image_note", "image_ai_generated"):
        if not newer.get(key) and older.get(key):
            merged[key] = older[key]
    old_recipe = older.get("recipe") if isinstance(older.get("recipe"), dict) else {}
    new_recipe = newer.get("recipe") if isinstance(newer.get("recipe"), dict) else {}
    recipe = dict(old_recipe)
    recipe.update(new_recipe)
    for key in ("image_url", "image_note", "image_ai_generated"):
        if not new_recipe.get(key) and old_recipe.get(key):
            recipe[key] = old_recipe[key]
    if recipe:
        merged["recipe"] = recipe
    merged["updated_at"] = max(
        _as_float(current.get("updated_at")),
        _as_float(incoming.get("updated_at")),
    )
    return merged


def _merge_memory_candidates() -> int:
    sources = (
        DATA_DIR / "memory_candidates.json",
        PROJECT_ROOT / "storage" / "data" / "memory_candidates.json",
    )
    by_id: dict[str, dict] = {}
    anonymous: list[dict] = []
    for source in sources:
        raw = _load_json(source, [])
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "")
            if not item_id:
                anonymous.append(item)
                continue
            previous = by_id.get(item_id)
            if previous is None or _candidate_rank(item) > _candidate_rank(previous):
                by_id[item_id] = item
    merged = sorted(
        [*by_id.values(), *anonymous],
        key=lambda item: str(item.get("created_at") or ""),
    )
    if not merged:
        return 0
    target = DATA_DIR / "memory_candidates.json"
    if _load_json(target, []) == merged:
        return 0
    atomic_write_json(target, merged)
    return len(merged)


def _candidate_rank(item: dict) -> tuple:
    status_rank = {
        "confirmed": 4,
        "dismissed": 3,
        "once": 2,
        "pending": 1,
    }.get(str(item.get("status") or ""), 0)
    return status_rank, str(item.get("created_at") or "")


def _iter_session_candidates() -> dict[str, list[Path]]:
    by_stem: dict[str, list[Path]] = {}
    roots = (SESSIONS_DIR, PROJECT_ROOT / "storage" / "sessions")
    for root in roots:
        if not root.is_dir():
            continue
        for main in root.glob("*.json"):
            by_stem.setdefault(main.stem, []).extend(
                [main, main.with_name(main.name + ".bak")]
            )
    return by_stem


def _session_candidate_rank(path: Path) -> tuple:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        valid = isinstance(payload, dict)
    except Exception:
        valid = False
    return (
        valid,
        path.stat().st_mtime_ns,
        not path.name.endswith(".bak"),
        path.parent != SESSIONS_DIR,
    )


def _merge_sessions() -> int:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    copied = 0
    for _stem, paths in _iter_session_candidates().items():
        candidates = [path for path in paths if path.exists()]
        if not candidates:
            continue
        selected = max(candidates, key=_session_candidate_rank)
        if not _session_candidate_rank(selected)[0]:
            continue
        target = SESSIONS_DIR / f"{_stem}.json"
        if target.exists() and target.resolve() == selected.resolve():
            continue
        if target.exists() and _session_candidate_rank(target)[1] >= _session_candidate_rank(selected)[1]:
            continue
        payload = _load_json(selected, {})
        atomic_write_json(target, payload)
        copied += 1
    return copied


def _merge_append_logs(state: dict) -> int:
    offsets = state.setdefault("append_offsets", {})
    copied = 0
    for source, target in JSONL_MERGES:
        if not source.is_file():
            continue
        key = source.relative_to(PROJECT_ROOT).as_posix()
        size = source.stat().st_size
        offset = int(offsets.get(key) or 0)
        if offset == size:
            continue
        if offset < 0 or offset > size:
            offset = 0
        with source.open("rb") as src, target.open("ab") as dst:
            src.seek(offset)
            shutil.copyfileobj(src, dst)
        offsets[key] = size
        copied += 1
    return copied


def migrate(apply_changes: bool = True) -> dict:
    state = _load_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    run_dir = STATE_FILE.parent / datetime.now().strftime("%Y%m%d_%H%M%S")
    result = {
        "backup_dir": str(run_dir),
        "dish_assets": 0,
        "memory_candidates": 0,
        "sessions": 0,
        "appended_files": 0,
    }
    if not apply_changes:
        return result
    run_dir.mkdir(parents=True, exist_ok=True)
    _backup_sources(run_dir)
    result["dish_assets"] = _merge_dish_assets()
    result["memory_candidates"] = _merge_memory_candidates()
    result["sessions"] = _merge_sessions()
    result["appended_files"] = _merge_append_logs(state)
    atomic_write_json(STATE_FILE, state, backup=False)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="合并分层重构期间的运行数据副本")
    parser.add_argument("--dry-run", action="store_true", help="只检查，不写入任何文件")
    args = parser.parse_args()
    result = migrate(apply_changes=not args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
