# agent_graph.py：组装 LangGraph 循环 Agent（模型/断点/节点/状态图）
# 只负责"编排"：把提示词、工具、LLM、记忆、路由串成一张可运行的图
# 具体工具怎么干活看 agent_tools.py，提示词内容看 agent_prompts.py

import json#结构化回答打包成 JSON 字符串落进消息
import logging#结构化节点异常观测：静默降级也必须留下可定位证据
import re
from pathlib import Path#读取 data/profile.json（家庭档案激活成员）
import openai#捕获上游 LLM 偶发 5xx/超时异常做重试
import threading#failover 并发锁
import time#重试间隔用
import sqlite3#持久化短期记忆（断点续跑、循环状态保存）
from infrastructure.paths import CHECKPOINT_DB, DATA_DIR
from infrastructure.model_name import (
    extract_message_text,
    get_langchain_llm,
    get_summary_llm,
    resolve_provider,
)
from langchain_core.messages import (  # 系统提示词节点用 + 长对话压缩用
    SystemMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
    RemoveMessage,#删除消息
)
# LangGraph 核心替换导入：用 StateGraph 手动搭流程图，替代 create_agent 的线性执行
from langgraph.graph import StateGraph, END, MessagesState#状态图+结束标志+状态
from langgraph.prebuilt import ToolNode, tools_condition  # 内置工具节点 + 是否继续调用工具的路由判断
from langgraph.checkpoint.sqlite import SqliteSaver#持久化短期记忆（断点续跑、循环状态保存）

from .trace import trace_node
from .prompts import SYSTEM_PROMPT#最上层的提示词从这里输出ai的最先回复
from .turn_decision import (
    classify_turn_intent as _classify_turn_intent_shared,
    is_recipe_change_request as _is_recipe_change_request,
    is_restaurant_ordering_scene as _is_restaurant_ordering_scene,
    is_taste_adjustment_request as _is_taste_adjustment_request,
    looks_like_dining_request as _looks_like_dining_request,
    looks_like_home_service_request as _looks_like_home_service_request,
)
from agent_tools import find_recipe_image, set_query_transform_llm, tools, web_search
from .chains import build_structured_answer, rank_recipes#LCEL 结构化链(prompt|llm|parser)+排序+格式自动重试
from .schemas import DishMatrixItem, GuardrailItem, HealthLight, SourceRef  # 结构化输出的确定性注入字段
#build_structured_answer标准链+parser检查出错误后再进行重试
from domain.nutrition_rules import (
    RULES,
    audit,
    conditions_from_profile,
    describe,
    detect_conditions,
    sodium_light_reason,
)  # L3 硬护栏：确定性健康禁忌审计
from domain.allergen_rules import (  # 过敏原 L3 硬护栏：与慢病规则分开，避免改变既有慢病降级语义
    allergen_label,
    audit_allergen_advisories,
    audit_allergens,
    describe_allergens,
    requested_allergen_additions,
    suggest_safe_dishes,
)
from services.image_notes import build_image_note
from domain.constraint_rules import build_matrix, build_member_adjustments


# 画像候选采集使用现有的“用户确认后才写入”链路；
# 候选只进入 pending，点击“加入长期画像”后才会写入 profile.json。
PROFILE_MEMORY_ENABLED = True

_PROFILE_PATH = DATA_DIR / "profile.json"
logger = logging.getLogger(__name__)


def _profile_path() -> Path:
    """返回统一口径的家庭健康档案路径。"""
    return _PROFILE_PATH


def _active_profile_conditions() -> list:
    """读 data/profile.json 激活成员的 conditions，映射成硬护栏规则键。

    P1 多画像与 L3 护栏的接线：档案里写了痛风/孕期，即使消息只说「想吃火锅」，
    硬护栏也要同口径启用。读不到档案/解析失败一律返回空（护栏兜底不因档案缺失而崩）。
    """
    try:
        path = _profile_path()
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return []
        members = raw.get("members")
        if isinstance(members, list) and members:
            active_id = raw.get("active_id")
            member = next((m for m in members if m.get("id") == active_id), members[0])
            profile = member.get("profile") or {}
        else:  # v1 平铺档案
            profile = raw
        return conditions_from_profile(profile)
    except Exception:
        return []


def _active_profile_allergens() -> list:
    """读取激活成员的过敏原自由文本；档案缺失或损坏时返回空列表。"""
    try:
        path = _profile_path()
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return []
        members = raw.get("members")
        if isinstance(members, list) and members:
            active_id = raw.get("active_id")
            member = next((m for m in members if m.get("id") == active_id), members[0])
            profile = member.get("profile") or {}
        else:  # v1 平铺档案
            profile = raw
        values = profile.get("allergens")
        if not isinstance(values, list):
            return []
        return [str(item).strip() for item in values if str(item or "").strip()]
    except Exception:
        return []


def _family_allergens() -> list:
    """读取全家成员过敏原并集；只用于过敏原护栏，不改变慢病条件口径。"""
    try:
        path = _profile_path()
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return []
        members = raw.get("members")
        profiles = []
        if isinstance(members, list) and members:
            profiles = [
                member.get("profile") or {}
                for member in members
                if isinstance(member, dict)
            ]
        else:  # v1 平铺档案
            profiles = [raw]
        merged = []
        for profile in profiles:
            values = profile.get("allergens") if isinstance(profile, dict) else None
            if not isinstance(values, list):
                continue
            for item in values:
                text = str(item or "").strip()
                if text and text not in merged:
                    merged.append(text)
        return merged
    except Exception:
        return []


def _allergens_for_audit(session_id: str | None = None) -> list:
    """审计激活成员及当前会话待确认过敏原；其他成员由逐菜分餐矩阵处理。"""
    merged = list(_active_profile_allergens())
    if session_id:
        try:
            from storage.memory_candidates import get_session_allergens

            merged.extend(get_session_allergens(session_id))
        except Exception:
            pass
    return list(dict.fromkeys(item for item in merged if item))


def _profile_health() -> tuple[dict, str]:
    """返回 (档案字典, 错误说明)。

    错误说明非空 = 档案存在但不可用。这时护栏会降级，**必须在右栏如实告知**：
    静默当成"没有约束"等于让护栏在用户不知情的情况下失效。
    """
    path = _profile_path()
    if not path.exists():
        return {}, ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"档案文件解析失败（{type(exc).__name__}）"
    if not isinstance(raw, dict):
        return {}, "档案文件结构不是对象"
    if not raw:
        return {}, "档案文件内容为空"
    members = raw.get("members")
    if isinstance(members, list) and members:
        usable = [
            m for m in members
            if isinstance(m, dict) and isinstance(m.get("profile"), dict)
        ]
        if not usable:
            return {}, "档案里没有任何可用的成员画像"
    return raw, ""


def _load_profile() -> dict:
    """读取 data/profile.json 原始字典；缺失/损坏返回 {}（不抛异常、不阻断主流程）。"""
    return _profile_health()[0]


def _active_member_name() -> str:
    """当前激活成员的名字；取不到时返回空串，前端据此不显示「主菜面向谁」。"""
    raw = _load_profile()
    members = raw.get("members")
    if isinstance(members, list) and members:
        active_id = raw.get("active_id")
        member = next(
            (m for m in members if isinstance(m, dict) and m.get("id") == active_id),
            None,
        )
        if member is None:
            member = next((m for m in members if isinstance(m, dict)), {})
        return str(member.get("name") or "").strip()
    return str(raw.get("name") or "").strip()


def _family_members() -> list[dict]:
    """读取同餐成员及画像；只读、容错，档案缺失时不阻断主流程。"""
    try:
        path = _profile_path()
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return []
        members = raw.get("members")
        if isinstance(members, list) and members:
            result = []
            for member in members:
                if not isinstance(member, dict):
                    continue
                name = str(member.get("name") or "").strip()
                profile = member.get("profile")
                if not name or not isinstance(profile, dict):
                    continue
                result.append({"name": name, "profile": profile})
            return result
        # v1 平铺档案兼容：整份画像视为一位成员。
        return [{"name": "我", "profile": raw}]
    except Exception:
        return []


def _apply_family_differentiation(answer, members=None):
    """确定性规则优先；规则说不出差异时，保留模型已给出的分餐判断。

    背景：盐/酱油改成「证据式」判定后（写明用量且不超上限就不算违规），
    家常菜很难拿到硬命中，确定性矩阵于是整片「可吃 / 空理由」，
    再被这里覆盖回去就把模型算对的分餐结论（如「爸爸·高血压：少盐版」）
    一并抹平成「可按共同做法正常食用」。规则有结论时永远规则赢，
    这里只补规则沉默的格子。
    """
    family_members = _family_members() if members is None else members
    try:
        rows = build_matrix(
            [recipe.model_dump() for recipe in answer.recipes],
            family_members or [],
        )
        merged = _merge_family_rows(rows, answer.dish_matrix)
        answer.dish_matrix = merged
        # 一人一句按成员逐条合并：规则说不出话的那位，保留模型自己写的
        # 「少盐、切碎、减份量」，不把三条都退化成同一句「正常食用」。
        answer.member_adjustments = _merge_member_adjustments(
            build_member_adjustments([row.model_dump() for row in merged]),
            answer.member_adjustments,
        )
    except Exception:
        # 矩阵只是差异化展示层，任何异常都不能影响已经通过审计的主卡片。
        answer.dish_matrix = []
        answer.member_adjustments = []
    return answer


def _matrix_verdict(row) -> str:
    """兼容 DishMatrixItem 与 dict 两种形态取 verdict。"""
    if isinstance(row, dict):
        return str(row.get("verdict") or "").strip()
    return str(getattr(row, "verdict", "") or "").strip()


def _merge_family_rows(deterministic_rows, model_rows):
    """规则命中以确定性结论为准；规则沉默的格子沿用模型自填的判断。"""
    fallback: dict = {}
    for row in model_rows or []:
        verdict = _matrix_verdict(row)
        member = str(getattr(row, "member", "") or "").strip()
        dish = str(getattr(row, "dish", "") or "").strip()
        if member and dish and verdict not in ("", "可吃"):
            fallback[(dish, member)] = row
    merged = []
    for row in deterministic_rows or []:
        key = (
            str(row.get("dish") or "").strip(),
            str(row.get("member") or "").strip(),
        )
        keep = fallback.get(key)
        if keep is not None and str(row.get("verdict") or "") == "可吃":
            merged.append(keep)
        else:
            merged.append(DishMatrixItem(**row))
    return merged


def _merge_member_adjustments(deterministic_lines, model_lines):
    """规则结论优先；只有退化成「可按共同做法正常食用」的成员才用模型原话。"""
    saved: dict = {}
    for line in model_lines or []:
        text = str(line or "").strip()
        member, sep, rest = text.partition("：")
        if sep and member and rest.strip():
            saved[member.strip()] = text
    merged = []
    for line in deterministic_lines or []:
        member = str(line or "").split("：", 1)[0].strip()
        if "可按共同做法正常食用" in str(line or "") and member in saved:
            merged.append(saved[member])
        else:
            merged.append(line)
    return merged


def _merged_conditions(user_text: str) -> list:
    """消息推断 + 档案声明 的护栏规则键合并（保持顺序，去重）。"""
    merged = detect_conditions(user_text)
    for cond in _active_profile_conditions():
        if cond not in merged:
            merged.append(cond)
    return merged

# 稳定输出规则：覆盖旧提示词中的多菜分支，保证流式正文和结构化卡片一致。
SINGLE_RECIPE_RULE = (
    "\n\n【默认单菜规则】用户没有明确要求多个选择时，"
    "每次最终只回答最合适的一道菜，不要列出第二道、备选菜或并列方案。"
)
# 外食找店规则：只在确定性意图判成 restaurant 时追加。
# 它与「单菜/候选清单」规则互斥，避免夜宵配酒找店被模型带回菜谱模板。
RESTAURANT_SCENE_RULE = (
    "\n\n【外食找店规则·本轮最高优先级】"
    "本轮是外食/到店找店需求，不是居家做菜。"
    "必须先调用 nearby_food 查询餐厅，再只输出店名、菜系、人均、距离或地址、"
    "适合本轮场景的点单建议和健康提醒。"
    "禁止输出任何菜谱、食材用量、烹饪步骤、结构化卡片或配图内容。"
    "用户说“换一家”或“换一批”时，沿用上一轮的城市、位置、人数、用途和预算条件，"
    "重新推荐未出现过的店。"
    "没有城市且消息中没有 [当前位置] 时先追问城市，不要猜测或使用默认城市。"
)
# 两阶段点菜第一阶段规则：泛推荐只出编号候选清单，不出做法、不出卡片。
# 编号是「用户说第 N 道」的唯一锚点，格式必须固定，后端靠编号解析做确定性映射。
# ⚠️ 「推荐理由」的维度必须在这里显式声明。实测：只写「20 字以内的推荐理由」时，
# 模型会用它最熟悉的默认维度（营养/口感）填空 —— 用户问「下酒菜」，理由却写成
# 「去皮后脂肪更低 / 软糯无骨爷爷好嚼 / 入口即化不费牙」，答的不是用户问的。
# 维度优先级：用途场景 > 候选间差异点 > 健康提醒（仅真正触发时一句）。
# 底线：**过敏原与致命禁忌不参与退让**，退居次位的只是「营养层面的好坏」。
# 「候选清单」开头口径：默认只许一句话（25 字以内）。
# 已确认食材清单在场时换用下面的变体（先交代推荐依据）。实测两条并列自称
# 「最高优先级」时，模型会挑字数更短的那条 —— 第一句只剩护栏、丢掉推荐依据。
_CANDIDATE_OPENING_RULE = "开头最多一句话（25 字以内）交代场景或护栏，不要长篇铺垫。"
_CANDIDATE_OPENING_RULE_WITH_INGREDIENTS = (
    "开头先交代推荐依据（按下方【本轮已确认食材清单】的要求来写），"
    "再照旧交代场景或护栏；依据句不受 25 字限制，但不要长篇铺垫。"
)
CANDIDATE_LIST_RULE_TEMPLATE = (
    "\n\n【候选清单规则·本轮最高优先级】"
    "本轮用户没有点名具体哪一道菜，属于「泛推荐」。"
    "因此本轮只输出候选菜名清单：不要写做法、步骤、调料、火候，不要输出 JSON，不要生成卡片，不要配图。"
    "格式必须严格是（每行一条，编号从 1 开始，菜名后接「 —— 」再写一句约 30 字的推荐理由，"
    "长短随性、不必卡死）：\n"
    "1. 菜名 —— 理由\n"
    "2. 菜名 —— 理由\n"
    "【推荐理由写什么·按此优先级】"
    "① 用户本轮说了用途/场合（下酒、宵夜、带饭、招待、加班、解馋等）→ 理由必须回答"
    "「为什么适合这个用途」，写这个场合真正在意的点（耐吃、不占手、够香、清爽、不甜等）；"
    "② 用户没说用途 → 才写这道菜相对其他候选的差异点（口感 / 做法省事 / 便宜 / 快手）；"
    "③ 健康提醒只在**真正触发**时用一句话带上（如「长辈牙口也能吃」），"
    "不要每道都写成体检报告。"
    "【底线·不参与上面的优先级】过敏原与致命禁忌**永不退让**：含过敏原的候选一律不进清单"
    "（系统另有确定性过滤兜底），理由里也绝不为「顺着用户」而淡化致命风险。"
    "退居次位的只是「营养层面的好坏」（低脂 / 低盐 / 热量），**不是「能不能吃」**。"
    "【人性化】用户表达「就想解馋 / 过瘾 / 明知不健康也要吃」时，理由顺着人写"
    "（够香、过瘾、配酒绝配、吃得开心），**不要**附带营养说教或「但是不太健康」这类转折。"
    "【写法示例·只示范写法】示例里的菜名与编号都不许照抄，✓/✗ 标记也不要出现在回答里：\n"
    "✓ 对的写法：1. 番茄炒蛋 —— 装盒不塌、凉了也下饭，带饭最省心\n"
    "✗ 错的写法：1. 番茄炒蛋 —— 去皮后脂肪更低（用户本轮没问健康，属于答非所问）\n"
    "按用户提到的食材、用途场合和健康约束给出 {count} 道；每道都必须是完整菜名（如「番茄炒蛋」），"
    "不能只写单个食材。"
    + _CANDIDATE_OPENING_RULE
    + "最后另起一行写「回复序号就行，我再把这一道的完整做法给你。」，不要用问句结尾。"
)

# 用途/场合词表 → 归一化标签。
# 只收「用途场合」，**不收受众**（爷爷 / 孩子一类由家庭档案与约束矩阵处理，
# 混进来会盖掉用户真正说的用途 —— 用户问的是「下酒」，不是「爷爷能吃」）。
_OCCASION_KEYWORDS = (
    ("下酒", ("下酒", "佐酒", "配酒", "喝酒", "啤酒", "白酒", "红酒", "小酌", "酒局", "碰杯")),
    ("宵夜", ("宵夜", "夜宵", "熬夜")),
    ("带饭", ("带饭", "便当", "打包带走", "上班带")),
    ("招待", ("招待", "请客", "客人", "聚餐", "家宴", "待客")),
    ("加班", ("加班", "赶工")),
    ("解馋", ("解馋", "过瘾", "破戒", "放纵", "馋")),
    ("早餐", ("早餐", "早饭")),
    ("减脂", ("减脂", "减肥", "瘦身", "控卡", "掉秤")),
    ("增肌", ("增肌", "健身餐", "补蛋白")),
)


def _occasion_hint(text) -> str:
    """从用户本轮原话里抽「用途/场合」（下酒/宵夜/带饭…），抽不到返回空串。

    纯函数、不依赖 LLM：候选清单的「推荐理由」必须锚定用途，否则模型会退回
    默认的营养/口感维度（实测：用户问下酒菜，理由写成「去皮后脂肪更低」）。
    """
    if not text:
        return ""
    for label, words in _OCCASION_KEYWORDS:
        if any(word in text for word in words):
            return label
    return ""


def _occasion_rule(occasion: str) -> str:
    """把用途钉成硬规则注入本轮提示词（确定性兜底，比只给写法示例更稳）。"""
    return (
        f"\n\n【本轮用途锚定】用户这轮的用途/场合是「{occasion}」。"
        f"候选理由必须说明「为什么适合{occasion}」，写这个场合真正在意的点；"
        "营养层面的好坏退居次位，只在真正触发健康约束时一句话带上。"
        "过敏原与致命禁忌照旧不退让。"
    )


def _candidate_list_rule(messages, *, ingredient_ready: bool = False) -> str:
    """用户明说「多来几道」时给更长的候选清单，默认给 3 道。

    已确认食材清单在场时换用「先交代推荐依据」的开头口径，避免与食材块
    并列争最高优先级（两条并列时模型会挑字数更短的那条，丢掉依据句）。
    """
    count = 5 if _wants_multiple_recipes(messages) else 3
    rule = CANDIDATE_LIST_RULE_TEMPLATE.format(count=count)
    if ingredient_ready:
        rule = rule.replace(
            _CANDIDATE_OPENING_RULE,
            _CANDIDATE_OPENING_RULE_WITH_INGREDIENTS,
        )
    return rule

# --------------------------------------------------------------------------- #
#  1. 模型 & 工具绑定
# --------------------------------------------------------------------------- #
# 主脑运行模型：不传参即走"自适配"——优先读 .env 的 CHEF_PROVIDER 开关（想用哪个写哪个），
# 没配 / 配了但没 key → 自动用 configs 第一个可用的（已将 gpt 放第一，不写即默认 gpt）。
# 本文件不硬编码任何模型名，切换模型只改 .env，无需动代码。
provider = None
# DeepSeek 的推理 token 与正文共用上限，1024 会把长回答截成半句。
# 4096 仍不够：重推理轮（实测 user_d2cc49f12c 第 4 轮「要严格控制」→
# 「减盐减油版红烧肉」）推理把预算吃光，流式正文在分餐矩阵第一行就被斩断
# （opening 停在「…不喝汤、不」），卡片文字与矩阵表全部缺失。
# 正常轮模型 finish_reason=stop 就停，放大上限不会让普通回答变长或变慢，
# 只在「本来就会被截断」的轮把未写完的正文补齐。
MAIN_AGENT_MAX_TOKENS = 8192
llm = None
llm_with_tools = None
retrieval_llm = None
summary_llm = None
_FAILOVER_LOCK = threading.Lock()
_LLM_INIT_LOCK = threading.Lock()


def rebuild_llms(force_provider=None):
    """构建/重建全部模块级 LLM。failover 时换 provider 重跑整轮。"""
    global provider, llm, llm_with_tools, retrieval_llm, summary_llm
    provider = force_provider or resolve_provider()#不写默认是.env中设置的第一个key
    llm = get_langchain_llm(provider, max_tokens=MAIN_AGENT_MAX_TOKENS)#获取模型对象
    # 检索侧思考（查询改写 / 多查询 / HyDE）：高频低难度任务，跟随主 provider 保证可用性
    try:
        retrieval_llm = get_langchain_llm(provider, temperature=0.3, max_tokens=200)
    except Exception:
        retrieval_llm = llm
    # 历史摘要专用模型：DeepSeek 默认独立使用 deepseek-chat，避免 thinking 模型
    # 只把正文写进 reasoning_content；failover 到其他 provider 时不覆盖型号名。
    try:
        summary_llm = get_summary_llm(provider, temperature=0.3)
    except Exception:
        summary_llm = llm
    llm_with_tools = llm.bind_tools(tools)#传个大模型告诉他有什么工具和怎么正确的用变成json格式给LLM


def _ensure_llms():
    """首次真正调用模型时再构建，避免后端启动被重依赖导入卡住。"""
    if llm_with_tools is not None:
        return
    with _LLM_INIT_LOCK:
        if llm_with_tools is None:
            rebuild_llms()


rebuild_llms()  # 保持既有导入契约：外部会直接读取 provider/llm


def failover_llms():
    """当前主 provider 黑洞后切换到备用 provider；返回实际切换到的名字或 None。"""
    from infrastructure.model_name import _provider_in_cooldown, mark_provider_down, pick_fallback_provider
    _ensure_llms()
    with _FAILOVER_LOCK:#并发请求同时触发 failover 时只换一次
        if _provider_in_cooldown(provider):
            return None  # 当前 provider 已在冷却，说明别处刚切过/试过
        alt = pick_fallback_provider(exclude=provider)
        if not alt:
            return None
        failed = provider
        rebuild_llms(alt)
        mark_provider_down(failed)#锁内标记，杜绝并发请求在标记前又切回旧家
    return alt

def _query_transform_adapter(system: str, user: str) -> str:
    _ensure_llms()
    try:
        return retrieval_llm.invoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        ).content
    except Exception:
        return ""
set_query_transform_llm(_query_transform_adapter, mode="multi")

# --------------------------------------------------------------------------- #
#  2. 断点持久化（SQLite checkpointer，thread_id 对应 checkpoint.db 里的单条任务断点）
# --------------------------------------------------------------------------- #
connection = sqlite3.connect(
    # 路径统一来自 infrastructure.paths，避免 CWD 不同导致断点库分裂成两份
    # （storage/checkpoints.py 的清理入口读的是同一个常量）。
    database=str(CHECKPOINT_DB),
    check_same_thread=False
)

checkpointer = SqliteSaver(connection)
checkpointer.setup()

# --------------------------------------------------------------------------- #
#  3. 长对话压缩节点（替代原 SummarizationMiddleware）：LLM 推理前触发
#  历史消息超过阈值时，把更老的"用户/AI 对话"总结成要点、删掉原文，防上下文溢出
#  只总结 user/AI，跳过 ToolMessage 工具返回；总结 Prompt 针对膳食管家场景定制
# --------------------------------------------------------------------------- #
MAX_HISTORY_KEEP = 6  # 保留最近约 3 轮(user+ai)，更早的参与总结（调大以减少压缩触发、保住菜品编号上下文）
MAX_TOOL_CALLS_PER_TURN = 4  # 单轮「同时可执行」的工具调用上限（并发宽度，不是循环轮数）
# 「已经选定一道菜，只是顺带问一句健康问题」这类轮次收紧到 2 次工具：
# 实测「2吧…我的父亲高血压，可以吃这个吗」会让模型连搜 3 次网页把 4 次预算搜爆，
# 白烧 60s 还拿不到卡片；这类轮次营养依据本来就在知识库里，不需要反复联网。
# 但收紧后模型第一圈常把 2 格一次用光，第二圈还要取证时会被判预算耗尽、正文被
# 兜底句顶掉：所以第二圈允许补回 1 格（每轮只补一次，见 _pick_turn_slot_patch）。
PICK_TURN_TOOL_BUDGET = 2
# 循环轮数单独封顶 —— 与「并发宽度」分开管：
# 上面两个常量管「一轮里放行几个工具」，这里管「整个循环能转几圈」，两者不是一回事。
# 教训：把两者混用一个旋钮（用「模型打算调几个」去比预算）时，模型一次并排提 3 个
# （选定第 5 道 + 换羊肉 + 不吃牛肉，很正常的并行取证）会被整批判成超预算、一个都不执行，
# 上下文里就没有 ToolMessage，收口时无证据 → 用户既看不到菜、也没有讲解。
# 现口径：路由只看「还有没有余额 / 圈数超没超」，执行时按余额截断，绝不整批否决。
MAX_TOOL_ROUNDS = 3
#MessagesState是所有的状态消息，包含 messages 属性
@trace_node("condense_history")
def maybe_condense(state: MessagesState):#压缩历史对话
    msgs = state["messages"]
    # 轮序号：每经过一次入口（= 每一条新的用户消息）自增并随 checkpoint 持久化。
    # 观测层靠它把 trace / usage 按轮对上，不再用「间隔 > 90s」猜轮次边界。
    try:
        turn_seq = int(state.get("turn_seq") or 0) + 1
    except (TypeError, ValueError):
        turn_seq = 1
    reset_turn_state = {
        "turn_seq": turn_seq,
        "verify_attempts": 0,
        "verify_warning": "",
        "verify_status": "ok",
        "verify_violated": [],
        "tool_calls_in_turn": 0,
        "tool_rounds": 0,
        "tool_budget_exhausted": False,
        "pick_turn_slot_granted": False,
        "retry_slot_granted": False,
        "tool_budget": _turn_tool_budget(msgs),
        "ingredient_gate_ready": False,
        "ingredient_gate_action": "",
    }
    # 最后一条消息不是用户发的或者没有消息的话就不执行后续操作
    if not msgs or not isinstance(msgs[-1], HumanMessage):
        return {}#不改变状态，防止打断正在运行的时候
    # 只统计 user + ai 对话，跳开 ToolMessage 工具返回，避免把冗长搜索结果搅进摘要
    talk = [m for m in msgs if isinstance(m, (HumanMessage, AIMessage))]#自动过滤没用消息
    if len(talk) <= MAX_HISTORY_KEEP:
        return reset_turn_state  # 还没到阈值，只重置本轮运行状态
    old = talk[:-MAX_HISTORY_KEEP]#从开始到倒数第max_history_keep，老的对话要总结后删除
    if not old:
        return reset_turn_state
    convo = "\n".join(#将列表转为字符串供llm生成摘要
        f"{'用户' if isinstance(m, HumanMessage) else 'AI'}：{m.content}"#用户：内容，ai：内容
        for m in old#循环把每一条旧对话转为字符串并拼接在一起
    )
    summary_prompt = (#提示词
        "请把这段小膳管家对话历史压缩成极简要点，严格按以下规则：\n"
        "①用户拥有的食材；②口味/忌口偏好（甜/辣/酸/控糖等）；\n"
        "③已推荐或已做的菜谱——【必须按用户原始提问顺序，逐道列出 第1道=… 第2道=… 第3道=… "
        "（有多道务必保留编号），并标注关键改动（如换甜口/换清淡）；不得合并、重命名、捏造菜名，"
        "用户没明确说过的菜标'无'】；\n"
        "④未完成的待办。用中文、分点、不超200字：\n\n" + convo
    )
    # LLM 偶发 502，简单重试，失败就先不压缩（不阻断主流程）
    summary = None#标记摘要是否成功生成
    _ensure_llms()
    for attempt in range(3):
        try:
            summary = extract_message_text(
                summary_llm.invoke([HumanMessage(content=summary_prompt)]),
                allow_reasoning_fallback=True,
            )
            break#服务器错误，网络错误，调用次数超过限制
        except (openai.InternalServerError, openai.APIConnectionError, openai.RateLimitError):
            time.sleep(1 + attempt)
    if not summary:
        return reset_turn_state#摘要生成失败就跳过压缩，但仍重置本轮运行状态
    # 关键：被删 AIMessage 若带 tool_calls，它对应的 ToolMessage 必须"连坐"删除，
    # 否则孤儿 ToolMessage 留在历史里，下次请求 LLM 直接 400：
    # "No tool call found for function call output with call_id ..."
    doomed_call_ids = set()#删了旧消息也不会自动把工具调用的消息也删除所以要标记一起删
    for m in old:#循环旧消息
        if isinstance(m, AIMessage):
            for tc in getattr(m, "tool_calls", None) or []:#获取工具调用信息，有就返回,m.tool_calls
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if tc_id:
                    doomed_call_ids.add(tc_id)#有的话就全部传入同一集合
    # 删除被总结掉的旧消息、插入摘要（RemoveMessage 真正从 state 移除，避免无限增长）
    removals = [RemoveMessage(id=m.id) for m in old if getattr(m, "id", None)]#state里的消息看到RemoveMessage就删除
    if doomed_call_ids:
        removals += [
            RemoveMessage(id=m.id)
            for m in msgs
            if isinstance(m, ToolMessage)
            and getattr(m, "tool_call_id", None) in doomed_call_ids
            and getattr(m, "id", None)
        ]
    summary_msg = HumanMessage(content=f"[历史对话摘要，供参考]\n{summary}")#在state里看到HumanMessage就插入
    return {**reset_turn_state, "messages": removals + [summary_msg]}#返回给langgraph处理，看成是先删后加
#返回的是对状态的增量
# --------------------------------------------------------------------------- #
#  4. 节点1：LLM 思考节点（绑定系统提示词 + 工具调用能力）
# --------------------------------------------------------------------------- #
def _drop_orphan_tool_messages(messages):#过滤AIMessage tool_calls
    """过滤孤儿 ToolMessage：tool_call_id 在历史里找不到对应的 AIMessage tool_calls。
    存量 checkpoint 可能已有这种孤儿（旧压缩逻辑遗留），带着它请求 LLM 会直接 400：
    "No tool call found for function call output with call_id ..."
    只影响发给 LLM 的 payload，不改 state。"""
    valid_ids = set()
    for m in messages:
        if isinstance(m, AIMessage):
            for tc in getattr(m, "tool_calls", None) or []:
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if tc_id:
                    valid_ids.add(tc_id)
    return [
        m for m in messages
        if not (isinstance(m, ToolMessage) and getattr(m, "tool_call_id", None) not in valid_ids)
    ]


def _latest_user_has_image(messages):
    """判断最近一条用户消息是否包含图片。"""
    for m in reversed(messages):
        if isinstance(m, HumanMessage) and not str(m.content).startswith("[历史对话摘要") and not str(m.content).startswith("[健康护栏审核"):
            return _message_has_image(m)
    return False


def _message_text(message):
    """提取一条用户消息中的文字部分。"""
    if isinstance(message.content, list):
        return " ".join(
            part.get("text", "")
            for part in message.content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(message.content)


def _message_has_image(message):
    """判断一条图文消息是否包含图片。"""
    return isinstance(message.content, list) and any(
        isinstance(part, dict) and part.get("type") == "image_url"
        for part in message.content
    )


# 模型偶尔不走「先说人话」的路子，直接把结构化 JSON 当成正文吐出来
# （可能带 ```json 围栏、也可能裸 JSON）。这段文本会被当作 opening 显示在对话框和卡片里。
# 这里不猜、不截断，只做一件事：如果拿到的确实是控制 JSON，就抽它自己的 opening 字段。
_CONTROL_JSON_KEYS = (
    "opening", "recipes", "answer_kind", "candidates",
    "guardrails", "health_lights", "primary_member",
)


def _strip_code_fence(text: str) -> str:
    """去掉最外层 ```json / ``` 围栏，只留里面的内容。"""
    body = text.strip()
    if not body.startswith("```"):
        return body
    body = body[3:]
    if body[:4].lower() == "json":
        body = body[4:]
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3]
    return body.strip()


_MAX_OPENING_UNWRAP = 3  # opening 逐层剥壳的上限，防畸形输入把递归拖爆
# 正文确实洗不出人话时的确定性兜底：宁可少一句开场白，也绝不把 JSON 露给用户。
_FINAL_OPENING_FALLBACK = "已按你的要求整理好这一道，完整做法见图卡。"

# 模型偶尔在 JSON 字符串**内部**写未转义的 `"`（实测：「风险点在成品"炸粉、脆皮粉、裹粉"
# ——这类…标注"可能含芝麻"。」），整段 payload 因此 json.loads 失败。旧实现的做法是
# 「解析失败 + 带控制键指纹 → 返回空串」，结果模型写好的整篇过敏原审计（3895 字）
# 被静默丢掉，用户只看到一张 opening 为空的卡片。
# 这里补一条**只取 opening 字段**的正则救援路径：虽然整包解析不了，但 opening 通常
# 是第一个字段且自身不带未转义引号，仍能安全取出人话正文。取不到才回落空串。
_OPENING_FIELD_PATTERN = re.compile(
    r'["\']?opening["\']?\s*:\s*"(?P<body>(?:[^"\\]|\\.)*)"',
    re.DOTALL,
)


def _rescue_opening_field(text: str) -> str:
    """JSON 解析失败时，用正则从控制 payload 里捞回 opening 正文。

    只在「整包解析失败」时作为补救使用，且返回值必须是像人话的文本
    （不含控制键指纹、长度合理），否则一律回落空串，绝不放行半坨 JSON。
    """
    match = _OPENING_FIELD_PATTERN.search(str(text or ""))
    if not match:
        return ""
    body = match.group("body")
    try:
        body = json.loads(f'"{body}"')  # 反转义 \n \" 等
    except Exception:
        body = body.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
    body = str(body or "").strip()
    if not body or len(body) < 4:
        return ""
    # 捞回来的正文自己又带控制键指纹 → 说明取错了，丢弃。
    if any(key in body for key in _CONTROL_JSON_KEYS):
        return ""
    if body.lstrip().startswith(("{", "[", "```")):
        return ""
    return body


