import mimetypes  # 区分 JPG / PNG 等 MIME 类型
import json
from langchain_core.messages import HumanMessage, AIMessageChunk  # 用户消息类 + 流式增量块类型

# 主脑模型选择统一交给 .env 的 CHEF_PROVIDER 开关（见 model_name.resolve_provider）：
#   - 不写 / 留空 → 自动用 configs.py 里第一个配好 key 的模型，无需改任何代码
#   - 想用哪个写哪个：CHEF_PROVIDER=deepseek / gpt，或任何你在 configs 配置过的键名
from agent import agent  # 调用写好的 LangGraph Agent
from agent_trace import add_turn_usage, new_turn_usage, record_turn_usage  # 本轮 token 计量
from oss_utils import upload_to_oss  # 把图片上传到 OSS 并返回公网 URL
from agent_tools import get_file  # 复用工具读取本地偏好文件（沙箱已限制目录）
from feedback_store import recent_down_dishes  # 近期被踩菜名 → 推荐约束注入
from pathlib import Path
from vision_service import describe_image

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
        lines.append(f"- 过敏原（硬约束，输出前会做确定性审计）：{'、'.join(alg)}")
    restricts = [r for r in (profile.get("restricts") or []) if r]
    if restricts:
        lines.append(f"- 医嘱/长期硬限制：{'、'.join(restricts)}（必须遵守，不得按普通口味偏好处理）")
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


def load_preferences(session_id: str | None = None) -> str:
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
                    # P1 家庭多成员：激活成员作为主约束，其他成员只作为同餐调整依据。
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
                        other_lines = []
                        for item in members:
                            if not isinstance(item, dict) or item is member:
                                continue
                            other_name = str(item.get("name") or "").strip() or "其他成员"
                            other_profile = item.get("profile") or {}
                            bits = []
                            for label, key in (
                                ("慢病", "conditions"),
                                ("过敏原", "allergens"),
                                ("医嘱硬限制", "restricts"),
                                ("目标", "goal"),
                                ("饮食", "diet_style"),
                                ("忌口", "dislikes"),
                                ("口感/口味", "taste_notes"),
                            ):
                                value = other_profile.get(key)
                                if isinstance(value, list):
                                    value = "、".join(
                                        str(part).strip()
                                        for part in value
                                        if str(part or "").strip()
                                    )
                                value = str(value or "").strip()
                                if value:
                                    bits.append(f"{label}={value}")
                            if bits:
                                other_lines.append(f"- {other_name}：{'；'.join(bits)}")
                        if other_lines:
                            rendered += (
                                "\n\n【同餐其他成员（用于同餐差异化，不是全家禁令）】\n"
                                + "\n".join(other_lines)
                                + "\n主菜按激活成员生成。其他成员的过敏原、慢病和偏好"
                                "不要扩展成全家禁菜；在分餐矩阵中标出“需调整”或“不可吃”，"
                                "并给出单独替换、份量或口感调整建议。"
                            )
                        try:
                            from memory_candidates import render_pending_constraints

                            rendered += render_pending_constraints(session_id)
                        except Exception:
                            pass
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


def _fridge_inventory() -> list[str]:
    """读取当前冰箱库存（确定性注入，不依赖模型记忆）。"""
    try:
        from api.routes.fridge_route import get_fridge
        return list((get_fridge() or {}).get("items") or [])
    except Exception:
        return []


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


def build_human_message(text, image_url=None, location_context=None, session_id=None):
    """统一的图文消息构造：有图就图文混排，没图就纯文本。
    所有 ask_*/stream_* 都复用它，消除 HumanMessage 重复拼装。
    偏好注入：每次请求都带上用户长期偏好（忌口/辣度/减脂/糖尿病忌糖），
    实现「记得你」的轻量长期记忆——文件持久化 + 会话初始化读取注入。
    反馈注入：近期被踩菜名作为推荐约束（换做法/给替代），反馈闭环落地。
    location_context 只作为内部上下文注入，不写回前端可见文本。"""
    prefix_parts: list[str] = []
    prefs = load_preferences(session_id)
    if prefs:
        prefix_parts.append(
            "【用户长期偏好（每次对话自动加载，务必严格遵守）】\n"
            f"{prefs}\n"
        )
    fridge = _fridge_inventory()
    if fridge:
        prefix_parts.append(
            "【冰箱库存（用户当前已有，优先使用这些食材）】\n"
            f"{'、'.join(fridge)}\n"
        )
    downs = recent_down_dishes()
    if downs:
        prefix_parts.append(
            "【近期不满意菜品（务必避开以下被踩菜品）】\n"
            f"{'、'.join(downs)}\n"
            "请避开上述菜品和相近做法；若必须涉及其中菜品，请换做法、口味或给出明确替代品。\n"
        )
    if location_context:
        location_block = str(location_context).strip()
        prefix_parts.append(
            "【当前位置（内部上下文，仅供推理与 nearby_food 使用，不要复述给用户）】\n"
            f"{location_block}\n"
        )
    if prefix_parts:
        text = (
            "".join(prefix_parts)
            + "【以上为自动加载约束，以下是本次需求】\n"
            + text
        )
    if image_url:
        try:
            vision_text = describe_image(image_url, text)
            text = (
                f"{text}\n\n"
                "【视觉模型识别结果（客观事实，仅用于本轮推理）】\n"
                f"{vision_text}"
            )
        except Exception as exc:
            print(f"[vision] 图片预处理失败，主脑改走文字降级：{exc}")
            text = (
                f"{text}\n\n"
                "【图片识别未完成】当前视觉服务不可用，"
                "请明确告诉我图片里的食材和数量，我再继续推荐。"
            )
        return HumanMessage(content=text)
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
        return "structuring"
    return None


