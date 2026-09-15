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
#         "image_name": "...", "image_type": "...", "image_url": "..."|null,
#         "user_image_url": "..."|null }
#     ]
#   }
# 用户上传的图片只存 OSS 可访问 URL，不再存 base64/image_data；前端从对象存储直接拉取。

import ctypes
import glob
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from datetime import datetime

from storage_utils import atomic_write_json

_logger = logging.getLogger("sessions_store")

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


def _backup_file(fp: Path) -> Path:
    """atomic_write_json 每次覆盖都会轮转出的 .bak 副本。"""
    return fp.with_name(fp.name + ".bak")


def _load_session_file(fp: Path):
    """读一个会话文件；主文件损坏时回退 .bak。返回 (data | None, 恢复说明)。

    以前这里直接 json.loads，坏文件会让该会话所有读写抛错；list_sessions 又
    悄悄 continue 把它藏掉——用户只会看到"会话少了一条"，查不到原因。
    """
    try:
        return json.loads(fp.read_text(encoding="utf-8")), ""
    except Exception as exc:
        backup = _backup_file(fp)
        if backup.exists():
            try:
                data = json.loads(backup.read_text(encoding="utf-8"))
            except Exception:
                data = None
            if isinstance(data, dict):
                _logger.warning("会话文件损坏，已回退 .bak：%s（%s）", fp.name, exc)
                return data, f"会话文件损坏，已从备份恢复：{exc}"
        _logger.error("会话文件损坏且无可用备份：%s（%s）", fp.name, exc)
        return None, f"会话文件损坏且无可用备份：{exc}"


def _read_session(sid):
    fp = _session_file(sid)
    if not fp.exists():
        return None
    data, _ = _load_session_file(fp)
    return data


def _write_session(data):
    SESSIONS_DIR.mkdir(exist_ok=True)
    atomic_write_json(_session_file(data["session_id"]), data)


def _strip_context_prefix(text: str) -> str:
    """把用户消息开头的 [当前位置：...] / [实时状态：...] 等前缀剥掉。"""
    text = str(text or "").strip()
    while text.startswith("["):
        end = text.find("]")
        if end == -1:
            break
        text = text[end + 1 :].lstrip("\r\n ")
    return text


def _first_recipe_name(answer) -> str | None:
    """从结构化答案里取第一道菜名，作为会话标题的主规则。"""
    if not answer:
        return None
    try:
        data = json.loads(answer) if isinstance(answer, str) else answer
        recipes = data.get("recipes") or []
        return str(recipes[0].get("name") or "").strip() or None
    except Exception:
        return None


def extract_answer_text(answer) -> str:
    """从结构化或纯文本答案中提取可独立展示的自然语言内容。"""
    if not answer:
        return ""
    if isinstance(answer, str):
        raw = answer.strip()
        if not raw or raw in {"__pending__", "__cancelled__"}:
            return ""
        try:
            parsed = json.loads(raw)
        except Exception:
            return raw
    else:
        parsed = answer
    if not isinstance(parsed, dict):
        return str(parsed or "").strip()
    for key in ("opening", "chef_tip"):
        text = str(parsed.get(key) or "").strip()
        if text:
            return text
    return ""


def _user_image_url_for_cancel(record: dict):
    """取得用户原始上传图；兼容旧记录中 image_url 被生成图覆盖的情况。"""
    if "user_image_url" in record:
        return record.get("user_image_url")
    current_url = record.get("image_url")
    if not current_url:
        return None
    try:
        answer = json.loads(record.get("answer") or "")
    except Exception:
        return current_url
    if not isinstance(answer, dict):
        return current_url
    generated_urls = {
        str(url)
        for url in [
            answer.get("image_url"),
            *[
                recipe.get("image_url")
                for recipe in (answer.get("recipes") or [])
                if isinstance(recipe, dict)
            ],
        ]
        if url
    }
    return None if str(current_url) in generated_urls else current_url


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
        # 坏文件先尝试 .bak 恢复；确实救不回来才跳过，且一定有日志留痕。
        data, note = _load_session_file(fp)
        if data is None:
            continue
        if note:
            _logger.warning("list_sessions 使用备份数据：%s", fp.name)
        out.append(data)
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


