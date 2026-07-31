import html
import os
import uuid
from datetime import datetime
from pathlib import Path

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

        /* ---------- 左右对话气泡（核心） ---------- */
        .chat-row {
            display: flex;
            width: 100%;
            margin-bottom: .2rem;
        }
        .user-row {
            justify-content: flex-start;
        }
        .ai-row {
            justify-content: flex-end;
        }
        .bubble {
            max-width: 78%;
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

        /* ---------- 文本框 ---------- */
        .stTextArea textarea {
            border: 1px solid rgba(121, 85, 72, .3);
            border-radius: 12px;
            background: #fffaf5;
            color: var(--brown);
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

        /* ---------- 侧栏历史记录 compact ---------- */
        .history-item {
            padding: .35rem .45rem;
            border-radius: 8px;
            background: rgba(255, 240, 224, .55);
            border: 1px solid rgba(255, 112, 67, .08);
            margin-bottom: .25rem;
            font-size: .78rem;
            color: var(--brown-light);
            line-height: 1.4;
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
#  状态初始化
# --------------------------------------------------------------------------- #
def init_state():
    defaults = {
        "chat_history": [],
        "session_id": f"user_{uuid.uuid4().hex[:10]}",
        "uploaded_image": None,
        "uploader_version": 0,
        "taste_prefs": [],
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val
    if st.session_state.pop("clear_question", False):
        st.session_state["question"] = ""


# --------------------------------------------------------------------------- #
#  后端通信
# --------------------------------------------------------------------------- #
def api_chat(api_url, session_id, message, uploaded_file=None, timeout=180):
    endpoint = f"{api_url.rstrip('/')}/api/chat/image"

    files = None
    if uploaded_file is not None:
        files = {
            "image": (
                uploaded_file.name,
                uploaded_file.getvalue(),
                uploaded_file.type or "application/octet-stream",
            )
        }

    response = requests.post(
        endpoint,
        data={
            "session_id": session_id,
            "message": message,
        },
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
#  对话展示（自定义左右气泡）
# --------------------------------------------------------------------------- #
def render_conversation():
    chat_history = st.session_state.get("chat_history", [])

    if not chat_history:
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
        user_text = html.escape(item["user_text"]).replace("\n", "<br>")
        st.markdown(
            f"""
            <div class="chat-row user-row">
                <div class="bubble user-bubble">
                    <div class="bubble-meta">🍴 你 · {item['time']}</div>
                    <div class="bubble-body">{user_text}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # ---- AI 消息：右对齐 ----
        st.markdown(
            f"""
            <div class="chat-row ai-row">
                <div class="bubble ai-bubble">
                    <div class="bubble-meta">🍳 AI 私厨 · {item['time']}</div>
            """,
            unsafe_allow_html=True,
        )
        # 在气泡内部渲染 Markdown 回答
        st.markdown(item["answer"])

        # 操作按钮行（复制 / 重新生成 / 删除）
        action_cols = st.columns([1.6, 1.0, 1.0, 0.6])
        with action_cols[1]:
            render_copy_button(item["answer"], f"copy_{index}")
        with action_cols[2]:
            if st.button("🔄 重新生成", key=f"regen_{index}", use_container_width=True):
                handle_regenerate(index)
        with action_cols[3]:
            if st.button("🗑️", key=f"del_{index}", help="删除这对对话"):
                st.session_state["chat_history"].pop(index)
                st.rerun()

        st.markdown(
            """
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def handle_regenerate(index):
    item = st.session_state["chat_history"][index]
    with st.spinner("👨‍🍳 重新生成中……"):
        try:
            uploaded = None
            img_data = item.get("image_data")
            if img_data:
                uploaded = StoredFile(
                    img_data["name"], img_data["data"], img_data["type"]
                )
            message = build_message(item["user_text"], item.get("taste_prefs", []))
            new_answer = api_chat(
                api_url=DEFAULT_API_URL,
                session_id=st.session_state["session_id"],
                message=message,
                uploaded_file=uploaded,
            )
            st.session_state["chat_history"][index]["answer"] = new_answer
            st.rerun()
        except requests.exceptions.RequestException as exc:
            st.error(f"🍽️ 暂时联系不上后端服务：{exc}")
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

        st.markdown("---")

        # 清空对话
        if st.button("🧹 清空对话记录", use_container_width=True):
            st.session_state["chat_history"] = []
            st.rerun()

        # 使用提示
        st.markdown(
            """
            <div class="tip">
                💡 <strong>使用提示</strong><br>
                • 上传食材图片让 AI 识别<br>
                • 输入菜名或烹饪问题<br>
                • 选择口味偏好获取个性化推荐<br>
                • 点击「重新生成」换一份食谱
            </div>
            """,
            unsafe_allow_html=True,
        )

        # ---- 对话记录（恢复） ----
        st.markdown("---")
        st.markdown("## 🍲 对话记录")

        chat_history = st.session_state.get("chat_history", [])
        if not chat_history:
            st.caption("暂无记录，开始第一次对话吧~")
        else:
            for idx, item in enumerate(chat_history):
                text_col, del_col = st.columns([0.82, 0.18])
                with text_col:
                    preview = item["user_text"]
                    if len(preview) > 22:
                        preview = preview[:22] + "…"
                    st.markdown(
                        f"""
                        <div class="history-item">
                            <strong>{item['time']}</strong> · {html.escape(preview)}
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with del_col:
                    if st.button("×", key=f"sidebar_del_{idx}", help="删除这条记录"):
                        st.session_state["chat_history"].pop(idx)
                        st.rerun()


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
        height=110,
        placeholder="比如：番茄牛腩怎么做更软烂？或者直接上传食材图片让我帮你搭配！",
        label_visibility="collapsed",
    )

    can_submit = current_image is not None or bool(question.strip())

    send_clicked = st.button(
        "✨ 发送给 AI 私厨",
        type="primary",
        use_container_width=True,
        disabled=not can_submit,
        help="请先输入问题或上传一张图片。",
    )

    if send_clicked:
        handle_send(question, current_image)


def handle_send(question, current_image):
    taste_prefs = st.session_state.get("taste_prefs", [])
    raw_question = question.strip() or "请识别图片中的食材，并推荐适合的家常菜。"
    message = build_message(raw_question, taste_prefs)

    with st.spinner("👨‍🍳 AI 私厨正在识别食材、搜索菜谱……"):
        try:
            answer = api_chat(
                api_url=DEFAULT_API_URL,
                session_id=st.session_state["session_id"],
                message=message,
                uploaded_file=current_image,
            )
        except requests.exceptions.RequestException as exc:
            st.error(
                "🍽️ 暂时联系不上后端服务，请确认 FastAPI 已启动。\n\n"
                f"错误信息：{exc}"
            )
        except Exception as exc:
            st.error(f"🍽️ 这次烹饪请求没有完成：{exc}")
        else:
            image_data = None
            if current_image is not None:
                image_data = {
                    "name": current_image.name,
                    "data": current_image.getvalue(),
                    "type": current_image.type or "application/octet-stream",
                }
            st.session_state["chat_history"].append(
                {
                    "user_text": raw_question,
                    "answer": answer,
                    "time": datetime.now().strftime("%H:%M"),
                    "image_data": image_data,
                    "taste_prefs": list(taste_prefs),
                }
            )
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
