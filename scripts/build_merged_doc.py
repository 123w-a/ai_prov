# -*- coding: utf-8 -*-
import pathlib, re

desktop = pathlib.Path("C:/Users/31834/Desktop")
ai = (desktop / "ai 私厨.md").read_text(encoding="utf-8")
can = (desktop / "私厨参赛.md").read_text(encoding="utf-8")

# ---- 拆分 私厨参赛.md 按 ## 标题 ----
def split_by_h2(text):
    parts = {}
    cur = None
    buf = []
    for line in text.splitlines():
        m = re.match(r'^##\s+(.*)$', line)
        if m:
            if cur is not None:
                parts[cur] = "\n".join(buf).strip("\n")
            cur = m.group(1).strip()
            buf = []
        else:
            buf.append(line)
    if cur is not None:
        parts[cur] = "\n".join(buf).strip("\n")
    return parts

sec = split_by_h2(can)
# 用关键词定位各节
def find(keys):
    for k, v in sec.items():
        for key in keys:
            if key in k:
                return f"## {k}\n\n{v}"
    return ""

s_liyi   = find(["一句话立意"])          # 0
s_why     = find(["为什么这个立意"])      # 一
s_kb      = find(["知识资产盘点"])        # 二
s_arch    = find(["系统架构草图"])        # 三
s_parser  = find(["知识库构建底座"])      # 四
s_scene   = find(["核心场景"])            # 五
s_rename  = find(["改名与品牌"])          # 六
s_plan    = find(["落地计划"])            # 七
s_risk    = find(["风险与合规"])          # 八

def demote(text, levels=1):
    """把 markdown 标题整体降 levels 级，避免与外层包裹标题撞号。"""
    def shift(m):
        hashes = m.group(1)
        if len(hashes) + levels <= 6:
            return "#" * (len(hashes) + levels) + " " + m.group(2)
        return m.group(0)
    return re.sub(r'^(#{1,5})\s(.*)$', shift, text, flags=re.M)

# 导入正文降一级：工程笔记(#→##、##→###)；私厨参赛各节(##→###)
ai_body = demote(ai, 1)
for name in ["s_liyi","s_why","s_kb","s_arch","s_parser","s_scene","s_rename","s_plan","s_risk"]:
    globals()[name] = demote(globals()[name], 1)

# ---- 终极私厨 重建章节（源文件已删，依据会话知识+私厨要知到的点.md 重建）----
zhongji = r'''## 四、主动式膳食评估机制：从 AgentMental 论文迁移的五大机制

> 来源：参考了一篇「主动式心理健康评估 Agent」的论文系统设计方法。不是学它做心理健康，而是学它的**系统设计方法论**——把"一次性生成答案"升级为"先判断信息够不够、不够就追问、够了才决策"的流程。下面 5 个机制已翻译到小膳管家膳食场景。

### 4.1 充分性判断门（Sufficiency Gate）
- 论文核心：系统不直接根据一句话下判断，而是先判断"信息够不够"，不够就追问。
- 小膳落地：用户说"我糖尿病，想吃面" → 系统不要马上推荐，而是先问"你是控糖减脂还是正常控糖？今天主食吃过了吗？有没有肾病/高尿酸？" → 比直接生成菜谱显得专业。
- 当前状态：框架已有（`chef_think` 的 system prompt 让 LLM 自行判断），规划做成**显式 stage**：输入 → 检查 `{健康画像, 忌口, 食材/场景, 预算}` → 只追问缺失项 → 仅高风险决策（慢病忌口/控糖）强制 gate → 普通对话（"今晚吃啥"）不 gate → 够了才进 Evidence → Plan → Audit。
- 答辩价值：把"聊天机器人"升级成"决策流程"的关键一步。

### 4.2 信息增益追问（Information-Gain Questioning）
- 不一次性问完，让系统主动补证据；只问"能改变决策"的问题。
- 小膳落地：只追问缺失的关键约束（慢病忌口/控糖状态），不甩 4 问烦用户；信息够了立即推荐。

### 4.3 树状记忆（Tree Memory）
- 论文把信息分成 user 节点 / topic 节点 / statement 节点。
- 小膳落地：`user_profile` 节点下分 `conditions`（高血压/糖尿病/痛风）/ `preferences`（不吃辣/预算）/ `context`（在家/在外/冰箱食材）/ `evidence`（引自哪份指南/哪条规则/哪个决策）。
- 价值：解释器能区分"用户事实 / 指南证据 / 系统推理"，正好喂给 `sources` 出处字段。

### 4.4 角色分离（Role Separation，不真起 5 个 LLM）
- 论文拆成多个 agent 角色。小膳落地（**单编排器 + 显式 stage，不真起多个 LLM**）：
  - `ProfileAgent`：整理健康画像
  - `EvidenceAgent`：从 RAG 找国家指南依据
  - `ConstraintAgent`：生成硬忌口/红线（= `nutrition_rules.py` 确定性规则）
  - `MealPlannerAgent`：生成菜谱/外食方案
  - `AuditAgent`：检查是否违反高血压/糖尿病/痛风限制
- 答辩讲"关注点分离"即可，评委看的是架构清晰不是 LLM 调用次数。

### 4.5 条目级评测消融（Item-level Ablation）
- 论文对每个机制做消融实验测贡献。
- 小膳规划：对每个护栏机制单独开关，测"无该机制时违规率/召回率变化"，证明每个机制都在干活。

### 4.6 可解释性：每条推荐可回溯
- 论文强调"可解释"不是说模型懂了，而是能回溯依据。
- 小膳落地：每条推荐能回溯到「用户信息 + 哪份指南 + 哪条限制 + 为什么选这道菜」。例：
  - 推荐：番茄鸡蛋荞麦面
  - 原因：糖尿病→主食定量优先低 GI/全谷物；高血压→少盐避开咸菜火腿；预算→鸡蛋番茄成本低；可执行→15 分钟内完成。

## 五、安全边界与合规

- 小膳管家提供膳食建议与风险提示，**不替代医生或营养师的临床诊断**。
- 严重慢病、孕产妇、肾病晚期等需专业复核。
- 营养数据来自《中国食物成分表》第 6 版，可能存在个体差异。
- AI 生成菜品图为示意，不代表实际成品外观。
- 诚实写边界不是示弱，是专业度信号：评委更信任"知道边界"的项目。

## 六、测试验收与答辩话术

### 6.1 测试验收用例
- **忌口问答**：高血压 + 咸鸭蛋 → 应引《高血压食养指南》作答并标出处。
- **在外场景**：定位商圈 → 推荐 X 家 + 各自点单红线 + 避开重油重盐店。
- **充分性门**：糖尿病想吃面 → 系统应先追问控糖状态而非直接推荐。
- **营养计算**：一餐总热量/钠 → 走 CSV 精确计算并和 DRI 上限比对报警。

### 6.2 答辩杀手锏（6 句话术浓缩）
> 小膳管家采用**主动式膳食评估流程**，而非一次性生成菜谱：先判断健康信息是否充分，通过追问补齐关键约束；再基于国家指南与确定性营养规则生成**可溯源的饮食护栏**；最后才输出菜谱或外食方案，且每条推荐都能回溯到「用户情况 + 指南依据 + 限制红线」。系统提供膳食建议与风险提示，**不替代医生或营养师的临床诊断**。

> 一句话对比：普通做菜 App 是"你说啥我做啥"，小膳管家是"替你把健康边界想清楚再决策"。
'''

