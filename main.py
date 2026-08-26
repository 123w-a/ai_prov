import mimetypes  # 区分 JPG / PNG 等 MIME 类型
from langchain_core.messages import HumanMessage, AIMessageChunk  # 用户消息类 + 流式增量块类型

# 主脑模型选择统一交给 .env 的 CHEF_PROVIDER 开关（见 model_name.resolve_provider）：
#   - 不写 / 留空 → 自动用 configs.py 里第一个配好 key 的模型，无需改任何代码
#   - 想用哪个写哪个：CHEF_PROVIDER=deepseek / gpt，或任何你在 configs 配置过的键名
from agent import agent  # 调用写好的 LangGraph Agent
from oss_utils import upload_to_oss  # 把图片上传到 OSS 并返回公网 URL
from agent_tools import get_file  # 复用工具读取本地偏好文件（沙箱已限制目录）
from pathlib import Path

# 用户长期偏好文件路径（白名单目录 data/ 下）
_PREFS_PATH = str(Path(__file__).resolve().parent / "data" / "preferences.txt")
# 结构化健康画像（P2 升级）：存在时优先于自由文本偏好
_PROFILE_PATH = Path(__file__).resolve().parent / "data" / "profile.json"


def _render_health_profile(profile: dict) -> str:
    """把结构化画像 dict 渲染成注入 prompt 的确定性中文段落。
    数值原样回显（BMI 代算），过敏原显式标注硬约束——
    不让 LLM 自行解释 JSON，保证「记得你」的注入内容可预测、可验收。"""
    lines = ["【结构化健康画像（每次对话自动加载，务必严格遵守）】"]
    basic = profile.get("basic") or {}
    h, w = basic.get("height_cm"), basic.get("weight_kg")
    if h and w:
        try:
            bmi = round(float(w) / (float(h) / 100) ** 2, 1)
            lines.append(f"- 身高体重：{h}cm / {w}kg（BMI {bmi}）")
        except Exception:
            pass
    age, sex = basic.get("age"), basic.get("sex") or ""
    if age or sex:
        sex_cn = {"male": "男", "female": "女"}.get(sex, "")
        seg = " ".join(x for x in [f"{age}岁" if age else "", sex_cn] if x)
        lines.append(f"- 年龄性别：{seg}")
    cond = [c for c in (profile.get("conditions") or []) if c]
    if cond:
        lines.append(f"- 慢病情况：{'、'.join(cond)}（相关忌口按硬约束执行，推荐前先过健康护栏）")
    alg = [a for a in (profile.get("allergens") or []) if a]
    if alg:
        lines.append(f"- 过敏原：{'、'.join(alg)}（绝对禁止出现在任何推荐与食谱中）")
    if profile.get("goal"):
        lines.append(f"- 当前目标：{profile['goal']}")
    if profile.get("diet_style"):
        lines.append(f"- 饮食流派：{profile['diet_style']}")
    dis = [d for d in (profile.get("dislikes") or []) if d]
    if dis:
        lines.append(f"- 不喜欢的食材：{'、'.join(dis)}（尽量避免）")
    return "\n".join(lines) if len(lines) > 1 else ""


def load_preferences() -> str:
    """会话初始化时读取用户长期偏好。
    P2 升级：存在 data/profile.json 时优先渲染结构化健康画像（确定性段落）；
    不存在或渲染为空则回落旧自由文本 preferences.txt（向后兼容）。
    文件不存在/读取被拒时返回空串，绝不阻断主流程。"""
    try:
        if _PROFILE_PATH.exists():
            import json
            profile = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
            if isinstance(profile, dict):
                rendered = _render_health_profile(profile)
                if rendered:
                    return rendered
    except Exception:
        pass
    try:
        content = get_file.invoke({"file_path": _PREFS_PATH})
        if content and not content.startswith(("文件不存在", "读取被拒绝", "读取失败")):
            lines = [ln.strip() for ln in content.splitlines()
                     if ln.strip() and not ln.strip().startswith("#")]
            return "\n".join(lines)
    except Exception:
        pass
    return ""