def _clean_opening_text(raw, _depth: int = 0) -> str:
    """把 chef_think 最后一轮的内容清洗成可展示的纯文本。

    正常情况模型给人话，原样返回。异常情况模型整段吐控制 JSON —— 那种文本
    **不能**原样当正文（用户会在气泡里看到一坨 JSON）。

    实测过的两种畸形（都会让正文变成一坨 JSON）：
      1) 单层包裹：正文就是 ```json{...}``` 或裸 JSON；
      2) **双层包裹**：外层 `{"opening": "```json{...}```"}` —— 模型把整包 payload
         又塞回了 opening 字段里。只剥一层壳的实现会被这种输入直接穿透。
    所以这里**逐层剥**（上限 _MAX_OPENING_UNWRAP 层），并且只要文本带控制键指纹
    就不可能是一段给人看的正文 —— 解析不出来也宁可**返回空串**，绝不外露原文。
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    # 注意 `[` 也要算：控制 JSON 可能是顶层数组（实测 `[{"recipes": 1}]` 会绕过只判 `{` 的实现）
    looks_json = text.startswith(("{", "[", "```"))
    if not looks_json:
        return text
    # 带引号的对象键指纹：正常中文正文里几乎不会出现 `"recipes":` 这种形态
    has_fingerprint = any(key in text for key in _CONTROL_JSON_KEYS)
    body = _strip_code_fence(text)
    data = None
    if body.startswith(("{", "[")):
        try:
            data = json.loads(body)
        except Exception:
            data = None
    if isinstance(data, dict):
        inner = data.get("opening")
        if isinstance(inner, str) and inner.strip():
            if _depth >= _MAX_OPENING_UNWRAP:
                return ""  # 套得太深，判定为控制 JSON，不外露
            # 递归：内层可能又是一段 fence/JSON（双层包裹）
            return _clean_opening_text(inner, _depth + 1)
        # 是控制 JSON 但里面没有可用的 opening：不要退化成把整坨 JSON 当正文。
        if has_fingerprint:
            return ""
        return text
    if isinstance(data, list):
        # 顶层是数组形态的控制 payload
        return "" if has_fingerprint else text
    # 解析失败 / 不是 JSON。
    # 带指纹说明这确实是控制 payload，只是 JSON 被模型写坏了（典型：字符串内未转义引号）。
    # 先尝试只捞 opening 字段 —— 捞得到就保住正文，捞不到才丢弃，绝不外露半坨 JSON。
    if has_fingerprint:
        return _rescue_opening_field(body)
    # 不带指纹：认为正文里贴了段代码，原样放行（不吞正文）。
    return text


def _final_opening_guard(text) -> str:
    """出卡前最后一道护栏：正文若仍是 JSON/围栏形态，换成确定性兜底句。

    只对"看起来像 JSON"的正文生效，正常人话一字不改（避免误伤）。
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    if not raw.lstrip().startswith(("{", "[", "```")):
        return raw
    cleaned = _clean_opening_text(raw)
    return cleaned or _FINAL_OPENING_FALLBACK


_INTERNAL_REQUEST_MARKERS = (
    "【配图开关：开启】",
    "【本轮需要配图】",
)
_INTERNAL_VISUAL_CONTEXT_MARKERS = (
    "【视觉模型识别结果（客观事实，仅用于本轮推理）】",
    "【图片识别未完成】",
)

# 路由层解析出「就第2个」对应的具体菜名后，把它显式注入给 Agent。
# 注入的原因：路由层读的是会话记录里的候选锚点，Agent 层读的是 checkpoint 里的候选
# payload，两处锚点可能不同时在场（实测 T2 就断了）。不给确定菜名时，模型会自己猜
# 「第2个」是哪道 —— 实测凭空造了一个候选清单里根本没有的「滑炒鸡丝」。
# 这是控制信令，不能回显给用户。
_SELECTED_CANDIDATE_PATTERN = re.compile(r"【已选定候选：([^】]{1,40})】")
_SELECTION_CONTROL_BLOCK_RE = re.compile(
    r"【已选定候选：[^】]+】"
    r"(?:\r?\n[（(][^\n]*[）)])?"
    r"[ \t]*(?:\r?\n)?"
)

# chat_route._global_asset_prompt() 会把跨会话菜品资产作为参考材料拼进用户消息开头。
# 这是「仅供本轮参考」的内部材料，绝不能回显给用户：实测兜底句与无工具收口模型都把它
# 当「用户原话」原样复述进可见正文（如「你提到的「【跨对话菜品资产参考】…」」）。
# 块以标记开头、以空行结尾，必须整块剥离（只删标签会留下残句）。
_ASSET_REFERENCE_BLOCK_RE = re.compile(
    r"【跨对话菜品资产参考】.*?(?=\r?\n\r?\n|\Z)",
    re.DOTALL,
)

# 所有内部控制信令的标记前缀，绝不能回显给用户（收口输出若复述即为内部泄漏，须拒用）。
_INTERNAL_MARKER_PREFIXES = (
    "【跨对话菜品资产参考】",
    "【已选定候选：",
    "【配图开关：开启】",
    "【本轮需要配图】",
    "【以上为自动加载约束",
    "【以上为偏好约束",
    "【视觉模型识别结果",
    "【图片识别未完成】",
    "[健康护栏审核",
    "[历史对话摘要",
)


def _extract_selected_candidate(text) -> str:
    """从注入的控制信令里取出已选定的候选菜名（没有则返回空串）。"""
    match = _SELECTED_CANDIDATE_PATTERN.search(str(text or ""))
    return match.group(1).strip() if match else ""


def _strip_internal_request_markers(text):
    """移除仅用于后端控制、不能回显给用户的内部标记。

    必须**整块**剥离：`【已选定候选：X】` 紧跟的那句后端控制说明（括号句）也是控制
    信令。只删标签会把括号句留在正文里 —— 兜底句又原样引用「用户本轮原话」，
    于是「本轮必须围绕它展开」这类内部说明被直接推给用户（实测「3吧」选菜轮复现）。
    """
    cleaned = _ASSET_REFERENCE_BLOCK_RE.sub("", str(text or ""))
    cleaned = _SELECTION_CONTROL_BLOCK_RE.sub("", cleaned)
    for marker in _INTERNAL_REQUEST_MARKERS:
        cleaned = cleaned.replace(marker, "")
    return cleaned.strip()


def _contains_internal_marker(text) -> bool:
    """判断文本是否夹带了后端控制信令（收口模型复述即为内部泄漏，必须拒用）。"""
    s = str(text or "")
    return any(prefix in s for prefix in _INTERNAL_MARKER_PREFIXES)


def _strip_internal_visual_context(text):
    """从用户意图文本中剥离视觉模型上下文，但保留原消息供主模型推理。"""
    cleaned = str(text or "")
    offsets = [
        cleaned.find(marker)
        for marker in _INTERNAL_VISUAL_CONTEXT_MARKERS
        if marker in cleaned
    ]
    if offsets:
        cleaned = cleaned[: min(offsets)]
    return cleaned.strip()


def _latest_visual_context(messages):
    """返回最近一条真实用户消息的视觉上下文：(是否有图片输入, 视觉文本)。

    当前主链路会先把图片转成文字，再以内部标记追加到 HumanMessage 文本里；
    因此这里不能只看 `_message_has_image`，还要识别视觉成功/失败标记。
    """
    for message in reversed(messages or []):
        if not isinstance(message, HumanMessage):
            continue
        if _is_internal_user_turn(message):
            continue
        raw = _message_text(message)
        if "【图片识别未完成】" in raw:
            return True, ""
        marker = "【视觉模型识别结果（客观事实，仅用于本轮推理）】"
        if marker in raw:
            return True, raw.split(marker, 1)[1].strip()
        return _message_has_image(message), ""
    return False, ""


def _current_request_text(text):
    """从用户消息中取出本次需求，排除每轮自动注入的长期偏好和内部标记。"""
    markers = (
        "【以上为自动加载约束，以下是本次需求】",
        "【以上为偏好约束，以下是本次需求】",
    )
    for marker in markers:
        if marker in text:
            text = text.split(marker, 1)[1]
            break
    text = _strip_internal_visual_context(text)
    return _strip_internal_request_markers(text)


def _is_recipe_selection_request(text: str) -> bool:
    """只把已经进入菜品选择的请求送入图片链路，健康泛问答不启动搜图。"""
    text = str(text or "").strip()
    if any(marker in text for marker in ("帮我做", "做道", "做个", "做一份", "来道", "来个", "菜品", "菜谱", "食谱")):
        return True
    if any(marker in text for marker in ("推荐", "想吃")):
        return any(char in text for char in ("鸡", "鱼", "肉", "蛋", "虾", "豆腐", "面", "饭", "菜", "汤", "粥", "粉"))
    return False


def is_execute_plan_request(text) -> bool:
    """用户是否在确认执行上一轮已经成形的方案。

    这句不是新的泛推荐，而是二跳卡片后的执行确认；路由层与 Agent 层必须
    复用同一判定，才能在不重新出候选的前提下继续保留卡片和配图。
    """
    current = str(text or "")
    if any(prefix in current for prefix in ("不要就按", "别就按", "先不按")):
        return False
    return any(
        phrase in current
        for phrase in ("就按这个方案执行", "按这个方案执行", "按照这个方案执行")
    )


def _wants_recipe_images(messages):
    """判断本轮是否由后端明确授权配图。

    配图跟随「已选定的一道菜」：健康闲聊/追问/餐馆/上门服务/候选列表阶段都不烧图；
    点名一道菜或确认（含从候选中选第 N 道）时才进入配图链路，最终仍以 recipes 为准。"""
    raw_text = _latest_user_text(messages, strip_internal=False)
    text = _current_request_text(raw_text)
    if not text:
        return False
    # 已交付单菜后的口味调整仍然是同一道菜的卡片更新，需要让图片链路
    # 重新绑定到这个不可变目标；它不是重新推荐，也不会生成第二道菜。
    if _taste_adjustment_followup_target(messages):
        return True
    intent = _classify_turn_intent(messages)
    is_specific = _is_specific_dish_request(text)
    if selected_target_for_turn(messages):
        return True
    if is_non_recipe_information_request(text):
        return False
    if intent == "recommend" and not is_specific:
        return False
    if any(marker in raw_text for marker in _INTERNAL_REQUEST_MARKERS):
        return True
    return intent == "confirm_one" or (
        intent in ("recommend", "change_one") and is_specific
    )


