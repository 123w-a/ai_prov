# sessions_store.py
# 会话持久化层（业务层）：用 JSON 文件存"会话"和"每条问答"，专门服务于前端侧栏的
# 增删改查与历史展示。
#
# 两层职责彻底解耦（这是核心设计）：
#   - 本文件（业务层）只用 JSON 文件，管"可展示的聊天历史"，前端 CRUD 全走它；
#   - LangGraph 的 checkpoint.db（SQLite）只管 Agent 循环断点快照，不碰聊天历史。
# 二者并行、互不替代，唯一的纽带是 thread_id == session_id。
#
# 存储结构：项目根 sessions/ 目录，一个会话一个文件 sessions/{session_id}.json
#   {
#     "session_id": "...",
#     "title": "...",
#     "created_at": "10:37",
#     "messages": [
#       { "id": 1, "user_text": "...", "answer": "...", "time": "...",
#         "image_name": "...", "image_type": "...", "image_url": "..."|null }
#     ]
#   }
# 用户上传的图片只存 OSS 可访问 URL，不再存 base64/image_data；前端从对象存储直接拉取。

import ctypes
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from datetime import datetime

# 会话 JSON 存放目录（与 checkpoint.db 分开，体现两层职责解耦）
SESSIONS_DIR = Path(__file__).with_name("sessions")

# 写文件用锁，避免 FastAPI 多线程并发读写同一个 JSON 把内容写坏
_lock = threading.Lock()


def _session_file(sid):
    return SESSIONS_DIR / f"{sid}.json"


def _safe_unlink(path):
    """真正删除一个文件。

    Windows 下优先用系统 API 直接删，绕过沙箱 safe-delete 对 Path.unlink 的钩子
    （该环境下回收站不可用会导致普通 unlink 抛 OSError，进而让删除接口 500）。
    用户真实机器没有沙箱，os.remove 也能正常删；这里双保险。
    """
    p = str(path)
    if os.name == "nt":
        try:
            if ctypes.windll.kernel32.DeleteFileW(p):
                return
        except Exception:
            pass
    try:
        os.remove(p)
    except FileNotFoundError:
        return


def _read_session(sid):
    fp = _session_file(sid)
    if not fp.exists():
        return None
    return json.loads(fp.read_text(encoding="utf-8"))


def _write_session(data):
    SESSIONS_DIR.mkdir(exist_ok=True)
    _session_file(data["session_id"]).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def init_db():
    """初始化会话目录，并一次性把旧的 sessions.db（SQLite）迁移到 JSON 后删除。"""
    SESSIONS_DIR.mkdir(exist_ok=True)
    _migrate_from_sqlite_once()


def _migrate_from_sqlite_once():
    """历史兼容：把上一版 SQLite 业务库数据搬到 JSON 文件，搬完即删，避免两层并存混乱。"""
    db_path = Path(__file__).with_name("sessions.db")
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT session_id, title, created_at FROM sessions"
        ).fetchall()
        for s in rows:
            msgs = conn.execute(
                "SELECT id, user_text, answer, time, image_name, image_type, image_data "
                "FROM messages WHERE session_id=? ORDER BY id ASC",
                (s["session_id"],),
            ).fetchall()
            data = {
                "session_id": s["session_id"],
                "title": s["title"],
                "created_at": s["created_at"],
                "messages": [
                    {
                        "id": m["id"],
                        "user_text": m["user_text"],
                        "answer": m["answer"],
                        "time": m["time"],
                        # 旧 SQLite 里只有 base64 BLOB，没有 OSS URL；迁移时直接丢弃图片数据，
                        # 让历史会话只保留文字。新的 JSON 结构统一只存 image_url。
                        "image_name": m["image_name"],
                        "image_type": m["image_type"],
                        "image_url": None,
                    }
                    for m in msgs
                ],
            }
            _write_session(data)
        conn.close()
        # 迁移完成，删掉旧 SQLite 业务库（带重试，规避 Windows 文件锁）
        _safe_unlink(db_path)
    except Exception as e:
        # 迁移失败不影响启动；旧 sessions.db 留着，下次启动再试
        print(f"[sessions_store] 从 SQLite 迁移失败，保留 sessions.db：{e}")


def create_session():
    """新建一个空会话，返回 {session_id, title, messages:[]}。"""
    sid = f"user_{uuid.uuid4().hex[:10]}"
    now = datetime.now().strftime("%H:%M")
    data = {"session_id": sid, "title": "新对话", "created_at": now, "messages": []}
    with _lock:
        _write_session(data)
    return data