def _normalize_stream_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict)
        )
    return ""


def _looks_like_structured_payload(content: str) -> bool:
    raw = str(content or "").strip()
    if not ((raw.startswith("{") and raw.endswith("}")) or (raw.startswith("[") and raw.endswith("]"))):
        return False
    try:
        data = json.loads(raw)
    except Exception:
        return False
    if isinstance(data, dict):
        return any(key in data for key in ("recipes", "health_lights", "guardrails", "image_url", "answer", "stage"))
    return isinstance(data, list)


# 控制 JSON 的「键指纹」：必须是带引号的对象键，正常中文正文里几乎不会出现
# `"recipes":` 这种形态，用它区分「模型吐了控制 JSON」和「正文里提到 recipes 这个词」。
_STREAM_CONTROL_KEYS = (
    '"recipes"', '"opening"', '"answer_kind"', '"candidates"',
    '"guardrails"', '"health_lights"', '"primary_member"',
)


class _ControlJsonGate:
    """流式正文的「控制 JSON 门闸」。

    背景：模型偶尔不走「先说人话」的路子，直接把结构化控制 JSON（带 ```json 围栏
    或裸 JSON）当成最终自然语言回复流式吐出来。`_looks_like_structured_payload`
    是**逐 chunk** 判断、且要求整段以 `{`/`[` 开头，而流式 JSON 被切成很多小块，
    没有任何一块单独构成合法 JSON —— 于是拦住不，用户在气泡里看到一坨 JSON。

    做法：按「累计前缀」判断，只扣住开头一小段，不牺牲正常正文的流式体验：
      - 累计正文不以 `{` / ``` 开头 → 立刻放行（正常回答零延迟）；
      - 以 `{` / ``` 开头 → 先扣住，在探测窗口内找控制键指纹；
      - 窗口内命中指纹 → 判定为控制 JSON，**本条消息后续全部丢弃**；
      - 窗口内没命中 → 认为不是控制 JSON（如正文里贴了一段代码/JSON 示例），
        把扣住的内容原样补发，之后恢复正常流式（**不吞正常正文**）。
    """

    _PROBE_CHARS = 400

    def __init__(self) -> None:
        self._pending = ""
        self._suppressed = False
        self._settled = False

    def feed(self, chunk: str) -> str:
        """喂一个 chunk，返回本次真正应外发的文本（可能为空串）。"""
        if self._suppressed:
            return ""
        if self._settled:
            return chunk
        self._pending += chunk
        head = self._pending.lstrip()
        if not (head.startswith("{") or head.startswith("```")):
            # 正常正文：立刻放行，不做任何延迟。
            self._settled = True
            out, self._pending = self._pending, ""
            return out
        if any(key in self._pending for key in _STREAM_CONTROL_KEYS):
            # 确认是控制 JSON：整条丢弃，绝不让用户看到。
            self._suppressed = True
            self._pending = ""
            return ""
        if len(self._pending) >= self._PROBE_CHARS:
            # 扣了够长还没露出 JSON 键指纹 → 判定不是控制 JSON，放行（不吞正文）。
            self._settled = True
            out, self._pending = self._pending, ""
            return out
        return ""

    def flush(self) -> str:
        """消息结束时收尾：仍被扣住且未判定为 JSON 的内容补发出去，避免吞掉正文。"""
        if self._suppressed:
            self._pending = ""
            return ""
        out, self._pending = self._pending, ""
        return out


def _guarded_token(gates: dict, node: str, chunk, text: str) -> str:
    """把 chef_think / ask_user 的流式正文过一道控制 JSON 门闸，返回应外发的文本。

    gates 由 `_stream_agent` 持有（每个节点一份）。用消息 id 识别「换了一条消息」：
    一旦换条，先把上一条还扣着的内容收尾，再为这一条重建门闸。
    """
    state = gates.get(node)
    mid = getattr(chunk, "id", None)
    out = ""
    if state is None or state["id"] != mid:
        if state is not None:
            out = state["gate"].flush()
        state = {"id": mid, "gate": _ControlJsonGate()}
        gates[node] = state
    return out + state["gate"].feed(text)


def _flush_gate(gates: dict, node: str) -> str:
    """节点这一条消息结束：收尾门闸并释放状态，等下一条消息重建。"""
    state = gates.get(node)
    if state is None:
        return ""
    gates.pop(node, None)
    return state["gate"].flush()