def image_to_oss_url(image_path):  # 本地图片 -> OSS 公网 URL
    mime_type, _ = mimetypes.guess_type(image_path)
    if mime_type is None:
        mime_type = "image/jpeg"
    with open(image_path, "rb") as image_file:
        image_bytes = image_file.read()
    return upload_to_oss(image_bytes, mime_type)


def image_bytes_to_oss_url(image_bytes, mime_type="image/jpeg"):
    """直接把图片 bytes 上传到 OSS 并返回可访问 URL（无需先落本地磁盘）。"""
    if mime_type is None:
        mime_type = "image/jpeg"
    return upload_to_oss(image_bytes, mime_type)


def build_human_message(text, image_url=None):
    """统一的图文消息构造：有图就图文混排，没图就纯文本。
    所有 ask_*/stream_* 都复用它，消除 HumanMessage 重复拼装。
    偏好注入：每次请求都带上用户长期偏好（忌口/辣度/减脂/糖尿病忌糖），
    实现「记得你」的轻量长期记忆——文件持久化 + 会话初始化读取注入。"""
    prefs = load_preferences()
    if prefs:
        text = (
            "【用户长期偏好（每次对话自动加载，务必严格遵守）】\n"
            f"{prefs}\n"
            "【以上为偏好约束，以下是本次需求】\n"
            f"{text}"
        )
    if image_url:
        return HumanMessage(
            content=[
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]
        )
    return HumanMessage(content=text)


# --------------------------------------------------------------------------- #
#  流式版本：agent.stream(stream_mode="messages") 逐 token 吐出，只过滤 LLM 增量。
#  图拓扑、工具、断点、压缩、结构化收尾逻辑完全不动。
#  图拓扑、工具、断点、压缩、结构化收尾逻辑完全不动。
#
#  两段式输出（LCEL 重构后）：每次 yield 一个 (kind, content) 元组——
#    ("token", 文字)  ：chef_think 节点的 LLM 增量块，前端打字机渲染正文
#    ("answer", JSON) ：structure_answer 节点整理好的 ChefAnswer 整包 JSON，前端画卡片
#  structure_answer 节点里结构化链自身的 token 碎片被丢弃（半截 JSON 没意义）。
# --------------------------------------------------------------------------- #


def _stage_for_node(node, message_chunk):
    """把 LangGraph 节点名映射成前端可展示的进度阶段。"""
    if node == "run_tools":
        return "searching"
    if node == "verify_answer":
        return "auditing"
    if node == "structure_answer":
        return "generating_image"
    return None

def _stream_agent(message, session_id):
    """公共流式生成器：按"消息来自哪个节点"分流输出。"""
    config = {"configurable": {"thread_id": session_id}}
    last_stage = None
    for message_chunk, metadata in agent.stream(
        {"messages": [message]},
        config=config,
        stream_mode="messages",
    ):
        node = metadata.get("langgraph_node")
        stage = _stage_for_node(node, message_chunk)
        if stage and stage != last_stage:
            yield ("stage", stage)
            last_stage = stage
        content = getattr(message_chunk, "content", "")
        if not content:
            continue
        # ask_user 节点：充分性门控生成的追问，直接作为正文推给前端。
        if node == "ask_user":
            yield ("token", content)
        elif node == "chef_think" and isinstance(message_chunk, AIMessageChunk):
            yield ("token", content)
        # structure_answer 节点返回的完整 JSON 消息 → 整包给前端；
        # 注意 AIMessageChunk 是 AIMessage 子类，必须显式排除链内部的流式碎片
        elif node == "structure_answer" and not isinstance(message_chunk, AIMessageChunk):
            yield ("answer", content)


def stream_agent(message, session_id):
    """流式核心：直接喂拼好的 message，供路由层 event_generator 调用。"""
    yield from _stream_agent(message, session_id)


def stream_agent_with_text(text, session_id):
    yield from _stream_agent(build_human_message(text), session_id)


def stream_agent_with_image_url(image_url, text, session_id):
    yield from _stream_agent(build_human_message(text, image_url), session_id)


def stream_agent_with_image(image_path, text, session_id):
    yield from stream_agent_with_image_url(image_to_oss_url(image_path), text, session_id)