def _recent_recipe_names(messages, limit=8):
    """从最近结构化卡片里取菜名，给确定/指代判断一个确定性锚点。"""
    names = []
    for m in reversed(messages or []):
        if not isinstance(m, AIMessage) or getattr(m, "tool_calls", None):
            continue
        raw = str(getattr(m, "content", "") or "").strip()
        if not raw.startswith("{"):
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        recipes = data.get("recipes") if isinstance(data, dict) else None
        if not isinstance(recipes, list):
            continue
        for recipe in recipes:
            if not isinstance(recipe, dict):
                continue
            name = str(recipe.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
                if len(names) >= limit:
                    return names
    return names


def _mentions_recent_recipe(text: str, names) -> bool:
    text = str(text or "")
    return any(name and name in text for name in names)


def _has_recipe_index_ref(text: str) -> bool:
    return bool(re.search(r"(第[一二三四五六七八九十123456789]\s*[道个款样号份]|一道|二道|三道|第一道|第二道|第三道|这个|这道|这道菜|来这个|做这个|吃这个)", str(text or "")))


# --------------------------------------------------------------------------- #
#  3.6 两阶段点菜交互：候选列表（第一跳）→ 用户选定（第二跳）
#  判定必须确定性：先看用户是否已点名一道具体的菜，只有「泛推荐」才出候选列表。
#  第一阶段只给编号候选（纯文本，不出卡片、不配图），第二阶段选定后才出单卡片+配图。
# --------------------------------------------------------------------------- #
# 纯食材字表：出现在「做/吃」后面且整段只有食材字时，说明用户给的是食材而不是一道菜
_PURE_INGREDIENT_CHARS = set(
    "鸡鸭鱼肉蛋虾蟹奶豆米面饭菜汤粥粉薯糯瓜茄葱姜蒜椒菇木耳叶花萝卜笋藕玉米山药枣花生"
    "芝麻紫菜海带粉丝年糕豆腐青菜白菜菠菜生菜油菜番茄西红柿土豆胡萝卜洋葱西兰花黄瓜"
    "南瓜冬瓜丝瓜韭菜芹菜豆角豌豆青豆燕麦藜麦小米糙米黑米意面挂面米粉河粉麦片排胸"
)
# 这些词作为完整菜名时说明用户没点名菜，而是要一个方向或一顿饭。
# 不能按“包含单字”判断，否则「徐福烩饭」「番茄鸡蛋面」这类完整菜名会被误杀。
_GENERIC_DISH_NAMES = (
    "什么", "点菜", "菜品", "菜谱", "食谱", "推荐", "几道", "几个", "一道", "两道", "三道",
    "吃的", "东西", "家常菜", "快手菜", "简单", "清淡", "健康", "营养", "减脂", "低卡",
    "晚饭", "午饭", "中饭", "早餐", "晚餐", "夜宵", "宵夜", "饭", "菜", "汤", "面", "餐",
)
# 出现在菜名中的泛推荐/口味描述，说明这是约束条件而非具体菜名。
_GENERIC_DISH_PHRASES = (
    "什么", "点菜", "菜品", "菜谱", "食谱", "推荐", "几道", "几个", "一道", "两道", "三道",
    "吃的", "东西", "家常菜", "快手菜", "简单", "清淡", "健康", "营养", "减脂", "低卡",
    "晚饭", "午饭", "中饭", "早餐", "晚餐", "夜宵", "宵夜",
    "几种", "哪些", "吃法",
    # 「帮我做膳食决策」是需求句，不是菜名；否则会被单菜分支直接出卡配图。
    "膳食决策", "饮食决策", "用餐决策",
)
# 口味/约束词本身不是一道菜名。`_DISH_REQUEST_PATTERN`（触发词「想吃…/来个…」）
# 会把「晚上想吃点清淡的，两个人吃就行」里的「清淡的」截成“菜名”，
# 「少盐少油的」「就行」同理。若不在这里挡掉，整句会被判成“点名了某道菜”，
# 跳过候选清单直接出卡片配图（实测稳定性电池 B 组第 1 轮，
# 同一句话两次都稳定出卡：清蒸鲈鱼 / 番茄西葫芦炒鸡蛋）。
_TASTE_ONLY_PATTERN = re.compile(
    r"^(?:更|稍微|偏|太|非常|比较|有点|略微|再)?"
    r"(?:(?:清淡|少盐|少油|低盐|低油|低脂|低糖|无糖|减盐|减油|减脂|减糖|"
    r"健康|营养|爽口|开胃|下饭|简单|快手|方便|省事|辣|咸|甜|酸|苦|麻|重口)"
    r"(?:一点|一些|点|些|的)?)+$"
)
# 纯应答/语气碎片（「…吃就行」截出来的「就行」、「都可以」等）同样不是菜名。
_NON_DISH_REPLY_PATTERN = re.compile(
    r"^(?:就|都|也|还|再|可|以|行|好|是|要|不|得|能|会|来|点|些|一|的|了|吧|呀|呢|啊|哈|嗯|随便|无所谓)+$"
)
_NEGATED_EATING_PATTERN = re.compile(
    r"(?:吃不了|吃不下|吃不完|吃不惯|不能吃|不爱吃|不想吃|不吃|吃过了|吃着|吃腻)"
)
_TASTE_COMPLAINT_PATTERN = re.compile(
    r"(?:腥味|膻味|苦味|怪味|味道|口感|太咸|太油|太辣|太甜)"
)
_DISH_REQUEST_NEGATIVE_LOOKAHEAD = r"(?!不了|不下|不完|不惯|不能|不爱|不想|不)"
_DISH_REQUEST_PATTERN = re.compile(
    r"(?:想吃|要吃|想吃点|来个|来道|来一份|做一个|做一只|做一道|做一款|做一份|做道|做个|做一下|帮我做|给我做|就做"
    r"|推荐(?:一|两|三|几)?[道个份款]?|吃)" + _DISH_REQUEST_NEGATIVE_LOOKAHEAD +
    r"([^\s，。、！？；;：:（）()【】\[\]]{1,10})"
)
_DISH_HOWTO_PATTERN = re.compile(
    r"([^\s，。、！？；;：:（）()【】\[\]]{2,14}?)"
    r"(?:怎么做才好吃|怎么做好吃|怎么做|如何做|怎样做|咋做|的做法|做法|怎么烹饪|烹饪方法)"
)
_DISH_HOWTO_PREFIX_PATTERN = re.compile(
    r"^(?:(?:我)?(?:想|要|打算|准备)|帮我|给我|请)?"
    r"(?:做|吃|来)(?:一|两|三|四|五|几)?(?:个|道|份|只|款)?"
)
# 「先别给做法 / 不要说步骤」是**元指令**（本轮明确不需要做法），不是点名一道菜。
# 实测翻车：`1、2、3这三道里，哪道最适合减重？先别给做法。` 被 _DISH_HOWTO_PATTERN
# 从「别给做法」里抠出「先别给」当菜名 → 判成点名菜 → 跳过候选清单直接出卡片和配图。
_NO_RECIPE_HOWTO_PATTERN = re.compile(
    r"(?:先|暂时|目前|接下来)?(?:别|不要|不用|无需|不需要|先不|暂不|不必)"
    r"(?:再|要|给我|替我|帮)?(?:给|说|讲|写|展开|列出)?(?:我)?"
    r"(?:具体的?)?(?:做法|步骤|过程|流程|菜谱|食谱|详细讲解)"
)
_LEADING_QUANTIFIER = re.compile(r"^(?:一|两|三|四|五|几|个|道|份|款|些|点)+")
# 菜名里混进疑问/意图残留（「火锅吗」「做法」）时，说明这轮不是点名一道菜
_DISH_NAME_NOISE = re.compile(r"(怎么|如何|做法|热量|多少|能不能|可以|适合|吗|呢|图|照片|图片|推荐|选择|几道)")
# 需求句式残留：触发词前若紧跟着这些词，说明截出来的是「一段需求」而不是菜名。
# 实测：用户写「等我选定后，再给我完整做法、营养估算…」时，
# `_DISH_HOWTO_PATTERN` 命中「再给我完整做法」→ 截出「再给我完整」→ 被当成一道具体的菜，
# 于是整轮跳过候选清单、直接出单卡（且卡片菜名是这句话）。
_REQUEST_FILLER_WORDS = (
    "再给", "再帮", "然后", "接着", "之后", "等我", "选定", "定后", "先给", "先来",
    "完整", "详细", "具体", "全部", "一共", "另外", "顺便", "还有", "以及",
    "请给", "请你", "告诉我", "说明", "列出", "写清", "标注",
)
# 菜名里不该出现的动词/连词（出现即不是菜名）
_NON_DISH_VERBS = ("给我", "帮我", "要一", "来一", "做一", "想吃", "我要", "请")
_CONTEXTUAL_DISH_REFS = ("这个", "这道", "这道菜", "它", "这种", "那种")
_MULTI_RECOMMENDATION_COUNT = (
    r"(?:两|二|三|四|五|六|七|八|九|十|几|[2-9]|[1-9]\d{1,})\s*(?:道|个|款|种)"
)
_RECOMMENDATION_COUNT_PATTERN = re.compile(
    rf"(?:推荐|来|要|给|列|挑).{{0,4}}(?:{_MULTI_RECOMMENDATION_COUNT}|一些|多点)"
    r"|(?:有|有没有)(?:哪些|几种|什么做法|什么吃法|什么花样)"
    r"|做法大全"
)
_PURE_INGREDIENT_HOWTO_PATTERN = re.compile(
    r"([^\s，。、！？；;：:（）()【】\[\]]{1,14}?)"
    r"(?:怎么做才好吃|怎么做好吃|怎么做|如何做|怎样做|咋做|的做法|做法|怎么烹饪|烹饪方法)"
)
_INFORMATION_PREP_PATTERN = re.compile(
    r"(?:怎么|如何|怎样|咋)"
    r"(?:处理|清洗|洗|去腥|去皮|腌|腌制|挑选|挑|选购|保存|储存|存放|解冻|泡发|焯水|切)"
)
_INFORMATION_SAFETY_PATTERN = re.compile(
    # 只认「问能不能入口/下料」这类信息问句。裸词「过敏」「忌口」不再算：
    # 用户说「爸爸高血压，妈妈痛风，孩子花生过敏」是在交代约束、要一份菜单，
    # 不是在做安全问答。把它当成信息请求会短路整个候选轮，用户只拿到兜底句
    # （实测 result2 A1：一家人异质约束的泛推荐）。
    r"(?:不能一起吃|相克|不能吃|吃不了|"
    r"(?:能|可以|能不能|可不可以)(?:吃|喝|放|加|用|沾|蘸)"
    r"[^，。！？\n]{0,10}(?:吗|么|嘛|？|\?)|"
    r"会胖吗|会长胖吗|升糖吗|"
    r"(?:别忘|记得|注意).{0,10}(?:不能吃|吃不了|过敏|忌口))"
)
_INFORMATION_COMPARISON_PATTERN = re.compile(
    r"(?:哪个|哪一种|哪种).{0,8}(?:更|最|比较|好|健康|营养)"
    r"|(?:和|跟).{1,12}(?:比|哪个)"
    r"|(?:有什么区别|有何区别|相比之下)"
)
_INFORMATION_DRINK_PATTERN = re.compile(
    r"(?:吃|喝).{0,12}(?:的时候|时).{0,8}(?:喝|吃)什么"
)
_INFORMATION_EXPLANATION_PATTERN = re.compile(
    r"(?:为什么|为何|为啥|解释|说明|讲解|讲一下|讲一讲|讲讲|"
    r"什么意思|啥意思|含义|原理|原因|缘由|没看懂|看不懂|看不明白|弄不明白|"
    r"适合谁吃|谁适合吃|适合什么人吃|适合哪些人吃)"
)


def _is_generic_dish_name(name: str) -> bool:
    """判断触发词后截出的是泛推荐/口味约束，而不是一道具体菜。"""
    return (
        name in _GENERIC_DISH_NAMES
        or any(word in name for word in _GENERIC_DISH_PHRASES)
        or bool(_TASTE_ONLY_PATTERN.fullmatch(name))
        or bool(_NON_DISH_REPLY_PATTERN.fullmatch(name))
    )


def _is_pure_ingredient_subject(name: str) -> bool:
    """整段是否只是食材本身（没有形成一道确定的菜名）。"""
    normalized = _LEADING_QUANTIFIER.sub("", str(name or "").strip())
    return bool(normalized) and all(char in _PURE_INGREDIENT_CHARS for char in normalized)


def _clean_howto_dish_name(name: str) -> str:
    """去掉“想做一个…怎么做”里的请求前缀，只留下菜名供具体菜品判定。"""
    cleaned = str(name or "").strip()
    cleaned = _DISH_HOWTO_PREFIX_PATTERN.sub("", cleaned, count=1).strip()
    return _LEADING_QUANTIFIER.sub("", cleaned).strip()


def is_non_recipe_information_request(text) -> bool:
    """是否为不需要菜谱卡片的信息请求（处理、安全、营养、比较、饮品、讲解等）。

    这条判定只处理「文字说明」型问题，不负责用户是否已选定候选。调用方若已有
    候选锚点或已注入选定菜名，必须先短路选定轮，避免把“第2个，父亲能吃吗”
    这类带安全追问的选定请求误降级成纯文本。
    """
    raw = _current_request_text(text)
    if not raw:
        return False
    if _RECOMMENDATION_COUNT_PATTERN.search(raw):
        return False

    # 明确点了一道非纯食材的菜并要求制作时，安全追问不能把整轮降级掉。
    # 例如「帮我做个番茄炒蛋，父亲高血压能吃吗」仍应走单菜卡片和安全复核。
    for match in _DISH_REQUEST_PATTERN.finditer(raw):
        name = _LEADING_QUANTIFIER.sub("", match.group(1).strip())
        if (
            len(name) >= 2
            and not _is_generic_dish_name(name)
            and not _is_pure_ingredient_subject(name)
            and not _DISH_NAME_NOISE.search(name)
        ):
            return False

    howto_match = _PURE_INGREDIENT_HOWTO_PATTERN.search(raw)
    if howto_match and _is_pure_ingredient_subject(
        _clean_howto_dish_name(howto_match.group(1))
    ):
        return True
    if _INFORMATION_PREP_PATTERN.search(raw):
        return True
    if _INFORMATION_SAFETY_PATTERN.search(raw):
        return True
    if _INFORMATION_COMPARISON_PATTERN.search(raw):
        return True
    if _INFORMATION_DRINK_PATTERN.search(raw):
        return True
    if _INFORMATION_EXPLANATION_PATTERN.search(raw):
        return True
    return False


_RECIPE_DETAIL_REQUEST_MARKERS = (
    "完整做法",
    "详细做法",
    "完整菜谱",
    "详细菜谱",
    "食材用量",
    "用量和步骤",
    "步骤和用量",
    "重新整理",
    "再整理一遍",
)


def _is_explicit_recipe_detail_request(text) -> bool:
    """用户明确要求重新给出可执行菜谱时，恢复结构化输出。"""
    raw = _current_request_text(text)
    if not raw:
        return False
    if any(marker in raw for marker in _RECIPE_DETAIL_REQUEST_MARKERS):
        return True
    return bool(re.search(r"(?:这道|这个|按这个|按上面的).{0,8}怎么做", raw))


def _is_recipe_detail_followup(messages, text) -> bool:
    """只有已有菜品目标时，才把追问恢复为结构化菜谱。"""
    if not _is_explicit_recipe_detail_request(text):
        return False
    return bool(
        selected_target_for_turn(messages)
        or _has_delivered_card(messages)
    )


def _is_specific_dish_request(text) -> bool:
    """用户是否已经点名了一道具体的菜。

    - 「我想吃番茄炒蛋」「推荐一道清蒸鲈鱼」→ True：直接出单卡片；
    - 「我有鸡胸肉和青菜」「推荐几道菜」「不知道吃什么」→ False：先出候选列表。
    只报食材、口味约束或“吃不了某味道”都不算点名。
    """
    raw = str(text or "").strip()
    if not raw:
        return False
    if _RECOMMENDATION_COUNT_PATTERN.search(raw):
        return False
    if is_non_recipe_information_request(raw):
        return False
    if _NEGATED_EATING_PATTERN.search(raw) and not _DISH_REQUEST_PATTERN.search(raw):
        return False
    # 「先别给做法」是元指令，不是点名菜（详见 _NO_RECIPE_HOWTO_PATTERN 注释）。
    if _NO_RECIPE_HOWTO_PATTERN.search(raw):
        return False

    howto_match = _DISH_HOWTO_PATTERN.search(raw)
    if howto_match:
        name = _clean_howto_dish_name(howto_match.group(1))
        if (
            len(name) >= 2
            and not _is_generic_dish_name(name)
            and not _TASTE_COMPLAINT_PATTERN.search(name)
            and not _DISH_NAME_NOISE.search(name)
            and not any(word in name for word in _REQUEST_FILLER_WORDS)
            and not any(word in name for word in _NON_DISH_VERBS)
            and not any(ref in name for ref in _CONTEXTUAL_DISH_REFS)
        ):
            return True

    for match in _DISH_REQUEST_PATTERN.finditer(raw):
        name = _LEADING_QUANTIFIER.sub("", match.group(1).strip())
        if len(name) < 2:
            continue
        if _is_generic_dish_name(name):
            continue
        if _TASTE_COMPLAINT_PATTERN.search(name):
            continue
        if _is_pure_ingredient_subject(name):
            continue
        if _DISH_NAME_NOISE.search(name):
            continue
        # 需求句残留不算点名一道菜（见 _REQUEST_FILLER_WORDS 注释）
        if any(word in name for word in _REQUEST_FILLER_WORDS):
            continue
        if any(word in name for word in _NON_DISH_VERBS):
            continue
        return True
    return False


def is_specific_dish_request(text) -> bool:
    """对外别名：路由层（chat_route）与 Agent 层必须同源，避免两层判定漂移。"""
    return _is_specific_dish_request(text)


# 明确要图时用户要的是成品图，不是候选清单：此时不进入候选阶段。
# 纯食材清单（「我有鸡胸肉和青菜」）也必须进候选阶段：用户给食材时直接塞一张卡片，
# 正是两阶段交互要消灭的现象。
_INGREDIENT_KEYWORDS = (
    "鸡胸肉", "鸡腿", "鸡翅", "鸡蛋", "鸭蛋", "猪肉", "牛肉", "羊肉", "五花肉", "排骨", "肉末",
    "虾", "鱼", "鱿鱼", "蛤蜊", "蟹", "豆腐", "豆干", "腐竹",
    "青菜", "白菜", "菠菜", "生菜", "油菜", "西兰花", "花菜", "黄瓜", "南瓜", "冬瓜", "丝瓜",
    "茄子", "土豆", "胡萝卜", "萝卜", "洋葱", "青椒", "番茄", "西红柿", "蘑菇", "香菇", "木耳",
    "豆角", "豌豆", "玉米", "山药", "莲藕", "韭菜", "芹菜", "粉丝", "年糕", "面条", "挂面",
    "米粉", "米饭", "大米", "燕麦", "红薯", "紫薯", "牛奶", "酸奶", "鸡蛋",
)


def _mentions_food_ingredients(text) -> bool:
    """消息里是否点到了具体食材（纯食材消息也要走候选阶段）。"""
    raw = str(text or "")
    return any(word in raw for word in _INGREDIENT_KEYWORDS)


_INGREDIENT_UNCERTAIN_MARKERS = (
    "疑似", "可能", "不确定", "看不清", "看不出来", "无法判断",
    "无法确认", "好像", "似乎", "大概是", "推测", "或许",
)
_INGREDIENT_GATE_CONFIRM_MARKERS = (
    "同意", "确认", "准确", "没错", "对的", "是的", "可以", "没问题",
    "就这些", "都这些", "都对", "好的", "好呀", "嗯",
)
_INGREDIENT_GATE_SKIP_MARKERS = (
    "不用确认", "无需确认", "不用再确认", "跳过确认", "不用核对",
    "直接推荐", "直接开始推荐", "直接给我推荐", "开始推荐",
)
_INGREDIENT_REMOVE_MARKERS = (
    "没有", "没看到", "不是", "去掉", "删除", "不要", "不吃", "少了", "缺了",
)
_INGREDIENT_REPLACE_MARKERS = ("换成", "改成", "改为", "替换成")
_INGREDIENT_ADD_MARKERS = (
    "还有", "加上", "加个", "加一", "加两", "加几", "加些", "加份",
    "补充", "漏了", "另外有", "多了一个", "多了", "有",
)
_INGREDIENT_DIRECTIVE_PREFIXES = (
    "不准确", "不正确", "不对", "有误", "错了", "认错了", "看错了",
    "我还想要", "还需要", "还要", "再要", "我想要", "我还想", "想要", "需要",
    "可以再", "可以加", "可以补充", "请再", "请加", "能不能再", "能不能加",
    "再加上", "再加点", "再加些", "再加个", "再加", "再放点", "再放",
    "加上", "加点", "加些", "加个", "加一", "加两", "加几", "加份",
    "另外有", "另外", "多了一个", "多了", "漏了", "补充",
    "可以", "请", "能不能", "再", "加", "放",
)
_INGREDIENT_DIRECTIVE_PREFIX_RE = re.compile(
    r"^(?:"
    + "|".join(
        re.escape(prefix)
        for prefix in sorted(
            _INGREDIENT_DIRECTIVE_PREFIXES,
            key=len,
            reverse=True,
        )
    )
    + r")+"
)
_INGREDIENT_BARE_STOPWORDS = {
    "好的", "好呀", "好的呀", "可以", "没问题", "确认", "同意", "准确",
    "没错", "对的", "是的", "就这些", "都这些", "都对", "没有了",
    "行了", "行", "嗯", "对", "是", "好",
    "不准确", "不正确", "不对", "有误", "错了", "认错了", "看错了",
    "还要", "还需要", "再要", "想要", "需要", "补充", "漏了",
    "另外有", "多了",
    # 方位/指代词：实测「不对，没有鸡蛋，里面还有豆腐和青椒。」会把「里面」
    # 单独当成一个食材项列进确认清单（10-10 Q16）。
    "里面", "里头", "其中", "这里", "这里还", "那里", "那边", "这边", "上面", "下面",
}
_INGREDIENT_QUANTITY_RE = re.compile(
    r"\s*[0-9一二三四五六七八九十半两]+\s*"
    r"(?:个|袋|盒|把|根|颗|瓶|包|罐|斤|克|千克|公斤|毫升|ml|ML|只|条|块|片|杯|碗|份|枚)"
)
_INGREDIENT_UNIT_RE = re.compile(
    r"^(?:个|袋|盒|把|根|颗|瓶|包|罐|斤|克|千克|公斤|毫升|ml|ML|只|条|块|片|杯|碗|份|枚)$"
)

_VISION_INGREDIENT_SECTION_HEADERS = (
    "明确可见的食材", "可见的食材", "可见食材", "食材清单", "食材",
)
_VISION_OTHER_SECTION_HEADERS = (
    "包装或标签", "包装和标签", "包装标签", "数量", "烹饪状态",
    "厨房环境", "其他", "备注", "不确定", "无法判断",
)
_VISION_SECTION_HEADER_RE = re.compile(
    r"^(?P<header>"
    + "|".join(
        re.escape(header)
        for header in sorted(
            _VISION_INGREDIENT_SECTION_HEADERS + _VISION_OTHER_SECTION_HEADERS,
            key=len,
            reverse=True,
        )
    )
    + r")\s*(?:[:：]\s*(?P<rest>.*))?$"
)
_VISION_LINE_PREFIX_RE = re.compile(r"^(?:[-*•]|\d+[.)、])\s*")
_VISION_INGREDIENT_NEGATIVE_MARKERS = (
    "没有", "未见", "未看到", "看不到", "不是", "不存在",
)

# 图片出现品牌、颜色、包装规格时，只归一到用户能确认的食材名；
# 找不到明确食材名的行不再从数量前硬截字符串。
_VISION_INGREDIENT_ALIASES = {
    **{keyword: keyword for keyword in _INGREDIENT_KEYWORDS},
    "西红柿": "番茄",
    "红苹果": "苹果",
    "青苹果": "苹果",
    "苹果": "苹果",
    "橙子": "橙子",
    "葡萄": "葡萄",
    "蓝莓": "蓝莓",
    "草莓": "草莓",
    "樱桃": "樱桃",
    "桃子": "桃子",
    "香蕉": "香蕉",
    "猕猴桃": "猕猴桃",
    "雪梨": "梨",
    "鸭梨": "梨",
    "凤梨": "菠萝",
    "西瓜": "西瓜",
    "哈密瓜": "哈密瓜",
    "可口可乐": "可口可乐",
    "可乐": "可口可乐",
    "百威啤酒": "百威啤酒",
    "百威": "百威啤酒",
    "啤酒": "啤酒",
    "苏打水": "苏打水",
    "气泡水": "气泡水",
    "果汁": "果汁",
    "橙汁": "果汁",
    "苹果汁": "果汁",
    "葡萄汁": "果汁",
    "冷冻肉块": "冷冻肉",
    "冻肉块": "冷冻肉",
    "冷冻肉": "冷冻肉",
    "冻肉": "冷冻肉",
    "鱼块": "鱼",
    "花生酱": "花生酱",
    "酸黄瓜酱": "酸黄瓜",
    "酸黄瓜": "酸黄瓜",
}
_VISION_INGREDIENT_RE = re.compile(
    "|".join(
        re.escape(alias)
        for alias in sorted(_VISION_INGREDIENT_ALIASES, key=len, reverse=True)
    )
)


def _normalize_ingredient_draft(draft):
    """规范化状态里的食材草稿，兼容 str / dict 两种表示。"""
    normalized = []
    seen = set()
    for item in draft or []:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            quantity = str(item.get("quantity") or "").strip()
        else:
            name = str(item or "").strip()
            quantity = ""
        if not name or name in seen:
            continue
        seen.add(name)
        normalized.append({"name": name, "quantity": quantity})
    return normalized


def _ingredient_names(draft):
    return [item["name"] for item in _normalize_ingredient_draft(draft)]


def _format_ingredient_draft(draft):
    lines = []
    for item in _normalize_ingredient_draft(draft):
        quantity = item.get("quantity") or ""
        suffix = f"（{quantity}）" if quantity else ""
        lines.append(f"- {item['name']}{suffix}")
    return "\n".join(lines)


def _clean_ingredient_segment(segment):
    text = str(segment or "").strip()
    text = re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s*", "", text)
    text = re.sub(
        r"^(?:图片(?:中|里)?|照片(?:中|里)?|冰箱(?:中|里)?|可见|看到|"
        r"还包括|包括|包含|其中有|食材(?:有|为)?|库存(?:有|为)?|还有|"
        r"不是|没有|没看到|去掉|删除|不要|不吃|把|是|有)+",
        "",
        text,
    )
    return text.strip(" 的：:")


def _ingredient_segments(text):
    raw = str(text or "").strip()
    if not raw:
        return []
    raw = re.sub(r"(?:以及|还有|和|及)", "、", raw)
    return [
        part.strip()
        for part in re.split(r"[\n,，、;；。]+", raw)
        if part.strip()
    ]


def _extract_ingredient_terms(text, *, allow_bare=False):
    """从视觉描述或用户纠正句里抽出食材项（确定性、不调用 LLM）。"""
    terms = []
    for segment in _ingredient_segments(text):
        clean = _clean_ingredient_segment(segment)
        if not clean:
            continue
        if any(marker in clean for marker in _INGREDIENT_UNCERTAIN_MARKERS):
            continue

        matched = ""
        for keyword in sorted(_INGREDIENT_KEYWORDS, key=len, reverse=True):
            if keyword in clean:
                matched = keyword
                break

        quantity_match = _INGREDIENT_QUANTITY_RE.search(clean)
        quantity = quantity_match.group(0).strip() if quantity_match else ""

        if not matched and quantity_match:
            prefix = clean[: quantity_match.start()].strip()
            prefix = re.sub(
                r"^(?:图片(?:中|里)?|照片(?:中|里)?|冰箱(?:中|里)?|可见|看到|"
                r"还包括|包括|包含|其中有|食材(?:有|为)?|库存(?:有|为)?|还有|有)+",
                "",
                prefix,
            )
            prefix = re.sub(r"[^\u4e00-\u9fff]", "", prefix)
            if prefix:
                matched = prefix[-6:]

        if not matched and allow_bare:
            bare = re.sub(r"[^\u4e00-\u9fff]", "", clean)
            leading_quantity = _INGREDIENT_QUANTITY_RE.match(bare)
            if leading_quantity:
                bare = bare[leading_quantity.end():].strip()
            if (
                1 < len(bare) <= 8
                and bare not in _INGREDIENT_BARE_STOPWORDS
                and not _INGREDIENT_UNIT_RE.fullmatch(bare)
            ):
                matched = bare

        if matched:
            terms.append({"name": matched, "quantity": quantity})
    return _normalize_ingredient_draft(terms)


def _vision_ingredient_source(vision_text):
    """只截取视觉描述里的“可见食材”章节，排除数量、包装标签等章节。"""
    chunks = []
    collecting = True
    for raw_line in str(vision_text or "").splitlines():
        line = re.sub(r"[*_`#]+", "", raw_line).strip()
        line = _VISION_LINE_PREFIX_RE.sub("", line).strip()
        if not line:
            continue

        header_match = _VISION_SECTION_HEADER_RE.match(line)
        if header_match:
            header = header_match.group("header")
            rest = (header_match.group("rest") or "").strip()
            if header in _VISION_INGREDIENT_SECTION_HEADERS:
                collecting = True
                if rest:
                    chunks.append(rest)
            else:
                collecting = False
            continue

        if collecting:
            chunks.append(line)
    return "\n".join(chunks)


def _extract_ingredient_draft(vision_text):
    """把视觉自由文本整理成待确认食材清单，只保留明确看到的食材。

    这条严格路径只服务图片确认门；用户后续的口述增删改仍走
    `_extract_ingredient_terms(..., allow_bare=True)`，不会被词典限制。
    """
    terms = []
    source = _vision_ingredient_source(vision_text)
    for segment in _ingredient_segments(source):
        clean = _clean_ingredient_segment(segment)
        if not clean:
            continue
        if any(marker in clean for marker in _INGREDIENT_UNCERTAIN_MARKERS):
            continue
        if any(
            marker in clean
            for marker in _VISION_INGREDIENT_NEGATIVE_MARKERS
        ):
            continue

        quantity_match = _INGREDIENT_QUANTITY_RE.search(clean)
        quantity = quantity_match.group(0).strip() if quantity_match else ""
        for match in _VISION_INGREDIENT_RE.finditer(clean):
            canonical = _VISION_INGREDIENT_ALIASES.get(match.group(0))
            if canonical:
                terms.append({"name": canonical, "quantity": quantity})
    return _normalize_ingredient_draft(terms)


def _is_ingredient_gate_confirmation(text) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    # 带增删改动词的句子是在修正清单（如「可以加个鸡蛋」），不是纯确认。
    if _has_ingredient_correction_marker(compact):
        return False
    return any(marker in compact for marker in _INGREDIENT_GATE_CONFIRM_MARKERS)


def _is_ingredient_gate_skip(text) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    return bool(compact) and any(
        marker in compact for marker in _INGREDIENT_GATE_SKIP_MARKERS
    )


def _has_ingredient_correction_marker(text) -> bool:
    raw = str(text or "")
    return any(
        marker in raw
        for marker in (
            _INGREDIENT_REPLACE_MARKERS
            + _INGREDIENT_REMOVE_MARKERS
            + tuple(marker for marker in _INGREDIENT_ADD_MARKERS if marker != "有")
        )
    )


def _strip_ingredient_correction_markers(segment):
    cleaned = str(segment or "")
    cleaned = re.sub(r"^(?:对|对的|是的|嗯|好|好的)[，,。；;]*", "", cleaned)
    cleaned = _INGREDIENT_DIRECTIVE_PREFIX_RE.sub("", cleaned).strip()
    for marker in (
        _INGREDIENT_REPLACE_MARKERS
        + _INGREDIENT_REMOVE_MARKERS
        + _INGREDIENT_ADD_MARKERS
    ):
        cleaned = cleaned.replace(marker, "、")
    return cleaned


_INGREDIENT_REPLACEMENT_PATTERNS = (
    re.compile(
        r"(?:不是|没有)\s*(?P<old>[^，,；;。\n]+?)\s*[，,；;]\s*"
        r"(?:是|有|换成|改成|改为|替换成)?\s*(?P<new>[^，,；;。\n]+)"
    ),
    re.compile(
        r"(?:把)?\s*(?P<old>[^，,；;。\n]+?)\s*"
        r"(?:换成|改成|改为|替换成)\s*(?P<new>[^，,；;。\n]+)"
    ),
)


def _extract_replacement_pairs(text):
    """提取「旧食材→新食材」替换对，供确定性修正复用。"""
    raw = str(text or "")
    pairs = []
    spans = []
    for pattern in _INGREDIENT_REPLACEMENT_PATTERNS:
        for match in pattern.finditer(raw):
            start, end = match.span()
            if any(start < used_end and end > used_start for used_start, used_end in spans):
                continue
            old_terms = _extract_ingredient_terms(match.group("old"), allow_bare=True)
            new_terms = _extract_ingredient_terms(match.group("new"), allow_bare=True)
            if not old_terms or not new_terms:
                continue
            pairs.append((old_terms, new_terms))
            spans.append((start, end))
    return pairs, spans


def _apply_ingredient_corrections(draft, text):
    """按用户纠正更新食材草稿，返回 (新草稿, 是否发生变化)。"""
    current = _normalize_ingredient_draft(draft)
    before = _ingredient_names(current)
    raw = str(text or "")
    replacement_pairs, replacement_spans = _extract_replacement_pairs(raw)
    for old_terms, new_terms in replacement_pairs:
        old_names = set(_ingredient_names(old_terms))
        current = [item for item in current if item["name"] not in old_names]
        for item in new_terms:
            if item["name"] not in _ingredient_names(current):
                current.append(item)

    remaining = raw
    for start, end in sorted(replacement_spans, reverse=True):
        remaining = remaining[:start] + "；" + remaining[end:]

    for segment in _ingredient_segments(remaining):
        stripped = _strip_ingredient_correction_markers(segment)
        terms = _extract_ingredient_terms(stripped, allow_bare=True)
        if not terms:
            continue

        is_remove = any(marker in segment for marker in _INGREDIENT_REMOVE_MARKERS)

        if is_remove:
            remove_names = {item["name"] for item in terms}
            current = [item for item in current if item["name"] not in remove_names]
            continue

        for item in terms:
            if item["name"] not in _ingredient_names(current):
                current.append(item)

    current = _normalize_ingredient_draft(current)
    return current, _ingredient_names(current) != before


def _ingredient_confirm_text(draft, *, updated=False):
    draft = _normalize_ingredient_draft(draft)
    if not draft:
        return (
            "我暂时没能从图片里确认出明确的食材。请直接告诉我冰箱里有哪些食材，"
            "我整理成清单后再继续推荐。"
        )
    lead = "已按你的说明更新清单：" if updated else "我先根据图片整理出这些食材："
    return (
        f"{lead}\n\n{_format_ingredient_draft(draft)}\n\n"
        "请确认是否准确。如果少了、多了或认错了，直接告诉我；"
        "确认无误后我再推荐菜品。"
    )


# 「素菜/配菜」类目词表：只用于把已确认食材归类，判断清单里有没有能当素菜的项。
# 实测（2026-10-10，两个会话）：清单里没有任何绿叶菜时，模型仍会习惯性补一道
# 「蒜香清炒上海青」/「蒜蓉炒菜心」——「只能基于这份清单」是否定式约束，绕得过去。
# 所以这里改成正向给料：把清单内可当素菜的项直接点名。
_INGREDIENT_VEGETABLE_WORDS = (
    "青菜", "白菜", "菠菜", "生菜", "油菜", "菜心", "上海青", "小白菜", "油麦菜",
    "西兰花", "花菜", "黄瓜", "南瓜", "冬瓜", "丝瓜", "苦瓜", "西葫芦", "秋葵",
    "茄子", "土豆", "胡萝卜", "萝卜", "洋葱", "青椒", "彩椒", "番茄", "西红柿",
    "蘑菇", "香菇", "木耳", "金针菇", "豆角", "豌豆", "毛豆", "玉米", "山药",
    "莲藕", "韭菜", "芹菜", "甘蓝", "豆芽", "莴笋", "芥蓝", "芦笋",
)
# 加工/腌渍/饮品标记：命中就不算素菜（否则「酸黄瓜」「果汁」会被当成配菜点名）。
_INGREDIENT_NON_VEGETABLE_MARKERS = ("酱", "汁", "罐", "瓶", "可乐", "啤酒", "苏打", "酸")


def _ingredient_vegetable_options(draft) -> list:
    """清单里可作为素菜/配菜的项（确定性归类，不调用 LLM）。"""
    return [
        item
        for item in _normalize_ingredient_draft(draft)
        if any(word in item["name"] for word in _INGREDIENT_VEGETABLE_WORDS)
        and not any(
            marker in item["name"] for marker in _INGREDIENT_NON_VEGETABLE_MARKERS
        )
    ]


def _ingredient_side_dish_rule(draft) -> str:
    """素菜/配菜范围：清单里有蔬菜就正向点名，没有就把「别补清单外青菜」说死。"""
    options = _ingredient_vegetable_options(draft)
    if not options:
        return (
            "\n【素菜/配菜范围】清单里没有可作为素菜或配菜的项时，"
            "不要为了凑一道素菜去用清单外的蔬菜，改用清单内现有的食材。"
        )
    detail = "、".join(
        f"{item['name']}（{item['quantity']}）" if item["quantity"] else item["name"]
        for item in options
    )
    return (
        "\n【素菜/配菜只能用清单里的这些】"
        "清单里可作为素菜或配菜的是：" + detail + "。"
        "候选菜名里的主食材必须来自上面这份清单，不要推荐需要清单外蔬菜的菜"
        "（清单里没有绿叶菜时，就不要出现需要青菜、菜心、菠菜一类绿叶菜的菜名），"
        "用上面这几样凑素菜即可。"
    )


def _ingredient_prompt_block(draft):
    names = _ingredient_names(draft)
    if not names:
        return ""
    return (
        "\n\n【本轮已确认食材清单·最高优先级】"
        "用户已经确认冰箱里有以下食材（括号内是确认过的数量）：\n"
        + _format_ingredient_draft(draft)
        + "\n本轮只能基于这份清单做泛推荐，不要重复询问食材，"
        "不要凭空加入用户明确说没有的食材。"
        "【开头第一句必须先交代推荐依据·覆盖候选规则里的开头字数限制】"
        "本轮第一句先说明这是「根据你已确认的冰箱食材和数量」来做的推荐"
        "（图片若是小票/购物清单而非冰箱，就写「根据你已确认的食材和数量」），"
        "并把清单里的食材和数量简要带出来，让用户看到推荐依据的是他确认过的库存；"
        "清单里没有数量的食材只写名字，不要编造数量。"
        "这一句之后照旧交代健康/过敏原护栏（若有）和本轮目标，再给编号候选清单。"
        "【第一句照这个句式写·只替换括号里的内容】"
        "根据你已确认的冰箱食材和数量（共 N 样）来推荐：食材A（数量）、食材B（数量）。"
        "✓ 合格示例：根据你已确认的冰箱食材和数量（共 2 样）来推荐：番茄（2个）、鸡蛋（3个）。"
        "✗ 不合格示例：这三道都在你的低盐护栏里，花生全程不出现。"
        "（只写护栏/用途、没交代依据 = 不合格，必须重写第一句。"
        "示例只示范句式，食材内容一律以本轮确认清单为准，不许照抄示例里的食材。）"
        "【给出清单前自检一次】第一句若没有以「根据你已确认的」开头，先补上再输出。"
        + _ingredient_side_dish_rule(draft)
    )


def _is_ingredient_gate_correction(text) -> bool:
    """确认门开启时，判断用户是否在补充/删除/替换识别出的食材。"""
    raw = str(text or "")
    stripped = _strip_ingredient_correction_markers(raw)
    terms = _extract_ingredient_terms(stripped, allow_bare=True)
    if not terms:
        return False
    return _has_ingredient_correction_marker(raw)


# 「按这个执行 / 就这个方案」这类**确认语**：用户已经接受了上一轮的结论，
# 现在要的是把结论落地（复查风险、问份量、要细节），**不是**重新开一轮泛推荐。
# 没有这道闸门时，「就按这个方案执行。请检查一次花生和芝麻风险，并告诉我每人建议吃多少。」
# 会穿透 `_is_candidate_turn` —— 它既不是点名菜、也不含健康问答词，
# 于是又把 T2 已经落地的卡片退回成编号候选清单，用户刚拿到的卡片和图一起消失。
_CONFIRM_EXECUTION_MARKERS = (
    "按这个方案", "按这个", "就按这个", "按你说的", "就按你说的", "照这个",
    "按上面的", "按上面的方案", "就这个方案", "这个方案", "就这个", "就这样",
    "按此", "照此", "按这份", "就按这份", "执行吧", "开始做吧", "就这么办",
    "可以了", "就这样吧", "没问题了", "确认执行", "按计划",
)


def _has_delivered_card(messages) -> bool:
    """当前轮之前最近一轮是否已经**出过结构化卡片**（而非候选清单）。

    两阶段交互的第二跳落地后，用户的后续轮次应该是「围绕这张卡片追问/确认」，
    绝不能再退回候选阶段 —— 那等于把已经交付的成果收回去。
    """
    history = list(messages or [])
    for index in range(len(history) - 1, -1, -1):
        message = history[index]
        if isinstance(message, HumanMessage) and not _is_internal_user_turn(message):
            history = history[:index]
            break
    for m in reversed(history):
        if not isinstance(m, AIMessage) or getattr(m, "tool_calls", None):
            continue
        raw = str(getattr(m, "content", "") or "").strip()
        if not raw.startswith("{"):
            return False
        try:
            data = json.loads(raw)
        except Exception:
            return False
        if not isinstance(data, dict):
            return False
        # 只认「已经交付了菜谱卡片」这一种形态；候选清单不算。
        recipes = data.get("recipes")
        return bool(isinstance(recipes, list) and recipes)
    return False


# 「再要一道菜」的信号：已交付卡片后，用户主动索取别的菜才算新一轮；
# 否则「别的呢」这类也会被当普通追问静默掉（那一轮应当继续出菜）。
# 词表偏宽（宁可漏挡保持旧行为，也不误挡该出的卡片）。
_NEW_DISH_REQUEST_MARKERS = (
    "别的", "其他菜", "其它的菜", "另外", "再来", "还有", "换一道", "换个菜",
    "换菜", "推荐",
)


def _wants_another_dish(text) -> bool:
    """本轮是否在索取新的菜品（区别于围绕当前卡片的追问）。"""
    raw = str(text or "").strip()
    if not raw:
        return False
    return any(marker in raw for marker in _NEW_DISH_REQUEST_MARKERS)


def _latest_delivered_card_recipe_names(messages, limit=8):
    """返回最近一张已交付卡片里的菜名，供卡片后的追问锁定对象。"""

    history = list(messages or [])
    for index in range(len(history) - 1, -1, -1):
        message = history[index]
        if isinstance(message, HumanMessage) and not _is_internal_user_turn(message):
            history = history[:index]
            break
    for message in reversed(history):
        if not isinstance(message, AIMessage) or getattr(message, "tool_calls", None):
            continue
        raw = str(getattr(message, "content", "") or "").strip()
        if not raw.startswith("{"):
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        recipes = data.get("recipes") if isinstance(data, dict) else None
        if not isinstance(recipes, list) or not recipes:
            continue
        names = []
        for recipe in recipes:
            if not isinstance(recipe, dict):
                continue
            name = str(recipe.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
                if len(names) >= limit:
                    break
        if names:
            return names
    return []


def _taste_adjustment_followup_target(messages) -> str:
    """识别「围绕上一张卡片调整口感」的追轮，并返回不可变菜名。

    这类轮次不是重新点菜，也不是泛推荐。只有最近一张卡片恰好是单菜时才锁定，
    多菜卡片本身存在歧义，继续交给通用追问链路澄清。
    """

    text = _current_request_text(
        _latest_user_text(messages, strip_internal=False)
    )
    if not text or not _is_taste_adjustment_request(text):
        return ""
    if _is_recipe_detail_followup(messages, text):
        return ""
    if parse_candidate_index(text) or selected_target_for_turn(messages):
        return ""
    if _is_recipe_change_request(text):
        return ""
    names = _latest_delivered_card_recipe_names(messages)
    return names[0] if len(names) == 1 else ""


# 「换话题」信号：用户追问后的这一轮若带这些词，说明是在问别的（知识/闲聊/信息），
# 不是在对追问作答 —— 不能用"追问前的求餐消息"去判定它算不算候选轮。
# 注意：中文口语输入常常不带问号（「北京天气怎么样」无「？」），所以不能用句尾标点判。
_TOPIC_SWITCH_MARKERS = (
    "怎么样", "是什么", "什么是", "多少", "为什么", "如何", "哪一", "哪个",
    "是不是", "能不能", "介绍", "讲讲", "科普", "吗", "呢",
)


def _messages_before_last_ask(messages) -> list:
    """回到「追问之前」的消息序列：跳过尾部的追问话术，让判定输入回到真正的求餐轮。

    背景：泛推荐轮可能先被追问一次，用户答完追问时
    本轮原话往往不含求餐词（「我爸医生让严格控盐」），直接判定会被当闲聊 → 候选清单
    （两阶段点菜第一跳）再也不出现。这里只影响**判定输入**，不改消息本身、不动图结构。
    """
    msgs = list(messages)
    while msgs:
        idx = None
        for i in range(len(msgs) - 1, -1, -1):
            m = msgs[i]
            if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
                idx = i
                break
        if idx is None:
            break
        if not _last_ai_asked([msgs[idx]]):
            break
        msgs = msgs[:idx]
    return msgs


def _has_independent_intent_after_ask(user_text: str, messages) -> bool:
    """判断追问后的本轮是否已经提出了独立意图。

    只有没有独立意图时，才能把本轮视为上一条追问的回答。
    这和候选轮的回溯口径一致，避免「上一轮问过」把新的求餐、知识或菜品问题
    一并短路掉充分性门。
    """
    text = _current_request_text(user_text or "")
    return (
        is_non_recipe_information_request(text)
        or _is_specific_dish_request(text)
        or _is_recipe_detail_followup(messages, text)
        or _looks_like_meal_request(text)
        or any(marker in text for marker in _TOPIC_SWITCH_MARKERS)
    )


def _is_candidate_turn(messages, *, ingredient_gate_ready: bool = False) -> bool:
    """本轮是否走「候选清单」阶段（只给编号候选，不出卡片不配图）。"""
    if ingredient_gate_ready:
        return True
    # 非求餐的提问 / 换话题轮（「什么是痛风」「北京天气怎么样」）不做候选清单。
    # 求餐优先：「晚上吃什么呢」含疑问词但仍算候选轮，不受影响。
    # （背景：既有 `_classify_turn_intent` 会把「什么是痛风」这类知识提问判成 recommend，
    #  这里在候选层面挡一道，避免给知识问答回一份菜单。）
    latest = _current_request_text(_latest_user_text(messages))
    if not _looks_like_meal_request(latest) and any(
        marker in latest for marker in _TOPIC_SWITCH_MARKERS
    ):
        return False
    # 本轮若是对追问的回答，判定与文本都回到"追问前的求餐消息"——否则答完追问
    # （「我爸医生让严格控盐」）会被判成 other/闲聊，候选清单（亮点①）不再出现。
    # 但**本轮自身带独立意图时不算"回答追问"**（换话题），此时不能截断：
    #   ① 信息问答 / 点名菜 / 做法追问；② 新的求餐请求；③ 含疑问词（知识/闲聊提问）
    independent_intent = _has_independent_intent_after_ask(latest, messages)
    if not independent_intent:
        messages = _messages_before_last_ask(messages)
    intent = _classify_turn_intent(messages)
    if intent not in ("recommend", "change_one", "other"):
        return False
    raw = _latest_user_text(messages, strip_internal=False)
    text = _current_request_text(raw)
    if not text or _is_specific_dish_request(text):
        return False
    if (
        (bool(_recent_recipe_names(messages)) or bool(_recent_candidates(messages)))
        and _is_taste_adjustment_request(text)
    ):
        return False
    # 路由层或 checkpoint 已经把候选序号解析成一道确定菜品时，本轮是选定轮，
    # 不能被“换成/改成”等词重新判回候选轮。
    if selected_target_for_turn(messages):
        return False
    if is_non_recipe_information_request(text):
        return False
    if _has_delivered_card(messages) and any(
        marker in text for marker in _CONFIRM_EXECUTION_MARKERS
    ):
        return False
    if (
        any(marker in raw for marker in _INTERNAL_REQUEST_MARKERS)
        and not _is_generic_dish_name(text)
    ):
        return False
    # “还能吃什么 / 能吃什么”是泛推荐，不是“能否食用”的安全追问。
    # 只把带明确疑问收尾的“能吃/能喝”识别成信息问题，保留真实安全问答。
    if re.search(
        r"(能不能吃|可不可以吃|能吃(?!什么)[^，。！？\n]{0,8}[吗么嘛？?]|"
        r"能喝(?!什么)[^，。！？\n]{0,8}[吗么嘛？?]|适合吃|该不该吃|要不要吃|可以吃吗)",
        text,
    ):
        return False
    # intent=other 时，只有明确给了食材才允许进入推荐流程，避免把闲聊列成菜单。
    if intent == "other" and not _mentions_food_ingredients(text):
        return False
    return True


_CANDIDATE_REVISION_MARKERS = (
    "难道",
    "不是",
    "不算",
    "不对",
    "说的是",
    "我说的是",
    "我要的是",
    "想要的是",
    "应该是",
)


def is_candidate_revision_request(text: str) -> bool:
    """识别否定上一批推荐、要求修正方向的请求。"""
    text = _current_request_text(text)
    if not text or parse_candidate_index(text):
        return False
    if _is_specific_dish_request(text):
        return False
    has_revision = any(marker in text for marker in _CANDIDATE_REVISION_MARKERS)
    has_question = "？" in text or "?" in text
    has_context_ref = any(ref in text for ref in ("这些", "这几道", "上一轮", "刚才", "那些"))
    has_purpose = bool(_occasion_hint(text))
    return has_revision and (has_question or has_context_ref or has_purpose)


def _is_candidate_revision_turn(messages) -> bool:
    """已有候选且用户在否定/纠正推荐方向时，只允许重新列候选。"""
    text = _current_request_text(_latest_user_text(messages))
    return bool(
        _recent_candidates(messages)
        and is_candidate_revision_request(text)
        and not selected_target_for_turn(messages)
    )


_CN_NUMERALS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _candidate_number(token: str):
    """把中文/阿拉伯序号转成整数，转不出来返回 None。"""
    token = str(token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token in _CN_NUMERALS:
        return _CN_NUMERALS[token]
    if token.startswith("十") and len(token) == 2 and token[1] in _CN_NUMERALS:
        return 10 + _CN_NUMERALS[token[1]]
    if token.endswith("十") and len(token) == 2 and token[0] in _CN_NUMERALS:
        return _CN_NUMERALS[token[0]] * 10
    return None


def parse_candidate_index(text):
    """把「就第2个 / 第二个 / 第2道 / 选2」解析成 1 起始的候选序号（解析不出返回 None）。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    match = re.search(r"第\s*([0-9]+|[一二两三四五六七八九十]+)\s*[个道款样号份]", raw)
    if match:
        number = _candidate_number(match.group(1))
        if number and number > 0:
            return number
    # 「选2吧 / 选二吧 / 选2，少盐点」这类命令式口语：数字后必须跟语气词或标点，
    # 后面仍可继续接口味追问。不能用结尾匹配，否则用户很自然的「选2吧」会漏判，
    # 整轮随后进入通用 Agent，既慢又可能答非所问。
    match = re.match(
        r"^\s*(?:选|就要|就选|就做|要|做)\s*"
        r"([0-9]{1,2}|[一二两三四五六七八九十]+)\s*"
        r"(?:号|个|道|款)?\s*(?:吧|啊|呀|呢|哦|嘛|，|,|。|！|!)",
        raw,
    )
    if match:
        number = _candidate_number(match.group(1))
        if number and number > 0:
            return number
    # 真实会话里把「做3吧」写成了「做3把」：只认完整的「做 + 序号 + 把」，
    # 不把「把」扩成通用量词，避免「做3把菜」等正常数量表达误触候选选定。
    match = re.match(
        r"^\s*做\s*([0-9]{1,2}|[一二两三四五六七八九十]+)\s*把\s*[，,。！!？?]?\s*$",
        raw,
    )
    if match:
        number = _candidate_number(match.group(1))
        if number and number > 0:
            return number
    # 「选2 / 就要2 / 做2」这类极短指令才当序号，避免把「做2个菜」误判成选第2道
    if len(raw) <= 12:
        match = re.search(r"(?:选|就要|就选|就做|要|做)\s*([0-9]{1,2})\s*(?:号|个|道)?$", raw)
        if match:
            number = _candidate_number(match.group(1))
            if number and number > 0:
                return number
    # 「3吧 / 3， / 3。」这类口语选定：裸序号 + 语气词或标点收尾（用户真实说法是「3吧，但是可以辣一点」）。
    # 必须紧跟语气词/标点才算，否则「做2个菜」「3个人」会被误判成选第 2/3 道；
    # 这里只做形态识别，真正的锚点校验在调用方——_classify_turn_intent 要求 has_prior_candidates、
    # _resolve_picked_candidate 要求会话里真有候选清单，所以「第9个」这种越界序号不会被认下。
    match = re.match(r"^\s*([0-9]{1,2})\s*(?:吧|啊|呀|呢|哦|，|,|。|！|!)", raw)
    if match:
        number = _candidate_number(match.group(1))
        if number and number > 0:
            return number
    # 「那就3吧 / 那就要3吧 / 那就选3吧 / 就3」这类带承接前缀的口语选定：用户在
    # 候选清单后常用「那就…吧」或直接「就3」承接，前缀会吃掉上面所有形态的匹配
    # （实测 user_c5d8dcc6b7 第 2 轮「那就3吧」、user_1ed7243c4a 第 2 轮「就3」
    # 既没开配图门、也没注入已选定菜名，卡片有图槽却没有图）。
    # 约束与裸序号口径保持一致：序号后必须紧跟语气词/标点或直接收尾，因此
    # 「那就2个菜」「那就3个人」「就3个人」不会被误判成选第 2/3 道。
    match = re.match(
        r"^\s*(?:那就选|那就要|那就|那选|那要|那|就)\s*"
        r"([0-9]{1,2}|[一二两三四五六七八九十]+)\s*"
        r"(?:吧|啊|呀|呢|哦|嘛|，|,|。|！|!|\s*$)",
        raw,
    )
    if match:
        number = _candidate_number(match.group(1))
        if number and number > 0:
            return number
    return None


def _is_internal_user_turn(message) -> bool:
    """系统自动注入的「假用户消息」：历史摘要与健康护栏审核。

    它们不是新的用户对话轮次，而是同一轮的内部续跑。判定必须与 `_latest_user_text`
    的跳过口径完全一致，否则会出现「正文取到了本轮需求、锚点却截断在内部消息上」的错位。
    """
    content = str(getattr(message, "content", "") or "")
    return content.startswith(("[历史对话摘要", "[健康护栏审核"))


def _recent_candidates(messages, limit=8):
    """取当前轮之前最近一轮登记的候选菜名（两阶段交互第一跳的确定性锚点）。

    只查最新一条**真实**用户消息之前的历史：当前轮生成的正文不能遮蔽上一轮候选，
    但候选之后若已经发生过别的对话轮次，也不能再把旧序号误认成本轮选定。

    内部消息（历史摘要 / 健康护栏审核）不算新轮次 —— 否则一轮里只要发生过护栏重试，
    候选锚点就会被截断掉：回溯时先撞上重试后的普通 AI 回答（非候选 payload）→ 返回空
    → 序号指代失效 → 走到 verify_route 时被判成「非点菜轮」直通纯文本，卡片和配图一起消失。
    实测复现：「饭点推荐 → 2吧，但是我的父亲高血压，可以吃这个吗」。
    """
    history = list(messages or [])
    for index in range(len(history) - 1, -1, -1):
        if isinstance(history[index], HumanMessage) and not _is_internal_user_turn(history[index]):
            history = history[:index]
            break
    for m in reversed(history):
        if not isinstance(m, AIMessage) or getattr(m, "tool_calls", None):
            continue
        raw = str(getattr(m, "content", "") or "").strip()
        if not raw:
            # 空内容占位消息（工具调用后的空 AIMessage）——内部噪声，跳过继续找。
            continue
        if not raw.startswith("{"):
            # 一段真正的正文（markdown）——说明候选之后已经发生过别的对话轮，停。
            return []
        try:
            data = json.loads(raw)
        except Exception:
            return []
        if not isinstance(data, dict):
            return []
        if str(data.get("answer_kind") or "") == "candidates":
            names = [str(n).strip() for n in (data.get("candidates") or []) if str(n).strip()]
            return names[:limit] if names else []
        # JSON 但不是候选 payload：区分「内部工具结果」和「另一轮正文」。
        # RAG 检索（found/hits）、联网搜索（text/query）、食材识别等工具结果
        # 夹在候选与当前轮之间，是**同一轮的内部消息**，绝不能当成「隔了一轮」
        # 而把候选锚点作废——否则工具链一多，序号选定就永远够不到候选（卡片消失）。
        if set(data) & {"found", "hits", "text", "query", "results", "matches", "items"}:
            continue
        # 其余 JSON（如另一份食谱卡、结构化回答）视为隔轮，按基线语义不再认旧序号。
        return []
    return []


def _older_candidates(messages, limit=8):
    """比 _recent_candidates 更深一层：**跨过中间的食谱 payload**，找上一份候选清单。

    ⚠️ 只用于「用户提的序号已经指不到东西了 → 回问一句并把清单重新登记」这类兜底，
    **绝不**用它把旧序号直接当成本轮选定 —— 那会破坏
    `tests/agent/test_candidate_flow.py::test_stale_candidates_after_newer_turn_are_not_confirm`
    冻结的产品语义（候选之后隔了别的对话轮，旧序号就不再算选定）。
    简言之：这里只回答「上一份清单长什么样」，不回答「用户这次选了哪道」。
    """
    for m in reversed(list(messages or [])):
        if not isinstance(m, AIMessage) or getattr(m, "tool_calls", None):
            continue
        raw = str(getattr(m, "content", "") or "").strip()
        if not raw.startswith("{"):
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if str(data.get("answer_kind") or "") != "candidates":
            continue
        names = [str(n).strip() for n in (data.get("candidates") or []) if str(n).strip()]
        if names:
            return names[:limit]
    return []


def _latest_card_recipe_count(messages) -> int:
    """最近一张结构化卡片有几道菜（用于判断用户说的「第N道」是否已经越界）。"""
    for m in reversed(list(messages or [])):
        if not isinstance(m, AIMessage) or getattr(m, "tool_calls", None):
            continue
        raw = str(getattr(m, "content", "") or "").strip()
        if not raw.startswith("{"):
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        recipes = data.get("recipes")
        if isinstance(recipes, list) and recipes:
            return len(recipes)
    return 0


_RECIPE_INDEX_RE = re.compile(r"第\s*([一二三四五六七八九十0-9]{1,2})\s*[道]")


def _index_ref_without_target(messages) -> bool:
    """用户用了序号指代（「第N个/第N道」），但它已经指不到任何候选/卡片。

    条件全满足才算：
      1. 正文里有序号指代；
      2. 没有实时候选锚点（`_recent_candidates` 为空 —— 这是冻结规则，不放宽）；
      3. 序号越界于最近一张卡片的菜品数（例如卡片只有 1 道，用户却说「第3个」）。

    命中后**必须**拦掉「让模型自由发挥」这条路：实测它会顺手换一道菜，
    并回复「前面其实只出过一道菜，没有第三道」——用户既拿不到想要的菜，也看不懂发生了什么。
    """
    if selected_target_for_turn(messages):
        return False
    text = _current_request_text(_latest_user_text(messages, strip_internal=False))
    index = parse_candidate_index(text)
    if not index:
        match = _RECIPE_INDEX_RE.search(text)
        index = _candidate_number(match.group(1)) if match else None
    if not index:
        return False
    if _recent_candidates(messages):
        return False
    return index > _latest_card_recipe_count(messages)


def resolve_candidate_pick(messages):
    """把用户这一轮的序号指代解析成候选里的 (序号, 菜名)；解析不出返回 None。"""
    index = parse_candidate_index(_latest_user_text(messages))
    if not index:
        return None
    candidates = _recent_candidates(messages)
    if not candidates or index > len(candidates):
        return None
    return index, candidates[index - 1]


def selected_target_for_turn(messages):
    """返回本轮唯一的已确认目标 `(序号|None, 菜名)`。

    路由层已经从持久化候选中解析出菜名时，其注入信令优先于 checkpoint 回查；
    只有没有注入值时，才回退到消息历史中的候选锚点。所有“是否选定、序号是否
    失效、提示词注入、图片门”都必须复用这一个入口，避免同轮出现多套对象。
    """
    raw = _latest_user_text(messages, strip_internal=False)
    injected = _extract_selected_candidate(raw)
    if injected:
        # 注入块自带一长段后端控制说明；序号必须从真正的用户正文里解析，
        # 否则「选2吧」会退化成只有菜名、没有序号的承接语。
        request_text = _current_request_text(
            _SELECTION_CONTROL_BLOCK_RE.sub("", raw, count=1)
        )
        index = parse_candidate_index(request_text)
        return index, injected
    return resolve_candidate_pick(messages)


def _selected_target_note(messages) -> str:
    """把本轮已确认目标格式化成可直接进入安全文案的前缀。"""
    picked = selected_target_for_turn(messages)
    if not picked:
        return ""
    index, name = picked
    if index:
        return f"第{index}道已选定为「{name}」。"
    return f"本轮已选定「{name}」。"


_CANDIDATE_ACK_PATTERN = re.compile(
    r"^(?:行|好的|好|可以|嗯+|没问题|ok|okay|xing)"
    r"(?:吧|呀|啊|哦|呢|了|哒|嘞|哈|的|谢谢)?$"
)


def _is_candidate_ack_without_pick(messages) -> bool:
    """上一轮有候选，本轮只是纯确认但还没指定菜名。

    例如候选清单后的「行 / xing / 好的 / 可以」。这种轮次只能追问序号，
    不能让模型替用户挑一道，更不能进入普通菜谱结构化后出卡片和图片。
    """
    if not _recent_candidates(messages):
        return False
    raw = _latest_user_text(messages, strip_internal=False)
    text = _current_request_text(raw)
    if not text:
        return False
    if selected_target_for_turn(messages) or parse_candidate_index(text):
        return False
    normalized = re.sub(r"[\s，,。.!！?？;；:：~～、]+", "", text).lower()
    return bool(_CANDIDATE_ACK_PATTERN.fullmatch(normalized))


def _candidate_ack_reply(names) -> str:
    """纯确认轮的统一追问文案，不包含任何菜品结论。"""
    count = max(1, len(list(names or [])))
    return f"收到，不过还没指定哪一道。直接回复 1 到 {count} 的序号就行。"


# 带健康/口味调整语气的词：出现这些说明是在「调整这道菜」（追问或改菜），而不是「选定」。
# `_classify_turn_intent` 与 `_is_dish_pick_turn` 共用这一份口径，避免两层判定漂移。
_DISH_ADJUST_WORDS = (
    "清淡", "少", "淡", "不要", "别", "盐", "油", "热量", "钠", "糖", "脂肪",
    "能不能", "可以吗", "适合吗", "怎么吃",
)


def _is_dish_pick_turn(messages) -> bool:
    """本轮是否「真的选定了一道菜」——给 `_is_ask_turn` 开一个窄口子用。

    背景：「2吧，可以更酸一点，但是我的父亲高血压，可以吃这个吗」这类轮次里，
    正文（opening）确实以问句收尾，但它同时是一次明确的选定；整体当追问挡掉，
    就会出现「用户已经确认了，却没有卡片、没有图片」。

    口子必须窄：没有菜名锚点的纯提问（「想确认一下，你更想吃清淡的还是重口的？」）
    依然要被挡住，否则会答非所问地弹卡片。
    """
    text = _latest_user_text(messages)
    if not text:
        return False
    # 序号选定（「就第2个」「2吧」）本身无歧义：只要真有候选锚点就算选定
    if selected_target_for_turn(messages):
        return True
    # 正文点名了候选清单/最近卡片里的菜也算选定；带调整语气的算追问（与 _classify_turn_intent 同口径）
    anchor_names = list(_recent_candidates(messages)) + list(_recent_recipe_names(messages))
    if not anchor_names or not _mentions_recent_recipe(text, anchor_names):
        return False
    return not any(word in text for word in _DISH_ADJUST_WORDS)


def _extract_candidate_names(text) -> list:
    """从正文里解析编号候选清单。

    候选序号必须与用户看到的正文一致，所以以正文为准解析，
    而不是另外让模型再生成一份清单（两份清单顺序可能不同，「第2个」就会指错菜）。
    兼容带理由和裸菜名两种形态；裸菜名仍需短小且不能呈现明显的步骤动作，
    避免把菜谱里的编号步骤（例如「2. **爆辣底**：下锅炒香」）登记成候选。
    """
    names = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(r"^(?:[-*•]\s*)?(\d{1,2})\s*[.、)）．:：]\s*(.+)$", line)
        if not match:
            continue
        body = match.group(2).strip()
        parts = re.split(r"\s*(——|—|--|–|:|：|\||｜)\s*", body, maxsplit=1)
        raw_name = parts[0].strip()
        separator = parts[1] if len(parts) >= 3 else ""
        # 加粗标题是步骤标题的强特征，不能把步骤名当菜名。
        if raw_name.startswith("**") or raw_name.endswith("**"):
            continue
        name = raw_name.strip("*`「」『』\"'“”").strip()
        name = re.sub(r"[，。！？；;,!.?;]+$", "", name).strip()
        if not (2 <= len(name) <= 14):
            continue
        # 候选菜名是「食材名词」而不是句子：名字内部出现中文句读就一定是做法/说明行。
        # 实测 user_777df48ed8 第 5 轮：`6. 趁热吃，凉了腥味回潮。` 被当成候选菜名登记，
        # 下一轮序号指代就锚到了这行散文上。菜名后接「 —— 理由」时理由已被上面的
        # 分列切走，不会误伤「炒青菜 / 蒸鲈鱼」这类合法候选。
        if re.search(r"[，。！？；、]", name):
            continue
        # 裸编号行只接受像菜名的短主体。带理由的动作型菜名仍按既有规则保留。
        if not separator and re.match(
            r"^(?:切|放|加|下|倒|淋|撒|盖|转|关|取|把|用|将|盛|备|打|调|搅|趁|留|装|摆|剁|拍)",
            name,
        ):
            continue
        # 做法动词开头的标题只在冒号步骤行里排除；菜名后接「 —— 理由」时，
        # 「炒青菜 / 蒸鲈鱼 / 拌黄瓜」仍是合法候选。
        if separator in {":", "："} and re.match(
            r"^(?:切|放|加|下|倒|淋|撒|盖|转|关|取|把|用|将|盛|备|想|要|"
            r"打|调|搅|"
            r"煮|炒|煎|蒸|炖|焖|腌|爆|拌|烤|烧|炸|卤|熘|烩)",
            name,
        ):
            continue
        if re.search(r"(去皮|切丝|切块|上浆|下锅|出锅|煮开|淋入)$", name):
            continue
        if name in names:
            continue
        names.append(name)
    return names[:8]


def _candidate_source_text(messages) -> str:
    """汇总本轮已经展示过的所有助手正文，供候选锚点解析。

    健康护栏可能打回重生成：第一段已经流式展示了编号候选，第二段只修成
    一道安全菜。只解析最后一条会丢掉用户真正看到的候选序号，因此这里按
    本轮真实用户消息之后的全部助手正文汇总，编号去重仍由候选解析负责。
    """
    history = list(messages or [])
    start = 0
    for index in range(len(history) - 1, -1, -1):
        message = history[index]
        if (
            isinstance(message, HumanMessage)
            and not str(message.content).startswith("[历史对话摘要")
            and not str(message.content).startswith("[健康护栏审核")
        ):
            start = index + 1
            break
    parts = []
    for message in history[start:]:
        if not isinstance(message, AIMessage) or getattr(message, "tool_calls", None):
            continue
        text = _message_text(message).strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def _filter_candidate_names(names, session_id: str | None = None) -> list:
    """候选菜名也要过过敏原硬审计：清单里不能出现用户碰不得的菜。"""
    kept = []
    allergens = _allergens_for_audit(session_id)
    for name in names or []:
        dish = str(name or "").strip()
        if not dish:
            continue
        if allergens:
            try:
                violations = audit_allergens(dish, allergens, use_optional=True)
            except Exception:
                violations = []
            if violations:
                continue
        kept.append(dish)
    return kept


def _classify_turn_intent(messages) -> str:
    """确定性意图门：recommend | confirm_one | change_one | followup | restaurant | home_service | other。"""
    text = _latest_user_text(messages)
    if not text:
        return "other"
    current = _current_request_text(text)
    recipe_names = _recent_recipe_names(messages)
    candidates = _recent_candidates(messages)
    selected_target = selected_target_for_turn(messages)
    has_prior_recipe = bool(recipe_names)
    has_prior_candidates = bool(candidates) or bool(selected_target)
    has_prior_restaurant = _has_prior_restaurant_context(messages)
    return _classify_turn_intent_shared(
        current,
        is_specific_dish=_is_specific_dish_request(current),
        execute_plan=is_execute_plan_request(current),
        candidate_index=(
            selected_target[0] if selected_target and selected_target[0]
            else parse_candidate_index(current)
        ),
        has_prior_recipe=has_prior_recipe,
        has_prior_candidates=has_prior_candidates,
        has_prior_restaurant=has_prior_restaurant,
        is_candidate_revision=(
            has_prior_candidates and _is_candidate_revision_turn(messages)
        ),
        has_recipe_index_ref=_has_recipe_index_ref(current),
        mentions_recent_recipe=_mentions_recent_recipe(current, recipe_names),
        mentions_recent_candidate=_mentions_recent_recipe(current, candidates),
        recommendation_count=bool(_RECOMMENDATION_COUNT_PATTERN.search(current)),
    )


def _has_prior_restaurant_context(messages) -> bool:
    """只看当前轮之前最近的餐厅轮，并允许连续的“换一家”继续沿用。

    中间出现任何普通用户轮就立即停止回溯，避免旧的外食场景在后面无关话题里
    把“换一家”永久解释成餐厅。内部历史摘要/护栏消息不算用户轮。
    """
    latest = _latest_user_index(messages)
    if latest is None:
        return False
    for index in range(latest - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, HumanMessage) or _is_internal_user_turn(message):
            continue
        text = _current_request_text(_message_text(message))
        if _is_restaurant_ordering_scene(text):
            return True
        if _is_restaurant_ordering_scene(
            text,
            has_prior_restaurant=True,
        ):
            continue
        return False
    return False


def _is_new_ingredient_image_request(text):
    """判断本轮图片是新的食材输入，还是对当前菜品的修改。"""
    # 调用此函数的前提是消息中已经包含图片。
    # 图片本身就是用户明确提供的新视觉输入，因此直接建立新的食材主题。
    # 普通的“清淡一点”“换成猪肉”等追问没有图片，不会进入这里，
    # 仍然通过 checkpoint 保留当前菜品记忆。
    return True


def _latest_user_index(messages):
    """返回最近一条真实用户消息的位置，跳过历史摘要消息。"""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if (
            isinstance(message, HumanMessage)
            and not str(message.content).startswith("[历史对话摘要")
            and not str(message.content).startswith("[健康护栏审核")
        ):
            return index
    return None


def _latest_new_image_index(messages):
    """返回最近一次新食材图片的起点，作为当前菜品主题边界。"""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if (
            isinstance(message, HumanMessage)
            and not str(message.content).startswith("[历史对话摘要")
            and _message_has_image(message)
            and _is_new_ingredient_image_request(_message_text(message))
        ):
            return index
    return None


def _messages_for_current_turn(messages, isolate_old_context=False):
    """按需要取当前轮消息，避免新图片请求混入旧菜品的工具结果。"""
    if not isolate_old_context:
        return messages
    index = _latest_new_image_index(messages)
    if index is None:
        index = _latest_user_index(messages)
    if index is None:
        return messages
    return messages[index:]


def _current_turn_has_tool_result(messages) -> bool:
    """本轮是否已经执行过工具，避免强制搜索在回边后重复触发。"""
    start = _latest_user_index(messages)
    if start is None:
        return False
    return any(isinstance(m, ToolMessage) for m in messages[start + 1:])


@trace_node("chef_think")
def chef_agent_node(state: "ChefState"):
    messages = state["messages"]#已经被压缩过后的4种消息类的消息
    # 前置插入系统提示词，再追加历史对话消息（先清掉孤儿 ToolMessage 防 API 400）
    latest_text = _latest_user_text(messages)
    latest_has_image = _latest_user_has_image(messages)
    # 候选清单后的纯确认（「行 / xing」）不代表已经选菜。这里必须确定性收口，
    # 不能把决定权交给模型，否则它会替用户挑一道并继续出卡片和图片。
    if _is_candidate_ack_without_pick(messages):
        return {
            "messages": [
                AIMessage(content=_candidate_ack_reply(_recent_candidates(messages)))
            ]
        }
    requested_allergens = requested_allergen_additions(
        latest_text,
        _allergens_for_audit(state.get("session_id")),
    )
    if requested_allergens:
        subject = "、".join(requested_allergens)
        target_note = _selected_target_note(messages)
        return {
            "messages": [
                AIMessage(
                    content=(
                        f"{target_note}家庭画像中已确认{subject}过敏，{subject}是硬约束，"
                        "不能加入这道菜，也不能推荐含该过敏原的做法。"
                    )
                )
            ]
        }
    taste_followup_target = _taste_adjustment_followup_target(messages)
    # 强制搜索只负责本轮第一次决策；工具结果回流到 chef_think 后必须交给
    # LLM 基于结果收口，否则同一轮会反复创建同样的 web_search，直到耗尽预算。
    if (
        not taste_followup_target
        and _should_force_web_search(latest_text)
        and not _current_turn_has_tool_result(messages)
    ):
        return {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": f"forced-web-search-{len(messages)}",
                            "name": "web_search",
                            "args": {"query": latest_text},
                        }
                    ],
                )
            ]
        }
    is_new_image_request = (
        latest_has_image and _is_new_ingredient_image_request(latest_text)
    )
    prompt_content = SYSTEM_PROMPT
    # 外食找店与居家做菜必须互斥：同一轮只给一种产品形态。
    turn_intent = _classify_turn_intent(messages)
    if turn_intent == "restaurant":
        prompt_content += RESTAURANT_SCENE_RULE
    # 两阶段点菜：泛推荐轮出候选清单（不出卡片），其余轮次保持单菜规则。
    elif _is_candidate_turn(
        messages,
        ingredient_gate_ready=bool(state.get("ingredient_gate_ready")),
    ):
        prompt_content += _candidate_list_rule(
            messages,
            ingredient_ready=bool(state.get("ingredient_gate_ready")),
        )
        # 用途锚定：把用户本轮的用途/场合（下酒/宵夜/带饭…）钉成硬规则。
        # 字符串模板里已给了维度优先级与写法示例，这里再补一道确定性兜底 ——
        # 只靠示例时模型仍可能退回默认的营养维度。
        occasion = _occasion_hint(_latest_user_text(messages))
        if occasion:
            prompt_content += _occasion_rule(occasion)
    elif not _wants_multiple_recipes(messages):
        prompt_content += SINGLE_RECIPE_RULE
    if state.get("ingredient_gate_ready"):
        prompt_content += _ingredient_prompt_block(state.get("ingredient_draft"))
    # 用户从上一轮候选清单里选了第 N 道：把菜名确定性注入本轮提示词，
    # 保证卡片就是用户选的那道菜，不靠模型自己猜「第2个」指谁。
    picked = selected_target_for_turn(messages)
    if picked:
        selection = f"第{picked[0]}道：" if picked[0] else ""
        prompt_content += (
            f"\n\n【本轮已选定的候选】用户已明确选定{selection}{picked[1]}。"
            "本轮只输出这一道菜的完整做法，不要换菜、不要再列候选清单，"
            "也不得声称不知道第几道或序号已经失效。"
        )
    if taste_followup_target:
        prompt_content += (
            f"\n\n【锁定上一张卡片】用户正在调整上一张卡片中的"
            f"「{taste_followup_target}」。必须保留原菜名和主食材，"
            "禁止换成别的菜；只调整火候、调味、口感或做法。"
            "不要调用工具、不要搜图、不要重列候选。"
            "请输出这道菜调整后的完整可执行做法，包含调整后的食材用量、步骤和注意事项，"
            "让后续结构化节点据此更新原菜谱卡片；不要输出 JSON、Markdown 代码块、"
            "思维链或内部控制字段。"
        )
    if is_new_image_request:
        prompt_content += (
            "\n\n【新食材图片优先规则】"
            "本轮用户上传的是新的食材图片，并且是在询问新的菜品推荐。"
            "必须以本轮图片识别出的食材为最高优先级，忽略历史中的当前菜名、旧食材和旧菜谱。"
            "先重新识别本轮图片，再基于本轮食材调用搜索工具。"
            "不得因为历史摘要中存在上一道菜，就继续生成上一道菜。"
        )
    # 序号指代已失效（清单跨轮过期 / 序号越界）：先给模型一条硬规则定住它的嘴。
    # 否则它会顺手挑一道菜顶替，还跟用户解释「其实没有第三道」——两件事都是错的。
    # 结构化节点那边会把上一份清单重新登记成候选 payload，让下一轮「就第3个」能认。
    if _index_ref_without_target(messages):
        recovered = _older_candidates(messages)
        if recovered:
            numbered = "\n".join(
                f"{i}. {name}" for i, name in enumerate(recovered, 1)
            )
            prompt_content += (
                "\n\n【序号失效·重新登记清单】用户用了「第N个/第N道」，但那份清单已经跨轮失效。"
                "本轮**不要**自己挑一道菜顶替。请先说明「之前那份清单已过期」，"
                "然后**原样重新列出**下面这份清单（编号与菜名都不许改，不写做法、不出卡片）：\n"
                + numbered
                + "\n最后另起一行写「回复序号就行，我再把这一道的完整做法给你。」，不要用问句结尾。"
            )
        else:
            prompt_content += (
                "\n\n【序号失效·无清单可用】用户用了「第N个/第N道」但已经找不到对应清单。"
                "**不要**自己挑一道菜顶替，也**不要**解释「其实没有第三道」这种内部推理；"
                "直接说明之前的清单已经过期，请用户把食材或想要的菜名再说一次。"
            )
    prompt_msg = SystemMessage(content=prompt_content)#保存字符串提示词
    model_messages = _messages_for_current_turn(
        messages,
        isolate_old_context=_latest_new_image_index(messages) is not None,
    )
    payload = [prompt_msg] + _drop_orphan_tool_messages(model_messages)#提示词+历史对话消息，中括号是转换成列表调用的涵数是把孤立的toolmessage删除
    # 上游 LLM 偶发 502/超时，加重试避免整轮对话直接 500 崩掉
    # 口感追轮是短文本收口，不调用工具。DeepSeek 推理模型在部分上游会
    # 把全部输出预算写进 reasoning_content，最终 content 为空；项目已有
    # deepseek-chat 摘要模型专门用于稳定返回正文，这里复用同一模型。
    _ensure_llms()
    turn_llm = (summary_llm or llm) if taste_followup_target else llm_with_tools
    last_err = None
    for attempt in range(3):
        try:
            resp = turn_llm.invoke(payload)#就是给LLM的所有上下文消息让他生成怎么调用和调用什么工具的消息 带tool_calls的 AI 消息
            break
        except (openai.InternalServerError, openai.APIConnectionError, openai.RateLimitError) as e:
            last_err = e#存储错误
            time.sleep(1 + attempt)  # 退避 1s / 2s 再试，但只试3次
    else:
        raise last_err  # 3 次都失败才真正抛出错误
    # 选定轮第二圈还要取证时补回一格额度：不补，下一跳就判预算耗尽，
    # 用户已经看到的正文会被 tool_budget_finalize 的兜底句替换（见 _pick_turn_slot_patch）。
    slot_patch = _pick_turn_slot_patch(state, resp)
    return {"messages": [resp], **slot_patch}

# 节点2：工具执行节点（自动调用 web_search / get_file）产出 ToolMessage
tool_executor = ToolNode(tools)#直接执行不思考


def _latest_tool_call_count(messages) -> int:
    """取最近一条 AI 工具调用消息里的 tool_call 数量。"""
    for m in reversed(messages or []):
        if isinstance(m, AIMessage):
            return len(getattr(m, "tool_calls", None) or [])
    return 0


def _turn_tool_budget(messages) -> int:
    """本轮「并发宽度」上限：常规 4 个；「已选定一道菜」的轮次收紧到 2 个。

    为什么单独收紧：用户选完菜顺带问一句健康问题（「2吧，可以更酸一点，但是我的父亲
    高血压，可以吃这个吗」）时，模型倾向反复联网检索，把预算搜爆后走收口逻辑，
    卡片和图片一起消失。判定锚点必须确定性 —— 复用 `_is_dish_pick_turn`
    （序号选定 / 正文点名候选或最近卡片里的菜），不依赖模型自述。

    语义边界（重要）：这里限的是「一轮里同时放行几个」，**不是**「模型一次能提几个」。
    模型并排提 5 个也只放行前 2 个，而不是整批拒绝 —— 整批拒绝会把正常并发误杀成 0 执行。
    """
    if _is_dish_pick_turn(messages):
        return PICK_TURN_TOOL_BUDGET
    return MAX_TOOL_CALLS_PER_TURN


def _remaining_tool_slots(state: "ChefState") -> int:
    """本轮还剩几个工具槽位（余额）；≤0 表示这一轮的工具额度已用完。"""
    used = int(state.get("tool_calls_in_turn", 0) or 0)
    budget = int(state.get("tool_budget", 0) or 0) or MAX_TOOL_CALLS_PER_TURN
    return max(0, budget - used)


def _retry_tool_slot_patch(state: "ChefState") -> dict:
    """重试回炉时补回「至少一次取证」的额度，避免重试必然落兜底句。

    为什么必须补：verify 判 retry 说明方案确实踩了硬禁忌，必须重新取证再生成；
    但选定轮的并发宽度只有 2（`PICK_TURN_TOOL_BUDGET`），模型出答案时早就用光，
    余额 0 时 chef_think 再要工具会被 `chef_route_with_tool_budget` 判成
    `tool_budget_exhausted`，直接落 `tool_budget_finalize`，把用户已经看到的
    好答案替换成兜底句（实测「2吧…我的父亲高血压，可以吃这个吗」）。

    边界（只补「一次」，不是放开采纳）：
      · 并发宽度每次重试只回退 1 格 → 重试后余额恰好为 1；
      · 圈数上限最多回退到 `MAX_TOOL_ROUNDS - 1` → 只再给一圈，不会回到无限循环；
      · **每轮只补一次**（`retry_slot_granted`）→ 同一轮的第 2、3 次重试不再各开
        一圈新工具：否则 `MAX_VERIFY=3` 会在并发宽度之外再叠最多 3 圈工具 + 3 次
        chef_think（实测慢轮每轮 8~10 次生成、平均 5.73 次调用）。
        这跟 `_pick_turn_slot_patch` 的「每轮只补一次」是同一口径，两处不再一个放一个收。
      · 外层还有 `MAX_VERIFY` 次重试封顶，补额次数不再随重试次数放大。
    仅在「余额已空」或「圈数已封顶」时打补丁，余额还有剩余时维持原状。

    补额机会只在「真的补到了」时才消耗：余额还有剩的重试原样放过，不占这次机会。
    """
    if state.get("retry_slot_granted"):
        return {}
    patch = {}
    if _remaining_tool_slots(state) <= 0:
        used = int(state.get("tool_calls_in_turn", 0) or 0)
        patch["tool_calls_in_turn"] = max(0, used - 1)
    if int(state.get("tool_rounds", 0) or 0) >= MAX_TOOL_ROUNDS:
        patch["tool_rounds"] = MAX_TOOL_ROUNDS - 1
    if patch:
        patch["retry_slot_granted"] = True
    return patch


def _pick_turn_slot_patch(state: "ChefState", response) -> dict:
    """选定轮第二圈仍要取证时，补回一格额度（本轮只补一次）。

    为什么必须补：选定轮的并发宽度收紧到 `PICK_TURN_TOOL_BUDGET=2`，模型第一圈常并排
    搜 2 个（做法 + 营养依据）就用光；第二圈它还要继续取证时，`chef_route_with_tool_budget`
    直接判 `tool_budget_exhausted` → `tool_budget_finalize` 把用户已经看到的正文换成兜底句。
    实测（2026-10-11，session user_5294894983，「3」选定「鸡丝黄瓜凉拌面」）：
    used=2 / budget=2 / rounds=1，第二圈再要工具 → 落库正文变成「这次没查到足够具体的做法…」，
    卡片照出、讲解却是兜底句。补 1 格后模型能取完证再自己写完整讲解。

    边界（与 `_retry_tool_slot_patch` 同口径，只补「一次」，不是放开采纳）：
      · 每次只回退 1 格 → 补完余额恰好为 1，`run_tools_node` 按余额截断只执行 1 个；
      · 每轮只补一次（`pick_turn_slot_granted`）→ 补的那圈用掉后计数回到封顶值，路由照旧收口；
      · 仍受 `MAX_TOOL_ROUNDS` 封顶；非选定轮（并发宽度本来就是 4）不补。
    """
    if state.get("pick_turn_slot_granted"):
        return {}
    if not getattr(response, "tool_calls", None):
        return {}
    if not _is_dish_pick_turn(state.get("messages") or []):
        return {}
    if int(state.get("tool_rounds", 0) or 0) >= MAX_TOOL_ROUNDS:
        return {}
    if _remaining_tool_slots(state) > 0:
        return {}
    used = int(state.get("tool_calls_in_turn", 0) or 0)
    return {
        "tool_calls_in_turn": max(0, used - 1),
        "pick_turn_slot_granted": True,
    }


def chef_route_with_tool_budget(state: "ChefState") -> str:
    """chef_think 后的路由：还有余额、且圈数没超，就去执行工具；否则优雅收尾。

    与旧实现的区别：**不再用 `pending`（模型打算调几个）去比预算**。
    旧写法 `used + max(pending, 1) > budget` 会让「模型一次并排提 3 个」被整批判死，
    一个工具都不执行、收口时拿不到任何证据。现在路由只判断「还有没有余额」，
    超出余额的部分交给 `run_tools_node` 按余额**截断**执行。
    """
    route = tools_condition(state)
    if route != "tools":
        return "verify"
    if int(state.get("tool_rounds", 0) or 0) >= MAX_TOOL_ROUNDS:
        return "tool_budget_exhausted"
    if _remaining_tool_slots(state) <= 0:
        return "tool_budget_exhausted"
    return "tools"


def _trim_last_tool_calls(messages, allowed: int):
    """把最后一条 AI 消息的 tool_call 截断到前 `allowed` 个。

    返回 `(写回 state 的补丁, 交给工具节点执行的消息列表)`。
    必须真把多余的 tool_call 删掉：OpenAI 兼容端点要求每个 tool_call 都有对应 ToolMessage，
    留下了却没人执行 → 下一次 LLM 调用直接 400。
    """
    if not messages:
        return [], messages
    last = messages[-1]
    calls = list(getattr(last, "tool_calls", None) or [])
    if not calls or allowed >= len(calls):
        return [], messages
    if not getattr(last, "id", None):
        # state 里的消息一定带 id（LangGraph 的 add_messages 会补），这里只是兜底：
        # 没 id 就删不掉原消息，与其留下「有 tool_call 却无 ToolMessage」的坏序列（下次调用 400），
        # 不如不截断 —— 宁可超一点余额，也不能把消息序列弄坏。
        return [], messages
    extra = dict(getattr(last, "additional_kwargs", None) or {})
    # 旧格式（function_call / additional_kwargs.tool_calls）里可能还留着完整调用，
    # 不清掉会跟截断后的 tool_calls 冲突，等于没截。
    extra.pop("tool_calls", None)
    extra.pop("function_call", None)
    trimmed = AIMessage(
        content=last.content,
        tool_calls=calls[:allowed],
        additional_kwargs=extra,
    )
    patches = []
    if getattr(last, "id", None):
        patches.append(RemoveMessage(id=last.id))
    patches.append(trimmed)
    return patches, list(messages[:-1]) + [trimmed]


@trace_node("run_tools")
def run_tools_node(state: "ChefState"):
    """按剩余余额截断执行工具，并累计本轮已执行数（并发）与循环轮数。"""
    messages = list(state.get("messages") or [])
    pending = _latest_tool_call_count(messages)
    allowed = max(1, min(pending, _remaining_tool_slots(state))) if pending else 1
    patches, exec_messages = _trim_last_tool_calls(messages, allowed)
    result = tool_executor.invoke({**state, "messages": exec_messages})
    # 以「真正交给执行器的消息」为准记账，截断兜底未生效时也不会记错
    executed = max(_latest_tool_call_count(exec_messages), 1)
    tool_messages = list((result or {}).get("messages") or [])
    # 结果体积可观测：省 token 的真杠杆在这里，不打印就看不见收益（异常静默，不阻塞主链路）
    try:
        report = _tool_result_budget_report(tool_messages)
        if report["tools"]:
            print(
                f"[agent-metrics] tool_result_chars before={report['before']} "
                f"after={report['after']} saved={report['saved']} "
                f"({report['saved_pct']}%) tools={report['tools']}"
            )
    except Exception:
        pass
    return {
        **(result or {}),
        # 补丁（先删原 AI 消息、再追加截断版）必须排在工具结果之前，
        # 否则截断版会落到 ToolMessage 后面，下次请求的消息顺序就错了。
        "messages": patches + tool_messages,
        "tool_calls_in_turn": int(state.get("tool_calls_in_turn", 0) or 0) + executed,
        "tool_rounds": int(state.get("tool_rounds", 0) or 0) + 1,
    }


@trace_node("tool_budget_finalize")
def tool_budget_finalize_node(state: "ChefState"):
    """工具预算耗尽时，删除未执行 tool_call，给用户一个基于已有信息的普通结论。"""
    messages = state.get("messages", [])
    removals = []
    for m in reversed(messages):
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            if getattr(m, "id", None):
                removals.append(RemoveMessage(id=m.id))
            break
    user_text = _current_request_text(_latest_user_text(messages))
    # 这段文案会直接成为用户看到的正文，所以只写面向用户的结论：
    # 不出现检索次数 / 预算 / 空转等内部流程描述（用户只关心能不能做、做什么）。
    fallback = (
        "这次没查到足够具体的做法，我先给你一个稳妥的方向："
        "优先少油少盐、食材明确、做法简单的一道；"
        "如果有高血压、糖尿病、痛风或肠胃不适，避开油炸、重辣、冷饮和高糖饮料。"
    )
    if user_text:
        fallback += (
            f"\n\n你提到的「{user_text[:60]}」就按这个方向来；"
            "也可以直接告诉我想吃哪道菜，我给你完整做法。"
        )

    # 预算耗尽不是错误：做一次不带工具的收口生成。若上游仍失败，
    # 或生成内容命中健康硬禁忌，则退回确定性的安全文案，绝不让本轮落库为空。
    # 触发条件要收准：实测选定轮里模型一次吐出多个 tool_call 会被整批判成预算耗尽、
    # 一个都不执行，此时 evidence 为空、直接把兜底句当正文 —— 用户既看不到菜，
    # 又被迫读了一段系统自述。所以「被拦下的工具调用」这一种情况也必须补一次生成。
    # 反过来，纯文本轮次本来就没有工具意图，保持确定性文案：不做无谓调用，也不在没证据时自由发挥。
    content = fallback
    evidence = _tool_result_evidence(messages)
    blocked_tools = _latest_tool_call_count(messages) > 0
    if evidence or blocked_tools:
        # 本轮是不是「泛推荐」阶段：收口正文的形态与审计口径都要跟着走，
        # 否则候选轮会被塞进一道单菜，或用整篇 prose 去过菜名级审计。
        try:
            is_candidate_turn = _is_candidate_turn(
                messages,
                ingredient_gate_ready=bool(state.get("ingredient_gate_ready")),
            )
        except Exception:
            is_candidate_turn = False
        # 本轮是不是「纯健康/安全咨询」：这类轮次要的是解释和替代方案，
        # 逼模型「收口一道菜」会把用户的问题替换成一道无关菜谱（实测 D1）。
        try:
            is_information_turn = (
                not is_candidate_turn
                and is_non_recipe_information_request(user_text)
                and not _is_dish_pick_turn(messages)
            )
        except Exception:
            is_information_turn = False
        _ensure_llms()
        if evidence:
            if is_candidate_turn:
                system_prompt = (
                    "你是小膳管家。本轮是「泛推荐」：用户没有点名具体哪一道菜。"
                    "请只根据用户需求和已经检索到的资料，直接给出编号候选清单，"
                    "不要描述检索次数、预算或任何内部流程。"
                    "格式必须是每行一条、编号从 1 开始、菜名后接「 —— 」再写一句约 30 字的推荐理由；"
                    "只输出候选菜名清单，不要写做法、步骤、调料、火候，不要输出 JSON 或卡片。"
                    "候选菜名必须避开家庭成员的忌口与过敏原（含隐含来源），资料不足时少给几道，不要编造。"
                )
            elif is_information_turn:
                system_prompt = (
                    "你是小膳管家。本轮用户问的是「能不能吃/能不能放」这类饮食安全咨询，"
                    "不是点菜。请只根据用户需求和已经检索到的资料，直接给出面向用户的中文解答："
                    "先给明确结论，再给可执行的替代方案或注意事项；"
                    "不要输出 JSON、不要输出菜谱卡片、不要描述内部流程。"
                    "家庭成员的过敏原是硬约束，绝不能建议加入；资料不足时说明不确定，不要编造。"
                )
            else:
                system_prompt = (
                    "你是小膳管家。请只根据用户需求和已经检索到的资料，直接给出最终中文建议。"
                    "不要再索取或调用工具，不要描述内部流程；优先用一道可执行、少油少盐的菜收口。"
                    "资料不足时明确说明不确定，不要编造。"
                )
            evidence_block = f"已检索到的工具资料：\n{evidence}"
        else:
            if is_information_turn:
                system_prompt = (
                    "你是小膳管家。本轮用户问的是饮食安全咨询，但本轮没有检索到可用资料。"
                    "请只根据用户需求直接给出面向用户的中文解答：先给明确结论，再给替代方案；"
                    "不要输出 JSON、不要输出菜谱卡片、不要描述内部流程。"
                    "家庭成员的过敏原是硬约束，绝不能建议加入；没有把握的部分要说明不确定，不要编造。"
                )
            else:
                system_prompt = (
                    "你是小膳管家。本轮没有检索到可用资料，请只根据用户需求直接给出最终中文建议。"
                    "不要再索取或调用工具，不要描述检索次数、预算或任何内部流程；"
                    "优先给出一道家常、可执行、少油少盐的菜，写清主要食材与关键步骤。"
                    "没有把握的营养数据或功效就说明不确定，不要编造。"
                )
            evidence_block = "（本轮没有检索到可用资料，请只依据用户需求给出稳妥做法）"
        try:
            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=(
                    f"用户需求：{user_text or '继续完成本轮建议'}\n\n{evidence_block}"
                )),
            ])
            candidate = response.content
            if isinstance(candidate, list):
                candidate = "".join(
                    str(block.get("text", ""))
                    for block in candidate
                    if isinstance(block, dict)
                )
            candidate = str(candidate or "").strip()
            # 审计口径与 verify_answer_node 完全一致：候选轮只看候选菜名，单菜轮看正文；
            # 过敏原审计沿用「先剥掉安全说明句」的做法。
            # 不复用整篇 prose 的直接原因（实测 user_b60716de29）：模型在收口正文里写
            # 「避免：浓肉汤/动物内脏/咸菜…」这类提醒句，会被菜名级规则当成真实下料，
            # 好答案因此被丢弃、用户只看到「这次没查到足够具体的做法」的系统自述。
            candidate_names = (
                _extract_candidate_names(candidate) if is_candidate_turn else []
            )
            plain_refusal = _is_plain_safety_refusal_turn(
                messages, candidate, is_candidate_turn=is_candidate_turn
            )
            audit_text, allergen_audit_text = _audit_texts_for_answer(
                candidate,
                candidate_names=candidate_names,
                plain_safety_refusal=plain_refusal,
                allergens=_allergens_for_audit(state.get("session_id")),
            )
            violations = audit(audit_text, _merged_conditions(user_text))
            try:
                violations = violations + audit_allergens(
                    allergen_audit_text,
                    _allergens_for_audit(state.get("session_id")),
                    use_optional=True,
                )
                violations = violations + _allergen_permission_violations(
                    candidate,
                    _allergens_for_audit(state.get("session_id")),
                    plain_safety_refusal=plain_refusal,
                )
            except Exception:
                pass
            if candidate and not violations and not _contains_internal_marker(candidate):
                content = candidate
        except Exception:
            pass
    return {
        "messages": removals + [AIMessage(content=content)],
        "tool_budget_exhausted": True,
        "verify_status": "ok",
    }


def _tool_result_evidence(messages, max_items=3, max_chars=1800) -> str:
    """把最近工具结果压成供收口模型使用的短证据，避免把原始长 JSON 再灌满上下文。"""
    blocks = []
    for message in reversed(messages or []):
        if not isinstance(message, ToolMessage):
            continue
        raw = _compact_tool_result(message)
        if not raw:
            continue
        name = str(getattr(message, "name", "") or "tool")
        blocks.append(f"[{name}] {raw}")
        if len(blocks) >= max_items:
            break
    return "\n\n".join(reversed(blocks))[:max_chars]


# --------------------------------------------------------------------------- #
#  工具结果压缩（「三旋钮」的第三只：结果体积）
#
#  省 token 的真杠杆在这里 —— 一次 tool_call 的 JSON 只有几十~一两百 token，
#  但一条工具**结果**动辄 3–10KB ≈ 1k–3k token，而且会随每一轮历史重复投喂。
#  所以工具返回后先按「白名单字段 + 分级截断」压一遍，再进上下文。
#
#  为什么是白名单而不是黑名单：工具结果里的噪声字段会随着工具升级不断出现，
#  黑名单永远追不上；白名单只留模型真正需要的字段，升级时也不会悄悄把上下文撑爆。
#  压缩必须**保留 JSON 结构**（下游结构化链按 key 取值），所以压的是字段长度，不是格式。
# --------------------------------------------------------------------------- #

# 各工具的「结果预算」（字符）：按信息密度分级，不搞一刀切。
#   web_search：做法/步骤类信息密度中等，单条结果留长一点
#   nutrition_kb_search：要用来源与片段做引用，保留正文但砍掉重复字段
#   nearby_food / 数值类：结构化字段本就短，只需砍列表长度
_MAX_RESULT_CHARS_DEFAULT = 2400
_TOOL_RESULT_LIMITS = {
    "web_search": 3600,
    "nutrition_kb_search": 3600,
    "nearby_food": 1800,
    "get_file": 2400,
    "calorie_lookup": 1200,
    "exercise_equiv": 1200,
}
_MAX_RESULT_ITEMS = 5  # 搜索结果 / 检索命中最多保留几条


def _clip(text, limit: int) -> str:
    """按字符截断并显式标注被截断，避免模型误以为内容完整。"""
    text = str(text or "")
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + "…（已截断）"


def _compact_tool_result(message, tool_name: str = "") -> str:
    """把一条 ToolMessage 的内容压成"结构不变、长度受控"的字符串。

    解析失败（旧格式纯文本 / 非 JSON）时退化为整体截断，绝不丢内容形态。
    """
    name = tool_name or str(getattr(message, "name", "") or "")
    limit = int(_TOOL_RESULT_LIMITS.get(name, _MAX_RESULT_CHARS_DEFAULT))
    raw = str(getattr(message, "content", "") or "").strip()
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return _clip(re.sub(r"\s+", " ", raw).strip(), limit)
    if not isinstance(parsed, dict):
        return _clip(re.sub(r"\s+", " ", raw).strip(), limit)

    out = dict(parsed)
    # web_search / generic：text 是主体，按总量截断即可（image_url 等短字段原样保留）
    if isinstance(out.get("text"), str):
        out["text"] = _clip(out["text"], limit)
    # nutrition_kb_search：hits 列表里 excerpt 与 text 高度重复，同时存在时只留 text。
    # 但真实工具只返回 excerpt（没有 text），此时必须保留 excerpt——否则命中片段
    # 在进结构化链前就被丢掉，sources.snippet 无原文可取，只能靠模型编。
    hits = out.get("hits")
    if isinstance(hits, list):
        trimmed = []
        for hit in hits[:_MAX_RESULT_ITEMS]:
            if not isinstance(hit, dict):
                continue
            item = dict(hit)
            if isinstance(item.get("text"), str):
                item.pop("excerpt", None)  # 与 text 重复，纯冗余
                item["text"] = _clip(item["text"], 800)
            elif isinstance(item.get("excerpt"), str):
                item["excerpt"] = _clip(item["excerpt"], 800)
            trimmed.append(item)
        out["hits"] = trimmed
    # 列表型结果（附近餐厅等）：控条数
    for key in ("results", "items", "candidates"):
        value = out.get(key)
        if isinstance(value, list) and len(value) > _MAX_RESULT_ITEMS:
            out[key] = value[:_MAX_RESULT_ITEMS]
    compacted = json.dumps(out, ensure_ascii=False)
    # 字段级截断后仍超限（例如 text 很小但 hits 很多）时也要**保持 JSON 合法**：
    # 整体按字符剁会把 JSON 切成残片，下游结构化链按 key 取值直接崩。
    # 正确做法是递归收紧长字段，而不是腰斩字符串。
    if len(compacted) > limit:
        out = _shrink_payload(out, limit)
        compacted = json.dumps(out, ensure_ascii=False)
        if len(compacted) > limit:
            # 实在收不下来（字段极多）时，至少保证仍是合法 JSON
            compacted = json.dumps(_shrink_payload(out, max(240, limit // 2)), ensure_ascii=False)
    return compacted


def _shrink_payload(value, limit: int, _depth: int = 0):
    """递归收紧 payload 里的长字符串字段，保持 JSON 结构合法（绝不腰斩整个 JSON）。"""
    if _depth > 6:
        return value
    if isinstance(value, str):
        # 单字段预算按剩余空间给，留出转义与其它字段的余量
        return _clip(value, max(120, min(limit, 800)))
    if isinstance(value, list):
        head = [_shrink_payload(item, limit, _depth + 1) for item in value[:_MAX_RESULT_ITEMS]]
        return head
    if isinstance(value, dict):
        return {key: _shrink_payload(item, limit, _depth + 1) for key, item in value.items()}
    return value


def _tool_result_budget_report(messages) -> dict:
    """统计本轮工具结果的原始体积与压缩后体积，供 [agent-metrics] 打印。

    没有计量就看不见收益：这条日志是判断"结果侧压缩有没有生效、省了多少"的唯一依据。
    """
    before = 0
    after = 0
    count = 0
    for message in messages or []:
        if not isinstance(message, ToolMessage):
            continue
        raw = str(getattr(message, "content", "") or "")
        if not raw:
            continue
        count += 1
        before += len(raw)
        after += len(_compact_tool_result(message))
    saved = before - after
    return {
        "tools": count,
        "before": before,
        "after": after,
        "saved": saved,
        "saved_pct": round(saved * 100.0 / before, 1) if before else 0.0,
    }

# --------------------------------------------------------------------------- #
# 从整个对话消息列表里，提取、拼接给结构化 LLM 使用的 Prompt 上下文
# 同时抓取本次搜索拿到的图片链接 + 是否为 AI 生成图两个标记
# 返回三个值：组装好的文本上下文、真实图片URL、是否AI生成图片布尔值
# --------------------------------------------------------------------------- #
def _build_structure_context(messages, isolate_old_context=False):#解析出了图片链接和来源和文本是进入LCEL之前的准备工作
    """给结构化链组装上下文：最近一条真实用户需求 + 本轮 web_search 的搜索结果。
    返回 (context_text, real_image_url, real_image_ai)：
      - real_image_url：最近一次搜索真实拿到的图片链接（可能为 None），
        作为"有没有图"的代码级依据，不信模型口头说法；
      - real_image_ai：该图是否由通义万相 AI 生成（image_source=="ai"），
        用于下游透明标注「AI 生成示意图」，防止把生成图当用户实拍图误导。
    注意：real_image_url 与 real_image_ai 始终成对赋值，保证"有图"与"是否 AI 图"口径一致。"""
    context_messages = _messages_for_current_turn(
        messages,
        isolate_old_context=isolate_old_context,
    )
    parts = []#存储上下文的纯文本
    real_image_url = None#存储真实图片链接
    real_image_ai = False#存储真实图片是否由 AI 生成
    # 最近一条真实用户消息（跳过压缩节点注入的"历史对话摘要"，图文混合消息只取文字）
    for m in reversed(context_messages):#是用户文档并且不是历史对话摘要的消息
        if isinstance(m, HumanMessage) and not str(m.content).startswith("[历史对话摘要") and not str(m.content).startswith("[健康护栏审核"):
            if isinstance(m.content, list):#图文混合：图片已识别进对话，只取文字部分
                text = " ".join(#用户发图 + 文字提问时，只保留文字需求，图片不塞进给 LLM 的文本上下文
                    part.get("text", "") for part in m.content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            else:
                text = str(m.content)
            parts.append("用户需求：" + text)
            break
    # 把本轮 Agent 已经确认的自然语言回答交给结构化模型。
    # 这样卡片使用同一轮已经识别出的菜名，不会根据旧搜索结果重新猜一道菜。
    for m in reversed(context_messages):
        if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
            ai_text = str(m.content).strip()
            if ai_text and not ai_text.startswith('{"opening"'):
                _excerpt = ai_text[:800] + ("…（正文已截断）" if len(ai_text) > 800 else "")
                parts.append("本轮 Agent 已确认的回答（菜名和食材以此为准）：\n" + _excerpt)
            break
    # 本轮所有 web_search 工具返回（JSON 字符串：text 搜索结果 + image_url 图片 + image_source 图源）
    search_blocks = []#2.有连坐删除，删的时候会把工具的返回结果也会删除不搞混
    for m in context_messages:#把工具返回的搜索结果都塞进parts不搞混，只取最近2条控制结构化上下文长度
        if isinstance(m, ToolMessage) and getattr(m, "name", "") == "web_search":
            content = _compact_tool_result(m)#统一走结果压缩：白名单字段 + 分级截断
            search_blocks.append(content)#存储搜索结果
            try:#新格式 {text, image_url, image_source}；旧格式是纯文本，json 解析失败就跳过
                parsed = json.loads(str(m.content))#将存的有用的变为字典（压缩只改长度、不改字段，这里仍能解析）
                img = parsed.get("image_url")#拿地址
                src = parsed.get("image_source")#拿来源
                # 成对赋值：有图才更新图源标记，保证"图"与"是否 AI 图"一致
                if img:
                    real_image_url = img#后出现的覆盖前面的，留下最近一次
                    real_image_ai = (src == "ai")#仅当该次返回明确标记为 AI 生成才置 True自动的
            except (ValueError, AttributeError):
                pass
    if search_blocks:
        parts.append(
            "搜索结果（每条是 JSON：text 为搜索文本、image_url 为成品图链接或 null、"
            "image_source 为图源 real/ai）：\n"
            + "\n\n".join(search_blocks[-2:])#最多取最近2次搜索（单条已按结果预算压缩）
        )
    # 本轮所有 nutrition_kb_search 工具返回（权威健康依据，JSON 含 source 文件名与命中片段 excerpt）
    kb_blocks = []
    for m in context_messages:
        if isinstance(m, ToolMessage) and getattr(m, "name", "") == "nutrition_kb_search":
            kb_blocks.append(_compact_tool_result(m))#同样走结果压缩（原先完全不截，是最容易撑爆上下文的一路）
    if kb_blocks:
        parts.append(
            "权威健康依据检索结果（来自 nutrition_kb_search，每条 JSON 含 source 文件名与命中片段 excerpt）：\n"
            + "\n\n".join(kb_blocks[-3:])
        )
    return "\n\n".join(parts), real_image_url, real_image_ai


# 明显面向特定人群的 KB 来源不能给错人群引用。这里只做保守白名单：
# 来源命中某个人群标记时，当前用户文本或激活成员画像必须出现同类标记才放行。
_AUDIENCE_SOURCE_RULES = (
    (
        "infant_toddler",
        ("婴幼儿", "婴儿", "幼儿", "托育"),
        ("婴幼儿", "婴儿", "幼儿", "宝宝", "托育"),
    ),
    (
        "school_age",
        ("学龄儿童", "中小学生", "学生餐"),
        ("学龄儿童", "中小学生", "学生餐", "小学生", "中学生"),
    ),
    (
        "pregnancy",
        ("备孕", "孕期", "孕妇", "妊娠", "怀孕", "哺乳期", "哺乳", "产妇", "产后"),
        ("备孕", "孕期", "孕妇", "妊娠", "怀孕", "哺乳期", "哺乳", "产妇", "产后"),
    ),
    (
        "elderly",
        ("老年", "老人"),
        ("老年", "老人", "老年人", "长者"),
    ),
)


def _active_profile_audience_text() -> str:
    """汇总激活成员画像中可证明目标人群的文本；其他成员不参与。"""
    try:
        raw = _load_profile()
        members = raw.get("members")
        if isinstance(members, list) and members:
            active_id = raw.get("active_id")
            member = next(
                (
                    item for item in members
                    if isinstance(item, dict) and item.get("id") == active_id
                ),
                None,
            )
            if member is None:
                member = next(
                    (item for item in members if isinstance(item, dict)),
                    {},
                )
            profile = member.get("profile") or {}
        else:
            profile = raw
        if not isinstance(profile, dict):
            return ""
        parts = [json.dumps(profile, ensure_ascii=False)]
        basic = profile.get("basic")
        if isinstance(basic, dict):
            try:
                age = float(basic.get("age") or 0)
            except (TypeError, ValueError):
                age = 0
            if 0 < age <= 3:
                parts.append("婴幼儿")
            elif 6 <= age <= 12:
                parts.append("学龄儿童")
            elif age >= 65:
                parts.append("老年人")
        return " ".join(parts)
    except Exception:
        return ""


def _source_audience_text(messages) -> str:
    return " ".join(
        part for part in (_latest_user_text(messages), _active_profile_audience_text())
        if part
    )


def _source_matches_audience(source, section, audience_text) -> bool:
    """来源若带明确人群标记，必须有同人群上下文，否则不引用。"""
    source_text = f"{source or ''} {section or ''}".lower()
    audience = str(audience_text or "").lower()
    for _, source_keywords, audience_keywords in _AUDIENCE_SOURCE_RULES:
        if not any(keyword.lower() in source_text for keyword in source_keywords):
            continue
        if not any(keyword.lower() in audience for keyword in audience_keywords):
            return False
    return True


def _filter_source_refs(sources, audience_text="") -> list:
    """过滤模型自填来源；不强求逐条映射，但不能引用明显错人群的文件。"""
    kept = []
    for ref in sources or []:
        if isinstance(ref, dict):
            source = ref.get("source") or ""
            section = ref.get("section") or ""
        else:
            source = getattr(ref, "source", "") or ""
            section = getattr(ref, "section", "") or ""
        if _source_matches_audience(source, section, audience_text):
            kept.append(ref)
    return kept


def _kb_source_refs(
    messages,
    *,
    isolate_old_context=False,
    limit: int = 3,
    audience_text: str | None = None,
) -> list[dict]:
    """从本轮 nutrition_kb_search 命中里抽出可引用的（文件名, 章节）。

    用于模型漏填 `sources` 时的确定性兜底：只回填来源文件与章节，不回填命中
    片段——语义是「本轮检索到这些权威依据」，不声称每条健康结论与该片段逐一
    对应，避免过度声称。取不到命中的那一轮返回空列表，不凭空造依据。
    """
    refs: list[dict] = []
    seen = set()
    if audience_text is None:
        audience_text = _source_audience_text(messages)
    window = _messages_for_current_turn(messages, isolate_old_context=isolate_old_context)
    for m in window:
        if not (isinstance(m, ToolMessage) and getattr(m, "name", "") == "nutrition_kb_search"):
            continue
        try:
            parsed = json.loads(str(m.content))
        except (ValueError, TypeError):
            continue
        if not isinstance(parsed, dict) or not parsed.get("found"):
            continue
        for hit in parsed.get("hits") or []:
            if not isinstance(hit, dict):
                continue
            source = str(hit.get("source") or "").strip()
            if not source:
                continue
            section = str(hit.get("section") or "").strip()
            if not _source_matches_audience(source, section, audience_text):
                continue
            key = (source, section)
            if key in seen:
                continue
            seen.add(key)
            refs.append({"source": source, "section": section})
            if len(refs) >= limit:
                return refs
    return refs


def _kb_source_payload(messages) -> list[dict]:
    """拒绝/阻断轮的 sources 回填：与 _kb_source_refs 同口径（只给文件+章节）。"""
    return [
        SourceRef(**ref).model_dump()
        for ref in _kb_source_refs(
            messages,
            isolate_old_context=_latest_new_image_index(messages) is not None,
        )
    ]


def _is_dining_scene(text: str) -> bool:
    """T2-P3：外出就餐场景 = 不输出烹饪步骤。关键词命中即视为该场景。"""
    import re as _re
    return bool(_re.search(r"食堂|外卖|外吃|外出就餐|点餐|吃饭|省钱吃|怎么吃|餐厅|档口|套餐|就餐", text))


def _is_non_food_followup(text: str) -> bool:
    """识别“锂电池怎么没有用上”这类非食材追问，避免再附一张菜谱卡片。"""
    raw = str(text or "").strip()
    if not raw or _mentions_food_ingredients(raw) or _looks_like_dining_request(raw):
        return False
    return bool(re.search(
        r"(?:怎么|为什么|咋)?(?:没有|没|未).{0,4}(?:用上|用|算上|放进去|考虑)",
        raw,
    ))


def _latest_user_text(messages, *, strip_internal=True):
    """提取本轮用户的文字需求，图文消息只取文字部分。"""
    for m in reversed(messages):
        if (
            isinstance(m, HumanMessage)
            and not str(m.content).startswith("[历史对话摘要")
            and not str(m.content).startswith("[健康护栏审核")
        ):
            text = _message_text(m)
            return _current_request_text(text) if strip_internal else text
    return ""


def _should_force_web_search(text: str) -> bool:
    """把明显需要联网查网页/做法/最新信息的请求，提前转成 web_search 工具调用。"""
    text = str(text or "").strip()
    if not text:
        return False
    if _is_dining_scene(text):
        return False
    if any(word in text for word in ("高血压", "糖尿病", "血糖", "尿酸", "痛风", "减脂", "控糖", "低嘌呤", "能不能吃", "营养", "热量", "钠")):
        return False
    force_words = (
        "联网", "网页", "网页搜索", "搜索一下", "查一下", "查查", "最新", "最新的",
        "做法", "菜谱", "怎么做", "家常菜", "快手菜", "食材搭配", "配方", "推荐做法",
        "网上", "上网", "外网", "百度", "Tavily",
    )
    return any(word in text for word in force_words)


def _wants_multiple_recipes(messages):
    """只有用户明确要求多个选择时才开启多菜模式。"""
    text = _latest_user_text(messages)
    multiple_words = (
        "多几道", "多道菜", "几道菜", "多个选择", "多种选择",
        "供我选择", "分别推荐", "多推荐几道", "多生成几道",
    )
    return any(word in text for word in multiple_words)


def _search_recipe_image(recipe_name):
    """按最终菜名搜索图片，返回 (url, 是否AI生成)。"""
    try:
        image_url, image_source = find_recipe_image(recipe_name, allow_ai_fallback=False)
        return image_url, image_source == "ai"
    except Exception:
        return None, False


def _matrix_cell(row, key: str) -> str:
    """从 DishMatrixItem（pydantic）或 dict 里取字段，便于单测直接喂 dict。"""
    if isinstance(row, dict):
        return str(row.get(key) or "")
    return str(getattr(row, key, "") or "")


def _family_conflict_guardrails(dish_matrix) -> list:
    """把分餐矩阵的冲突抬进右栏护栏，保证「矩阵说什么、护栏就显示什么」。

    这是安全表达问题而不只是 UI 问题：矩阵已判定某成员不可吃，
    右栏却显示「未触发额外健康约束」，等于当着用户的面自相矛盾。
    """
    from domain.allergen_rules import UNRESOLVED_ALLERGEN_ADVICE

    items = []
    for row in dish_matrix or []:
        verdict = _matrix_cell(row, "verdict")
        member = _matrix_cell(row, "member") or "成员"
        dish = _matrix_cell(row, "dish") or "本轮主菜"
        reason = _matrix_cell(row, "reason")
        if verdict == "不可吃":
            items.append(GuardrailItem(
                condition=f"同餐冲突:{member}",
                rule=f"{member}不可吃「{dish}」，必须单独替换并避免交叉接触",
                status="member_conflict",
                reason=reason or f"{member}的硬约束与本轮主菜冲突；需单独替换并避免交叉接触",
            ))
        elif verdict == "待确认":
            items.append(GuardrailItem(
                condition=f"过敏原待确认:{member}",
                rule=f"{member}档案里的过敏原未纳入标准规则，仅按原文提醒",
                status="warn",
                reason=reason or UNRESOLVED_ALLERGEN_ADVICE,
            ))
    return items


def _build_guardrails(
    user_text,
    verify_status,
    verify_violated,
    dish_matrix=None,
    session_id: str | None = None,
):
    """依据 verify 节点真实审计结论，为前端右栏拼出『本轮健康护栏』列表（健康链可见化的核心）。

    - 对每个检测到的健康标签，给出 pass / warn / adjusted 结论与一句理由；
    - 不依赖 LLM，以 verify 的确定性审计口径为准，避免关键字子串误判
      （如『少盐』被『盐』误命中导致合规方案被误标为已调整）；
    - 与 verify_answer_node 共用同一套 RULES，口径一致。
    """
    conditions = _merged_conditions(user_text)
    audit_allergens_for_turn = _allergens_for_audit(session_id)
    # 结构化收尾直接合并会话内待确认约束，避免 state 未带 session_id
    # 时出现“文字已遵守、卡片护栏却漏项”的证据断裂。
    if session_id:
        try:
            from storage.memory_candidates import get_session_allergens

            audit_allergens_for_turn.extend(get_session_allergens(session_id))
        except Exception:
            pass
    for allergen in dict.fromkeys(audit_allergens_for_turn):
        condition = f"过敏原:{allergen_label(allergen)}"
        if condition not in conditions:
            conditions.append(condition)
    # blocked 场景兜底：本轮实际命中的过敏原也并进来。
    # 理由——档案可能在审计后变更，只靠 _allergens_for_audit() 会漏，
    # 而"拦截了却不在右栏显示"等于把护栏证据弄丢。
    for violation in verify_violated or []:
        text = str(violation)
        if text.startswith("过敏原:") and text not in conditions:
            conditions.append(text)
    violated = set(verify_violated or [])
    items = []
    for cond in conditions:
        rule_msg = RULES.get(cond, {}).get("message", "")
        if not rule_msg and str(cond).startswith("过敏原:"):
            rule_msg = f"必须完全不含{str(cond).split(':', 1)[-1]}，包括调料与隐含来源"
        # 分支顺序不可调换：blocked 必须先判，否则会落进 adjusted 分支，
        # 把"拒绝推荐"说成"已自动调整至合规"——那是不实表述。
        if cond in violated and verify_status == "blocked":
            status, reason = "blocked", "已拦截，本轮未输出含该过敏原的菜品：" + rule_msg
        elif cond in violated and verify_status == "degraded":
            status, reason = "warn", "经多次重生成仍有需注意项，请谨慎：" + rule_msg
        elif cond in violated:
            # 曾被硬护栏命中、但最终通过了审计（已重生成至合规）
            status, reason = "adjusted", "初始方案命中硬禁忌，已由健康护栏自动调整至合规：" + rule_msg
        else:
            status, reason = "pass", "已符合" + cond + "膳食原则"
        items.append(GuardrailItem(condition=cond, rule=rule_msg, status=status, reason=reason))
    # 同餐成员冲突必须以矩阵为准：没有健康标签不等于没有冲突。
    items.extend(_family_conflict_guardrails(dish_matrix))
    # 档案不可用时护栏必然降级：必须让用户看见，不能让"没有约束"冒充"全部通过"。
    _, profile_error = _profile_health()
    if profile_error:
        items.append(GuardrailItem(
            condition="健康档案不可用",
            rule="本轮护栏已降级：档案里的健康约束与过敏原都没有生效",
            status="warn",
            reason=f"{profile_error}。请重新建档或在「家庭成员」中修正后再决策。",
        ))
    return items


def _ingredient_basis_line(draft) -> str:
    """依据句：把已确认的食材和数量用一句话摆出来（确定性，不调用 LLM）。"""
    items = _normalize_ingredient_draft(draft)
    if not items:
        return ""
    detail = "、".join(
        f"{item['name']}（{item['quantity']}）" if item.get("quantity") else item["name"]
        for item in items
    )
    return f"根据你已确认的食材和数量（共 {len(items)} 样）来推荐：{detail}。"


def _opening_has_ingredient_basis(opening, draft) -> bool:
    """正文是否已经交代了推荐依据——已交代就不再由代码前置，避免重复。"""
    text = str(opening or "").strip()
    if not text:
        return False
    if "已确认" in text:
        return True
    names = _ingredient_names(draft)
    return sum(1 for name in names if name in text) >= 2


def _candidate_list_payload(
    opening, names, latest_text, state, *, ingredient_basis=False
):
    """两阶段点菜第一阶段的候选清单 payload（recipes 为空 = 前端根本不出卡片）。

    候选菜名以用户真正看到的正文编号为准，这样「第2个」的映射不会指错菜。
    返回 None 表示拿不到候选——此时保持纯文本，绝不在推荐阶段硬塞一张卡片。

    已确认食材清单在场时（`ingredient_basis=True`）：模型正文漏了推荐依据就由
    代码前置一句确定性依据，并标记 opening_authoritative，前端不再用流式文本覆盖。
    """
    names = [str(name).strip() for name in (names or []) if str(name).strip()]
    if len(names) < 2:
        return None
    basis_added = False
    if ingredient_basis:
        draft = state.get("ingredient_draft")
        basis = _ingredient_basis_line(draft)
        if basis and not _opening_has_ingredient_basis(opening, draft):
            body = str(opening or "").strip()
            opening = f"{basis}\n\n{body}".strip()
            basis_added = True
    payload = {
        "opening": opening,
        "answer_kind": "candidates",
        "candidates": names,
        "recipes": [],
        "image_url": None,
        "image_ai_generated": False,
        "image_requested": False,
        "image_note": "",
        "chef_tip": "回复序号就行，我再把这一道的完整做法给你。",
        "sources": [],
        "health_lights": [],
        "guardrails": [
            item.model_dump()
            for item in _build_guardrails(
                latest_text,
                state.get("verify_status", ""),
                state.get("verify_violated", []),
                session_id=state.get("session_id"),
            )
        ],
        "primary_member": _active_member_name(),
    }
    if basis_added:
        payload["opening_authoritative"] = True
    return payload


_ALLERGEN_VIOLATION_PREFIX = "过敏原:"


def _allergen_rejection_labels(verify_violated) -> list:
    """挑出本轮审计命中的过敏原条目（形如 ``过敏原:花生及其制品``）的标签部分。"""
    labels = []
    for item in verify_violated or []:
        text = str(item).strip()
        if text.startswith(_ALLERGEN_VIOLATION_PREFIX):
            label = text[len(_ALLERGEN_VIOLATION_PREFIX):].strip()
            if label:
                labels.append(label)
    return labels


def _requested_dish_name(text: str) -> str:
    """从用户本轮原话里抽出被点名的具体菜名，抽不到返回空串。"""
    raw = _current_request_text(str(text or ""))
    if not raw:
        return ""
    for match in _DISH_REQUEST_PATTERN.finditer(raw):
        name = _LEADING_QUANTIFIER.sub("", match.group(1).strip())
        if (
            len(name) >= 2
            and not _is_generic_dish_name(name)
            and not _is_pure_ingredient_subject(name)
            and not _DISH_NAME_NOISE.search(name)
        ):
            return name
    return ""


def _safe_retry_opening(verify_violated, latest_text, safe_names) -> str:
    """过敏原打回重生成、拿到安全替代后，用确定性文案说清「为什么换菜」。

    重生成成功时正文只有模型自己写的一句敷衍确认，用户看不到「原菜为什么不能推荐、
    换成了哪一道」。这里在出卡前补一句可复现的说明；没有过敏原命中或没有安全替代
    菜名时返回空串，调用方保持既有 opening 不变。
    """
    labels = _allergen_rejection_labels(verify_violated)
    names = [str(name).strip() for name in (safe_names or []) if str(name).strip()]
    if not labels or not names:
        return ""
    subject = "、".join(labels)
    rejected = _requested_dish_name(latest_text)
    if rejected and rejected != names[0]:
        head = f"含{subject}的「{rejected}」我不能推荐。"
    else:
        head = f"含{subject}的做法我不能推荐。"
    return head + f"已换成不含{subject}的安全替代：{names[0]}，做法见下方卡片。"


_SELECTED_OPENING_CONFLICT_MARKERS = (
    "看不到前文",
    "看不到",
    "无法确认",
    "不能确认",
    "无法确定",
    "不能确定",
    "没有出现在",
    "对应哪一条",
    "序号已经失效",
    "清单已经过期",
    "没有第三道",
    "没有第3道",
)

# 「自称认不出序号/候选」的措辞族（2026-10-15 实测）：选定轮的卡片和图片都认对了
# 目标，讲解正文却写「我这边没有看到你前面列的“第3个”……」，并把整道菜换成候选
# 清单里根本没有的另一道（卡片「番茄蒸水蛋」/ 讲解「番茄豆腐菌菇汤」）。旧 marker 表
# 只认「看不到前文 / 无法确认」的固定说法，这一族措辞直接漏判，错位讲解原样发给用户。
#
# 措辞本身不能单独定案：「看不到图片」「没有找到火候说明」都是正常句子。所以再要求
# 正文同时谈到序号/候选/清单，才算「自称候选上下文缺失」——正常讲解不会同时出现
# 这两个特征，误伤面收得很窄。
_SELECTED_OPENING_DENIAL_TOKENS = (
    "看不到", "没有看到", "没看到", "未看到", "没有看见", "看不见",
    "无法确认", "不能确认", "无法确定", "不能确定", "无法判断", "无法识别",
    "不确定你指", "不知道你指", "不清楚你指",
    "没有找到", "找不到", "检索不到", "没有出现", "没有列出",
    "对应哪一条",
)
_SELECTED_OPENING_ANCHOR_RE = re.compile(
    r"第\s*[0-9一二两三四五六七八九十]+\s*[个道款样号份]|序号|候选|清单"
)


def _opening_denies_candidate_context(opening) -> bool:
    """正文是否在宣称「认不出用户选的序号/候选」。

    只在「措辞族 + 序号/候选锚点」同时出现时才成立；单独一句「看不到图片」不算。
    """
    body = str(opening or "")
    if not body:
        return False
    if not any(token in body for token in _SELECTED_OPENING_DENIAL_TOKENS):
        return False
    return bool(_SELECTED_OPENING_ANCHOR_RE.search(body))


def _opening_names_other_candidate(opening, target, messages) -> bool:
    """正文是否认成了清单里的另一道候选菜（且完全没提本轮已选目标）。

    只用「候选清单里的另一道菜」当确定性锚点：拿别的菜替换已选目标才算同轮错位。
    「只是没重复菜名」不算错位 —— 2026-10-11 实测模型在选定轮写了完整讲解但没重复
    菜名，整段被确定性模板顶掉（违反基线「选定后保留完整讲解」）。
    """
    text = str(opening or "")
    if not text or target in text:
        return False
    others = [
        str(name).strip()
        for name in _recent_candidates(messages)
        if str(name).strip() and str(name).strip() != target
    ]
    return any(name in text for name in others)


def _selected_target_opening_is_consistent(opening, messages, answer) -> bool:
    """已选目标轮的 opening 是否认对了同一道菜。

    只在结构化结果也命中已选目标时检查；若结构化模型自己改成别的菜，保留既有
    的不认领语义，避免用错误目标替模型背锅。

    触发口径必须窄：只有正文明确宣称候选失效，或认成清单里的另一道候选菜时才算
    错位。「正文没重复菜名」不再触发替换 —— 那会把完整讲解压成一句模板。
    """
    picked = selected_target_for_turn(messages)
    if not picked:
        return True
    if not answer.recipes:
        return True
    recipe_name = str(answer.recipes[0].name or "").strip()
    target = str(picked[1] or "").strip()
    if not recipe_name or recipe_name != target:
        return True
    text = str(opening or "").strip()
    # 正文为空时不替换：前端用流式原话兜底，模板不能顶掉用户已经看到的讲解。
    if not text:
        return True
    if any(marker in text for marker in _SELECTED_OPENING_CONFLICT_MARKERS):
        return False
    if _opening_denies_candidate_context(text):
        return False
    return not _opening_names_other_candidate(text, target, messages)


def _repair_selected_target_opening(opening, messages, answer) -> str:
    """修正“卡片/图片认对了菜，管家讲解却说看不到候选”的同轮错位。

    正常完整讲解一字不改；只有已选目标与结构化菜名一致、且正文明确宣称候选失效
    或认成清单里另一道候选菜时，才换成正确目标的确定性承接语。触发口径由
    `_selected_target_opening_is_consistent` 收窄，单纯漏写菜名不替换。流已经推给
    前端的 token 不动，最终 answer payload 必须让气泡、卡片、图片指向同一道菜。
    """
    if _selected_target_opening_is_consistent(opening, messages, answer):
        return opening
    picked = selected_target_for_turn(messages)
    if not picked:
        return opening
    index, target = picked
    recipe = answer.recipes[0] if answer.recipes else None
    intro = str(getattr(recipe, "intro", "") or "").strip()
    prefix = f"你选的第{index}道是「{target}」。" if index else f"本轮选定的是「{target}」。"
    detail = f"这道菜的特点是：{intro}" if intro else ""
    return (
        f"{prefix}{detail}"
        "下面按这道菜的食材用量、火候和步骤完整整理给你。"
    )


def _requested_dish_allergen_block(state, latest_text, messages):
    """点名菜命中已确认过敏原时的确定性拦截包。

    返回 ``(payload, merged_violations)``；不命中返回 ``None``。
    生成前拦截门与结构化阶段复核共用这一份构建函数，保证两处的文案、
    护栏和「替代方向」口径完全一致，不会各写一套。
    """
    requested_dish = _requested_dish_name(latest_text)
    if not requested_dish:
        return None
    allergens = _allergens_for_audit(state.get("session_id"))
    requested_allergen_violations = audit_allergens(
        requested_dish,
        allergens,
        use_optional=True,
    )
    if not requested_allergen_violations:
        return None
    requested_conditions = sorted({
        violation["condition"] for violation in requested_allergen_violations
    })
    merged_violations = sorted(
        set(state.get("verify_violated") or []) | set(requested_conditions)
    )
    labels = [
        condition.split(":", 1)[1].strip()
        for condition in requested_conditions
        if ":" in condition and condition.split(":", 1)[1].strip()
    ]
    subject = "、".join(labels) or "相关过敏原"
    safe = suggest_safe_dishes(allergens, k=3, use_optional=True)
    block_tip = (
        f"家庭画像中已确认{subject}过敏，{subject}是硬约束，"
        f"因此「{requested_dish}」不能推荐，也不能推荐含{subject}的做法。"
        "以下是不含该成分的替代方向："
        + "、".join(safe)
        + "。请挑一个，我再给完整做法。"
    )
    payload = {
        "opening": block_tip,
        "recipes": [],
        "image_url": None,
        "image_ai_generated": False,
        "image_requested": False,
        "image_note": "",
        "chef_tip": "",
        "sources": _kb_source_payload(messages),
        "health_lights": [],
        "guardrails": [
            item.model_dump()
            for item in _build_guardrails(
                latest_text,
                "blocked",
                merged_violations,
                session_id=state.get("session_id"),
            )
        ],
        "primary_member": _active_member_name(),
    }
    return payload, merged_violations


def _requested_dish_gate_hit(state) -> bool:
    """本轮是否应在模型开口之前就拦截点名菜。

    判据与 structure_answer 的点名菜复核同源，只多排除三类本轮本来就不会
    出菜谱的轮次（餐馆/上门服务、非饮食追问、非菜谱信息提问）。这样生成前
    拦截只会是结构化阶段拦截的**子集**，不会凭空多拦一轮。
    """
    messages = state.get("messages") or []
    latest_text = _latest_user_text(messages)
    if _classify_turn_intent(messages) in ("restaurant", "home_service"):
        return False
    if _is_non_food_followup(latest_text):
        return False
    if is_non_recipe_information_request(latest_text):
        return False
    return _requested_dish_allergen_block(state, latest_text, messages) is not None


@trace_node("requested_dish_gate")
def requested_dish_gate_node(state: "ChefState"):
    """生成前的点名菜过敏原硬拦截。

    必须在 chef_think 之前：模型看到画像里的过敏原后会自己换菜并流式输出
    一整篇替代成品，拦截包事后覆盖它，用户看到的正文就会「先出一版再被换掉」。
    这里直接不出模型，只回确定性拦截包（recipes 为空数组，不出卡片、不配图）。
    """
    messages = state.get("messages") or []
    latest_text = _latest_user_text(messages)
    blocked = _requested_dish_allergen_block(state, latest_text, messages)
    if blocked is None:
        return {}
    payload, merged_violations = blocked
    return {
        "messages": [
            AIMessage(content=json.dumps(payload, ensure_ascii=False))
        ],
        "verify_status": "blocked",
        "verify_warning": "点名菜命中已确认过敏原，已在生成前取消成品输出。",
        "verify_violated": merged_violations,
    }


def requested_dish_gate_route(state: "ChefState") -> str:
    """路由判据重新计算，不读 state["verify_status"]。

    LangGraph 的 checkpointer 会把上一轮的 verify_status 带进本轮，若路由读它，
    上一轮被拦过就会把本轮也误判成 blocked。
    """
    return "blocked" if _requested_dish_gate_hit(state) else "pass"


@trace_node("structure_answer")
def structure_answer_node(state: "ChefState"):#结构化回答节点
    messages = state["messages"]
    # chef_think 最后一轮的自然语言回答 = 流式已经推给前端的正文，原样保留进 opening
    opening = ""
    if messages and isinstance(messages[-1], AIMessage):
        # 这里已经传入前端了，所以比结构化卡片快；但模型偶尔直接吐 JSON，
        # 必须清洗后再当正文用（否则气泡和卡片里会出现一坨 JSON）。
        opening = _clean_opening_text(messages[-1].content)
        #context是纯文本给了LCEL结构化链，其他的图片的链接和图片的来源在下文传出来的answer来赋值
    latest_text = _latest_user_text(messages)
    turn_intent = _classify_turn_intent(messages)
    # 对已交付的单菜做口感追轮时，沿用同一个目标重新生成更新后的单菜卡片。
    # 目标在前面已由确定性规则锁定，不能让结构化模型把它扩展成多菜推荐。
    taste_followup_target = _taste_adjustment_followup_target(messages)
    # 候选清单后的纯确认轮只重新登记原有候选，不进入菜谱结构化。
    # 这样既不生成 recipes / 卡片 / 图片，又保证下一轮「2」仍能找到序号锚点。
    if _is_candidate_ack_without_pick(messages):
        candidate_names = _filter_candidate_names(
            _recent_candidates(messages),
            session_id=state.get("session_id"),
        )
        candidate_payload = _candidate_list_payload(
            opening, candidate_names, latest_text, state
        )
        if candidate_payload is not None:
            return {
                "messages": [
                    AIMessage(content=json.dumps(candidate_payload, ensure_ascii=False))
                ]
            }
        return {"messages": []}
    if turn_intent in ("restaurant", "home_service"):
        return {"messages": []}
    if _is_non_food_followup(latest_text):
        return {"messages": []}
    if (
        is_non_recipe_information_request(latest_text)
        and not _is_recipe_detail_followup(messages, latest_text)
        and not _is_dish_pick_turn(messages)
    ):
        return {"messages": []}
    # 询问/澄清轮次不出卡片：正文整段是一条追问时保持纯文本，
    # 否则「先问用户想法」和「直接给卡片」会同时发生（语义自相矛盾）。
    # 例外：「已经选定了一道菜，只是顺带问一句」不算追问——否则用户确认完却拿不到卡片和图。
    if (
        _is_ask_turn(opening)
        and not _is_dish_pick_turn(messages)
        and not _is_recipe_detail_followup(messages, latest_text)
        and not taste_followup_target
    ):
        return {"messages": []}
    # 已交付卡片后的普通追问 / 参数补充：只回文字，不重出卡片、不补图。
    # 基线要求「普通追问不能误触发结构化出卡」（技能验收场景 5）。
    # 实测 user_d2cc49f12c：下面三轮都被判成 followup/other 后直落结构化，
    # 重出一张几乎相同的卡片，且 image_requested=False —— 用户既丢了图，
    # 也分不清是上一道菜的补充还是新一轮：
    #   · 「这道菜大概要炖多久？」（intent=other）
    #   · 「我妈妈在控糖，我爸爸没有，今晚做两个人的饭」（intent=followup）
    #   · 「你好，你能做什么」（intent=other，闲聊）
    # 只放行真正需要重出卡的信号（选定/点名新菜/换菜/显式完整做法/口味调整/
    # 追加要菜/点名被复核的菜）；判定全部走确定性函数，宁可漏挡也不误挡。
    if (
        _has_delivered_card(messages)
        and turn_intent in ("followup", "other")
        and not _is_dish_pick_turn(messages)
        and not _is_recipe_detail_followup(messages, latest_text)
        and not taste_followup_target
        and not _is_specific_dish_request(latest_text)
        and not _is_recipe_change_request(latest_text)
        and not _requested_dish_name(latest_text)
        and not _wants_another_dish(latest_text)
    ):
        return {"messages": []}
    # 两阶段点菜第一阶段标记：泛推荐轮只出候选清单，不出卡片、不配图。
    # 但判定结果要等到「结构化过敏原复核」之后才生效——安全拦截优先于产品形态。
    is_candidate_turn = _is_candidate_turn(
        messages,
        ingredient_gate_ready=bool(state.get("ingredient_gate_ready")),
    )
    wants_images = _wants_recipe_images(messages)
    is_new_image_request = (
        _latest_user_has_image(messages)
        and _is_new_ingredient_image_request(latest_text)
    )
    context, real_image_url, real_image_ai = _build_structure_context(
        messages,
        isolate_old_context=_latest_new_image_index(messages) is not None,
    )#解包拿到
    allow_multiple = _wants_multiple_recipes(messages) and not taste_followup_target
    if not context.strip():#没有可整理的上下文（理论上不会走到这），直接结束
        return {"messages": []}
    # 点名菜先过一遍确定性过敏原审计，再进入结构化模型。
    # 实测「花生酱拌面」在文字层可能被模型改写成安全替代并按普通单菜出卡，
    # 这样虽然做法安全，却把用户明确点名的禁忌菜包装成了可交付结果。安全阻断
    # 必须优先于产品形态：命中时只给替代方向，recipes 保持空数组，不生成卡片。
    # 正常轮次已被 requested_dish_gate 在生成前拦下；这里保留同源复核做兜底，
    # 两处共用 _requested_dish_allergen_block，文案与护栏完全一致。
    requested_dish_block = _requested_dish_allergen_block(
        state, latest_text, messages
    )
    if requested_dish_block is not None:
        blocked_payload, merged_violations = requested_dish_block
        return {
            "messages": [
                AIMessage(content=json.dumps(blocked_payload, ensure_ascii=False))
            ],
            "verify_status": "blocked",
            "verify_warning": "点名菜命中已确认过敏原，已在结构化前取消卡片输出。",
            "verify_violated": merged_violations,
        }
    # 候选正文已经成形时，候选锚点不应依赖后续菜谱结构化链是否解析成功。
    # 实测结构化链连续 parse-fail 会把用户已经看到的编号清单一起吞掉，下一轮
    # 「选第2个」便没有锚点。这里先做确定性快速路径，且照旧经过过敏原过滤。
    # 候选轮不需要菜谱卡片，正文清单完整时也不该再调用结构化模型；否则前端
    # 会在正文结束后继续显示“正在整理卡片”，并白白多等一次模型返回。
    candidate_names = []
    if is_candidate_turn:
        candidate_names = _filter_candidate_names(
            _extract_candidate_names(_candidate_source_text(messages) or opening),
            session_id=state.get("session_id"),
        )
        if len(candidate_names) >= 2:
            candidate_payload = _candidate_list_payload(
                opening,
                candidate_names,
                latest_text,
                state,
                ingredient_basis=bool(state.get("ingredient_gate_ready")),
            )
            if candidate_payload is not None:
                return {
                    "messages": [
                        AIMessage(content=json.dumps(candidate_payload, ensure_ascii=False))
                    ]
                }
    # 候选锚点只约束 chef_think 生成本轮结论，不能在结构化阶段再次覆盖结论。
    # `_build_structure_context` 已明确要求菜名和食材以本轮 Agent 回答为准；若用户同时
    # 补充了新条件，Agent 可能据此调整菜品，recipes 必须跟随这个最终结论，不能被旧锚点拉回。
    try:#结构化链带「格式自动重试」：解析失败会回灌 LLM 修正，重试耗尽才降级
        answer = build_structured_answer(context)#会返回一个实例
        answer = rank_recipes(answer, allow_multiple=allow_multiple)
        if taste_followup_target and answer.recipes:
            # 口味调整只更新原卡片对应的菜，不接受结构化模型擅自换名或扩展候选。
            answer.recipes = answer.recipes[:1]
            answer.recipes[0].name = taste_followup_target
        # 自然语言已通过一次审计，但结构化模型仍可能改写食材或调料。
        # 卡片输出前再审一次，命中过敏原时直接放弃卡片，保留已通过审计的正文。
        structured_text = "\n".join(
            line
            for recipe in answer.recipes
            for line in (
                recipe.name,
                recipe.intro,
                " ".join(
                    f"{seasoning.name} {seasoning.amount}"
                    for seasoning in recipe.seasonings
                ),
                *(recipe.steps or []),
            )
        )
        # 菜谱本体里的「不用花生油」「（替花生）」是否定/替代说明，不是下料。
        # 这里必须用**保守口径** `_strip_all_negated_lines`：结构化审计是过敏原
        # 安全底线，`_strip_negated_allergen_mentions`（自由正文那版）会把
        # 「换花生油」「花生油 1 勺，不用花生碎」这类真下料整行剥掉 ⇒ 漏判。
        # 且必须**保持逐行结构**：若把整道菜拼成一行，剥离会把整道菜从审计里
        # 删掉，等于把硬审计放空，真下料一样漏过。
        allergens_for_audit = _allergens_for_audit(state.get("session_id"))
        structured_text = _strip_all_negated_lines(
            structured_text, allergens_for_audit
        )
        structured_allergen_violations = audit_allergens(
            structured_text,
            allergens_for_audit,
            use_optional=True,
        )
        if structured_allergen_violations:
            logger.warning(
                "[tempdiag] structured allergen block violations=%s text=%r",
                [
                    {"c": v.get("condition"), "k": v.get("keyword")}
                    for v in structured_allergen_violations
                ],
                structured_text[:1500],
            )
        if structured_allergen_violations:
            structured_conditions = sorted({
                violation["condition"] for violation in structured_allergen_violations
            })
            merged_violations = sorted(
                set(state.get("verify_violated") or []) | set(structured_conditions)
            )
            # 与 allergen_block_node 同口径：只回纯文本的话右栏健康链是空的，
            # 用户只看到"菜没了"却看不到"为什么没了"。统一输出结构化 payload。
            block_tip = (
                "抱歉，这道菜在备料细节（调料或步骤）里含有需要避开的过敏原，"
                "我不能推荐。请告诉我家里现有的食材，我按不含该过敏原重新配一道。"
            )
            # 阻断轮同样标依据：本轮确实检索过权威条目就带上（只给文件+章节）。
            blocked_sources = _kb_source_payload(messages)
            return {
                "messages": [AIMessage(content=json.dumps({
                    "opening": block_tip,
                    "recipes": [],
                    "image_url": None,
                    "image_ai_generated": False,
                    "image_requested": False,
                    "image_note": "",
                    "chef_tip": "",
                    "sources": blocked_sources,
                    "health_lights": [],
                    "guardrails": [
                        item.model_dump()
                        for item in _build_guardrails(
                            latest_text,
                            "blocked",
                            merged_violations,
                            session_id=state.get("session_id"),
                        )
                    ],
                    # UI 必须让用户看到「主菜当前面向谁」，否则家庭场景下无法判断该听谁的。
                    "primary_member": _active_member_name(),
                }, ensure_ascii=False))],
                "verify_status": "blocked",
                "verify_warning": "结构化卡片复核命中过敏原，已取消卡片输出。",
                "verify_violated": merged_violations,
            }
        # 声明式「约束纠正」轮：用户是在纠正成员/慢病的归属或有效性（「并没有说弟弟要减重，
        # 这个到后面都不需要管」），不是在改菜。这种情况下重跑结构化链只会让模型顺手把菜名
        # 换掉、再弹一张卡片，与用户意图完全相反，所以直接回纯文本、原卡片原地不动。
        # 位置是硬规则：产品形态短路必须排在结构化过敏原复核之后（见 tests/domain/test_allergen_guardrail.py）。
        if _is_constraint_correction_turn(latest_text):
            return {"messages": []}
        # 序号指代已失效（清单跨轮过期 / 序号越界）：不许把这一轮当「换菜」交给模型自由发挥
        # （实测它会顺手换一道菜，还回复「其实没有第三道」）。
        # 能找回上一份清单时，把它**重新登记成候选 payload**：既不出卡，又让用户下一轮
        # 说「就第3个」时锚点重新可用——否则用户会卡在「说了序号 → 清单已过期」的死循环里。
        if _index_ref_without_target(messages):
            recovered = _older_candidates(messages)
            if recovered:
                relist_payload = _candidate_list_payload(
                    "之前那份清单已经过期了，我把它重新列一遍，回复序号就行。",
                    recovered,
                    latest_text,
                    state,
                )
                if relist_payload is not None:
                    return {
                        "messages": [
                            AIMessage(content=json.dumps(relist_payload, ensure_ascii=False))
                        ]
                    }
            return {"messages": []}
        # 两阶段点菜第一阶段（排在安全复核之后）：泛推荐轮只出候选清单，不出卡片。
        # 候选优先取正文里的编号菜名（与用户看到的编号一致），
        # 正文没按编号列时才退回结构化结果的菜名。
        if is_candidate_turn:
            if len(candidate_names) < 2:
                candidate_names = _filter_candidate_names(
                    [
                        str(recipe.name).strip()
                        for recipe in answer.recipes
                        if str(recipe.name).strip()
                    ],
                    session_id=state.get("session_id"),
                )
            candidate_payload = _candidate_list_payload(
                opening,
                candidate_names,
                latest_text,
                state,
                ingredient_basis=bool(state.get("ingredient_gate_ready")),
            )
            if candidate_payload is None:
                return {"messages": []}
            return {
                "messages": [
                    AIMessage(content=json.dumps(candidate_payload, ensure_ascii=False))
                ]
            }
        # 家庭差异化必须由 Python 规则确定性计算，模型自填矩阵只能作为候选，
        # 最终卡片统一用 build_matrix 的结果覆盖，保证可审计、可复现。
        answer = _apply_family_differentiation(answer)
        # 单菜卡片的钠灯不能时有时无：模型漏填时，只要备料/步骤有确定的
        # 成盐或高钠调味线索，就按保守口径补黄灯。已有钠灯不覆盖；没有
        # 任何线索时不伪造绿灯，保持模型原有结构。
        if (
            not allow_multiple
            and len(answer.recipes) == 1
            and not any(light.label == "钠" for light in answer.health_lights)
        ):
            sodium_reason = sodium_light_reason(
                answer.recipes[0].seasonings,
                answer.recipes[0].steps,
            )
            if sodium_reason:
                answer.health_lights.append(
                    HealthLight(label="钠", level="yellow", reason=sodium_reason)
                )
        # 外出就餐场景：不清空 steps，改在 Prompt 里要求模型产出「取餐/怎么吃」而非烹饪步骤。#将实例中的菜谱进行排序
        # 健康护栏可见化：把 verify 的确定性审计结论注入卡片，供前端右栏渲染『健康链』
        answer.guardrails = _build_guardrails(
            latest_text,
            state.get("verify_status", ""),
            state.get("verify_violated", []),
            dish_matrix=answer.dish_matrix,
            session_id=state.get("session_id"),
        )
        # 健康护栏：若多次重生成仍不通过，把安全警示带进卡片（绝不静默放行）
        warning = state.get("verify_warning", "")
        if warning:
            answer.chef_tip = (answer.chef_tip + " " + warning).strip()
        answer.image_requested = wants_images
        if not wants_images:
            for recipe in answer.recipes:
                recipe.image_url = None
                recipe.image_ai_generated = False
            answer.image_url = None
            answer.image_ai_generated = False
            answer.image_note = ""
        else:
            # 图片搜索已剥离到 chat_route 的后台线程（structure 曾在节点内串行搜图，
            # 四级图源瀑布+逐候选下载+视觉审核动辄 120s，是全链最大瓶颈）。
            # 这里只保留用户本轮上传图的兼容赋值；其余菜图由 SSE image 事件异步补推。
            if allow_multiple and answer.recipes:
                for index, recipe in enumerate(answer.recipes):
                    if not recipe.image_url and index == 0 and real_image_url:
                        recipe.image_url = real_image_url
                        recipe.image_ai_generated = real_image_ai
            elif answer.recipes and real_image_url:
                answer.recipes[0].image_url = real_image_url
                answer.recipes[0].image_ai_generated = real_image_ai
            # 代码兜底：图片 URL 以工具真实返回为准——有真链接才给图，没有就强制 null，
            # 杜绝"正文说找到图、卡片却没图"的口径不一
            if allow_multiple and answer.recipes:
                # 多道菜时顶层字段只保留第一道，供旧前端/旧历史兼容；新前端读取每道菜自己的 image_url。
                answer.image_url = answer.recipes[0].image_url
                answer.image_ai_generated = answer.recipes[0].image_ai_generated
            else:
                answer.image_url = answer.recipes[0].image_url if answer.recipes else None#赋值图片路径
                # 透明标注（项目亮点）：图片若由通义万相生成，强制让前端知道，绝不伪装成实拍图
                answer.image_ai_generated = (
                    answer.recipes[0].image_ai_generated if answer.recipes else False
                )#赋值图片是否由 AI 生成
            # 图片状态和文案必须同源：有 AI 图时统一带透明标注，同时移除模型
            # 早先留下的「没有图/不放图」旧结论，避免截图里图文互相打架。
            answer.image_note = build_image_note(
                bool(answer.image_ai_generated),
                answer.image_note,
            )
        # primary_member 刻意不进 ChefAnswer schema：不改模型格式指令，避免扰动结构化解析；
        # 只在出卡时由代码注入，语义是「本轮主菜按谁的健康约束求解」。
        # 已选目标轮的正文不能与卡片/图片各说各话：真实会话里卡片和图片都是
        # 「番茄烘蛋」，但 opening 说「看不到前文 1/2/3」并改成清蒸鲈鱼。
        # 仅在结构化菜名也命中已选目标时才修正文，正常完整讲解保持原样；
        # 判定口径已收窄（2026-10-11）：正文没重复菜名不算错位，不得替换成模板。
        repaired_opening = _repair_selected_target_opening(opening, messages, answer)
        opening_authoritative = repaired_opening != opening
        opening = repaired_opening
        # 过敏原打回重生成后出卡时，用确定性文案替换模型那句敷衍确认，说清原菜为何被拒、
        # 换成了哪一道安全替代；没有过敏原命中时不改动既有 opening。
        safe_retry_opening = _safe_retry_opening(
            state.get("verify_violated", []),
            latest_text,
            [recipe.name for recipe in answer.recipes],
        )
        if safe_retry_opening:
            # 前置说明，不替换：模型写好的完整讲解必须原样保留。替换式实测会把整段
            # 讲解压成一句说明，而说明自身只有几十字，短于前端
            # `streamedText.length > opening.length + 80` 的兜底阈值 ——
            # 结果两种长度下「说明」和「讲解」必被吞掉一个。
            opening = f"{safe_retry_opening}\n\n{opening}".strip()
        # 确定性处理依据：先过滤模型自填来源中明显错人群的文件；过滤后为空时，
        # 再从本轮 KB 命中回填文件与章节。宁可来源为空，也不引用婴幼儿/孕期/
        # 学龄儿童/老年等不匹配人群的指南。
        source_audience_text = _source_audience_text(messages)
        answer.sources = _filter_source_refs(
            answer.sources,
            audience_text=source_audience_text,
        )
        if not answer.sources:
            kb_refs = _kb_source_refs(
                messages,
                isolate_old_context=_latest_new_image_index(messages) is not None,
                audience_text=source_audience_text,
            )
            if kb_refs:
                answer.sources = [SourceRef(**ref) for ref in kb_refs]
        payload = {
            # 最后一道护栏：opening 若仍是 JSON/围栏形态（模型双层包裹），换确定性兜底句，
            # 绝不让前端把一坨 JSON 渲染成正文。
            "opening": _final_opening_guard(opening),
            "primary_member": _active_member_name(),
            **answer.model_dump(),
        }
        # 仅在代码为已选目标校准过正文时下发；前端据此拒绝用更长的旧流式文本
        # 在配图回填后的会话同步中覆盖已经校正的 opening。普通回答保持 payload 不变。
        if opening_authoritative:
            payload["opening_authoritative"] = True
        return {
            "messages": [
                AIMessage(content=json.dumps(payload, ensure_ascii=False))#ai结构化完的消息加进 messages
            ]
        }
    except Exception as exc:#降级：候选轮保留已经流式展示的候选清单
        # 这里原本静默返回空消息，导致“有正文但没卡片/没图片”时无法定位异常。
        # 只记录诊断信息，不改变降级返回值，也不触碰已经流式输出的正文。
        logger.exception(
            "structure_answer_node failed: "
            "exc_type=%s latest_text=%r is_candidate_turn=%s "
            "wants_images=%s turn_intent=%s context_chars=%d message_count=%d",
            type(exc).__name__,
            latest_text[:200],
            is_candidate_turn,
            wants_images,
            turn_intent,
            len(context),
            len(messages),
        )
        if is_candidate_turn:
            candidate_names = _filter_candidate_names(
                _extract_candidate_names(_candidate_source_text(messages) or opening),
                session_id=state.get("session_id"),
            )
            candidate_payload = _candidate_list_payload(
                opening,
                candidate_names,
                latest_text,
                state,
                ingredient_basis=bool(state.get("ingredient_gate_ready")),
            )
            if candidate_payload is not None:
                return {
                    "messages": [
                        AIMessage(content=json.dumps(candidate_payload, ensure_ascii=False))
                    ]
                }
        return {"messages": []}#不变成节构卡片了

# --------------------------------------------------------------------------- #
#  4.5 健康护栏节点（L3 硬护栏）：chef_think 输出后、结构化前做确定性审计
# --------------------------------------------------------------------------- #
MAX_VERIFY = 3  # 打回重生成的上限，防无限循环


# 纯口头安全问答里，正文讲的是「别放/为什么不能放/换成什么」，
# 这些提醒句本身会反复出现过敏原名字，不能当成真实下料。
# 只在 `_is_plain_safety_refusal_turn` 成立时才启用这一层。
_ADVISORY_LINE_MARKERS = (
    "过敏", "忌口", "红线", "硬约束", "回避", "排除", "剔除",
    "不能用", "不要用", "不能吃", "不要吃", "不宜", "不建议", "不推荐",
    "不能放", "不要放", "不宜放", "不能加", "不要加", "不宜加",
    "不能沾", "不能蘸", "不要沾", "不要蘸",
    "替代", "换成", "改成", "换掉", "风险", "危险", "可能含有",
    "掺", "隐形", "误食", "标签", "配料表", "成分", "来源", "依据",
    "指南", "标准", "白皮书", "医生", "医师", "营养师", "咨询",
    "观察", "注意", "提醒", "警惕", "症状", "反应", "急救", "就医",
)
# 正文已经交付可执行菜谱结构时，不再走「口头问答」这条放宽路径。
_ANSWER_RECIPE_STRUCTURE_MARKERS = (
    "食材", "用料", "主料", "配料", "调料", "步骤", "备料",
)
# 「讲解型正文」的专用词汇：出处、依据、结论、膳食指南口径、每日限量、换算……
# 只在这些词出现、且正文里又认不出任何菜谱分节时，才判定这一轮交付的是
# 讲解/问答而不是菜谱本体（见 `_is_explanation_prose`）。
# 刻意不含「建议/注意/不能/避免」这类通用词：它们同样出现在菜谱提示句里，
# 用来放行会把「番茄牛肉面：面条煮熟，加生抽2大勺、盐5g调味」这类口语菜谱
# 一起放掉（实测 2026-10-09 选定轮的真实链路）。
_EXPLANATION_PROSE_MARKERS = (
    "指南", "食养", "依据", "出处", "来源", "结论", "原则", "标准", "白皮书",
    "摄入", "限量", "换算", "附录", "每日", "不替代", "参考", "提醒",
)
# 正文明确表达拒绝/回避，才说明它在讲安全，而不是在教怎么下料。
_ANSWER_REFUSAL_MARKERS = (
    "不能放", "不要放", "不宜放", "不能加", "不要加", "不宜加",
    "不能吃", "不要吃", "不宜吃", "不能用", "不要用", "禁止",
    "避免", "不推荐", "不建议", "不含", "排除", "回避", "禁用",
)
# 纯口头安全问答里真正危险的方向只有一个：「把过敏原当下料加进去」。
# 因此定点检测「下料动词 + 紧邻的过敏原名」，而不是把整篇提到过敏原的提醒都当违规。
_ALLERGEN_ADD_VERBS = frozenset("放加用吃喝拌淋倒蘸沾撒添")
_ALLERGEN_ADD_MODIFIER_CHARS = frozenset(
    "一点些的上下入进去来勺滴克半两几茶杯个块片张把"
)
_ALLERGEN_NEGATION_CHARS = ("不", "别", "勿", "莫", "禁", "免", "无")


def _has_recipe_structure(answer_text: str) -> bool:
    """正文是否已经交付可执行菜谱结构（食材清单/步骤分节）。

    只在行首、且后面紧跟冒号时才认，避免把安全说明里的
    「替代食材：芝麻酱」这类提醒误判成菜谱分节，从而把纯问答轮挡在放宽路径外。
    """
    for line in str(answer_text or "").splitlines():
        stripped = line.strip().lstrip("#*-•·>【 ").strip()
        stripped = re.sub(r"^\d+[.、)）]\s*", "", stripped)
        for marker in _ANSWER_RECIPE_STRUCTURE_MARKERS:
            if stripped.startswith(marker) and re.match(
                rf"{re.escape(marker)}\s*[:：]", stripped
            ):
                return True
    return False


def _recipe_content_for_audit(answer_text: str, *, advisory_only: bool = False) -> str:
    """排除安全说明句，只保留候选菜名、食材和做法供过敏原硬审计。"""
    kept = []
    safety_markers = (
        "不能推荐", "禁止", "绝对禁止", "不含", "不使用", "不用", "不放",
        "不得", "避免", "交叉接触", "交叉污染", "硬约束", "安全替代",
    )
    if advisory_only:
        safety_markers = safety_markers + _ADVISORY_LINE_MARKERS
    for line in str(answer_text or "").splitlines():
        compact = line.replace(" ", "")
        if any(marker in compact for marker in safety_markers):
            continue
        kept.append(line)
    return "\n".join(kept)


_JSON_RECIPE_FIELDS = ("name", "serving", "servings", "dishes", "ingredients", "steps")


def _json_recipe_leaves(recipe) -> list[str]:
    """把一份 recipe 结构里「算下料/做法」的文本叶子摊平。"""
    if not isinstance(recipe, dict):
        return [str(recipe)] if isinstance(recipe, (str, int, float)) else []
    parts: list[str] = []
    for field in _JSON_RECIPE_FIELDS:
        value = recipe.get(field)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    # 食材常写成 {"name": "盐", "amount": "1g"}
                    parts.extend(str(v) for v in item.values() if isinstance(v, str))
    return parts


def _json_recipe_audit_text(answer_text: str) -> tuple[bool, str]:
    """JSON 结构化交付：返回 ``(是否结构化交付, 只含菜谱本体的审计文本)``。

    JSON payload 里的 ``health_lights``、``guardrails``、``sources``、``reasons``、
    ``member_adjustments`` 这些字段本身就在复述「本菜撞了哪条红线」或引用指南
    原文（实测 user_e604de5674 的 ``sources[].snippet`` 抄了「鱼、虾、蟹」，
    perf-1791806412 的 ``health_lights`` 写「高嘌呤（动物内脏）」）。把它们当违规
    证据，会让一份合规交付反复打回、前端连闪「正在形成膳食建议」。

    因此这一支只认菜谱本体：``recipes`` 的菜名/份量/食材/步骤，加上 ``opening``
    markdown 正文里的食材/做法分节（部分交付把菜谱写进 opening）。
    结构化交付一律不再退回整篇——payload 的其余字段是解释与引用，不是下料。
    """
    text = str(answer_text or "").strip()
    if not text:
        return False, ""
    payload = None
    try:
        payload = json.loads(text)
    except Exception:  # noqa: BLE001
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                payload = json.loads(text[start:end + 1])
            except Exception:  # noqa: BLE001
                payload = None
    if not isinstance(payload, dict):
        return False, ""
    parts: list[str] = []
    recipes = payload.get("recipes")
    if isinstance(recipes, dict):
        recipes = [recipes]
    for recipe in recipes or []:
        parts.extend(_json_recipe_leaves(recipe))
    opening = payload.get("opening")
    if isinstance(opening, str) and opening.strip():
        parts.append(_allergen_core_audit_text(opening, broad_headings=True))
    return True, "\n".join(part for part in parts if str(part).strip())


# 过敏原硬审计的范围边界：只有「菜谱本体」能触发重生成。
# 提醒、分餐、依据、热量、小建议这些段落里出现过敏原名，属于安全说明或替代
# 说明（实测：「脆感替代：用黄瓜丁+胡萝卜丁顶替花生」「确认酱料配料表无花生」
# 「依据：《…白皮书》…花生是常见致敏食物」「传统版少一份油炸花生」），不是在
# 教下料；把它们当违规会让一段好答案被反复打回、最后一句话兜底。
# 因此硬审计只认食材/用料/调料分节与做法/步骤分节（含行内「食材：…」写法）。
_INGREDIENT_SECTION_MARKERS = (
    "食材", "用料", "主料", "配料", "调料", "材料", "原料", "备料", "腌料",
)
_STEP_SECTION_MARKERS = ("做法", "步骤", "制作", "操作", "流程", "烹饪")
_SECTION_MARKERS = _INGREDIENT_SECTION_MARKERS + _STEP_SECTION_MARKERS
_INLINE_SECTION_RE = re.compile(
    r"(?:" + "|".join(_SECTION_MARKERS) + r")\s*[：:]"
)


def _strip_line_markup(line: str) -> str:
    """去掉行首的标题符号、项目符号与序号，得到用于识别的正文。"""
    text = str(line or "").strip()
    text = re.sub(r"^#{1,6}\s*", "", text).strip()
    text = re.sub(r"^[-*>•·]+\s*", "", text).strip()
    text = re.sub(r"^\d+[.、)）]\s*", "", text).strip()
    return text


def _is_section_header_line(line: str) -> bool:
    """这一行是不是一个新的分节标题（用来结束上一个分节）。"""
    raw = str(line or "").strip()
    if not raw:
        return False
    if raw.startswith("#"):
        return True
    if re.fullmatch(r"\*\*[^*]+\*\*[：:]?", raw):
        return True
    if re.fullmatch(r"【[^】]{1,12}】", raw):
        return True
    return False


def _starts_with_section_marker(text: str, *, broad_headings: bool = False) -> bool:
    """这一行是不是「食材 / 做法」这类分节标题的开头。

    只认标题形态：``食材``、``食材（2-3人份）``、``食材：…``、``步骤1：…``。
    ``操作要点：…``、``材料不新鲜``、``配料表没写`` 这类只是借了同名前缀的
    安全说明或普通句子，不算分节起点——否则提醒段会被当成菜谱本体重新卷进
    硬审计，又回到「好答案被反复打回」的老问题。

    ``broad_headings=True``（慢病审计用）额外认 ``**【用料】**（约3–4人份）``、
    ``【做法】`` 这种「书名号 + 加粗」混写的分节标题：模型很爱这么写，认不出来
    时整篇建议/依据会被当成菜谱本体卷进硬审计。过敏原侧保持默认口径不变。
    """
    for marker in _SECTION_MARKERS:
        if not text.startswith(marker):
            continue
        rest = text[len(marker):].lstrip("*＊ \t")
        if broad_headings:
            rest = rest.lstrip("*＊】 \t")
        if not rest:
            return True
        if rest[0] in "：:（(【":
            return True
        head = rest[:4]
        if rest[0].isdigit() and (
            "：" in head or ":" in head or any(ch in head for ch in "、.．")
        ):
            return True
        return False
    return False


def _allergen_core_audit_text(answer_text: str, *, broad_headings: bool = False) -> str:
    """抽取能触发硬拦的「菜谱本体」文本。

    食材/调料分节与做法/步骤分节里的过敏原名是真实下料风险，进硬审计；
    提醒、分餐、依据、热量、小建议等段落里的名字只是被提及，交给提示层。

    ``broad_headings`` 只给慢病审计开（认 ``**【用料】**（约3–4人份）`` 这类
    混写标题）；过敏原硬审计沿用默认口径，不在这里顺手放宽/收紧。
    """
    kept: list[str] = []
    active = False
    for line in str(answer_text or "").splitlines():
        stripped = _strip_line_markup(line)
        if not stripped:
            continue
        if broad_headings:
            stripped = stripped.lstrip("【 \t")
        if _starts_with_section_marker(stripped, broad_headings=broad_headings):
            active = True
            kept.append(line)
            continue
        if _INLINE_SECTION_RE.search(line):
            active = True
            kept.append(line)
            continue
        if _is_section_header_line(line):
            active = False
            continue
        if active:
            kept.append(line)
    return "\n".join(kept)


# 菜谱本体里模型会写「藕丁 80g（替花生）」「替代花生米的脆感」「不用花生油」这类
# 否定/替代括注。字面包含过敏原名，语义却是「不加」，与真实下料正好相反。
# 收窄到菜谱本体后它们仍会把好答案反复打回（实测 E1/E2 宫保鸡丁最终落成替换菜）。
# 因此在硬审计前，把「紧邻否定/替代标记」的提及清掉；真正下料的写法
# （花生油 2 勺 / 花生碎 1 小把 / 生抽、花生酱）前面没有这类标记，照旧命中。
_ALLERGEN_MENTION_NEGATION_MARKERS = (
    "不", "别", "勿", "莫", "禁", "免", "无", "替", "换", "改", "忌", "避",
)


def _strip_negated_allergen_mentions(text: str, allergens) -> str:
    """丢掉审计文本里「整行都是否定/替代说明」的行，只留可能真实下料的部分。

    判定粒度是「行」，不再逐词删除：逐词删除会把「不用花生、花生碎、花生油、
    花生酱」里打头的「不用」一起吃掉，剩下的长词（花生油/花生酱）反而失去否
    定语境，被当成真下料 —— 实测 E2 宫保鸡丁的好答案就是这样被反复打回、最后
    落成一句兜底。整行删除后，同行里不再有任何过敏原残留，也就无从误判。
    真下料的写法（花生油 2 勺 / 花生碎 1 小把）不含否定/替代标记，照旧命中。
    """
    if not text or not allergens:
        return text
    words = _allergen_surface_words(allergens)
    if not words:
        return text
    kept_lines: list[str] = []
    for line in str(text).splitlines():
        if any(word in line for word in words) and any(
            marker in line for marker in _ALLERGEN_MENTION_NEGATION_MARKERS
        ):
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines)


# 结构化审计专用的**保守**剥离标记：只认纯否定词。
# 为什么不复用 `_strip_negated_allergen_mentions`（自由正文路径那版）：那版为了
# 压住「（替花生）」这类替代说明，把「替/换/改」也算作警示，并**整行剔除**。
# 这套口径搬到「食材/步骤」语境会漏判真下料，实测三条：
#   「换花生油」                        → 整行被剥 → 漏判
#   「替换成花生酱」                     → 整行被剥 → 漏判
#   「花生油 1 勺，不用花生碎装饰」        → 整行被剥 → 真下料一起没了
# 结构化审计是过敏原安全底线：**宁可多误报一次（最多不出卡片），也不能漏判**。
# 因此这里只做一件事：该行内**每一处**过敏原提及都紧邻纯否定词时，才整行剔除。
_SAFE_NEGATION_MARKERS = ("不", "别", "勿", "莫", "禁", "免", "无", "忌", "避")
# 窗口取 6：覆盖「不可以使用花生油」「请不要放花生碎」这类双字否定短语（否定词
# 距提及可达 5 字）。判定是「每一处提及都紧邻否定才整行剔除」+ 只看提及**前** window
# 字符，加宽只影响单处提及的归属，不会把后置的真下料（「花生碎少许不用多」）误剥。
_SAFE_NEGATION_WINDOW = 6


def _strip_all_negated_lines(text: str, allergens) -> str:
    """只剔除「整行每一处过敏原提及都被否定」的行；有任何一处像真下料就整行保留。"""
    if not text or not allergens:
        return text
    words = _allergen_surface_words(allergens)
    if not words:
        return text
    kept: list[str] = []
    for line in str(text).splitlines():
        mentions, start = [], 0
        while True:
            hits = [(line.find(w, start), w) for w in words]
            hits = [(i, w) for i, w in hits if i >= 0]
            if not hits:
                break
            index, word = min(hits)
            mentions.append(index)
            start = index + len(word)
        if not mentions:
            kept.append(line)
            continue
        if all(
            any(
                marker in line[max(0, index - _SAFE_NEGATION_WINDOW):index]
                for marker in _SAFE_NEGATION_MARKERS
            )
            for index in mentions
        ):
            continue  # 整行都是「不用/无/忌…」的说明，剔除
        kept.append(line)  # 有一处像真下料，整行保留交审计
    return "\n".join(kept)


def _is_plain_safety_refusal_turn(
    messages,
    answer_text: str,
    *,
    is_candidate_turn: bool,
) -> bool:
    """本轮是不是「只为解答一句安全疑问」的口头回答（不交付菜谱）。

    三个条件同时成立才算成立，任一不成立都继续按整篇审计：
      1. 用户这一轮问的是能不能吃/能不能放某样东西，不是点菜；
      2. 正文没有交付菜谱结构（没有食材/步骤这类可执行清单）；
      3. 正文自己已经明确表达拒绝/回避。
    实测 D1：正文「花生酱属于花生及其制品…不能放」被整篇过敏原审计判成
    「在下料」，连续重生成后前端只剩一道与提问无关的成品菜谱。
    """
    if is_candidate_turn:
        return False
    raw = _latest_user_text(messages)
    if not raw or not is_non_recipe_information_request(raw):
        return False
    if _is_dish_pick_turn(messages) or _is_explicit_recipe_detail_request(raw):
        return False
    compact = re.sub(r"\s+", "", str(answer_text or ""))
    if not compact:
        return False
    if _has_recipe_structure(answer_text):
        return False
    return any(marker in compact for marker in _ANSWER_REFUSAL_MARKERS)


def _is_explanation_prose(answer_text: str) -> bool:
    """认不出菜谱分节时，判断正文是不是「讲解/问答」而不是口语菜谱。

    实测 perf-1791807060 / user_357fdc3df8：「成人高血压每天盐吃多少」「我妈痛风
    晚上能不能喝排骨汤」这类问答，正文必然写着「浓肉汤/老火汤/火腿肠/咸菜/猪肝」
    来解释忌口——那是这轮回答的主题词，不是下料。旧口径认不出分节就退回整篇，
    同一段讲解每轮都被判违规、重生成到 MAX_VERIFY 才收口，前端连着闪
    「正在形成膳食建议」。

    判据只用「引用/结论」专用词（`_EXPLANATION_PROSE_MARKERS`），不碰
    「建议/注意」这类通用词：口语菜谱（「番茄牛肉面：…加生抽2大勺、盐5g调味」）
    里没有这些词，仍按原来的「剥掉安全说明句」口径进硬审计。
    """
    return any(marker in str(answer_text or "") for marker in _EXPLANATION_PROSE_MARKERS)


def _audit_texts_for_answer(
    answer_text: str,
    *,
    candidate_names,
    plain_safety_refusal: bool,
    allergens=None,
) -> tuple[str, str]:
    """按轮次形态给出 (慢病审计文本, 过敏原审计文本)。

    - 候选轮：只审计用户看到的候选菜名，提醒句不进审计；
    - 纯口头安全问答：正文必然反复提到过敏原本身，先剥掉提醒句再审计；
    - 其余轮次：慢病与过敏原硬审计都只看「菜谱本体」（食材/调料分节 +
      做法/步骤分节）。

    只有菜谱本体能触发重生成：提醒、分餐、依据、热量、小建议段落里出现过敏原
    名或病种忌口词，是安全说明/替代说明（「脆感替代：用黄瓜丁顶替花生」
    「别用浓肉汤、鱼汤打底，也别加小鱼小虾类干货」），把它们当违规会把一段好
    答案反复打回、最后一句话兜底（实测宫保鸡丁无花生版、番茄豆腐蛋花汤）。
    正文里认不出任何菜谱分节时，慢病审计不再退回整篇建议：饮食问答/讲解里
    必然写着忌口词（「长时间熬的排骨浓汤属于要严格限制的一类」「隐形盐：火腿肠、
    咸菜」「动物内脏、浓肉汤、部分海鲜」），那是这类答案的**主题**而非下料，
    整篇审计等于让每一次讲解都重生成直到兜底（实测全库 100 条打回样本里 11 条
    属于此类）。过敏原侧口径不变，仍保留「剥掉安全说明句」的整篇回退。

    结构化交付（JSON payload）走单独一支：只看 ``recipes`` 与 ``opening`` 正文
    里的菜谱分节，不再回退到整篇/剥说明句——payload 里的依据、健康灯、
    分餐矩阵、引用来源是解释，不是下料，见 `_json_recipe_audit_text`。
    """
    if candidate_names:
        names = "\n".join(candidate_names)
        return names, names
    if plain_safety_refusal:
        stripped = _recipe_content_for_audit(answer_text, advisory_only=True)
        # 纯口头问答必然反复点名过敏原本身来解释，通用过敏原审计在这里只会误伤；
        # 这一支的过敏原风险交给 `_allergen_permission_violations` 的定点下料检测。
        return stripped, ""
    core = _allergen_core_audit_text(answer_text)
    # 过敏原审计的正文沿用原口径：认不出分节时退回「剥掉安全说明句」的整篇，
    # 不让过敏原硬审计被整体放空。
    allergen_text = core if core.strip() else _recipe_content_for_audit(answer_text)
    # 慢病审计多认一层混写分节标题（**【用料】**（约3–4人份））：认不出来时
    # 整篇「为什么这样吃/依据/小膳建议」都会被当成菜谱本体，好答案被反复打回。
    chronic_core = core or _allergen_core_audit_text(
        answer_text, broad_headings=True
    )
    # 慢病审计同样只认菜谱本体：小建议段里的「别用虾皮/避开浓肉汤」是安全说明，
    # 按整篇审计会被当成真实忌口命中，好答案被反复打回。取本体按四级回退：
    #   1. JSON 交付只取 recipes 的菜名/份量/食材/步骤（+ opening 里的菜谱分节）；
    #   2. 其余正文只认食材/做法分节（含 `**【用料】**（约3–4人份）` 混写标题）；
    #   3. 认不出分节但正文是「讲解/问答」（带指南/依据/结论/摄入限量这类词）：
    #      本轮交付的是解释而不是菜谱，慢病审计文本为空——没有下料动作就没有慢病
    #      违规，不能拿建议正文当证据（否则「浓汤/老火汤/火腿肠/隐形盐」这些
    #      **主题词**会让同一段讲解被无限打回，前端连闪「正在形成膳食建议」）；
    #   4. 其余（口语菜谱等）沿用既有的「剥掉安全说明句」口径，避免硬审计被放空。
    structured, structured_text = _json_recipe_audit_text(answer_text)
    if structured:
        chronic_text = structured_text
    else:
        chronic_text = chronic_core
        if not chronic_text.strip() and not _is_explanation_prose(answer_text):
            chronic_text = _recipe_content_for_audit(answer_text, advisory_only=True)
            if not chronic_text.strip():
                chronic_text = answer_text
    # 菜谱本体里「（替花生）」「不用花生油」这类否定/替代括注字面含过敏原名，
    # 但不是下料（实测 E1/E2 宫保鸡丁），审计前先清掉这一层。
    return chronic_text, _strip_negated_allergen_mentions(allergen_text, allergens)


def _allergen_permission_violations(
    answer_text: str,
    allergens,
    *,
    plain_safety_refusal: bool,
) -> list:
    """纯口头安全问答里的定点兜底：正文若「许可加入/食用」过敏原，仍要判违规。

    只认「下料动词紧邻过敏原名」这一种形态（放花生酱/加花生碎/用花生油…），
    并且动词前 3 字内不能是否定（不能放/别加/避免用）。
    实测 D1 正文里「刚拌过花生酱的勺子」「再放任何带花生的酱」「直接放回货架」
    这类安全提醒都会被宽松的中心词匹配误伤，所以窗口收窄到动词与过敏原相邻。
    提醒句剥离只放宽「提到名字」，不能放过「可以放一点花生酱提香」这种危险建议。
    """
    if not plain_safety_refusal or not allergens:
        return []
    words = _allergen_surface_words(allergens)
    if not words:
        return []
    violations: list = []
    for clause in re.split(r"[。；;！？!?\n]", str(answer_text or "")):
        if not clause.strip():
            continue
        if not any(word in clause for word in words):
            continue
        if not _clause_adds_allergen(clause, words):
            continue
        violations.extend(audit_allergens(clause, allergens, use_optional=True))
    return violations


def _allergen_surface_words(allergens) -> list[str]:
    """取该过敏原在规则表里的全部表层词（别名 + 隐含来源），用于定位下料对象。"""
    from domain import allergen_rules

    try:
        codes, _ = allergen_rules.resolve_allergens(allergens, use_optional=True)
    except Exception:
        return []
    words: list[str] = []
    for code in codes:
        rule = allergen_rules.ALLERGEN_RULES.get(code) or {}
        for key in ("aliases", "hidden"):
            for word in rule.get(key) or []:
                if word and word not in words:
                    words.append(word)
    # 长词优先，避免「花生」抢先匹配掉「花生酱」导致窗口错位。
    words.sort(key=len, reverse=True)
    return words


def _clause_adds_allergen(clause: str, words) -> bool:
    """分句里是否存在「下料动词 + 修饰 + 过敏原名」的许可式下料。"""
    for word in words:
        idx = clause.find(word)
        while idx >= 0:
            window = clause[max(0, idx - 4):idx]
            probe = window.rstrip("".join(_ALLERGEN_ADD_MODIFIER_CHARS))
            if probe and probe[-1] in _ALLERGEN_ADD_VERBS:
                verb_at = max(0, idx - 4) + len(probe) - 1
                prefix = clause[max(0, verb_at - 3):verb_at]
                if not any(ch in prefix for ch in _ALLERGEN_NEGATION_CHARS):
                    return True
            idx = clause.find(word, idx + len(word))
    return False


# 自定义状态：在 MessagesState 基础上扩展护栏所需的计数字段
class ChefState(MessagesState):
    session_id: str = ""
    turn_seq: int = 0  # 轮序号：入口每轮 +1，供 trace / usage 按轮归因
    verify_attempts: int      # 已打回重生成次数
    verify_warning: str       # 超限仍不通过时带给前端的安全警示
    verify_status: str        # ok / retry / degraded / blocked，供条件边路由
    verify_violated: list = []  # 本轮曾命中的病种列表（供右栏『健康链』如实展示）
    profile_ready: bool = True  # 充分性门控：健康画像是否足够进入检索/审计（AgentMental 范式）
    profile_missing: list = []  # 充分性门控：本轮尚未确认的高风险病种
    tool_calls_in_turn: int = 0  # 本轮已执行工具调用数（并发宽度累计），入口重置
    tool_rounds: int = 0  # 本轮已进过工具节点的圈数，入口重置（与并发宽度分开封顶）
    tool_budget_exhausted: bool = False  # 本轮工具预算是否耗尽，供日志/降级识别
    tool_budget: int = 0  # 本轮工具「并发宽度」上限（选定轮收紧为 2，见 _turn_tool_budget）
    pick_turn_slot_granted: bool = False  # 选定轮第二圈是否已补过一格取证额度（每轮只补一次）
    retry_slot_granted: bool = False  # 本轮重试是否已补过一次取证额度（每轮只补一次，见 _retry_tool_slot_patch）
    ingredient_gate_active: bool = False  # 图片食材确认门是否仍在追问用户
    ingredient_gate_action: str = ""  # ask / corrected / confirmed / skip
    ingredient_draft: list = []  # 视觉识别出的待确认食材草稿
    ingredient_gate_ready: bool = False  # 本轮已确认，可进入候选推荐

@trace_node("verify_answer")
def verify_answer_node(state: ChefState):
    """输出前硬审计：慢病可降级，过敏原连续命中时必须阻断，不得放行。"""
    attempts = state.get("verify_attempts", 0)
    previous_violations = list(state.get("verify_violated") or [])
    # 取最新一条 chef_think 的自然语言回答做审计
    answer_text = ""
    for m in reversed(state["messages"]):
        if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
            answer_text = str(m.content)
            break
    user_text = _latest_user_text(state["messages"])
    conditions = _merged_conditions(user_text)
    allergens = _allergens_for_audit(state.get("session_id"))
    requested_allergens = requested_allergen_additions(user_text, allergens)
    if requested_allergens:
        requested_violations = [
            f"过敏原:{allergen}" for allergen in requested_allergens
        ]
        return {
            "verify_status": "blocked",
            "verify_warning": "",
            "verify_violated": sorted(
                set(previous_violations) | set(requested_violations)
            ),
        }
    is_candidate_turn = _is_candidate_turn(
        state["messages"],
        ingredient_gate_ready=bool(state.get("ingredient_gate_ready")),
    )
    candidate_names = _extract_candidate_names(answer_text) if is_candidate_turn else []
    # 候选轮的安全说明常会原样提到“虾/花生”等禁忌词来提醒用户，不能把说明本身
    # 当成菜品违规，否则会触发无谓重生成。真正需要硬审计的是用户看到的候选菜名。
    # 同理，纯口头安全问答（如「花生酱能不能放」）的正文必然反复出现过敏原本身，
    # 必须剥掉提醒句再审计，否则正确答案会被判违规、连续重生成成一道无关菜谱。
    plain_refusal = _is_plain_safety_refusal_turn(
        state["messages"], answer_text, is_candidate_turn=is_candidate_turn
    )
    # 安全问答轮即使模型没有明确写拒绝语（例如直接建议「放一点花生酱」），
    # 打回时也必须继续回答同一个疑问，不能改口去交付一道无关菜谱。
    information_turn = False
    if not is_candidate_turn:
        try:
            information_turn = (
                is_non_recipe_information_request(user_text)
                and not _is_dish_pick_turn(state["messages"])
                and not _is_explicit_recipe_detail_request(user_text)
            )
        except Exception:
            information_turn = False
    safety_retry_turn = plain_refusal or information_turn
    audit_text, allergen_audit_text = _audit_texts_for_answer(
        answer_text,
        candidate_names=candidate_names,
        plain_safety_refusal=plain_refusal,
        allergens=allergens,
    )
    allergen_violations = audit_allergens(
        allergen_audit_text,
        allergens,
        use_optional=True,
    )
    # 提醒句剥离只放宽「提到名字」，不能放过「可以放一点花生酱提香」这类危险许可。
    allergen_violations = allergen_violations + _allergen_permission_violations(
        answer_text, allergens, plain_safety_refusal=plain_refusal
    )
    violations = audit(audit_text, conditions) + allergen_violations
    has_allergen_violation = any(
        str(v.get("condition", "")).startswith("过敏原:")
        for v in violations
    )
    violated_conditions = sorted({v["condition"] for v in violations})
    if not violations:
        notices = audit_allergen_advisories(
            answer_text,
            allergens,
            use_optional=True,
        )
        notice_warning = ""
        if notices:
            notice_warning = "⚠️ 过敏原提示：" + "；".join(
                f"{v['condition']}需确认「{v['keyword']}」是否含致敏成分"
                for v in notices
            )
        return {"verify_status": "ok", "verify_attempts": attempts + 1,
                "verify_warning": notice_warning,
                "verify_violated": previous_violations}
    # 工具预算已经耗尽时，过敏原不能再打回空转，直接阻断并给出确定性替代。
    if has_allergen_violation and state.get("tool_budget_exhausted"):
        return {"verify_status": "blocked", "verify_warning": "",
                "verify_violated": sorted(
                    set(previous_violations) | set(violated_conditions)
                )}
    # 过敏原达到重试上限后必须 blocked；慢病仍保持既有 degraded 语义。
    if has_allergen_violation and attempts >= MAX_VERIFY:
        return {"verify_status": "blocked", "verify_warning": "",
                "verify_violated": sorted(
                    set(previous_violations) | set(violated_conditions)
                )}
    if state.get("tool_budget_exhausted"):
        warn = "健康护栏提示：工具预算已耗尽，本轮先按已有信息保守收口；仍需注意——" + "；".join(
            f"{v['condition']}忌{v['keyword']}" for v in violations
        )
        return {"verify_status": "degraded", "verify_warning": warn,
                "verify_violated": sorted(set(previous_violations) | set(violated_conditions))}
    if attempts < MAX_VERIFY:
        feedback_text = describe(violations)
        if has_allergen_violation:
            feedback_text += (
                "\n过敏原是绝对硬约束，必须完全避开，包括调料与隐含来源"
                "（如酱油含小麦、蛋黄酱含蛋）；请换一道完全不含该成分的菜。"
            )
        retry_instruction = (
            "请保持候选清单格式：只输出编号候选菜名和简短理由，替换违规候选，"
            "不要输出完整做法、步骤、调料或卡片。"
            if is_candidate_turn
            else (
                # 安全问答轮即使被判违规，也必须继续回答同一个疑问；
                # 逼模型「换一道菜」会把用户的问题替换成无关菜谱（实测 D1）。
                "请重新直接回答用户的饮食安全疑问：先给明确结论（能不能），"
                "再给可执行的替代方案；不要输出菜谱卡片、步骤或 JSON。"
                if safety_retry_turn
                else "请换用符合该人群膳食原则的食材与调料，保持菜谱可执行，只输出一道菜。"
            )
        )
        feedback = HumanMessage(content=(
            "[健康护栏审核] 你给出的方案违反了以下硬禁忌，必须重新生成一道合规的菜：\n"
            + feedback_text
            + "\n" + retry_instruction
        ))
        # 打回重生成时补回一次取证额度：否则余额为 0 的重试会直接落兜底句，
        # 用户看到的好答案被替换成「这次没查到足够具体的做法」。
        retry_patch = _retry_tool_slot_patch(state)
        return {"verify_status": "retry", "verify_attempts": attempts + 1,
                "verify_violated": sorted(set(previous_violations) | set(violated_conditions)),
                "messages": [feedback], **retry_patch}
    # 已达上限仍不通过：放行但附安全警示，绝不静默放行
    warn = "⚠️ 健康护栏提示：本方案经多次重生成仍含需注意项——" + "；".join(
        f"{v['condition']}忌{v['keyword']}" for v in violations
    )
    return {"verify_status": "degraded", "verify_warning": warn,
            "verify_violated": sorted(set(previous_violations) | set(violated_conditions))}


@trace_node("allergen_block")
def allergen_block_node(state: ChefState):
    """过敏原连续命中：不调 LLM、不输出违规卡片，只给确定性安全替代方向。"""
    messages = state.get("messages", [])
    target_note = _selected_target_note(messages)
    labels = []
    for condition in state.get("verify_violated") or []:
        text = str(condition)
        if text.startswith("过敏原:"):
            label = text.split(":", 1)[1].strip()
            if label and label not in labels:
                labels.append(label)
    safe = suggest_safe_dishes(
        _allergens_for_audit(state.get("session_id")),
        k=3,
        use_optional=True,
    )
    subject = "、".join(labels) if labels else "相关过敏原"
    if labels:
        tip = (
            f"{target_note}家庭画像中已确认{subject}过敏，{subject}是硬约束，"
            "因此不能加入这道菜，"
            f"也不能推荐含{subject}的做法。以下是不含该成分的替代方向："
            + "、".join(safe)
            + "。请挑一个，我再给完整做法。"
        )
    else:
        tip = (
            f"抱歉，在{subject}过敏的前提下，这道菜我不能推荐。"
            "以下是不含该成分的替代方向："
            + "、".join(safe)
            + "。请挑一个，我再给完整做法。"
        )
    if len(safe) < 3:
        tip += (
            "当前成员的过敏原限制下，安全可共用的家常菜较少；"
            "其他无该过敏原的成员仍可按原菜就餐，相关成员单独替换并避免交叉接触。"
        )
    violated = state.get("verify_violated") or []
    guardrails = [
        item.model_dump()
        for item in _build_guardrails(
            _latest_user_text(state.get("messages", [])),
            "blocked",
            violated,
            session_id=state.get("session_id"),
        )
    ]
    # 仍然输出结构化 payload（recipes 为空数组）：
    # 前端只在拿到 ChefAnswer 结构时才渲染右栏『健康链』，若这里只回纯文本，
    # 就会变成"拦是拦住了，但没有任何可见证据"——答辩时拿不出来。
    # recipes 给空数组：App.tsx 用 Array.isArray(recipes) 判定，空数组不会渲染卡片。
    payload = {
        "opening": tip,
        "recipes": [],
        "image_url": None,
        "image_ai_generated": False,
        "image_requested": False,
        "image_note": "",
        "chef_tip": "",
        # 阻断轮同样标依据：本轮确实检索过权威条目就带上（只给文件+章节）。
        "sources": _kb_source_payload(messages),
        "health_lights": [],
        "guardrails": guardrails,
    }
    return {
        "messages": [AIMessage(content=json.dumps(payload, ensure_ascii=False))],
        "verify_warning": tip,
        "verify_status": "blocked",
    }


@trace_node("profile_memory")
def profile_memory_node(state: ChefState):
    """结构化回答后提取画像候选；默认关闭时不产生任何状态变化。"""
    if not PROFILE_MEMORY_ENABLED:
        return {}
    text = _latest_user_text(state.get("messages", []))
    if not text:
        return {}
    try:
        from storage.memory_candidates import extract_candidates, remember_candidates

        family = {}
        path = _profile_path()
        if path.exists():
            family = json.loads(path.read_text(encoding="utf-8"))
        candidates = extract_candidates(
            text,
            family.get("members") or [],
            active_member_id=str(family.get("active_id") or ""),
        )
        candidates = remember_candidates(candidates)
    except Exception:
        return {}
    if not candidates:
        return {}
    candidate = candidates[0]
    member = candidate.get("member") or "这位家人"
    value = candidate.get("value") or "这条饮食限制"
    message = (
        f"我记下了：{member}{value}。要加入{member}的长期饮食画像吗？"
        "你可以确认、仅本次记住或永久不记录。"
    )
    return {"messages": [AIMessage(content=message)]}


def verify_route(state: ChefState) -> str:
    """根据审计状态路由：ok/degraded → 结构化收尾；retry → 回到思考节点。
    P3 性能二段：仅当用户明确是非菜品查询（闲聊/纯健康问答）时才直通 END，
    跳过 structure LLM —— 任何可能输出菜品卡片（含图片）的轮次都保留结构化流程。"""
    status = state.get("verify_status", "ok")
    # 安全相关的两个终态优先返回，不被下面的追问短路影响（retry 必须回炉重生成）
    if status in ("blocked", "retry"):
        return status
    # 候选后的纯确认轮必须继续走到 structure_answer：那里仅重登记候选锚点，
    # 不生成卡片；若在这里按普通短句直通 END，下一轮序号锚点会丢失。
    if _is_candidate_ack_without_pick(state.get("messages", [])):
        return status
    latest_text = _current_request_text(
        _latest_user_text(state.get("messages", []), strip_internal=False)
    )
    if (
        is_non_recipe_information_request(latest_text)
        and not _is_recipe_detail_followup(
            state.get("messages", []), latest_text
        )
        and not _is_dish_pick_turn(state.get("messages", []))
    ):
        return "plain"
    if state.get("tool_budget_exhausted") and not (
        _is_dish_pick_turn(state.get("messages", []))
        or _wants_recipe_images(state.get("messages", []))
    ):
        # 预算耗尽就收口，是为了避免"继续空转"；但如果本轮用户已经明确选定了一道菜，
        # 直接走纯文本会让卡片和配图一起消失（实测「2吧…父亲高血压，可以吃这个吗」）。
        # 同一原则也适用于直接点名新菜（实测「油炸花生米怎么做」）：配图门已放行，
        # 预算耗尽只该停止继续调工具，不能把已授权的结构化卡片和配图一起跳过。
        # 选定轮保留结构化出卡：结构化链仍在卡片输出前做过敏原复核，
        # 且 verify_answer_node 对 blocked + 预算耗尽的硬阻断不受影响。
        return "plain"
    # 追问轮次直接收口：整段以问句收尾且没有菜谱结构时，不进结构化。
    # 否则「先问用户想法」的那一轮仍会附带一张卡片，与追问语义自相矛盾。
    last_ai = ""
    for m in reversed(state.get("messages", [])):
        if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
            last_ai = str(m.content).strip()
            break
    if (
        _is_ask_turn(last_ai)
        and not _is_recipe_detail_followup(
            state.get("messages", []), latest_text
        )
        and not _taste_adjustment_followup_target(state.get("messages", []))
    ):
        return "plain"
    if status == "ok":
        has_tool_result = any(
            isinstance(m, ToolMessage) for m in state.get("messages", [])[-8:]
        )
        if not has_tool_result:
            txt = _latest_user_text(state.get("messages", []))
            if txt and not _is_dining_scene(txt) and "菜" not in txt and "做" not in txt and "吃" not in txt:
                return "plain"
    return status


def _is_followup_only(text: str) -> bool:
    """健康问答追问态：整段以问句收尾且没有菜谱结构时才跳过出菜。"""
    text = str(text or "").strip()
    if not text or not text.endswith(("？", "?")):
        return False
    recipe_markers = (
        "食材", "做法", "步骤", "调料", "菜谱", "配料", "食用油",
        "盐", "克", "毫升", "分钟", "翻炒", "蒸", "煮", "炸", "烤",
        "食材清单", "用量", "第一道", "第二道",
    )
    return not any(marker in text for marker in recipe_markers)


# 带征询语气的收尾问句（「你想吃清淡的还是重口的？」）
_ASK_TURN_MARKERS = (
    "吗", "呢", "还是", "要不要", "哪个", "哪道", "哪一", "是不是",
    "确认一下", "告诉我", "请说", "你希望",
)
# 出现 ≥2 个强菜谱结构词，说明这段是在出菜而不只是提问（不能因为结尾问句就丢掉卡片）
_STRONG_RECIPE_MARKERS = ("步骤", "第一道", "食材清单", "调料表", "用量", "克", "毫升", "分钟")


def _looks_like_ask_turn(text: str) -> bool:
    """整段是一条追问（而非出菜）：以问句收尾、带征询语气、且没有成形的菜谱结构。"""
    raw = str(text or "").strip()
    if not raw or len(raw) > 200:
        return False
    if not raw.endswith(("？", "?")):
        return False
    if not any(marker in raw for marker in _ASK_TURN_MARKERS):
        return False
    if sum(1 for marker in _STRONG_RECIPE_MARKERS if marker in raw) >= 2:
        return False
    return True


def _is_ask_turn(text: str) -> bool:
    """追问轮次判定：先复用已有的健康问答追问口径，再补一层征询语气判定。"""
    return _is_followup_only(text) or _looks_like_ask_turn(text)


# 「约束纠正」轮的词汇锚点：成员称呼 / 慢病与目标 / 否定纠正语气。
_CORRECTION_MEMBER_WORDS = (
    "爸爸", "父亲", "妈妈", "母亲", "老婆", "妻子", "老公", "丈夫", "儿子", "女儿",
    "弟弟", "哥哥", "姐姐", "妹妹", "爷爷", "奶奶", "外公", "外婆", "姥姥", "姥爷",
    "家人", "全家", "孩子", "老人",
)
_CORRECTION_CONDITION_WORDS = (
    "高血压", "血压", "糖尿病", "血糖", "减重", "减肥", "超重", "肥胖", "体重",
    "痛风", "高尿酸", "高血脂", "高脂血症", "血脂", "肾病", "慢性肾脏病",
    "过敏", "忌口", "孕期",
)
_CORRECTION_NEGATION_WORDS = (
    "并没有", "没有说", "没说", "不需要", "不用", "不必", "别管", "忽略",
    "无关", "搞错", "弄错", "记错", "不是",
)
# 出现这些就说明用户是在「动这道菜」（换菜 / 没胃口），不能再当纠正轮吞掉
_CORRECTION_CHANGE_WORDS = (
    "换一道", "换一个", "换别的", "换成", "改成", "做成", "换做", "改做", "改为",
    "变成", "没胃口", "不想吃", "吃不下", "胃口", "食欲",
)


def _is_constraint_correction_turn(text: str) -> bool:
    """声明式「约束纠正」轮：用户在纠正成员/慢病的归属或有效性，而不是在改菜。

    典型说法：「并没有说弟弟要减重，这个到后面都不需要管」。这类话如果放行到结构化链，
    模型会顺手把菜名换掉、再弹一张卡片（实测「柠檬酸香清蒸鲈鱼」被改成「柠檬米醋蒸鲈鱼」），
    与用户意图完全相反。

    判定必须确定性，且要求「既命中纠正语气、又确认没有在动这道菜」：
      1. 命中否定/纠正语气词；
      2. 命中成员称呼或慢病/目标词（说明话题是「谁有什么约束」，不是「菜怎么做」）；
      3. 不含任何菜品调整词、换菜词、没胃口类词，也没有点名具体菜；
      4. 不是问句（问句走 _is_ask_turn 那条路，由它决定是否保留正文）。
    这条判定只用来「不出新卡片」，不改动任何安全审计结论；代价是纠正轮仍会跑一次
    结构化链（因为它必须排在过敏原复核之后），但结果被丢弃、不会呈现给用户。
    """
    raw = str(text or "").strip()
    if not raw or len(raw) > 80:
        return False
    if raw.endswith(("？", "?")):
        return False
    if not any(word in raw for word in _CORRECTION_NEGATION_WORDS):
        return False
    if not (
        any(word in raw for word in _CORRECTION_MEMBER_WORDS)
        or any(word in raw for word in _CORRECTION_CONDITION_WORDS)
    ):
        return False
    # 先把纠正语气词本身洗掉再查调整词，否则「别管这条」会被「别」误判成在调味道
    scrubbed = raw
    for word in _CORRECTION_NEGATION_WORDS:
        scrubbed = scrubbed.replace(word, "")
    # 宁可多出一张卡，也不吞掉真实的改菜意图
    if any(word in scrubbed for word in _DISH_ADJUST_WORDS):
        return False
    if any(word in scrubbed for word in _CORRECTION_CHANGE_WORDS):
        return False
    return True

# --------------------------------------------------------------------------- #
#  4.5 充分性门控（AgentMental 范式）：高风险健康决策前先确定性判断画像是否足够
# --------------------------------------------------------------------------- #
def _parse_declared_conditions(user_text: str) -> set:
    """解析画像中的已声明病种，兼容旧字段和当前渲染格式。"""
    import re
    segments = []
    for pattern in (
        r"慢病约束\s*=\s*([^】\n]*)",
        r"慢病情况\s*[：:]\s*([^\n（(]*)",
        r"当前目标\s*[：:]\s*([^\n（(]*)",
        r"医嘱[/／]?长期硬限制\s*[：:]\s*([^\n（(]*)",
    ):
        segments.extend(match.group(1) for match in re.finditer(pattern, user_text))
    declared = set()
    for segment in segments:
        segment = segment.strip()
        if segment in ("无", "无特殊", ""):
            continue
        declared.update(
            item.strip()
            for item in re.split(r"[、,，;；\s]+", segment)
            if item.strip()
        )
    return declared

def _declared_covers(condition: str, declared: set) -> bool:
    """模糊匹配：declared 任一包含/被包含于 condition 即视为已声明该约束。"""
    return any(condition in d or d in condition for d in declared)


def _hit_without_negation(text: str, phrase: str) -> bool:
    """子串命中且命中点前两字内无否定词（『没有高血压』不算声明高血压）。"""
    start = 0
    while True:
        idx = text.find(phrase, start)
        if idx < 0:
            return False
        if not any(neg in text[max(0, idx - 2) : idx] for neg in ("没", "无", "非")):
            return True
        start = idx + 1


def _self_declared_conditions(user_text: str) -> set:
    """解析用户本轮原话中明确自述的健康状态，避免重复追问。

    放宽版：子串别名匹配（『妊娠期高血压』『孕18周』『尿酸偏高』等自然措辞
    都算声明），并排除否定前缀——『我没有高血压』不会被误认成声明。"""
    text = (user_text or "").replace(" ", "")
    out = set()
    pairs = [
        ("高血压", ("高血压", "血压高", "血压偏高", "血压有点高", "血压不稳")),
        ("糖尿病", ("糖尿病", "血糖高", "血糖偏高", "血糖有点高", "血糖不稳")),
        ("高脂血症", ("高血脂", "高脂血症", "血脂高", "血脂偏高", "甘油三酯")),
        ("痛风", ("痛风", "尿酸高", "尿酸偏高", "高嘌呤")),
        ("慢性肾脏病", ("肾病", "肾脏不好", "肾功能", "肾炎", "肌酐高")),
        ("肥胖", ("肥胖", "体重超标", "超重", "减肥", "减重", "控制体重")),
        ("孕期", ("怀孕", "孕妇", "孕期", "妊娠", "孕")),
    ]
    for condition, phrases in pairs:
        if condition == "孕期" and "备孕" in text:
            continue  # 备孕≠孕期，营养建议差异大，宁可追问
        if any(_hit_without_negation(text, phrase) for phrase in phrases):
            out.add(condition)
    return out


def _is_vague_health_input(user_text: str) -> bool:
    """识别只有泛化不适、没有具体症状的健康求餐表达。"""
    text = (user_text or "").replace(" ", "")
    vague_markers = ("不舒服", "难受", "身体不适", "不太舒服", "不大舒服")
    if not any(marker in text for marker in vague_markers):
        return False
    specific_markers = (
        "胃", "肚", "腹", "恶心", "呕吐", "腹泻", "拉肚", "便秘", "发烧",
        "发热", "咳嗽", "喉咙", "咽痛", "头痛", "胸痛", "气短", "过敏",
        "疼", "痛", "眩晕", "头晕", "血压", "血糖", "尿酸",
    )
    return not any(marker in text for marker in specific_markers)


# --------------------------------------------------------------------------- #
#  充分性门控
#
#  充分性门统一判断健康信息是否足够：用户只说明病种但没有说明控制强度时，
#  先追问一次；症状、过敏原、人群和备孕信息也走同一套确定性判断。
# --------------------------------------------------------------------------- #

_MISSING_INTENSITY = "该病种的控制强度（是否遵医嘱严格限制）"
_MISSING_PREGNANCY = "是否已怀孕及孕周"

# 控制强度标记：命中即认为用户已说明「严到什么程度」，不再追问。
_INTENSITY_MARKERS = (
    "严格控", "严格限", "严格控制", "遵医嘱", "医生要求", "医生让", "医生说要",
    "医嘱", "控制稳定", "控制得还行", "控制得不错", "控制得不好",
    "吃药", "在服药", "按时服药", "平时注意", "日常注意", "注意一点", "注意点",
    "清淡就行", "少盐就行",
    # 「我想减肥」这类是用户自己给的目标口径 —— 等于已经说了控制到什么程度，
    # 再问「医生有没有要求严格限制」就很怪。
    "减肥", "减重", "瘦身", "控制体重", "想瘦", "控糖", "控盐", "控油",
    "低盐饮食", "低脂饮食", "低糖饮食",
)


def _self_declared_intensity(user_text: str) -> bool:
    """用户本轮是否已说明该病种的控制强度（严格遵医嘱 vs 平时注意）。"""
    text = (user_text or "").replace(" ", "")
    return any(marker in text for marker in _INTENSITY_MARKERS)


def _active_profile_restricts() -> list:
    """读 data/profile.json 激活成员的医嘱/长期硬限制。

    与 _active_profile_conditions 同源同兜底：档案里已写明硬限制，
    说明强度已明确，门控不再追问。读不到档案一律返回空。
    """
    try:
        path = _profile_path()
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return []
        members = raw.get("members")
        if isinstance(members, list) and members:
            active_id = raw.get("active_id")
            member = next((m for m in members if m.get("id") == active_id), members[0])
            profile = member.get("profile") or {}
        else:  # v1 平铺档案
            profile = raw
        return [
            str(value).strip()
            for value in (profile.get("restricts") or [])
            if str(value or "").strip()
        ]
    except Exception:
        return []


_MEMBER_PERSON_MARKERS = (
    # 与 _CORRECTION_MEMBER_WORDS 同源扩充：父亲/母亲/妻子/丈夫/儿子/女儿/家人/全家
    # 同样属于"点名了成员"。实测缺了它们，成员错配缺陷会在「我父亲高血压」上原样复现。
    "爸爸", "父亲", "妈妈", "母亲", "儿子", "女儿", "孩子", "小孩", "宝宝",
    "妻子", "老婆", "媳妇", "丈夫", "老公", "弟弟", "哥哥", "姐姐", "妹妹",
    "爷爷", "奶奶", "外公", "外婆", "姥姥", "姥爷",
    "岳父", "岳母", "公公", "婆婆", "家人", "全家", "家里人", "老人", "长辈",
    # 「我爸」「我妈」这类口语变体不含「爸爸/妈妈」字样，必须单列
    "我爸", "我妈",
)


def _mentions_specific_member(user_text: str) -> bool:
    """本轮是否点名了某个具体家庭成员（区别于默认的「本人/激活成员」）。"""
    text = (user_text or "").replace(" ", "")
    return any(marker in text for marker in _MEMBER_PERSON_MARKERS)


def _intensity_sufficient(user_text: str) -> bool:
    """控制强度是否已经明确：本轮说过，或档案里有医嘱硬限制。

    ⚠️ 档案 restricts 属于**激活成员**。当本轮点名了具体成员（「我爸高血压」）时，
    不能拿激活成员的硬限制去顶——成员错配会让追问静默失效（实测：激活成员填了
    医嘱后，问「我爸高血压」就完全不再追问）。精确的「成员×约束」绑定留到批 2；
    这里点名了成员就一律视为「未说清」。
    """
    if _self_declared_intensity(user_text):
        return True
    if _mentions_specific_member(user_text):
        return False
    return bool(_active_profile_restricts())


# —— B2：症状严重程度 / 过敏原强度；B3：人群维度 / 追问去重 ——
_MISSING_SYMPTOM_SEVERITY = "症状的持续时间与严重程度"
_MISSING_ALLERGEN_SEVERITY = "过敏反应的严重程度"
_MISSING_POPULATION = "这位家人的年龄（或是否孕期 / 哺乳期）"

# 具体主诉词：命中说明用户已说「哪儿不舒服」，但未必说了「到什么程度」。
# 一律用多字词，避免单字（如裸「胃」）在「开胃」「胃口好」上误触发。
_SYMPTOM_MARKERS = (
    "胃疼", "胃痛", "胃胀", "胃不舒服", "胃不好", "反酸", "烧心", "腹胀", "腹泻",
    "拉肚", "便秘", "恶心", "呕吐", "食欲不振", "没胃口", "不想吃",
    "肚子胀", "肚子疼", "肚子痛", "肚子不", "腹部不适", "胃部不适",
    "头痛", "偏头痛", "头晕", "眩晕", "失眠", "睡不好", "睡不着",
    "咳嗽", "喉咙痛", "咽痛", "嗓子疼", "嗓子不",
    "皮疹", "起疹", "湿疹", "发痒", "瘙痒", "乏力", "水肿", "抽筋", "口苦", "腰疼", "腰痛",
)

# 程度 / 时长词：命中即认为「严重程度」已说明，不再追问。
# 末段是过敏式的程度表达（「吃一点就」「一碰就」），对症状同样成立，
# 否则「吃一点就起疹」会被症状维度当成「没说程度」而误追问。
_SEVERITY_MARKERS = (
    "偶尔", "有时候", "有时", "经常", "老是", "总是", "一直", "反复", "好几天", "几天了",
    "一周", "半个月", "一个月", "很久", "轻微", "有点", "稍微", "一点点", "厉害",
    "严重", "很严重", "不严重", "刚出现", "昨天开始", "今天开始", "从上周", "近来",
    "吃一点就", "碰一点", "一碰", "沾一点", "闻一下", "微量",
)

# 过敏反应程度词。
_ALLERGEN_SEVERITY_MARKERS = (
    "严重", "很严重", "不严重", "轻微", "一点点", "有点", "嘴痒", "嘴唇", "起疹", "皮疹",
    "喉头", "呼吸困难", "休克", "急救", "送医", "碰一点", "吃一点就", "闻一下", "微量",
)

# 人群维度：提到这些人称、又没给年龄/生理状态、且对象不在档案成员里时才追问。
_POPULATION_RULES = (
    ("老年人", ("老人", "爷爷", "奶奶", "外公", "外婆", "姥爷", "姥姥", "长辈", "年纪大")),
    ("儿童", ("小孩", "孩子", "儿童", "小朋友", "宝宝", "幼儿", "婴儿", "娃")),
    ("哺乳期", ("哺乳", "喂奶", "母乳", "产妇", "产后")),
)

# 求餐意图词：充分性门的所有补问都只在「本轮确实在问吃什么」时触发。
# 否则「我同事说他高血压挺久了」这类闲聊会被当成自我声明而误追问。
_MEAL_REQUEST_MARKERS = (
    "吃什么", "吃啥", "吃点什么", "吃哪", "怎么吃", "能吃什么", "想吃", "该吃",
    "做饭", "做菜", "做顿", "下厨", "菜谱", "食谱", "菜单", "推荐", "搭配", "点餐",
    "早餐", "午餐", "晚餐", "早饭", "午饭", "晚饭", "夜宵", "加餐", "外卖",
    "吃点啥", "吃啥好", "吃点什么",
    "饭", "菜",
)

# 非家庭第三人称标记：这类句子里的病名是「转述别人」，不是自己要吃，
# 第三人称一律不补问（保守放行 —— 少问一次的安全代价远小于误追问）。
_THIRD_PARTY_MARKERS = ("同事", "邻居", "网友", "别人", "他说", "她说", "听说", "朋友圈")


def _looks_like_meal_request(user_text: str) -> bool:
    """本轮是否在求餐（问吃什么 / 要菜谱 / 要做饭）。"""
    text = (user_text or "").replace(" ", "")
    return any(marker in text for marker in _MEAL_REQUEST_MARKERS)


def _is_third_party_talk(user_text: str) -> bool:
    """本轮是否在转述别人的健康状况（而非自己/同餐家人）。"""
    text = (user_text or "").replace(" ", "")
    return any(marker in text for marker in _THIRD_PARTY_MARKERS)


def _has_specific_symptom(user_text: str) -> bool:
    """本轮是否说了「具体哪儿不舒服」（区别于只有泛化不适）。"""
    text = (user_text or "").replace(" ", "")
    return any(marker in text for marker in _SYMPTOM_MARKERS)


def _severity_declared(user_text: str) -> bool:
    """症状的持续时长 / 严重程度是否已说明。"""
    text = (user_text or "").replace(" ", "")
    return any(marker in text for marker in _SEVERITY_MARKERS)


def _allergen_severity_declared(user_text: str) -> bool:
    """过敏反应的程度是否已说明。"""
    text = (user_text or "").replace(" ", "")
    return any(marker in text for marker in _ALLERGEN_SEVERITY_MARKERS)


def _self_declared_allergens(user_text: str) -> set:
    """本轮原话里出现的过敏原编码集合（复用既有确定性词表，不另建一份）。

    这里是在**自由文本**上探测，未归一时底层会发 UserWarning（属预期噪声），
    故仅在本函数内静音，不影响其它调用方的告警。
    """
    try:
        import warnings

        from domain.allergen_rules import normalize_allergens

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return {str(code) for code in normalize_allergens(user_text or "")}
    except Exception:
        return set()


def _active_profile_allergen_codes() -> set:
    """档案激活成员已记录的过敏原，**归一成编码**后再比较。

    为什么必须归一：档案里存的是原文（「花生」「芒果」），本轮原话给的是编码，
    直接比字符串会把「已在档」误判成「新过敏原」而重复追问。

    为什么不干脆用「档案里有没有任何过敏原」一刀切：那样「档案里有花生、
    本轮改说对虾过敏」这种真正的新信息就永远问不到。
    """
    try:
        values = [
            str(item).strip()
            for item in (_active_profile_allergens() or [])
            if str(item or "").strip()
        ]
    except Exception:
        return set()
    if not values:
        return set()
    try:
        import warnings

        from domain.allergen_rules import normalize_allergens

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return {
                str(code) for code in normalize_allergens("、".join(sorted(set(values))))
            }
    except Exception:
        return set()


def _family_member_names() -> set:
    """档案里已建过档的成员名**及其常见变体**（「妈妈」→「我妈」「我妈妈」）。

    人群维度必须避开它们，避免对已在档成员重复索取年龄/生理状态。
    变体是必需的：用户说「我妈」，档案里存的是「妈妈」，直接子串匹配会漏，
    结果对已经建过档的妈妈再问一次。
    """
    names = set()
    for item in _family_members():
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        names.add(name)
        names.add("我" + name)
        if len(name) > 1:
            names.add("我" + name[-1])
    return names


def _population_detail_declared(user_text: str) -> bool:
    """年龄 / 孕周 / 哺乳状态 / 婴幼儿阶段是否已给出。"""
    text = (user_text or "").replace(" ", "")
    if re.search(r"\d+\s*(岁|周岁)", text):
        return True
    if re.search(r"孕\s*\d+\s*周", text):
        return True
    return any(
        word in text
        for word in (
            "哺乳", "喂奶", "母乳", "产后", "产妇", "刚出生",
            # 说「婴儿」「辅食」本身已经定位了人群阶段，再问「多大年纪」很别扭。
            "婴儿", "新生儿", "宝宝", "幼儿", "月龄", "辅食",
        )
    )


def _needs_population_clarify(user_text: str) -> bool:
    """是否需要就「这位家人的年龄 / 生理状态」追问一次。"""
    text = (user_text or "").replace(" ", "")
    if not text:
        return False
    names = _family_member_names()
    for _label, markers in _POPULATION_RULES:
        if not any(marker in text for marker in markers):
            continue
        if _population_detail_declared(text):
            continue
        if any(name and name in text for name in names):
            continue  # 已在档成员：档案里已有其约束，不再重复索取
        return True
    return False


def _has_negated_only_discomfort(user_text: str) -> bool:
    """用户是否**明确否认**了不适（「我没有不舒服」）。

    这是基线的既有误报：`_is_vague_health_input` 只做了子串命中，没做否定排除，
    于是「我没有不舒服，就想吃点清淡的」会被当成泛化不适而追问。
    充分性门统一生效。
    """
    text = (user_text or "").replace(" ", "")
    markers = ("不舒服", "难受", "身体不适", "不太舒服", "不大舒服")
    present = [m for m in markers if m in text]
    if not present:
        return False
    return not any(_hit_without_negation(text, m) for m in present)


def _sufficiency_extra_ask(user_text: str, asked: set):
    """充分性门统一追问出口：返回需要补问的一项，或 None。

    优先级：备孕（人群安全差异最大）> 病种控制强度 > 症状严重程度 > 过敏原强度 > 人群。
    `asked` = 上一轮问过的键（取自 state["profile_missing"]），同键不连问第二次，
    这就是追问去重 —— 不需要新增 state 字段，也不动图结构。
    """

    def _ask(key: str):
        if key in asked:
            return None
        return {"profile_ready": False, "profile_missing": [key]}

    text = user_text or ""
    tight = text.replace(" ", "")

    # 求餐守门：本轮不是在问吃什么，就不补问——避免把闲聊里的病名当成自我声明。
    # 第三人称守门：转述同事/邻居的病名不补问——那不是这顿饭的约束。
    if not _looks_like_meal_request(text) or _is_third_party_talk(text):
        return None

    # 备孕放最前：它走的是「人群安全」而不是「病种约束」，且原实现嵌在
    # if not conditions 里，只要同时说了病种就永远走不到（已修）。
    if "备孕" in tight:
        hit = _ask(_MISSING_PREGNANCY)
        if hit:
            return hit

    # 病种控制强度：孕期（含仅孕期一个条件）走的是人群口径，
    # 不该去问「医生有没有要求严格控盐」这种慢病式强度。
    text_conditions = set(detect_conditions(text))
    if (
        text_conditions
        and not text_conditions <= {"孕期"}
        and not _intensity_sufficient(text)
    ):
        hit = _ask(_MISSING_INTENSITY)
        if hit:
            return hit

    if _has_specific_symptom(text) and not _severity_declared(text):
        hit = _ask(_MISSING_SYMPTOM_SEVERITY)
        if hit:
            return hit

    # 过敏原强度：只对「本轮新提到、档案里还没记过」的过敏原追问一次。
    # 已在档的过敏原按既有 L3 全禁处理，再问只是打扰（安全收益为 0）。
    if (
        "过敏" in tight
        and _hit_without_negation(tight, "过敏")
        and _self_declared_allergens(text) - _active_profile_allergen_codes()
        and not _allergen_severity_declared(text)
    ):
        hit = _ask(_MISSING_ALLERGEN_SEVERITY)
        if hit:
            return hit

    if _needs_population_clarify(text):
        hit = _ask(_MISSING_POPULATION)
        if hit:
            return hit

    return None


def _is_ambiguous_health_safety_question(user_text: str, messages) -> bool:
    """拦截没有明确食物对象的「能不能吃」安全判断请求。"""
    text = (user_text or "").replace(" ", "")
    if not text:
        return False
    safety_question = re.search(
        r"(能不能吃|可不可以吃|可以吃|能吃|该不该吃|要不要吃|适合吃)"
        r"[^，。！？\n]{0,8}[吗么嘛？?]"
        r"|适合(?!吃什么|吃啥|吃哪)[^，。！？\n]{0,10}吃"
        r"[^，。！？\n]{0,4}[吗么嘛？?]"
        r"|吃(?!什么|啥|哪|哪些)[^，。！？\n]{0,10}"
        r"(行不行|行吗|可以吗|吗|么|嘛)",
        text,
    )
    if not safety_question:
        return False
    contextual_refs = ("这个", "这道", "这道菜", "它", "那道", "那份", "这份")
    if not any(ref in text for ref in contextual_refs) and _mentions_food_ingredients(text):
        return False
    # 已经交付单菜卡片时，「这个能吃吗」是有效的卡片后追问。
    if _latest_delivered_card_recipe_names(messages):
        return False
    # 只有候选清单或没有菜品锚点时，指代对象仍不明确，必须先澄清。
    return True


_ASK_TURN_LEADS = ("想确认一下", "想先确认一下", "请补充", "请告诉我")


def _last_ai_asked(messages) -> bool:
    """最近一条 AI 消息是不是本系统的追问话术（用于「问过 → 答过 → 不再问」去重）。

    只依赖消息内容，不依赖 `state["profile_missing"]` 跨轮传递 —— 后者在真实链路
    里可能丢失，会让用户的自然回答（「就是少吃点咸的」）被原样再问一次。

    判定 = 固定引导语开头 + 短文本（追问话术都很短）。**不能用问号结尾当必要条件**：
    实测本系统 7 条追问话术里，孕周 / 症状 / 过敏 / 人群四条都以「。」结尾。
    """
    for m in reversed(messages):
        if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
            content = str(m.content or "").strip()
            if not content:
                continue
            return content.startswith(_ASK_TURN_LEADS) and len(content) <= 120
    return False


@trace_node("profile_gate")
def profile_gate_node(state: ChefState):
    """充分性门控节点：进入 chef_think 前，确定性判断健康画像是否足够。

    - 信息足够：profile_ready=True，放行到 chef_think；
    - 信息不足：profile_ready=False，路由到 ask_user，不进入工具检索/审计。
    """

    messages = state["messages"]
    user_text = _latest_user_text(messages)
    conditions = _merged_conditions(user_text)
    # 追问去重：上一轮已问过的键不再连问（state["profile_missing"] 就是上一轮的结果）。
    asked = set(state.get("profile_missing") or [])
    # 自然回答去重（不依赖 profile_missing 跨轮传递）：只有本轮没有独立意图时，
    # 才把最近一条系统追问视为本轮正在回答。新的求餐/知识/菜品问题必须继续经过
    # 下面的充分性检查，不能被「上一轮问过」无条件短路。
    if _last_ai_asked(messages) and not _has_independent_intent_after_ask(
        user_text, messages
    ):
        return {"profile_ready": True, "profile_missing": []}
    if _is_vague_health_input(user_text):
        # 用户明确否认不适（「我没有不舒服」）时不再追问。
        if not _has_negated_only_discomfort(user_text):
            return {
                "profile_ready": False,
                "profile_missing": ["具体症状、持续时间及严重程度"],
            }
    if _is_ambiguous_health_safety_question(user_text, messages):
        if _recent_candidates(messages):
            missing = "具体是哪一道菜（或回复候选序号）"
        else:
            missing = "具体食物或做法"
        return {
            "profile_ready": False,
            "profile_missing": [missing],
        }
    if not conditions:
        # 无慢病语境时，仍可能因症状 / 过敏原 / 人群 / 备孕而追问一次。
        extra = _sufficiency_extra_ask(user_text, asked)
        if extra:
            return extra
        return {"profile_ready": True, "profile_missing": []}

    declared = (
        _parse_declared_conditions(user_text)
        | _self_declared_conditions(user_text)
        | set(_active_profile_conditions())
    )
    missing = [c for c in conditions if not _declared_covers(c, declared)]
    if missing:
        return {"profile_ready": False, "profile_missing": missing}

    # 病种已声明只代表「知道有这个病」，不代表「说清了要控制到什么程度」；
    # 症状严重程度 / 过敏原严重程度 / 人群同理。同键只问一次，说清即不再问。
    extra = _sufficiency_extra_ask(user_text, asked)
    if extra:
        return extra

    return {"profile_ready": True, "profile_missing": []}


@trace_node("ask_user")
def ask_user_node(state: ChefState):
    """生成一条面向用户的追问，不调用工具、不生成菜谱。"""

    missing = list(state.get("profile_missing") or [])
    if not missing:
        return {
            "messages": [
                AIMessage(content="请补充一下你的健康情况，我才能给出更安全的饮食建议。")
            ]
        }

    # 这些是路由层确定的缺失输入，必须原样问到点上，不能交给模型改写成
    # 泛泛的健康情况追问，否则用户仍不知道下一轮要补什么。
    direct_questions = {
        "具体食物或做法": "请告诉我具体想判断哪种食物或做法，例如菜名、主要食材或烹饪方式。",
        "具体是哪一道菜（或回复候选序号）": "请告诉我具体是哪一道菜，或直接回复候选序号，我再按对应成员的约束判断。",
        "具体症状、持续时间及严重程度": "请补充具体症状、持续了多久，以及是否有明显或严重表现，我再给你更安全的饮食建议。",
        _MISSING_INTENSITY: "想确认一下：医生有没有给你具体的忌口要求（比如严格控盐、限嘌呤），还是平时注意一下就行？",
        _MISSING_PREGNANCY: "想确认一下：是已经怀孕了吗？如果已怀孕，现在孕几周了？我好按对应的营养要求来安排。",
        _MISSING_SYMPTOM_SEVERITY: "想先确认一下：这个情况持续多久了、是偶尔出现还是经常？严重到什么程度？我好判断要不要先避开某些食材。",
        _MISSING_ALLERGEN_SEVERITY: "想确认一下：这个过敏是轻微不适，还是会出现明显反应（比如起疹、喉头不适）？我会按对应严格程度处理。",
        _MISSING_POPULATION: "想确认一下这位家人的年龄或生理状态（比如多大年纪、是否孕期或哺乳期）？不同人群的食养要求差别很大。",
    }
    if len(missing) == 1 and missing[0] in direct_questions:
        return {"messages": [AIMessage(content=direct_questions[missing[0]])]}

    user_text = _latest_user_text(state["messages"])
    try:
        _ensure_llms()
        response = llm.invoke(
            [
                SystemMessage(
                    content=(
                        "你是小膳管家。当前只负责向用户提出一个简短澄清问题。"
                        "禁止给出菜谱、禁止调用工具、禁止使用工具调用。"
                        "只追问用户健康状态或忌口严格程度，1-2 句话，语气自然。"
                    )
                ),
                HumanMessage(
                    content=(
                        "用户本轮说："
                        + user_text
                        + "\n\n尚未确认的高风险健康约束："
                        + "、".join(missing)
                    )
                ),
            ]
        )
        content = str(response.content).strip()
    except Exception:
        content = ""

    if not content:
        content = "想确认一下，你是否涉及" + "、".join(missing) + "？请简单告诉我你的饮食控制情况。"

    return {"messages": [AIMessage(content=content)]}


def profile_gate_route(state: ChefState) -> str:
    """信息足够走主流程，信息不足走追问节点。"""

    return "ask" if not state.get("profile_ready", True) else "ready"


@trace_node("ingredient_gate")
def ingredient_gate_node(state: ChefState):
    """图片食材确认门：先整理识别草稿，用户确认后才允许候选推荐。

    这条门只处理带视觉上下文的新图片及其后续确认/纠正轮；普通文字食材请求
    不经过它，继续沿原基线直接进入候选清单。
    """
    messages = state.get("messages") or []
    latest_text = _current_request_text(
        _latest_user_text(messages, strip_internal=False)
    )
    has_image, vision_text = _latest_visual_context(messages)
    previous_draft = _normalize_ingredient_draft(state.get("ingredient_draft"))
    previous_active = bool(state.get("ingredient_gate_active"))

    if has_image:
        draft = _extract_ingredient_draft(vision_text)
        return {
            "ingredient_gate_active": True,
            "ingredient_gate_action": "ask",
            "ingredient_draft": draft,
            "ingredient_gate_ready": False,
        }

    if previous_active:
        if _is_ingredient_gate_skip(latest_text):
            return {
                "ingredient_gate_active": False,
                "ingredient_gate_action": "skip",
                "ingredient_draft": previous_draft,
                "ingredient_gate_ready": True,
            }

        if _is_ingredient_gate_correction(latest_text):
            draft, changed = _apply_ingredient_corrections(
                previous_draft,
                latest_text,
            )
            return {
                "ingredient_gate_active": True,
                "ingredient_gate_action": "corrected" if changed else "ask",
                "ingredient_draft": draft,
                "ingredient_gate_ready": False,
            }

        # 图片没识别出食材时，用户直接口述清单（如「鸡蛋、青菜、豆腐」）
        # 也要能落成草稿，不能一直重复追问。
        if not previous_draft:
            supplement = _extract_ingredient_terms(latest_text, allow_bare=False)
            if supplement:
                return {
                    "ingredient_gate_active": True,
                    "ingredient_gate_action": "corrected",
                    "ingredient_draft": supplement,
                    "ingredient_gate_ready": False,
                }

        if _is_ingredient_gate_confirmation(latest_text):
            return {
                "ingredient_gate_active": False,
                "ingredient_gate_action": "confirmed",
                "ingredient_draft": previous_draft,
                "ingredient_gate_ready": True,
            }

        # 确认门未结束前不把问题交给推荐模型，继续展示同一份清单等待确认。
        return {
            "ingredient_gate_active": True,
            "ingredient_gate_action": "ask",
            "ingredient_draft": previous_draft,
            "ingredient_gate_ready": False,
        }

    return {
        "ingredient_gate_active": False,
        "ingredient_gate_action": "",
        "ingredient_gate_ready": False,
    }


@trace_node("ingredient_confirm")
def ingredient_confirm_node(state: ChefState):
    """确定性输出图片食材草稿确认文案，不调用 LLM、不生成菜谱。"""
    draft = _normalize_ingredient_draft(state.get("ingredient_draft"))
    updated = state.get("ingredient_gate_action") == "corrected"
    return {
        "messages": [
            AIMessage(content=_ingredient_confirm_text(draft, updated=updated))
        ]
    }


def ingredient_gate_route(state: ChefState) -> str:
    """确认门开启时先确认，确认完成后再进入健康画像门。"""
    if (
        state.get("ingredient_gate_active")
        and not state.get("ingredient_gate_ready")
    ):
        return "ask"
    return "ready"


# --------------------------------------------------------------------------- #
#  5. 构建图实例（状态图 + 条件边 + 回边 = LangGraph 循环 Agent）
# --------------------------------------------------------------------------- #
# 创建LangGraph图状态机，通过节点流转规则读写、操控全局上下文容器MessagesState
workflow = StateGraph(ChefState)#创建状态图，状态容器扩展为带护栏字段的 ChefState
# 注册两个节点，左是节点名，右是函数
workflow.add_node("chef_think", chef_agent_node)#思考节点自己思考能看图片
workflow.add_node("run_tools", run_tools_node)#工具执行节点
workflow.add_node("condense_history", maybe_condense)# 注册长对话压缩节点
workflow.add_node("verify_answer", verify_answer_node)# 注册健康护栏审计节点
workflow.add_node("structure_answer", structure_answer_node)# 注册结构化回答节点
workflow.add_node("profile_memory", profile_memory_node)# 画像候选后置节点（默认关闭）
workflow.add_node("allergen_block", allergen_block_node)# 过敏原硬拦截：确定性安全文案，不调用 LLM
workflow.add_node("profile_gate", profile_gate_node)# 注册充分性门控节点（AgentMental 范式）
workflow.add_node("ask_user", ask_user_node)# 注册健康画像追问节点
workflow.add_node("tool_budget_finalize", tool_budget_finalize_node)# 工具预算耗尽后的优雅收尾节点
workflow.add_node("ingredient_gate", ingredient_gate_node)# 图片食材确认门
workflow.add_node("ingredient_confirm", ingredient_confirm_node)# 确定性输出食材确认清单
workflow.add_node("requested_dish_gate", requested_dish_gate_node)# 生成前的点名菜过敏原硬拦截
workflow.set_entry_point("condense_history")# 设置入口：先压缩历史
workflow.add_edge("condense_history", "ingredient_gate")#连线长对话压缩→图片食材确认门
workflow.add_conditional_edges(
    "ingredient_gate",
    ingredient_gate_route,
    {
        "ask": "ingredient_confirm",
        "ready": "profile_gate",
    },
)
workflow.add_edge("ingredient_confirm", END)
workflow.add_conditional_edges(
    "profile_gate",
    profile_gate_route,
    {
        "ready": "requested_dish_gate",
        "ask": "ask_user",
    },
)
# 点名菜命中已确认过敏原：在模型开口之前就结束本轮，只给替代方向与护栏。
# 不能让模型先流式输出一整篇替代成品再被事后覆盖，否则前端会「先出一版再换掉」。
workflow.add_conditional_edges(
    "requested_dish_gate",
    requested_dish_gate_route,
    {
        "blocked": END,
        "pass": "chef_think",
    },
)
workflow.add_edge("ask_user", END)#追问结束后本轮结束，等待用户下一轮回答

# 核心循环逻辑：条件边
# 1. LLM 思考完，判断是否要调用工具：要调用→去执行工具；不调用→去健康护栏审计
workflow.add_conditional_edges(#条件分支边
    source="chef_think",#源节点
    path=chef_route_with_tool_budget,# 有工具调用时先过本轮预算
    path_map={#条件分支二选一对应目标节点
        "tools": "run_tools",#有就执行工具
        "verify": "verify_answer",#不调用工具→先过健康护栏审计，再结构化收尾
        "tool_budget_exhausted": "tool_budget_finalize",#工具超预算→普通结论收口
    },
)
workflow.add_edge("tool_budget_finalize", "verify_answer")

# 2. 工具执行完毕，**回流到 LLM 节点再次思考（实现循环！）**
#    这就是 create_agent 做不到的闭环：工具结果回来重新让 LLM 校验、二次搜索、反思修
workflow.add_edge("run_tools", "chef_think")#再连线将工具执行结果回溯到思考节点

# 2.5 健康护栏审计后的路由：
#   ok / degraded → 结构化收尾；retry → 打回 chef_think 重新生成（带次数上限）
workflow.add_conditional_edges(#新增一个条件边分支
    source="verify_answer",
    path=verify_route,
    path_map={"ok": "structure_answer", #下一步进入结构化输出
              "retry": "chef_think",#回去重新生成
              "degraded": "structure_answer",#能用就节构化输出
              "blocked": "allergen_block",#过敏原不得降级放行
              "plain": END},#P3：无工具纯问答直接结束，跳过结构化 LLM
)
# 3. 结构化回答完毕 → 可选画像候选提取；关闭时节点直接透传后结束
workflow.add_edge("structure_answer", "profile_memory")
workflow.add_edge("profile_memory", END)
# 过敏原阻断文案输出后结束，不得再进入结构化节点生成违规卡片
workflow.add_edge("allergen_block", END)
# 编译可运行的图，挂载 Sqlite 断点持久化
agent = workflow.compile(checkpointer=checkpointer)#创建可运行的图的实例对象