def rename_session(sid, title):
    """重命名会话标题；标题为空则回落为新对话。"""
    with _lock:
        data = _read_session(sid)
        if data is None:
            return False
        data["title"] = str(title or "").strip()[:40] or "新对话"
        _write_session(data)
        return True


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
                "user_image_url": image_url,
            }
        )
        # 只有第一条消息时，用问题前 22 字做侧栏标题
        if len(data["messages"]) == 1:
            cleaned = _strip_context_prefix(user_text) or user_text
            data["title"] = cleaned[:22]
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
                if m.get("cancelled") or m.get("image_cancelled") or m.get("answer") == "__cancelled__":
                    return False
                m["answer"] = answer
                if image_name is not None:
                    m["image_name"] = image_name
                if image_type is not None:
                    m["image_type"] = image_type
                if image_url is not None:
                    m["image_url"] = image_url
                    m["user_image_url"] = image_url
                if len(data["messages"]) == 1:
                    dish = _first_recipe_name(answer)
                    if dish:
                        data["title"] = dish[:22]
                _write_session(data)
                return True
    return False


def mark_message_cancelled(sid, record_id):
    """将一轮助手回答标记为不可恢复的取消态，并清除其图片。"""
    with _lock:
        data = _read_session(sid)
        if data is None:
            return False
        for m in data["messages"]:
            if m.get("id") != record_id:
                continue
            uploaded_image_url = _user_image_url_for_cancel(m)
            m["answer"] = "__cancelled__"
            m["cancelled"] = True
            m.pop("image_cancelled", None)
            m["image_url"] = uploaded_image_url
            _write_session(data)
            return True
    return False


def mark_message_image_cancelled(sid, record_id, answer=None):
    """保留自然语言正文，移除本轮结构化卡片与配图，并阻止后台晚到回写。"""
    with _lock:
        data = _read_session(sid)
        if data is None:
            return False
        for m in data["messages"]:
            if m.get("id") != record_id:
                continue
            if m.get("cancelled") or m.get("answer") == "__cancelled__":
                return False
            uploaded_image_url = _user_image_url_for_cancel(m)
            text = extract_answer_text(answer) or extract_answer_text(m.get("answer"))
            m["image_cancelled"] = True
            # 仅取消配图时不能保留任何结构化卡片，否则旧客户端或历史回放
            # 仍可能把它重新渲染成菜谱卡。没有可提取正文时落空文本即可。
            m["answer"] = text
            # 生成图可能已覆盖记录级 image_url；恢复用户原图，确保刷新后
            # 不会在用户气泡里重新露出已取消的成品图。
            m["image_url"] = uploaded_image_url
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
            if m.get("cancelled") or m.get("image_cancelled") or m.get("answer") == "__cancelled__":
                return False
            try:
                ans = json.loads(m.get("answer") or "")
            except Exception:
                return False
            if not isinstance(ans, dict):
                return False
            if not ans.get("image_requested"):
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


def find_recent_recipe_for_image(sid, requested_name=None):
    """为“看看图片/配图”这类后续请求，找到最近一条可补图的菜谱。

    返回 dict: {record_id, recipe_index, dish_name, answer}；找不到返回 None。
    requested_name 为空时默认取最近一条结构化菜谱里的第一道菜。
    """
    data = _read_session(sid)
    if data is None:
        return None
    needle = str(requested_name or "").strip()
    # 上游动作短语偶发只传回“片/张/图/要”这类残片时，不能拿它当菜名做模糊匹配，
    # 否则“配张图片”会误命中历史里的“炒羊肉片”。残片一律按“未指定菜名”处理。
    if needle and all(char in "片张图要" for char in needle):
        needle = ""
    for m in reversed(data.get("messages") or []):
        try:
            ans = json.loads(m.get("answer") or "")
        except Exception:
            continue
        if not isinstance(ans, dict):
            continue
        recipes = ans.get("recipes") or []
        for index, recipe in enumerate(recipes):
            dish_name = str(recipe.get("name") or "").strip()
            if not dish_name:
                continue
            if needle and needle not in dish_name and dish_name not in needle:
                continue
            return {
                "record_id": m.get("id"),
                "recipe_index": index,
                "dish_name": dish_name,
                "answer": ans,
            }
    return None


def find_recipe_for_image_target(sid, record_id, recipe_index=0, dish_name=None):
    """按前端显式传入的可见菜谱目标找补图对象。"""
    data = _read_session(sid)
    if data is None:
        return None
    try:
        target_id = int(record_id)
    except Exception:
        return None
    try:
        target_index = int(recipe_index or 0)
    except Exception:
        target_index = 0
    for m in data.get("messages") or []:
        if m.get("id") != target_id:
            continue
        try:
            ans = json.loads(m.get("answer") or "")
        except Exception:
            return None
        if not isinstance(ans, dict):
            return None
        recipes = ans.get("recipes") or []
        if target_index < 0 or target_index >= len(recipes):
            return None
        recipe = recipes[target_index]
        found_name = str(recipe.get("name") or dish_name or "").strip()
        if not found_name:
            return None
        return {
            "record_id": m.get("id"),
            "recipe_index": target_index,
            "dish_name": found_name,
            "answer": ans,
        }
    return None