# ---- 组装 ----
parts = []
parts.append("# 小膳管家 · AI 私厨 项目完整技术思路（四文档合并版）\n")
parts.append("> **本文合并自四份项目文档**，按「工程主线为根、立意/知识库/机制融合」原则组织：\n")
parts.append("> - 《ai 私厨》（工程笔记，占主体）—— 工程化架构逐行走查\n")
parts.append("> - 《私厨参赛》（小膳管家参赛文档）—— 立意升维 / 知识库底座 / 场景\n")
parts.append("> - 《终极私厨》（AgentMental 机制迁移）—— 主动式评估五大机制 / 安全边界 / 测试答辩 〔**该源文件已删，本章为据会话知识重建**〕\n")
parts.append("> - 《AI 银发助手参赛方案》—— 银发专属内容，与本项目无关，**已整体删除不纳入**\n")
parts.append("\n---\n")

parts.append("## 〇、定位与一句话立意\n\n")
parts.append(s_liyi + "\n\n")
parts.append(s_why + "\n\n")
parts.append(s_arch + "\n\n")
parts.append("---\n")

parts.append("## 一、工程化架构详解（来自《ai 私厨》工程笔记）\n\n")
parts.append("> 本章是项目工程主线的完整逐行走查，每个知识点都标注了对应文件与代码位置。\n\n")
parts.append(ai + "\n\n")
parts.append("---\n")

parts.append("## 二、知识库与权威护栏底座（来自《私厨参赛》知识库章节）\n\n")
parts.append(s_kb + "\n\n")
parts.append(s_parser + "\n\n")
parts.append("---\n")

parts.append("## 三、小膳管家立意升维与场景（来自《私厨参赛》）\n\n")
parts.append(s_scene + "\n\n")
parts.append(s_rename + "\n\n")
parts.append(s_plan + "\n\n")
parts.append(s_risk + "\n\n")
parts.append("---\n")

parts.append(zhongji + "\n\n")

parts.append("---\n")
parts.append("## 附：关于《AI 银发助手参赛方案》\n\n")
parts.append("该文档为早期废弃选题（银发/无障碍助手）的内容，与「小膳管家 / AI 私厨」本体无关（适老化 UI、用药提醒、防诈科普等），合并时已整体删除，不纳入本技术思路。\n")

out = (desktop / "ai私厨思路.md")
out.write_text("\n".join(parts), encoding="utf-8")
print("已写出:", out, "| 字节:", out.stat().st_size)
