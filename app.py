import base64
import html
import os
import re
import json  # 解析后端 SSE 流式事件
from pathlib import Path
from datetime import datetime

import requests
import streamlit as st
import streamlit.components.v1 as components


st.set_page_config(
    page_title="AI 私厨",
    page_icon="🍳",
    layout="wide",
    initial_sidebar_state="expanded",
)


DEFAULT_API_URL = os.getenv("AI_CHEF_API_URL", "http://127.0.0.1:8010")
LOGO_PATH = Path(__file__).with_name("ai_chef_logo.jpg")

QUICK_PROMPTS = [
    ("🥗", "减脂餐推荐"),
    ("🍲", "今晚吃什么"),
    ("🔄", "食材替换建议"),
    ("⚡", "快手家常菜"),
]

TASTE_OPTIONS = ["重辣", "清淡", "减脂", "增肌"]


# --------------------------------------------------------------------------- #
#  样式
# --------------------------------------------------------------------------- #
def inject_styles():
    st.markdown(
        """
        <style>
        :root {
            --orange: #ff7043;
            --orange-dark: #ff5722;
            --cream: #fffaf5;
            --peach: #fff0e0;
            --brown: #3e2723;
            --brown-light: #5d4037;
            --green: #1b5e20;
            --fresh: #edf8e9;
        }

        .stApp {
            background:
                radial-gradient(circle at 10% 8%, rgba(255, 183, 77, .16), transparent 24%),
                radial-gradient(circle at 95% 10%, rgba(139, 195, 74, .13), transparent 22%),
                linear-gradient(135deg, #fffaf5 0%, #fff4e8 48%, #fffaf5 100%);
            color: var(--brown);
        }

        /* ---------- 主内容区加宽（对话界面是主角，默认 730px 太窄） ---------- */
        [data-testid="stMainBlockContainer"],
        .block-container {
            max-width: 1500px !important;
            padding-left: 2.5rem;
            padding-right: 2.5rem;
        }

        /* ---------- 侧栏 ---------- */
        [data-testid="stSidebar"] {
            background: rgba(255, 250, 245, .96);
            border-right: 1px solid rgba(255, 112, 67, .14);
        }
        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3 {
            color: var(--brown);
        }

        /* ---------- 侧栏 Logo（裁剪掉英文 tagline） ---------- */
        [data-testid="stSidebar"] .sidebar-logo {
            text-align: center;
            padding: .4rem .2rem .6rem;
            margin-bottom: .6rem;
            background: rgba(255, 255, 255, .55);
            border: 1px solid rgba(255, 112, 67, .1);
            border-radius: 18px;
        }
        [data-testid="stSidebar"] .sidebar-logo img {
            width: 150px !important;
            height: 130px !important;
            object-fit: cover;
            object-position: top center;
            border-radius: 12px;
            display: block;
            margin: 0 auto;
        }

        /* ---------- Hero 压缩条 ---------- */
        .hero-bar {
            display: flex;
            align-items: center;
            gap: .8rem;
            padding: .65rem 1.3rem;
            border: 1px solid rgba(255, 112, 67, .15);
            border-radius: 16px;
            background: linear-gradient(115deg, rgba(255, 255, 255, .90), rgba(255, 240, 224, .80));
            box-shadow: 0 8px 24px rgba(104, 57, 32, .08);
            margin-bottom: .6rem;
        }
        .hero-bar h1 {
            margin: 0;
            color: var(--brown);
            font-size: 1.4rem;
            font-weight: 700;
            line-height: 1.2;
            white-space: nowrap;
        }
        .hero-bar p {
            margin: 0;
            color: var(--brown-light);
            font-size: .82rem;
            line-height: 1.3;
        }
        .hero-bar .hero-emojis {
            margin-left: auto;
            font-size: 1.3rem;
            letter-spacing: .2rem;
            opacity: .5;
            white-space: nowrap;
        }

        /* ---------- 分区标题 ---------- */
        .section-title {
            display: flex;
            align-items: center;
            gap: .5rem;
            margin: .7rem 0 .35rem;
            color: var(--brown);
            font-size: 1rem;
            font-weight: 700;
        }

        /* ---------- 分隔线 ---------- */
        .divider {
            margin: .7rem 0 .2rem;
            color: #c27a57;
            text-align: center;
            letter-spacing: .3rem;
            opacity: .65;
            font-size: .8rem;
        }

        /* ---------- 厨师烹饪等待动画（三个跳动的点，防"变白像卡住"） ---------- */
        .cooking-indicator {
            display: inline-flex;
            align-items: center;
            gap: .3rem;
            font-size: .88rem;
            color: var(--brown-light);
        }
        .cooking-dots {
            display: inline-flex;
            gap: .18rem;
            margin-left: .2rem;
        }
        .cooking-dots i {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: var(--orange);
            display: inline-block;
            animation: cooking-bounce 1.2s infinite ease-in-out;
        }
        .cooking-dots i:nth-child(2) { animation-delay: .15s; }
        .cooking-dots i:nth-child(3) { animation-delay: .3s; }
        @keyframes cooking-bounce {
            0%, 60%, 100% { transform: translateY(0); opacity: .5; }
            30% { transform: translateY(-5px); opacity: 1; }
        }

        /* ---------- 侧栏「回答方式」radio 高亮：选中用主题橙，未选中用棕色，一眼看清 ---------- */
        [data-testid="stRadio"] label p,
        [data-testid="stRadio"] div[data-testid="stWidgetLabel"] p {
            color: var(--orange) !important;
            font-weight: 700 !important;
            font-size: 1.05rem !important;
        }
        [data-testid="stRadio"] input[type="radio"] + div {
            border-color: var(--brown-light) !important;
            width: 20px !important;
            height: 20px !important;
        }
        [data-testid="stRadio"] input[type="radio"]:checked + div {
            border-color: var(--orange) !important;
            background-color: var(--orange) !important;
            box-shadow: 0 0 0 4px rgba(255, 112, 67, .18) !important;
        }
        [data-testid="stRadio"] input[type="radio"]:checked + div > div {
            background-color: #fff !important;
        }
        [data-testid="stRadio"] div[role="radiogroup"] > div {
            align-items: center;
            gap: .35rem;
        }

        /* ---------- 成品图加载占位：6 秒没出来就切换文案，避免用户以为卡死 ---------- */
        @keyframes img-placeholder-fade {
            0%, 99.99% { opacity: 1; }
            100% { opacity: 0; pointer-events: none; }
        }
        @keyframes img-placeholder-show {
            0%, 99.99% { opacity: 0; }
            100% { opacity: 1; }
        }
        .img-loading-wrap {
            position: relative;
            min-height: 40px;
        }
        .img-loading-text,
        .img-timeout-text {
            font-size: 12px;
            color: #999;
            padding: 10px;
            text-align: center;
            border: 1px dashed rgba(255, 112, 67, .35);
            border-radius: 10px;
            box-sizing: border-box;
        }
        .img-loading-text {
            animation: img-placeholder-fade 0.01s 6s linear forwards;
        }
        .img-timeout-text {
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            opacity: 0;
            animation: img-placeholder-show 0.01s 6s linear forwards;
        }
        /* ---------- AI 生成示意图专用徽标：醒目、明确，绝不伪装成实拍图 ---------- */
        .ai-img-badge {
            margin-top: 8px;
            font-size: 12px;
            font-weight: 700;
            color: #b34900;
            background: #fff4e6;
            border: 1px solid #ffb877;
            border-radius: 10px;
            padding: 6px 10px;
            text-align: center;
        }
        .ai-img-badge span {
            font-weight: 400;
            color: #c0661f;
        }

        /* ---------- 左右对话气泡（核心） ---------- */
        .chat-row {
            display: flex;
            width: 100%;
            margin-bottom: .2rem;
        }
        .user-row {
            justify-content: flex-end;
        }
        .ai-row {
            justify-content: flex-start;
        }
        .bubble {
            max-width: 86%;
            border-radius: 14px;
            padding: .7rem .9rem;
            line-height: 1.65;
            color: var(--brown) !important;
            font-size: .92rem;
        }
        .user-bubble {
            background: #fff0e0;
            border: 1px solid rgba(255, 112, 67, .18);
            border-top-left-radius: 4px;
        }
        .ai-bubble {
            background: #ffffff;
            border: 1px solid rgba(255, 112, 67, .12);
            border-top-right-radius: 4px;
            box-shadow: 0 2px 10px rgba(104, 57, 32, .05);
        }
        .bubble-meta {
            font-size: .72rem;
            font-weight: 700;
            margin-bottom: .25rem;
            opacity: .75;
            color: var(--brown-light) !important;
        }
        .bubble-body {
            color: var(--brown) !important;
        }
        .bubble-body p,
        .bubble-body li,
        .bubble-body strong,
        .bubble-body h1,
        .bubble-body h2,
        .bubble-body h3,
        .bubble-body h4,
        .bubble-body code,
        .bubble-body pre {
            color: var(--brown) !important;
        }
        .bubble-body a {
            color: #d84315 !important;
            text-decoration: underline;
        }
        .bubble-body img {
            display: block;
            width: min(100%, 420px);
            max-height: 320px;
            margin: .7rem auto .15rem;
            object-fit: contain;
            border-radius: 12px;
            border: 1px solid rgba(255, 112, 67, .16);
            box-shadow: 0 6px 16px rgba(104, 57, 32, .08);
        }
        .img-wrap {
            display: block;
            text-align: center;
        }
        .img-fallback {
            display: none;
            font-size: 13px;
            color: #795548;
            background: #fff3e0;
            border: 1px dashed rgba(255, 112, 67, .35);
            border-radius: 10px;
            padding: 12px 14px;
            margin: .4rem auto;
            max-width: 420px;
            text-align: center;
            line-height: 1.5;
        }

        /* ---------- AI 操作按钮行 ---------- */
        .ai-actions {
            display: flex;
            justify-content: flex-end;
            gap: .4rem;
            margin-top: .1rem;
            margin-bottom: .6rem;
        }

        /* ---------- 空状态 ---------- */
        .chat-empty {
            padding: 3rem 1rem;
            color: #8d6e63;
            text-align: center;
            line-height: 1.8;
            font-size: .9rem;
        }
        .chat-empty .icon {
            margin-bottom: .5rem;
            font-size: 2.6rem;
        }
        .chat-empty strong {
            display: block;
            color: var(--brown);
            font-size: 1rem;
            margin-bottom: .2rem;
        }

        /* ---------- 提示卡 ---------- */
        .tip {
            padding: .7rem .85rem;
            border-left: 4px solid var(--orange);
            border-radius: 8px;
            background: rgba(255, 240, 224, .75);
            color: #795548;
            font-size: .8rem;
            line-height: 1.65;
            margin-top: .6rem;
        }

        /* ---------- 文本框（动态高度） ---------- */
        .stTextArea textarea {
            border: 1px solid rgba(121, 85, 72, .3);
            border-radius: 12px;
            background: #fffaf5;
            color: var(--brown);
            min-height: 60px;
            max-height: 300px;
            field-sizing: content;
            overflow-y: auto;
        }
        .stTextArea textarea:focus {
            border-color: var(--orange);
            box-shadow: 0 0 0 2px rgba(255, 112, 67, .12);
        }

        /* ---------- 按钮 ---------- */
        .stButton > button,
        .stFormSubmitButton > button {
            border: 0;
            border-radius: 10px;
            background: linear-gradient(135deg, #ff7043, #ff5722);
            color: white;
            font-weight: 600;
            box-shadow: 0 6px 14px rgba(255, 87, 34, .18);
            transition: transform .15s ease, box-shadow .15s ease;
        }
        .stButton > button:hover,
        .stFormSubmitButton > button:hover {
            transform: translateY(-1px);
            box-shadow: 0 10px 20px rgba(255, 87, 34, .25);
            color: white;
        }

        /* ---------- 文件上传器 ---------- */
        [data-testid="stFileUploader"] {
            padding: .3rem;
            border: 1px solid rgba(255, 112, 67, .15);
            border-radius: 12px;
            background: rgba(255, 250, 245, .7);
        }

        /* ---------- 滚动对话区 ---------- */
        .stVerticalBlock > div[style*="overflow"] {
            border: 1px solid rgba(255, 112, 67, .1);
            border-radius: 14px;
            background: rgba(255, 255, 255, .35);
        }

        /* ---------- 侧栏会话列表 ---------- */
        .session-item {
            padding: .45rem .55rem;
            border-radius: 10px;
            background: rgba(255, 240, 224, .55);
            border: 1px solid rgba(255, 112, 67, .08);
            margin-bottom: .3rem;
            font-size: .8rem;
            color: var(--brown-light);
            line-height: 1.4;
            cursor: pointer;
            transition: background .15s;
        }
        .session-item:hover {
            background: rgba(255, 224, 200, .75);
        }
        .session-item.active {
            background: rgba(255, 112, 67, .12);
            border-color: rgba(255, 112, 67, .25);
        }
        .session-item strong {
            color: var(--brown);
        }

        @media (max-width: 780px) {
            .hero-bar {
                padding: .5rem 1rem;
            }
            .hero-bar h1 {
                font-size: 1.15rem;
            }
            .hero-bar .hero-emojis {
                display: none;
            }
            .bubble {
                max-width: 88%;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
#  状态初始化（会话概念）
# --------------------------------------------------------------------------- #
def api_list_sessions(api_url, timeout=30):
    endpoint = f"{api_url.rstrip('/')}/api/sessions"
    response = requests.get(endpoint, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    return data.get("sessions", [])


def api_create_session(api_url, timeout=30):
    endpoint = f"{api_url.rstrip('/')}/api/sessions"
    response = requests.post(endpoint, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    session = data.get("session")
    if not session:
        raise RuntimeError("后端创建会话成功，但没有返回会话数据")
    return session


def api_delete_session(api_url, session_id, timeout=30):
    endpoint = f"{api_url.rstrip('/')}/api/sessions/{session_id}"
    response = requests.delete(endpoint, timeout=timeout)
    response.raise_for_status()


def api_clear_session(api_url, session_id, timeout=30):
    endpoint = f"{api_url.rstrip('/')}/api/sessions/{session_id}/clear"
    response = requests.post(endpoint, timeout=timeout)
    response.raise_for_status()


def api_delete_message(api_url, session_id, message_id, timeout=30):
    endpoint = f"{api_url.rstrip('/')}/api/sessions/{session_id}/messages/{message_id}"
    response = requests.delete(endpoint, timeout=timeout)
    response.raise_for_status()


def refresh_sessions(preferred_session_id=None):
    """从后端重新读取会话，前端 session_state 只保存临时缓存。"""
    try:
        sessions = api_list_sessions(DEFAULT_API_URL)
    except requests.exceptions.RequestException as exc:
        st.session_state["sessions"] = []
        # 5xx 是后端处理出错，4xx 是请求参数问题，连不上才是网络/未启动
        if "500" in str(exc) or "502" in str(exc) or "503" in str(exc):
            hint = "后端服务内部出错，请检查终端日志或重启 run.py"
        elif "404" in str(exc):
            hint = "后端接口不存在，请确认 run.py 已正常启动"
        elif "Connection refused" in str(exc) or "Max retries" in str(exc):
            hint = "连接不上后端，请确认 8010 端口已启动"
        else:
            hint = "读取后端会话失败"
        st.session_state["sessions_error"] = f"{hint}：{exc}"
        st.session_state["current_session_index"] = None
        return False

    st.session_state["sessions"] = sessions
    st.session_state.pop("sessions_error", None)

    if preferred_session_id:
        for index, session in enumerate(sessions):
            if session.get("session_id") == preferred_session_id:
                st.session_state["current_session_index"] = index
                return True

    current_index = st.session_state.get("current_session_index")
    if current_index is not None and 0 <= current_index < len(sessions):
        return True

    st.session_state["current_session_index"] = 0 if sessions else None
    return True


def init_state():
    # 每次页面运行都同步后端会话，确保后端恢复/修复后错误提示能自动消失
    preferred = None
    sessions = st.session_state.get("sessions", [])
    idx = st.session_state.get("current_session_index")
    if idx is not None and 0 <= idx < len(sessions):
        preferred = sessions[idx].get("session_id")
    refresh_sessions(preferred_session_id=preferred)

    defaults = {
        "uploaded_image": None,
        "uploader_version": 0,
        "taste_prefs": [],
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

    if st.session_state.pop("clear_question", False):
        st.session_state["question"] = ""


def create_new_session(set_current: bool = True):
    try:
        session = api_create_session(DEFAULT_API_URL)
    except requests.exceptions.RequestException as exc:
        st.error(f"无法创建新会话，请确认后端已启动：{exc}")
        return None

    refresh_sessions(
        preferred_session_id=session.get("session_id") if set_current else None
    )
    return session


def get_current_session():
    idx = st.session_state.get("current_session_index")
    sessions = st.session_state.get("sessions", [])
    if idx is not None and 0 <= idx < len(sessions):
        return sessions[idx]

    if sessions:
        st.session_state["current_session_index"] = 0
        return sessions[0]

    return None


def switch_session(index: int):
    sessions = st.session_state.get("sessions", [])
    if 0 <= index < len(sessions):
        selected_session_id = sessions[index].get("session_id")
        st.session_state["current_session_index"] = index
        st.session_state["uploaded_image"] = None
        st.session_state["uploader_version"] += 1
        st.session_state["question"] = ""
        refresh_sessions(preferred_session_id=selected_session_id)
        st.rerun()


def delete_session(index: int):
    sessions = st.session_state.get("sessions", [])
    if 0 <= index < len(sessions):
        session_id = sessions[index].get("session_id")
        try:
            api_delete_session(DEFAULT_API_URL, session_id)
        except requests.exceptions.RequestException as exc:
            st.error(f"删除会话失败：{exc}")
            return
        refresh_sessions()
        st.rerun()


def clear_current_session():
    session = get_current_session()
    if session is None:
        return
    try:
        api_clear_session(DEFAULT_API_URL, session["session_id"])
    except requests.exceptions.RequestException as exc:
        st.error(f"清空会话失败：{exc}")
        return
    refresh_sessions(preferred_session_id=session["session_id"])
    st.rerun()


# --------------------------------------------------------------------------- #
#  后端通信
# --------------------------------------------------------------------------- #
def api_chat(api_url, session_id, message, uploaded_file=None, image_url=None, timeout=180):
    endpoint = f"{api_url.rstrip('/')}/api/chat/image"

    files = None
    data = {
        "session_id": session_id,
        "message": message,
    }
    if image_url is not None:
        data["image_url"] = image_url
    elif uploaded_file is not None:
        files = {
            "image": (
                uploaded_file.name,
                uploaded_file.getvalue(),
                uploaded_file.type or "application/octet-stream",
            )
        }

    response = requests.post(
        endpoint,
        data=data,
        files=files,
        timeout=timeout,
    )

    try:
        response_data = response.json()
    except ValueError:
        response.raise_for_status()
        raise RuntimeError("后端返回了无法解析的内容")

    if not response.ok:
        detail = response_data.get("detail", response.text)
        raise RuntimeError(f"后端请求失败：{detail}")

    if response_data.get("code") != 200:
        raise RuntimeError(response_data.get("messages", "后端返回失败"))

    data = response_data.get("data") or {}
    answer = data.get("answer") or data.get("assion")
    if not answer:
        raise RuntimeError("后端返回成功，但没有找到回答内容")

    return str(answer)


# --------------------------------------------------------------------------- #
#  非流式聊天：POST 到统一 /api/chat（stream=false），agent.ainvoke 一次性返回
#  完整 ChefAnswer JSON（干净 dict，无 SSE 封装），适合慢网/调试/第三方对接演示。
# --------------------------------------------------------------------------- #
def api_chat_sync(api_url, session_id, message, uploaded_file=None, image_url=None, timeout=180):
    endpoint = f"{api_url.rstrip('/')}/api/chat"
    files = None
    data = {
        "session_id": session_id,
        "message": message,
        "stream": "false",  # 关键：关掉流式，走 ainvoke 一次性返回
    }
    if image_url is not None:
        data["image_url"] = image_url
    elif uploaded_file is not None:
        files = {
            "image": (
                uploaded_file.name,
                uploaded_file.getvalue(),
                uploaded_file.type or "application/octet-stream",
            )
        }

    response = requests.post(endpoint, data=data, files=files, timeout=timeout)
    try:
        response_data = response.json()
    except ValueError:
        response.raise_for_status()
        raise RuntimeError("后端返回了无法解析的内容")

    if not response.ok:
        detail = response_data.get("detail", response.text)
        raise RuntimeError(f"后端请求失败：{detail}")

    # /api/chat 非流式直接返回 ChefAnswer dict（非信封），可直接交给卡片渲染
    return response_data


# --------------------------------------------------------------------------- #
#  流式聊天：POST 到 /api/chat/stream，用 requests 边收边解析 SSE 事件
#  生成器每次 yield 一个 token，交给 Streamlit 的 st.write_stream 做打字机渲染
# --------------------------------------------------------------------------- #
def api_chat_stream(api_url, session_id, message, uploaded_file=None, image_url=None, timeout=180):
    endpoint = f"{api_url.rstrip('/')}/api/chat/stream"
    files = None
    data = {"session_id": session_id, "message": message}
    if image_url is not None:
        data["image_url"] = image_url
    elif uploaded_file is not None:
        files = {
            "image": (
                uploaded_file.name,
                uploaded_file.getvalue(),
                uploaded_file.type or "application/octet-stream",
            )
        }
    # stream=True 边收边处理，实现打字机效果
    with requests.post(
        endpoint,
        data=data,
        files=files,
        stream=True,
        timeout=timeout,
    ) as response:
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError:
            raise RuntimeError(f"后端请求失败：{response.text}")
        # 逐行读取 SSE：每行形如 "data: {...}"
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            payload = line[len("data: "):]
            try:
                data = json.loads(payload)
            except ValueError:
                continue
            # 两段式事件流：token(正文逐字) → structuring(切骨架屏) → answer(整包JSON画卡片)
            if "token" in data:
                yield ("token", data["token"])
            elif "structuring" in data:
                yield ("structuring", True)
            elif "answer" in data:
                yield ("answer", data["answer"])
            elif "finish" in data:
                return
            elif "error" in data:
                raise RuntimeError(data["error"])


# --------------------------------------------------------------------------- #
#  辅助：存储的图片文件（用于重新生成）
# --------------------------------------------------------------------------- #
class StoredFile:
    """模拟 UploadedFile 接口，供重新生成时使用。"""

    def __init__(self, name, data, type_):
        self.name = name
        self._data = data
        self.type = type_

    def getvalue(self):
        return self._data


def build_message(question, taste_prefs):
    """将口味偏好拼接到问题末尾。"""
    if taste_prefs:
        prefs = "、".join(taste_prefs)
        return f"{question}\n\n（口味偏好：{prefs}）"
    return question


# --------------------------------------------------------------------------- #
#  复制按钮（自包含 iframe）
# --------------------------------------------------------------------------- #
def render_copy_button(text, key):
    safe = (
        text.replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("${", "\\${")
    )
    components.html(
        f"""
        <button id="cp_{key}" style="
            border: 1px solid rgba(255,112,67,.2);
            background: rgba(255,250,245,.6);
            color: #795548;
            border-radius: 8px;
            padding: 3px 10px;
            font-size: 12px;
            cursor: pointer;
            font-family: -apple-system, 'Segoe UI', sans-serif;
            transition: all .15s;
            width: 100%;
        ">📋 复制</button>
        <script>
            (function() {{
                var btn = document.getElementById('cp_{key}');
                btn.addEventListener('click', function() {{
                    var txt = `{safe}`;
                    function ok() {{
                        btn.textContent = '✅ 已复制';
                        btn.style.background = '#edf8e9';
                        btn.style.color = '#1b5e20';
                        btn.style.borderColor = 'rgba(76,175,80,.3)';
                        setTimeout(function() {{
                            btn.textContent = '📋 复制';
                            btn.style.background = 'rgba(255,250,245,.6)';
                            btn.style.color = '#795548';
                            btn.style.borderColor = 'rgba(255,112,67,.2)';
                        }}, 1500);
                    }}
                    function fail() {{
                        btn.textContent = '❌ 失败';
                        setTimeout(function() {{
                            btn.textContent = '📋 复制';
                        }}, 1500);
                    }}
                    if (navigator.clipboard && navigator.clipboard.writeText) {{
                        navigator.clipboard.writeText(txt).then(ok).catch(function() {{
                            var ta = document.createElement('textarea');
                            ta.value = txt;
                            ta.style.position = 'fixed';
                            ta.style.opacity = '0';
                            document.body.appendChild(ta);
                            ta.select();
                            try {{ document.execCommand('copy'); ok(); }}
                            catch(e) {{ fail(); }}
                            document.body.removeChild(ta);
                        }});
                    }} else {{
                        var ta = document.createElement('textarea');
                        ta.value = txt;
                        ta.style.position = 'fixed';
                        ta.style.opacity = '0';
                        document.body.appendChild(ta);
                        ta.select();
                        try {{ document.execCommand('copy'); ok(); }}
                        catch(e) {{ fail(); }}
                        document.body.removeChild(ta);
                    }}
                }});
            }})();
        </script>
        """,
        height=30,
    )


# --------------------------------------------------------------------------- #
#  轻量 Markdown → HTML（用于把 AI 回答完整包进气泡）
# --------------------------------------------------------------------------- #
def md_to_html(text: str) -> str:
    """把常用 Markdown 转成 HTML，保证回答内容能完整放进一个气泡。"""
    lines = text.split("\n")
    out = []
    in_code = False
    code_lines = []
    in_ul = False
    in_ol = False

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    def inline_fmt(s: str) -> str:
        # 图片 Markdown 转成浏览器可以直接显示的 img 标签
        s = html.escape(s)
        image_pattern = r"!\[([^\]]*)\]\((https?://[^)\s]+)\)"

        def image_to_html(match):
            alt = match.group(1)
            url = match.group(2)
            safe_alt = html.escape(alt)
            return (
                f'<span class="img-wrap">'
                f'<img src="{url}" alt="{safe_alt}" class="answer-image" '
                f'onerror="this.onerror=null;this.style.display=\'none\';'
                f'this.nextElementSibling.style.display=\'block\';">'
                f'<span class="img-fallback">🍽️ 该成品图暂时无法显示<br>'
                f'<small style="opacity:.7;">图片来源在当前网络下不可访问（常见于 Instagram / 海外图床）</small></span>'
                f'</span>'
            )

        s = re.sub(image_pattern, image_to_html, s)
        # 普通 Markdown 链接：[文字](https://...)，避免长 URL 撑破气泡
        s = re.sub(
            r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
            r'<a href="\2" target="_blank" style="word-break:break-all;">\1</a>',
            s,
        )
        # 加粗
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        # 斜体
        s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", s)
        # 行内代码
        s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
        return s

    for line in lines:
        # 代码块
        if line.strip().startswith("```"):
            if in_code:
                code_html = "\n".join(code_lines)
                out.append(f'<pre><code>{html.escape(code_html)}</code></pre>')
                code_lines = []
                in_code = False
            else:
                close_lists()
                in_code = True
            continue

        if in_code:
            code_lines.append(line)
            continue

        stripped = line.strip()

        # 空行
        if not stripped:
            close_lists()
            continue

        # 标题
        if re.match(r"^#{1,6} ", stripped):
            close_lists()
            level = len(re.match(r"^#+", stripped).group())
            content = inline_fmt(stripped[level + 1:])
            out.append(f"<h{level}>{content}</h{level}>")
            continue

        # 无序列表
        if re.match(r"^[-*] ", stripped):
            if not in_ul:
                close_lists()
                in_ul = True
                out.append("<ul>")
            item_text = inline_fmt(stripped[2:])
            out.append(f"<li>{item_text}</li>")
            continue

        # 有序列表
        m_ol = re.match(r"^(\d+)\. ", stripped)
        if m_ol:
            if not in_ol:
                close_lists()
                in_ol = True
                out.append("<ol>")
            item_text = inline_fmt(stripped[m_ol.end():])
            out.append(f"<li>{item_text}</li>")
            continue

        # 普通段落
        close_lists()
        out.append(f"<p>{inline_fmt(line)}</p>")

    close_lists()
    return "".join(out)


# --------------------------------------------------------------------------- #
#  流式对话临时渲染（让当前 Q&A 也出现在对话食谱容器里并居中）
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
#  结构化回答（ChefAnswer JSON）→ 卡片 HTML
#  新回答落库的是 JSON 字符串；旧回答是 markdown。双格式兼容：
#  parse_structured_answer 能解析出 recipes 就走卡片，否则走原来的 md_to_html
# --------------------------------------------------------------------------- #
def parse_structured_answer(text):
    """尝试把 answer 解析成 ChefAnswer JSON；成功返回 dict，不是结构化数据返回 None"""
    if not isinstance(text, str):
        return None
    s = text.strip()
    if not s.startswith("{"):
        return None
    try:
        data = json.loads(s)
    except ValueError:
        return None
    # 必须含 recipes 列表才认作 ChefAnswer，避免把别的 JSON 误判
    if isinstance(data, dict) and isinstance(data.get("recipes"), list):
        return data
    return None


def _stars_html(n):
    """1-5 整数 → 5 颗星（亮 n 颗暗 5-n 颗），星级由前端画，不再靠模型画"""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    n = max(0, min(5, n))
    return (
        f'<span style="color:#f5a623;">{"★" * n}</span>'
        f'<span style="color:#dcdcdc;">{"★" * (5 - n)}</span>'
    )


def recipe_card_html(data):
    """ChefAnswer dict → 菜谱卡片 HTML（菜名/简介/双星级/调料表/步骤/图片/小建议）"""
    blocks = []
    for r in data.get("recipes", []):
        name = html.escape(str(r.get("name", "")))
        intro = html.escape(str(r.get("intro", "")))
        diff = _stars_html(r.get("difficulty"))
        nutr = _stars_html(r.get("nutrition"))

        seasoning_items = "".join(
            f'<li><b>{html.escape(str(s.get("name", "")))}</b>：'
            f'{html.escape(str(s.get("amount", "")))}</li>'
            for s in r.get("seasonings", [])
            if isinstance(s, dict)
        )
        seasoning_block = ""
        if seasoning_items:
            seasoning_block = (
                '<div style="margin-top:6px;font-size:13px;"><b>🧂 调料：</b>'
                f'<ul style="margin:4px 0 0;padding-left:18px;">{seasoning_items}</ul></div>'
            )

        step_items = "".join(
            f"<li>{html.escape(str(step))}</li>"
            for step in r.get("steps", [])
        )
        step_block = ""
        if step_items:
            step_block = (
                '<div style="margin-top:6px;font-size:13px;"><b>👨‍🍳 步骤：</b>'
                f'<ol style="margin:4px 0 0;padding-left:18px;">{step_items}</ol></div>'
            )

        blocks.append(
            '<div style="border:1px solid #f0e6d8;border-radius:12px;padding:12px 14px;'
            'margin:10px 0;background:#fffaf3;">'
            f'<div style="font-weight:600;font-size:15px;">🍲 {name}</div>'
            f'<div style="color:#888;font-size:12px;margin:2px 0 6px;">{intro}</div>'
            f'<div style="font-size:13px;">难度 {diff}　营养 {nutr}</div>'
            f"{seasoning_block}{step_block}"
            "</div>"
        )

    # 图片渲染原则：有真 URL 才渲染 <img>，没有就显示 image_note 文字说明，绝不渲染乱码/二进制流。
    # 关键体验：
    #   1) 图片从 OSS 拉取可能较慢——先显示"正在拉取"占位（img 用 display:none 藏起），
    #      等 onload 成功才换图、onerror 改成"加载失败"；
    #   2) 用纯 CSS 动画做 6 秒超时兜底：超过 6 秒还没 onload/onerror，占位自动切换成
    #      "未在合理时间内找到可展示的图片"，避免用户以为卡死或"已经生成完了"。
    img_url = data.get("image_url")
    # 透明标注核心开关：该图是否由 AI 生成（来自 ChefAnswer.image_ai_generated）。
    ai_img = bool(data.get("image_ai_generated"))
    if img_url and str(img_url).startswith(("http://", "https://")):
        safe_img = html.escape(str(img_url))
        if ai_img:
            # AI 生成图：占位文案升级为"正在生成菜品图"，并附醒目徽标，绝不伪装成实拍图
            loading_text = "🤖 正在为你生成菜品图…"
            timeout_text = "🖼️ AI 生成示意图加载较慢，请稍候…"
            alt_text = "AI 生成示意图"
            err_text = "🖼️ AI 生成示意图加载失败，可稍后刷新重试"
            badge = ('<div class="ai-img-badge">🤖 AI 生成示意图'
                     '<span>（非真实成品照，仅供样式参考）</span></div>')
        else:
            # 真实搜索图：维持原"成品图正在拉取"占位
            loading_text = "🍳 成品图正在拉取，请稍候…"
            timeout_text = "🍽️ 未在合理时间内找到可展示的图片"
            alt_text = "成品图"
            err_text = "🍽️ 图片加载失败，可稍后刷新重试"
            badge = ""
        img_block = (
            '<div class="img-loading-wrap" style="margin-top:8px;">'
            f'<div class="img-loading-text">{loading_text}</div>'
            f'<div class="img-timeout-text">{timeout_text}</div>'
            f'<img src="{safe_img}" alt="{alt_text}" '
            f'style="max-width:100%;border-radius:10px;margin-top:8px;display:none;" '
            f'onload="var w=this.parentElement; '
            f'w.querySelector(\'.img-loading-text\').style.display=\'none\'; '
            f'w.querySelector(\'.img-timeout-text\').style.display=\'none\'; '
            f'this.style.display=\'block\';" '
            f'onerror="var w=this.parentElement; '
            f'w.querySelector(\'.img-loading-text\').style.display=\'none\'; '
            f'var t=w.querySelector(\'.img-timeout-text\'); '
            f't.innerHTML=\'{err_text}\'; '
            f't.style.opacity=\'1\'; t.style.display=\'block\';">'
            f"{badge}"
            "</div>"
        )
    else:
        note = html.escape(str(data.get("image_note") or "未找到可正常展示的成品图片。"))
        img_block = (
            f'<div style="font-size:12px;color:#999;margin-top:8px;">🍽️ {note}</div>'
        )

    tip = html.escape(str(data.get("chef_tip", "")).strip())
    tip_block = ""
    if tip:
        tip_block = (
            '<div style="margin-top:8px;font-size:13px;background:#fdf6ec;'
            'border-radius:10px;padding:8px 10px;">'
            f"👨‍🍳 <b>私厨建议：</b>{tip}</div>"
        )
    return "".join(blocks) + img_block + tip_block


def render_streaming_exchange(pending):
    """在对话容器内渲染"正在发送的用户消息 + 正在流式生成的 AI 回答"。

    流式结束后再刷新一次，把完整消息并到历史记录里统一展示。
    """
    session_id = pending["session_id"]
    message = pending["message"]
    question = pending["question"]
    image = pending.get("image")
    time_str = pending["time"]

    # ---- 用户气泡（问题 + 本次上传的缩略图）----
    clean_q = re.sub(r"<[^>]+>", "", question)
    user_html = html.escape(clean_q).replace("\n", "<br>")
    img_html = ""
    if image is not None:
        b64 = base64.b64encode(image.getvalue()).decode("ascii")
        img_html = (
            f'<img src="data:{image.type};base64,{b64}" alt="用户上传图片" '
            f'style="max-width:180px;max-height:140px;border-radius:10px;margin-top:6px;">'
        )
    st.markdown(
        f'<div class="chat-row user-row">'
        f'<div class="bubble user-bubble">'
        f'<div class="bubble-meta">🍴 你 · {time_str}</div>'
        f'<div class="bubble-body">{user_html}</div>{img_html}'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # ---- AI 气泡（先放"厨师烹饪中"动画占位，拿到 token 再逐字换成正文）----
    # 搜索阶段后台要跑好几秒，空白气泡会让用户以为卡死，动画占位给出明确反馈
    ai_placeholder = st.empty()
    card_placeholder = st.empty()  # 结构化卡片单独一个占位：骨架屏 → 卡片

    def cooking_html(text):
        return (
            f'<div class="chat-row ai-row">'
            f'<div class="bubble ai-bubble">'
            f'<div class="bubble-meta">🍳 AI 私厨 · {time_str}</div>'
            f'<div class="bubble-body">'
            f'<span class="cooking-indicator">👨‍🍳 {text}'
            f'<span class="cooking-dots"><i></i><i></i><i></i></span>'
            f'</span></div>'
            f'</div></div>'
        )

    # ---- 统一开关：非流式（直接出结果）走 /api/chat?stream=false，一次性拿完整卡片 ----
    stream_mode = st.session_state.get("stream_mode", "stream")
    if stream_mode == "sync":
        ai_placeholder.markdown(
            cooking_html("私厨正在烹饪中（非流式，请稍候）"), unsafe_allow_html=True
        )
        try:
            data = api_chat_sync(
                DEFAULT_API_URL,
                session_id=session_id,
                message=message,
                uploaded_file=image,
            )
            card_placeholder.markdown(recipe_card_html(data), unsafe_allow_html=True)
        except Exception as exc:
            st.error(f"🍽️ 这次烹饪请求没有完成：{exc}")
            return
        st.session_state.pop("pending_stream", None)
        refresh_sessions(preferred_session_id=session_id)
        st.rerun()

    ai_placeholder.markdown(
        cooking_html("私厨正在识别食材、联网搜菜谱"), unsafe_allow_html=True
    )
    full_parts = []
    try:
        for kind, payload in api_chat_stream(
            DEFAULT_API_URL,
            session_id=session_id,
            message=message,
            uploaded_file=image,
        ):
            if kind == "token":
                # 正文逐字打字机（开场白/做法细节全文流式，体验保留）
                full_parts.append(payload)
                answer_html = md_to_html("".join(full_parts))
                ai_placeholder.markdown(
                    f'<div class="chat-row ai-row">'
                    f'<div class="bubble ai-bubble">'
                    f'<div class="bubble-meta">🍳 AI 私厨 · {time_str}</div>'
                    f'<div class="bubble-body">{answer_html}</div>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )
            elif kind == "structuring":
                # 正文流完，卡片占位切"整理中"动画
                card_placeholder.markdown(
                    '<div style="font-size:12px;color:#999;padding:6px 2px;">'
                    '<span class="cooking-indicator">🍳 正在整理菜谱卡片'
                    '<span class="cooking-dots"><i></i><i></i><i></i></span>'
                    '</span></div>',
                    unsafe_allow_html=True,
                )
            elif kind == "answer":
                # 整包 ChefAnswer JSON 到达，骨架屏替换成卡片
                card_placeholder.markdown(
                    recipe_card_html(payload), unsafe_allow_html=True
                )
    except Exception as exc:
        st.error(f"🍽️ 这次烹饪请求没有完成：{exc}")
        return

    # 流式结束：清掉 pending，刷新历史，rerun 让本轮进入正常历史渲染
    st.session_state.pop("pending_stream", None)
    refresh_sessions(preferred_session_id=session_id)
    st.rerun()


# --------------------------------------------------------------------------- #
#  对话展示（自定义左右气泡）
# --------------------------------------------------------------------------- #
def render_conversation():
    session = get_current_session()
    chat_history = session.get("messages", []) if session is not None else []
    pending = st.session_state.get("pending_stream")

    if not chat_history and not pending:
        st.markdown(
            """
            <div class="chat-empty">
                <div class="icon">🍳</div>
                <strong>厨房还很安静</strong>
                上传食材图片或告诉我你想做什么菜，<br>AI 私厨会为你识别食材、搜索食谱、给出详细做法。
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    for index, item in enumerate(chat_history):
        # ---- 用户消息：左对齐 ----
        # 把用户可能粘贴的 HTML 标签（<div>、</div> 等）全部剥掉，防止显示成代码块
        clean_user_text = re.sub(r"<[^>]+>", "", item["user_text"])
        user_text = html.escape(clean_user_text).replace("\n", "<br>")
        img_html = ""
        img_url = item.get("image_url")
        if img_url:
            # 新数据：只存 OSS URL，前端直接从对象存储拉取
            safe_url = html.escape(img_url)
            img_html = (
                f'<img src="{safe_url}" '
                f'alt="用户上传图片" class="history-user-image" '
                f'style="max-width:180px;max-height:140px;border-radius:10px;margin-top:6px;">'
            )
        else:
            # 兼容旧数据：早期 JSON 可能仍存 base64 的 image_data
            img_data = item.get("image_data")
            if img_data and img_data.get("data"):
                img_html = (
                    f'<img src="data:{img_data["type"]};base64,{img_data["data"]}" '
                    f'alt="用户上传图片" class="history-user-image" '
                    f'style="max-width:180px;max-height:140px;border-radius:10px;margin-top:6px;">'
                )
        # 注意：必须单行拼接 HTML！三引号多行写法里，若 img_html 为空会留下"纯缩进空格行"，
        # markdown 会把它当 blank line 截断 HTML 块，剩下的 </div> 被当成缩进代码块渲染成黑块
        st.markdown(
            f'<div class="chat-row user-row">'
            f'<div class="bubble user-bubble">'
            f'<div class="bubble-meta">🍴 你 · {item["time"]}</div>'
            f'<div class="bubble-body">{user_text}</div>'
            f'{img_html}'
            f'</div></div>',
            unsafe_allow_html=True,
        )

        # ---- AI 消息：右对齐（标题 + 内容完整包在一个气泡内） ----
        # 双格式兼容：新回答是 ChefAnswer JSON → opening 正文 + 卡片；
        # 旧回答是 markdown → 走原来的 md_to_html 渲染
        structured = parse_structured_answer(item["answer"])
        if structured:
            body_html = md_to_html(structured.get("opening", "")) + recipe_card_html(structured)
        else:
            body_html = md_to_html(item["answer"])
        st.markdown(
            f'<div class="chat-row ai-row">'
            f'<div class="bubble ai-bubble">'
            f'<div class="bubble-meta">🍳 AI 私厨 · {item["time"]}</div>'
            f'<div class="bubble-body">{body_html}</div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

        # 操作按钮行（复制 / 重新生成 / 删除）—— 右对齐
        action_cols = st.columns([2.4, 1.0, 1.0, 0.6])
        with action_cols[1]:
            # 结构化回答复制 opening 正文（用户要的是菜的做法，不是 JSON）
            copy_text = structured.get("opening", "") if structured else item["answer"]
            render_copy_button(copy_text, f"copy_{index}")
        with action_cols[2]:
            if st.button("🔄 重新生成", key=f"regen_{index}", use_container_width=True):
                handle_regenerate(index)
        with action_cols[3]:
            if st.button("🗑️", key=f"del_{index}", help="删除这对问答"):
                message_id = item.get("id")
                if message_id is None:
                    st.error("这条历史消息缺少后端编号，暂时无法删除")
                    return
                try:
                    api_delete_message(
                        DEFAULT_API_URL,
                        session["session_id"],
                        message_id,
                    )
                except requests.exceptions.RequestException as exc:
                    st.error(f"删除消息失败：{exc}")
                    return
                refresh_sessions(preferred_session_id=session["session_id"])
                st.rerun()

    # 当前正在流式生成的问答也渲染到对话容器里，避免出现在容器外/不居中
    if pending:
        render_streaming_exchange(pending)


def handle_regenerate(index):
    session = get_current_session()
    messages = session.get("messages", [])
    if not (0 <= index < len(messages)):
        return
    item = messages[index]
    with st.spinner("👨‍🍳 重新生成中……"):
        try:
            uploaded = None
            regen_image_url = item.get("image_url")
            # 兼容旧数据：早期 JSON 仍可能存 base64 image_data
            img_data = item.get("image_data")
            if img_data:
                raw = img_data["data"]
                image_bytes = (
                    base64.b64decode(raw)
                    if isinstance(raw, str)
                    else raw
                )
                uploaded = StoredFile(
                    img_data["name"], image_bytes, img_data["type"]
                )
            message = build_message(item["user_text"], item.get("taste_prefs", []))
            new_answer = api_chat(
                api_url=DEFAULT_API_URL,
                session_id=session["session_id"],
                message=message,
                uploaded_file=uploaded,
                image_url=regen_image_url,
            )
            old_message_id = item.get("id")
            if old_message_id is not None:
                api_delete_message(
                    DEFAULT_API_URL,
                    session["session_id"],
                    old_message_id,
                )
            refresh_sessions(preferred_session_id=session["session_id"])
            st.rerun()
        except requests.exceptions.RequestException as exc:
            if "500" in str(exc) or "502" in str(exc) or "503" in str(exc):
                st.error(f"🍽️ 后端处理出错，请检查终端日志：{exc}")
            else:
                st.error(f"🍽️ 连接不上后端服务：{exc}")
        except Exception as exc:
            st.error(f"🍽️ 重新生成失败：{exc}")


# --------------------------------------------------------------------------- #
#  侧栏
# --------------------------------------------------------------------------- #
def render_sidebar():
    with st.sidebar:
        st.markdown('<div class="sidebar-logo">', unsafe_allow_html=True)
        st.image(str(LOGO_PATH), width=150)
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("## 🧂 厨房设置")

        # 口味偏好
        taste = st.pills(
            "口味偏好（自动带入提问）",
            TASTE_OPTIONS,
            selection_mode="multi",
            default=st.session_state.get("taste_prefs", []),
            key="taste_pills",
        )
        st.session_state["taste_prefs"] = taste or []

        # ---- 回答方式：流式 / 非流式 统一开关 ----
        mode_options = {
            "stream": "🎙️ 边说边出（打字机流式）",
            "sync": "⚡ 一次性出完整菜谱",
        }
        answer_mode = st.radio(
            "回答方式",
            list(mode_options.values()),
            index=0,
            horizontal=True,
            help="两种方式共用同一套 Agent，仅响应方式不同",
        )
        st.session_state["stream_mode"] = (
            "sync" if answer_mode == mode_options["sync"] else "stream"
        )

        st.markdown("---")

        # ---- 会话操作区 ----
        col_new, col_clear = st.columns(2)
        with col_new:
            if st.button("➕ 新建会话", use_container_width=True):
                new_session = create_new_session(set_current=True)
                if new_session is not None:
                    st.session_state["uploaded_image"] = None
                    st.session_state["uploader_version"] += 1
                    st.session_state["question"] = ""
                    st.rerun()
        with col_clear:
            if st.button("🧹 清空当前", use_container_width=True):
                clear_current_session()

        # ---- 会话列表 ----
        st.markdown("## 🍲 会话记录")

        sessions = st.session_state.get("sessions", [])
        current_idx = st.session_state.get("current_session_index", 0)

        if st.session_state.get("sessions_error"):
            st.error(st.session_state["sessions_error"])

        if not sessions:
            st.caption("暂无会话，点击「新建会话」开始")
        else:
            for idx, session in enumerate(sessions):
                is_active = idx == current_idx
                active_class = "active" if is_active else ""
                title = session.get("title", "新对话")
                time_str = session.get("time", "")
                msg_count = len(session.get("messages", []))
                count_badge = f" · {msg_count} 条" if msg_count > 0 else ""

                text_col, del_col = st.columns([0.78, 0.22])
                with text_col:
                    # 点击会话切换
                    clicked = st.button(
                        f"{title}{count_badge}\n\n{time_str}",
                        key=f"session_switch_{idx}",
                        use_container_width=True,
                    )
                    if clicked and not is_active:
                        switch_session(idx)
                with del_col:
                    if st.button("×", key=f"session_del_{idx}", help="删除这个会话"):
                        delete_session(idx)

        st.markdown("---")

        # 使用提示（最底部）
        st.markdown(
            """
            <div class="tip">
                💡 <strong>使用提示</strong><br>
                • 点击「新建会话」开启一轮新话题<br>
                • 同一会话里的连续问答会自动归在一起<br>
                • 上传食材图片让 AI 识别<br>
                • 选择口味偏好获取个性化推荐<br>
                • 🎙️ 边说边出：像直播一样逐字显示 AI 思考过程；<br>
                • ⚡ 一次性出完整菜谱：直接拿到完整卡片，适合想快速看结果
            </div>
            """,
            unsafe_allow_html=True,
        )


# --------------------------------------------------------------------------- #
#  输入栏
# --------------------------------------------------------------------------- #
def render_input_bar():
    # ---- 图片上传 ----
    uploaded_file = st.file_uploader(
        "📸 上传食材或菜品图片",
        type=["jpg", "jpeg", "png"],
        label_visibility="visible",
        key=f"image_uploader_{st.session_state['uploader_version']}",
    )
    if uploaded_file is not None:
        st.session_state["uploaded_image"] = uploaded_file

    current_image = st.session_state.get("uploaded_image")

    # ---- 缩略图预览 ----
    if current_image is not None:
        thumb_col, btn_col = st.columns([0.88, 0.12])
        with thumb_col:
            st.image(current_image, width=72)
        with btn_col:
            if st.button("🗑️", key="clear_img", help="清空已选图片"):
                st.session_state["uploaded_image"] = None
                st.session_state["uploader_version"] += 1
                st.rerun()

    # ---- 快捷问题 ----
    st.markdown(
        '<div class="section-title" style="font-size:.9rem;margin:.5rem 0 .25rem">⚡ 快捷提问</div>',
        unsafe_allow_html=True,
    )
    prompt_cols = st.columns(len(QUICK_PROMPTS))
    for i, (emoji, text) in enumerate(QUICK_PROMPTS):
        if prompt_cols[i].button(
            f"{emoji} {text}", key=f"prompt_{i}", use_container_width=True
        ):
            st.session_state["question"] = text
            st.rerun()

    # ---- 文本输入 + 发送 ----
    question = st.text_area(
        "烹饪问题",
        key="question",
        placeholder="比如：番茄牛腩怎么做更软烂？或者直接上传食材图片让我帮你搭配！",
        label_visibility="collapsed",
    )

    session = get_current_session()
    session_exists = session is not None
    if not session_exists:
        st.caption("💡 请先在左侧点击「➕ 新建会话」开始一轮新对话。")

    can_submit = session_exists and (current_image is not None or bool(question.strip()))

    help_text = (
        "请先点击「新建会话」，再输入问题或上传图片。"
        if not session_exists
        else "输入问题或上传图片后点击发送。"
    )
    send_clicked = st.button(
        "✨ 发送给 AI 私厨",
        type="primary",
        use_container_width=True,
        disabled=not can_submit,
        help=help_text,
    )

    if send_clicked:
        session = get_current_session()
        if session is None:
            st.error("请先在左侧点击「➕ 新建会话」开始一轮新对话。")
        else:
            taste_prefs = st.session_state.get("taste_prefs", [])
            raw_question = question.strip() or "请识别图片中的食材，并推荐适合的家常菜。"
            # 把当前问题+图片登记为 pending_stream，rerun 后由 render_conversation
            # 在"对话食谱"容器内统一渲染流式 Q&A，避免出现在容器外/不居中
            st.session_state["pending_stream"] = {
                "session_id": session["session_id"],
                "message": build_message(raw_question, taste_prefs),
                "question": raw_question,
                "image": current_image,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            st.session_state["clear_question"] = True
            st.session_state["uploaded_image"] = None
            st.session_state["uploader_version"] += 1
            st.rerun()


# --------------------------------------------------------------------------- #
#  主入口
# --------------------------------------------------------------------------- #
def main():
    inject_styles()
    init_state()

    # 压缩 Hero
    st.markdown(
        """
        <div class="hero-bar">
            <h1>🍳 AI 私厨</h1>
            <p>您的智能烹饪伴侣<br>把冰箱里的灵感变成餐桌上的美味</p>
            <span class="hero-emojis">🥕 🍅 🥬 🥩</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 侧栏
    render_sidebar()

    # 主区域（居中）
    main_col = st.columns([0.06, 0.88, 0.06])[1]
    with main_col:
        # 对话区（可滚动）
        st.markdown('<div class="section-title">🍲 对话食谱</div>', unsafe_allow_html=True)
        with st.container(height=400):
            render_conversation()

        # 分隔线
        st.markdown(
            '<div class="divider">🥄 ······ 🥢 ······ 🍴</div>',
            unsafe_allow_html=True,
        )

        # 输入栏
        render_input_bar()


if __name__ == "__main__":
    main()