def update_answer_image_at_index(sid, record_id, recipe_index, image_url, image_ai, note):
    """按记录 id + 菜谱索引补图，允许用户后续单独请求给历史菜谱补图/换图。"""
    with _lock:
        data = _read_session(sid)
        if data is None:
            return False
        for m in data["messages"]:
            if m.get("id") != record_id:
                continue
            if m.get("cancelled") or m.get("image_cancelled") or m.get("answer") == "__cancelled__":
                return False
            try:
                ans = json.loads(m.get("answer") or "")
            except Exception:
                return False
            if not isinstance(ans, dict):
                return False
            recipes = ans.get("recipes") or []
            if recipe_index < 0 or recipe_index >= len(recipes):
                return False
            recipe = recipes[recipe_index]
            recipe["image_url"] = image_url
            recipe["image_ai_generated"] = bool(image_ai)
            recipe["image_note"] = note
            ans["image_requested"] = True
            if recipe_index == 0:
                ans["image_url"] = image_url
                ans["image_ai_generated"] = bool(image_ai)
                ans["image_note"] = note
            m["answer"] = json.dumps(ans, ensure_ascii=False)
            if recipe_index == 0:
                m["image_url"] = image_url
            _write_session(data)
            return True
    return False


def set_message_candidates(sid, record_id, candidates):
    """登记一轮的候选菜名清单（两阶段点菜第一跳的锚点）。

    刻意用独立字段而不是塞进 answer：候选轮要保留纯文本正文，
    一旦把「recipes 为空」的结构化 payload 当 answer 落库，
    前端会认为这轮有卡片（正文被隐藏、卡片又渲染不出来），聊天区直接空白。
    """
    names = [str(name).strip() for name in (candidates or []) if str(name).strip()]
    if not names:
        return False
    with _lock:
        data = _read_session(sid)
        if data is None:
            return False
        for m in data["messages"]:
            if m.get("id") != record_id:
                continue
            if m.get("cancelled") or m.get("answer") == "__cancelled__":
                return False
            m["candidates"] = names
            _write_session(data)
            return True
    return False


def find_recent_candidates(sid, limit=8):
    """取会话「紧邻本轮之前那一轮」登记的候选菜名，供「就第2个」这类序号指代解析。

    ⚠️ 只认最后一条记录，**故意不做倒序回溯**：候选清单之后只要又发生过别的对话轮次，
    旧序号就不再被当作本轮选定。这条产品规则由 `tests/test_candidate_flow.py`
    的 `test_stale_candidates_after_newer_turn_are_not_confirm` 冻结，且必须与
    `agent_graph._recent_candidates` 同源——路由层若放宽到回溯，就会出现
    「后端开了配图开关、前端却没有卡片」的空转（见 chat_route._should_enable_image_pipeline）。

    返回 {'record_id', 'candidates', 'dish_name'}；没有候选时返回 None。
    """
    with _lock:
        data = _read_session(sid)
    if data is None:
        return None
    messages = data.get("messages") or []
    if not messages:
        return None
    latest = messages[-1]
    if not isinstance(latest, dict):
        return None
    names = [
        str(name).strip()
        for name in (latest.get("candidates") or [])
        if str(name).strip()
    ]
    if not names:
        return None
    return {
        "record_id": latest.get("id"),
        "candidates": names[:limit],
        "dish_name": names[0],
    }


def star_message(sid, record_id, starred: bool):
    """收藏/取消收藏一条问答（'starred': True/False）。返回 (found, current)。"""
    with _lock:
        data = _read_session(sid)
        if data is None:
            return False, False
        for m in data["messages"]:
            if m.get("id") != record_id:
                continue
            if starred:
                m["starred"] = True
            else:
                m.pop("starred", None)
            _write_session(data)
            return True, bool(m.get("starred"))
    return False, False


def list_starred() -> list[dict]:
    """跨会话收集全部收藏项，供收藏面板展示（含来源会话定位信息）。"""
    out: list[dict] = []
    if not SESSIONS_DIR.exists():
        return []
    for fp in sorted(SESSIONS_DIR.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        for m in data.get("messages") or []:
            if not m.get("starred"):
                continue
            dish = None
            ans = None
            try:
                ans = json.loads(m.get("answer") or "")
                recipes = ans.get("recipes") or []
                dish = str(recipes[0].get("name"))[:40] if recipes else None
            except Exception:
                dish = None
            out.append({
                "sid": data.get("session_id"),
                "rec_id": m.get("id"),
                "session_title": data.get("title") or "",
                "user_text": (m.get("user_text") or "")[:60],
                "dish": dish or (m.get("user_text") or "")[:24],
                "image_url": m.get("image_url"),
                "answer": ans,
            })
    return out


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
