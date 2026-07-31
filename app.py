import html
import os
import uuid
from datetime import datetime

import requests
import streamlit as st


st.set_page_config(
    page_title="AI 私厨",
    page_icon="🍳",
    layout="wide",
    initial_sidebar_state="expanded",
)


DEFAULT_API_URL = os.getenv("AI_CHEF_API_URL", "http://127.0.0.1:8010")


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

        [data-testid="stSidebar"] {
            background: rgba(255, 250, 245, .94);
            border-right: 1px solid rgba(255, 112, 67, .14);
        }

        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3 {
            color: var(--brown);
        }

        .hero {
            position: relative;
            overflow: hidden;
            padding: 2.1rem 2.4rem 1.9rem;
            border: 1px solid rgba(255, 112, 67, .18);
            border-radius: 24px;
            background:
                linear-gradient(115deg, rgba(255, 255, 255, .90), rgba(255, 240, 224, .84)),
                repeating-linear-gradient(0deg, rgba(255, 112, 67, .025) 0 1px, transparent 1px 5px);
            box-shadow: 0 18px 45px rgba(104, 57, 32, .11);
        }

        .hero::after {
            content: "🥕  🍅  🥬  🥩  🍳";
            position: absolute;
            right: 2rem;
            bottom: 1.25rem;
            opacity: .46;
            font-size: 1.7rem;
            letter-spacing: .28rem;
        }

        .hero h1 {
            margin: 0;
            color: var(--brown);
            font-size: clamp(2rem, 4vw, 3.35rem);
            line-height: 1.1;
        }

        .hero p {
            margin: .75rem 0 0;
            color: #795548;
            font-size: 1.05rem;
        }

        .section-title {
            display: flex;
            align-items: center;
            gap: .55rem;
            margin: 1.35rem 0 .65rem;
            color: var(--brown);
            font-size: 1.17rem;
            font-weight: 700;
        }

        .upload-shell {
            min-height: 235px;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 1.25rem;
            border: 1px solid rgba(255, 112, 67, .2);
            border-radius: 20px;
            background: rgba(255, 255, 255, .64);
            box-shadow: 0 12px 28px rgba(104, 57, 32, .08);
        }

        .upload-shell:hover {
            box-shadow: 0 16px 34px rgba(104, 57, 32, .13);
        }

        .placeholder {
            padding: 2.2rem 1rem;
            text-align: center;
            color: #8d6e63;
        }

        .placeholder .icon {
            margin-bottom: .65rem;
            font-size: 3rem;
        }

        .placeholder strong {
            display: block;
            color: var(--brown);
            font-size: 1.05rem;
        }

        .placeholder span {
            display: block;
            margin-top: .35rem;
            font-size: .92rem;
        }

        .divider {
            margin: 1.05rem 0 .25rem;
            color: #c27a57;
            text-align: center;
            letter-spacing: .42rem;
            opacity: .78;
        }

        .chat-heading {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding-bottom: .75rem;
            border-bottom: 1px dashed rgba(76, 175, 80, .25);
            color: var(--green);
            font-weight: 700;
        }

        [data-testid="stSidebar"] .chat-heading {
            margin-top: .4rem;
            font-size: .98rem;
        }

        .chat-empty {
            padding: 1.55rem .25rem;
            color: #7d8b72;
            text-align: center;
            line-height: 1.6;
            font-size: .88rem;
        }

        .bubble {
            margin: .65rem 0;
            padding: .72rem .78rem;
            border-radius: 16px;
            line-height: 1.65;
            overflow-wrap: anywhere;
            font-size: .88rem;
        }

        .bubble.user {
            border: 1px solid rgba(255, 112, 67, .18);
            background: #fff0e0;
            color: var(--brown);
        }

        .bubble-meta {
            margin-bottom: .25rem;
            font-size: .76rem;
            font-weight: 700;
            opacity: .72;
        }

        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-color: rgba(76, 175, 80, .16) !important;
            border-radius: 16px !important;
            background: #edf8e9 !important;
            box-shadow: 0 8px 18px rgba(61, 91, 44, .06) !important;
        }

        div[data-testid="stVerticalBlockBorderWrapper"]:has([data-testid="stImage"]) {
            border-color: rgba(255, 112, 67, .2) !important;
            background: rgba(255, 255, 255, .64) !important;
            box-shadow: 0 12px 28px rgba(104, 57, 32, .08) !important;
        }

        .stTextArea textarea {
            border: 1px solid rgba(121, 85, 72, .35);
            border-radius: 14px;
            background: #fffaf5;
            color: var(--brown);
        }

        .stTextArea textarea:focus {
            border-color: var(--orange);
            box-shadow: 0 0 0 2px rgba(255, 112, 67, .12);
        }

        .stButton > button,
        .stFormSubmitButton > button {
            border: 0;
            border-radius: 12px;
            background: linear-gradient(135deg, #ff7043, #ff5722);
            color: white;
            font-weight: 700;
            box-shadow: 0 8px 18px rgba(255, 87, 34, .2);
            transition: transform .18s ease, box-shadow .18s ease;
        }

        .stButton > button:hover,
        .stFormSubmitButton > button:hover {
            transform: translateY(-2px);
            box-shadow: 0 12px 24px rgba(255, 87, 34, .3);
            color: white;
        }

        [data-testid="stFileUploader"] {
            padding: .35rem;
            border: 1px solid rgba(255, 112, 67, .18);
            border-radius: 14px;
            background: rgba(255, 250, 245, .78);
        }

        .tip {
            padding: .7rem .85rem;
            border-left: 4px solid var(--orange);
            border-radius: 8px;
            background: rgba(255, 240, 224, .75);
            color: #795548;
            font-size: .88rem;
        }

        @media (max-width: 780px) {
            .hero {
                padding: 1.5rem 1.2rem 5rem;
            }

            .hero::after {
                right: 1rem;
                bottom: 1rem;
                font-size: 1.25rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


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


def render_history():
    st.markdown(
        '<div class="chat-heading"><span>🍲 对话食谱</span><span>AI 私厨记录</span></div>',
        unsafe_allow_html=True,
    )

    history = st.session_state.get("history", [])
    if not history:
        st.markdown(
            '<div class="chat-empty">👨‍🍳 你的厨房还很安静，上传食材或问我一道菜吧。</div>',
            unsafe_allow_html=True,
        )
        return

    for item in history:
        user_text = html.escape(item["user_text"]).replace("\n", "<br>")
        st.markdown(
            f"""
            <div class="bubble user">
                <div class="bubble-meta">🍴 你 · {item["time"]}</div>
                {user_text}
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.container(border=True):
            st.markdown(
                f'<div class="bubble-meta" style="color:#1b5e20;">👨‍🍳 AI 私厨 · {item["time"]}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(item["answer"])


def main():
    inject_styles()

    if "history" not in st.session_state:
        st.session_state["history"] = []
    if "session_id" not in st.session_state:
        st.session_state["session_id"] = f"user_{uuid.uuid4().hex[:10]}"
    if "uploaded_image" not in st.session_state:
        st.session_state["uploaded_image"] = None
    if "uploader_version" not in st.session_state:
        st.session_state["uploader_version"] = 0
    if st.session_state.pop("clear_question", False):
        st.session_state["question"] = ""

    st.markdown(
        """
        <div class="hero">
            <h1>🍳 AI 私厨 · 您的智能烹饪伴侣</h1>
            <p>探索食材的无限可能，把冰箱里的灵感变成餐桌上的美味。</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown("## 🧂 厨房设置")
        api_url = st.text_input(
            "FastAPI 地址",
            value=DEFAULT_API_URL,
            help="默认连接本机的 FastAPI 服务。",
        )
        st.session_state["session_id"] = st.text_input(
            "会话 ID",
            value=st.session_state["session_id"],
            help="相同会话 ID 可以继续使用同一段后端记忆。",
        )
        st.caption("当前后端接口：`POST /api/chat/image`")
        st.markdown(
            '<div class="tip">当前后端要求上传图片后才能调用图片聊天接口。</div>',
            unsafe_allow_html=True,
        )

        render_history()

        if st.button("🧹 清空对话记录", use_container_width=True):
            st.session_state["history"] = []
            st.rerun()

    main_col = st.columns([0.12, 0.76, 0.12])[1]

    with main_col:
        st.markdown(
            '<div class="section-title">📸 上传食材或菜品图片</div>',
            unsafe_allow_html=True,
        )

        uploaded_file = st.file_uploader(
            "选择图片",
            type=["jpg", "jpeg", "png"],
            label_visibility="collapsed",
            key=f"image_uploader_{st.session_state['uploader_version']}",
        )

        if uploaded_file is not None:
            st.session_state["uploaded_image"] = uploaded_file

        current_image = st.session_state.get("uploaded_image")
        if current_image is None:
            st.markdown(
                """
                <div class="upload-shell">
                    <div class="placeholder">
                        <div class="icon">📸</div>
                        <strong>拍下你的食材或菜品</strong>
                        <span>让 AI 私厨为你解读 · 🥕 🍅 🥩</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            with st.container(border=True):
                st.image(
                    current_image,
                    caption=f"已选择：{current_image.name}",
                    use_container_width=True,
                )

        if current_image is not None:
            if st.button("🗑️ 清空已选图片", use_container_width=True):
                st.session_state["uploaded_image"] = None
                st.session_state["uploader_version"] += 1
                st.rerun()

        st.markdown(
            '<div class="divider">🥄 ······ 🥢 ······ 🍴</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="section-title">💬 告诉 AI 大厨你的烹饪困惑吧~</div>',
            unsafe_allow_html=True,
        )

        question = st.text_area(
            "烹饪问题",
            value=st.session_state.get("question", ""),
            key="question",
            height=160,
            placeholder="比如：番茄牛腩怎么做更软烂？或者直接上传食材图片让我帮你搭配！",
            label_visibility="collapsed",
        )

        if st.button("🧽 清空问题", use_container_width=True):
            st.session_state["clear_question"] = True
            st.rerun()

        can_submit = current_image is not None or bool(question.strip())
        st.caption("可以只输入文字、只上传图片，也可以同时提交，让 AI 私厨更准确地理解你的需求。")

        send_clicked = st.button(
            "✨ 发送给 AI 私厨",
            type="primary",
            use_container_width=True,
            disabled=not can_submit,
            help="请先输入问题或上传一张图片。",
        )

        if send_clicked:
            with st.spinner("👨‍🍳 AI 私厨正在识别食材、搜索菜谱……"):
                try:
                    answer = api_chat(
                        api_url=api_url,
                        session_id=st.session_state["session_id"],
                        message=question.strip() or "请识别图片中的食材，并推荐适合的家常菜。",
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
                    st.session_state["history"].append(
                        {
                            "user_text": question.strip() or "请识别图片中的食材，并推荐适合的家常菜。",
                            "answer": answer,
                            "time": datetime.now().strftime("%H:%M"),
                        }
                    )
                    st.session_state["clear_question"] = True
                    st.rerun()


if __name__ == "__main__":
    main()
