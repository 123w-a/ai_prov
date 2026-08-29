import mimetypes  # 区分 JPG / PNG 等 MIME 类型
from langchain_core.messages import HumanMessage, AIMessageChunk  # 用户消息类 + 流式增量块类型

# 主脑模型选择统一交给 .env 的 CHEF_PROVIDER 开关（见 model_name.resolve_provider）：
#   - 不写 / 留空 → 自动用 configs.py 里第一个配好 key 的模型，无需改任何代码
#   - 想用哪个写哪个：CHEF_PROVIDER=deepseek / gpt，或任何你在 configs 配置过的键名
from agent import agent  # 调用写好的 LangGraph Agent
from oss_utils import upload_to_oss  # 把图片上传到 OSS 并返回公网 URL
from agent_tools import get_file  # 复用工具读取本地偏好文件（沙箱已限制目录）
from feedback_store import recent_down_dishes  # 近期被踩菜名 → 推荐约束注入
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
            lines.append(f"- 身高体重：{h:g}cm / {w:g}kg（BMI {bmi:g}）")
        except Exception:
            pass
    age, sex = basic.get("age"), basic.get("sex") or ""
    if age or sex:
        sex_cn = {"male": "男", "female": "女"}.get(sex, "")
        seg = " ".join(x for x in [f"{age:g}岁" if age else "", sex_cn] if x)
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
    tn = [t for t in (profile.get("taste_notes") or []) if t]
    if tn:
        lines.append(f"- 口味偏好：{'、'.join(tn)}（推荐与做法必须遵守）")
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
                members = profile.get("members")
                if isinstance(members, list) and members:
                    # P1 家庭多成员：只渲染激活成员画像，标题带成员名
                    active_id = profile.get("active_id")
                    member = next(
                        (m for m in members if m.get("id") == active_id),
                        members[0],
                    )
                    name = str(member.get("name") or "").strip()
                    rendered = _render_health_profile(member.get("profile") or {})
                    if rendered:
                        if name:
                            rendered = rendered.replace(
                                "【结构化健康画像（", f"【健康画像·{name}（", 1
                            )
                        return rendered
                    return ""
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
    实现「记得你」的轻量长期记忆——文件持久化 + 会话初始化读取注入。
    反馈注入：近期被踩菜名作为推荐约束（换做法/给替代），反馈闭环落地。"""
    prefix_parts: list[str] = []
    prefs = load_preferences()
    if prefs:
        prefix_parts.append(
            "【用户长期偏好（每次对话自动加载，务必严格遵守）】\n"
            f"{prefs}\n"
        )
    downs = recent_down_dishes()
    if downs:
        prefix_parts.append(
            "【近期不满意菜品（用户点过踩，务必参考）】\n"
            f"{'、'.join(downs)}\n"
            "若本次推荐命中同类菜品或做法，请主动换做法、口味或给出替代品；"
            "若确实要推荐其中菜品，请说明这次在做法/口味上的具体不同。\n"
        )
    if prefix_parts:
        text = (
            "".join(prefix_parts)
            + "【以上为自动加载约束，以下是本次需求】\n"
            + text
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
    gate_asked = False  # 充分性门控已追问时，抑制后续节点的重复正文
    # 双流模式：messages 给 token/阶段；updates 给节点最终返回值。
    # answer 必须从 updates 取——messages 流里 structure 的返回消息同样以
    # AIMessageChunk 形态流出，isinstance 过滤在官方端点流式正常后永远滤空。
    for mode, payload in agent.stream(
        {"messages": [message]},
        config=config,
        stream_mode=["messages", "updates"],
    ):
        if mode == "updates":
            for node, update in (payload or {}).items():
                if node != "structure_answer":
                    continue
                msgs = (update or {}).get("messages") or []
                tail_type = type(msgs[-1]).__name__ if msgs else "none"
                print(f"[stream] updates structure_answer tail={tail_type} n={len(msgs)}")
                # 类型名字符串判定而非 isinstance：项目里存在两份 langchain
                # 类对象（agent_chains 与 main 各自 import），isinstance 跨身份恒 False。
                if msgs and tail_type == "AIMessage":
                    raw = msgs[-1].content
                    if isinstance(raw, list):
                        # 新版 LangChain/官方端点可能给结构化 content blocks，规范化为纯文本
                        raw = "".join(
                            block.get("text", "") for block in raw if isinstance(block, dict)
                        )
                    yield ("answer", raw)
            continue
        message_chunk, metadata = payload
        node = metadata.get("langgraph_node")
        stage = _stage_for_node(node, message_chunk)
        if stage and stage != last_stage:
            yield ("stage", stage)
            last_stage = stage
        content = getattr(message_chunk, "content", "")
        if not content:
            continue
        # ask_user 节点：充分性门控生成的追问，直接作为正文推给前端。
        # ask_user 节点：只放行 LLM 流式块；节点最终返回的完整 AIMessage 会再次
        # 出现在 messages 流里，不过滤就会把同一句追问推给前端两遍。
        if node == "ask_user" and isinstance(message_chunk, AIMessageChunk):
            gate_asked = True
            yield ("token", content)
        elif node == "chef_think" and isinstance(message_chunk, AIMessageChunk):
            if gate_asked:
                continue
            yield ("token", content)


def stream_agent(message, session_id):
    """流式核心：直接喂拼好的 message，供路由层 event_generator 调用。"""
    yield from _stream_agent(message, session_id)


def stream_agent_with_text(text, session_id):
    yield from _stream_agent(build_human_message(text), session_id)


def stream_agent_with_image_url(image_url, text, session_id):
    yield from _stream_agent(build_human_message(text, image_url), session_id)


def stream_agent_with_image(image_path, text, session_id):
    yield from stream_agent_with_image_url(image_to_oss_url(image_path), text, session_id)
