# agent.py：对外统一入口（门面层）
# 实际逻辑已解耦拆分到五个模块，互不直接纠缠：
#   - agent_prompts.py : 系统提示词（角色/流程/约束）
#   - agent_tools.py   : 工具定义（web_search 联网菜谱、get_file 读本地文件）
#   - agent_schemas.py : 结构化输出的数据形状（Seasoning/Recipe/ChefAnswer）
#   - agent_chains.py  : LCEL 链组装（ChatPromptTemplate | llm | PydanticOutputParser）
#   - agent_graph.py   : 编排层（LLM/断点/节点/状态图），并编译出可运行的 agent
# 这样既保留 decoupled 结构，又不影响 main.py 的 `from agent import agent` 调用方式

from agent_graph import agent, tools# 编译好的LangGraph实例、tools列表
from agent_tools import web_search, get_file#工具
from agent_prompts import SYSTEM_PROMPT#提示词
from agent_schemas import ChefAnswer, Recipe, Seasoning#结构化输出的数据形状
from agent_chains import chef_answer_chain, rank_recipes#LCEL 结构化链 + 排序

__all__ = [
    "agent", "tools", "web_search", "get_file", "SYSTEM_PROMPT",
    "ChefAnswer", "Recipe", "Seasoning", "chef_answer_chain", "rank_recipes",
]