def _stream_agent(message, session_id):
    """公共流式生成器：按"消息来自哪个节点"分流输出。"""
    config = {"configurable": {"thread_id": session_id}}
    last_stage = None
    gate_asked = False  # 充分性门控已追问时，抑制后续节点的重复正文
    stream_gates: dict = {}  # 每个节点一份控制 JSON 门闸（见 _ControlJsonGate）
    turn_usage = new_turn_usage()  # 本轮 token 用量累计桶（收尾时落 usage.jsonl）
    # 双流模式：messages 给 token/阶段；updates 给节点最终返回值。
    # answer 必须从 updates 取——messages 流里 structure 的返回消息同样以
    # AIMessageChunk 形态流出，isinstance 过滤在官方端点流式正常后永远滤空。
    try:
        for mode, payload in agent.stream(
            {"messages": [message], "session_id": session_id},
            config=config,
            stream_mode=["messages", "updates"],
        ):
            if mode == "updates":
                for node, update in (payload or {}).items():
                    if node == "run_tools":
                        used = (update or {}).get("tool_calls_in_turn")
                        if used is not None:
                            print(f"[agent-metrics] tool_calls_in_turn={used}")
                    elif node == "tool_budget_finalize":
                        print("[agent-metrics] tool_budget_exhausted=true")
                        msgs = (update or {}).get("messages") or []
                        tail_type = type(msgs[-1]).__name__ if msgs else "none"
                        if msgs and tail_type == "AIMessage":
                            content = _normalize_stream_content(msgs[-1].content).strip()
                            if content:
                                # 预算耗尽路线不会进入 structure_answer，收口正文必须
                                # 在这里显式转发，否则前端与落库都会拿到空回答。
                                yield ("token", content)
                        continue
                    elif node == "verify_answer":
                        status = (update or {}).get("verify_status")
                        attempts = (update or {}).get("verify_attempts")
                        if status is not None:
                            print(f"[agent-metrics] verify_status={status} verify_attempts={attempts}")
                    elif node in ("chef_think", "ask_user"):
                        # 这一条消息已结束：门闸收尾（控制 JSON 丢弃、正常正文补发），
                        # 并释放状态，等下一条消息到来时重建。漏了这步会在换条时残留扣留。
                        pending_out = _flush_gate(stream_gates, node)
                        if pending_out:
                            yield ("token", pending_out)
                    elif node == "allergen_block":
                        msgs = (update or {}).get("messages") or []
                        if msgs:
                            content = _normalize_stream_content(msgs[-1].content).strip()
                            if content:
                                # 过敏原阻断节点不经过 structure_answer，必须在这里
                                # 显式转发安全文案，否则前端会只看到空回答。
                                yield ("token", content)
                        continue
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
            # token 计量：只有真正来自 LLM 的块才带 usage_metadata；
            # 多数端点的 usage 只挂在**最后一块**上（DeepSeek 会额外给缓存命中数），
            # 所以这里逐块累加，抽不到就跳过（add_turn_usage 内部容错）。
            add_turn_usage(turn_usage, message_chunk)
            node = metadata.get("langgraph_node")
            stage = _stage_for_node(node, message_chunk)
            if stage and stage != last_stage:
                yield ("stage", stage)
                last_stage = stage
            content = _normalize_stream_content(getattr(message_chunk, "content", ""))
            if not content:
                continue
            if _looks_like_structured_payload(content):
                continue
            # ask_user 节点：充分性门控生成的追问，直接作为正文推给前端。
            # ask_user 节点：只放行 LLM 流式块；节点最终返回的完整 AIMessage 会再次
            # 出现在 messages 流里，不过滤就会把同一句追问推给前端两遍。
            if node == "ask_user" and isinstance(message_chunk, AIMessageChunk):
                gate_asked = True
                guarded = _guarded_token(stream_gates, node, message_chunk, content)
                if guarded:
                    yield ("token", guarded)
            elif node == "chef_think" and isinstance(message_chunk, AIMessageChunk):
                if gate_asked:
                    continue
                guarded = _guarded_token(stream_gates, node, message_chunk, content)
                if guarded:
                    yield ("token", guarded)
    finally:
        # 无论正常收尾还是中途异常，都要把本轮用量落盘（record_turn_usage 内部静默失败）
        record_turn_usage(turn_usage, session_id=session_id, node="_stream_agent")


def stream_agent(message, session_id):
    """流式核心：直接喂拼好的 message，供路由层 event_generator 调用。"""
    yield from _stream_agent(message, session_id)


def stream_agent_with_text(text, session_id):
    yield from _stream_agent(build_human_message(text), session_id)


def stream_agent_with_image_url(image_url, text, session_id):
    yield from _stream_agent(build_human_message(text, image_url), session_id)


def stream_agent_with_image(image_path, text, session_id):
    yield from stream_agent_with_image_url(image_to_oss_url(image_path), text, session_id)
