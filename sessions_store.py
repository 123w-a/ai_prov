# sessions_store.py
# 会话持久化层：用 SQLite 存"会话"和"每条问答"，取代前端本地的 sessions.json。
# 重要：所有会话的增删改查都集中在这里，路由层(routes)只管 HTTP，不直接碰 SQL。

import base64
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime

# 数据库文件与本模块同目录（项目根）。第一次运行会自动建表。
DB_PATH = Path(__file__).with_name("sessions.db")


def _conn():
    # check_same_thread=False：FastAPI 是多线程，允许跨线程用同一个连接
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # 让查询结果能用字段名访问，返回更像字典
    return conn


def init_db():
    """建表。必须在 FastAPI 启动后立刻调用一次。"""
    conn = _conn()
    # 会话表：一个会话 = 一轮连续对话
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            title TEXT,
            created_at TEXT
        )"""
    )
    # 消息表：每条"用户问+AI答"是一对记录，靠 session_id 关联回会话
    conn.execute(
        """CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            user_text TEXT,
            answer TEXT,
            time TEXT,
            image_name TEXT,
            image_type TEXT,
            image_data BLOB
        )"""
    )
    conn.commit()
    conn.close()


def create_session():
    """新建一个空会话，返回 {session_id, title, messages:[]}。"""
    sid = f"user_{uuid.uuid4().hex[:10]}"
    now = datetime.now().strftime("%H:%M")
    conn = _conn()
    conn.execute("INSERT INTO sessions VALUES (?,?,?)", (sid, "新对话", now))
    conn.commit()
    conn.close()
    return {"session_id": sid, "title": "新对话", "messages": []}


def list_sessions():
    """返回全部会话（含各自消息），按创建时间倒序。前端渲染侧栏列表用。"""
    conn = _conn()
    rows = conn.execute(
        """SELECT s.session_id, s.title, s.created_at,
                  (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.session_id) AS cnt
           FROM sessions s ORDER BY s.created_at DESC"""
    ).fetchall()
    out = []
    for s in rows:
        msgs = conn.execute(
            """SELECT id, user_text, answer, time, image_name, image_type, image_data
               FROM messages WHERE session_id=? ORDER BY id ASC""",
            (s["session_id"],),
        ).fetchall()
        out.append(
            {
                "session_id": s["session_id"],
                "title": s["title"],
                "time": s["created_at"],
                "messages": [
                    {
                        "id": m["id"],
                        "user_text": m["user_text"],
                        "answer": m["answer"],
                        "time": m["time"],
                        # SQLite BLOB 不能直接 JSON 序列化；包装成前端可直接用的 dict
                        "image_data": (
                            {
                                "name": m["image_name"],
                                "type": m["image_type"],
                                "data": base64.b64encode(m["image_data"]).decode("ascii"),
                            }
                            if m["image_data"]
                            else None
                        ),
                    }
                    for m in msgs
                ],
            }
        )
    conn.close()
    return out


def delete_session(sid):
    """彻底删除整个会话及其全部消息。"""
    conn = _conn()
    conn.execute("DELETE FROM messages WHERE session_id=?", (sid,))
    conn.execute("DELETE FROM sessions WHERE session_id=?", (sid,))
    conn.commit()
    conn.close()


def clear_session(sid):
    """清空会话内所有消息，但保留会话本身（标题复位为"新对话"）。"""
    conn = _conn()
    conn.execute("DELETE FROM messages WHERE session_id=?", (sid,))
    conn.execute("UPDATE sessions SET title=? WHERE session_id=?", ("新对话", sid))
    conn.commit()
    conn.close()


def delete_message(sid, msg_id):
    """删除单一条问答记录。"""
    conn = _conn()
    conn.execute(
        "DELETE FROM messages WHERE session_id=? AND id=?", (sid, msg_id)
    )
    conn.commit()
    conn.close()


def append_message(
    sid,
    user_text,
    answer,
    time,
    image_name=None,
    image_type=None,
    image_data=None,
):
    """追加一条问答。第一条消息会自动用问题文本当会话标题。"""
    conn = _conn()
    conn.execute(
        """INSERT INTO messages
           (session_id, user_text, answer, time, image_name, image_type, image_data)
           VALUES (?,?,?,?,?,?,?)""",
        (sid, user_text, answer, time, image_name, image_type, image_data),
    )
    # 只有第一条消息时，用问题前 22 字做侧栏标题
    cnt = conn.execute(
        "SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (sid,)
    ).fetchone()["c"]
    if cnt == 1:
        conn.execute(
            "UPDATE sessions SET title=? WHERE session_id=?", (user_text[:22], sid)
        )
    conn.commit()
    conn.close()