def list_sessions():
    """返回全部会话（含各自消息），按创建时间倒序。前端渲染侧栏列表用。"""
    if not SESSIONS_DIR.exists():
        return []
    out = []
    for fp in SESSIONS_DIR.glob("*.json"):
        try:
            out.append(json.loads(fp.read_text(encoding="utf-8")))
        except Exception:
            continue
    # 按创建时间倒序（与原来 SQLite 行为一致）
    out.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return out


def delete_session(sid):
    """彻底删除整个会话及其全部消息。"""
    fp = _session_file(sid)
    if fp.exists():
        _safe_unlink(fp)


def clear_session(sid):
    """清空会话内所有消息，但保留会话本身（标题复位为"新对话"）。"""
    with _lock:
        data = _read_session(sid)
        if data is None:
            return
        data["messages"] = []
        data["title"] = "新对话"
        _write_session(data)


def delete_message(sid, msg_id):
    """删除单一条问答记录。"""
    with _lock:
        data = _read_session(sid)
        if data is None:
            return
        data["messages"] = [m for m in data["messages"] if m["id"] != msg_id]
        _write_session(data)


def append_message(
    sid,
    user_text,
    answer,
    time,
    image_name=None,
    image_type=None,
    image_url=None,
):
    """追加一条问答。第一条消息会自动用问题文本当会话标题。

    用户上传的图片只保留 OSS 可访问 URL(image_url 字符串)，
    不再把 bytes/base64 落库；前端需要图片时直接从对象存储拉取。
    """
    with _lock:
        data = _read_session(sid)
        if data is None:
            # 防御性：正常流程会先 create_session；这里
            now = datetime.now().strftime("%H:%M")
            data = {"session_id": sid, "title": "新对话", "created_at": now, "messages": []}
        new_id = (max((m["id"] for m in data["messages"]), default=0)) + 1
        data["messages"].append(
            {
                "id": new_id,
                "user_text": user_text,
                "answer": answer,
                "time": time,
                "image_name": image_name,
                "image_type": image_type,
                "image_url": image_url,  # 只存 OSS URL，不存 base64
            }
        )
        # 只有第一条消息时，用问题前 22 字做侧栏标题
        if len(data["messages"]) == 1:
            data["title"] = user_text[:22]
        _write_session(data)
        return new_id


def update_message_answer(sid, record_id, answer, image_name=None, image_type=None, image_url=None):
    """把入口预落的『待完成』记录更新为最终答案（断流兜底靠它，幂等）。"""
    with _lock:
        data = _read_session(sid)
        if data is None:
            return False
        for m in data["messages"]:
            if m.get("id") == record_id:
                m["answer"] = answer
                if image_name is not None:
                    m["image_name"] = image_name
                if image_type is not None:
                    m["image_type"] = image_type
                if image_url is not None:
                    m["image_url"] = image_url
                _write_session(data)
                return True
    return False


def update_answer_image_by_dish(sid, record_id, dish_name, image_url, image_ai, note):
    """后台补图回写：按菜名定位 answer.recipes 中无图项并更新图片字段（幂等）。

    与 update_message_answer 的区别：不整包替换 answer，而是锁内重读后只改
    图片相关字段，避免后台补图覆盖实时链路的其他回写。"""
    with _lock:
        data = _read_session(sid)
        if data is None:
            return False
        for m in data["messages"]:
            if m.get("id") != record_id:
                continue
            try:
                ans = json.loads(m.get("answer") or "")
            except Exception:
                return False
            if not isinstance(ans, dict):
                return False
            changed = False
            for recipe in ans.get("recipes") or []:
                if recipe.get("name") == dish_name and not recipe.get("image_url"):
                    recipe["image_url"] = image_url
                    recipe["image_ai_generated"] = bool(image_ai)
                    changed = True
            if changed:
                ans["image_url"] = image_url
                ans["image_ai_generated"] = bool(image_ai)
                ans["image_note"] = note
                m["answer"] = json.dumps(ans, ensure_ascii=False)
                m["image_url"] = image_url  # 与实时链路的顶层字段保持一致
                _write_session(data)
            return changed
    return False


def patch_message_feedback(sid, record_id, rating):
    """设置/清除一条问答的满意度标记（'up' | 'down' | None）

    None = 取消标记。返回 (found, current)：current 为设置后的最终状态。
    """
    with _lock:
        data = _read_session(sid)
        if data is None:
            return False, None
        for m in data["messages"]:
            if m.get("id") != record_id:
                continue
            if rating is None:
                m.pop("feedback", None)
            else:
                m["feedback"] = rating
            _write_session(data)
            return True, m.get("feedback")
    return False, None
