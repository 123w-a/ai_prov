# Agent 与 RAG

## Agent 的职责分工

```text
请求进入
  -> 读取画像和当前会话
  -> 判断问题类型
  -> 必要时调用 RAG / 搜索 / 数据工具
  -> 模型生成候选答案
  -> 结构化为 ChefAnswer
  -> 健康和过敏原护栏
  -> 流式返回
```

主要实现集中在 `agent/graph.py`。不要只从提示词判断行为，实际分支、工具循环和状态恢复都在图中。

## 三类约束

| 层 | 文件 | 作用 |
|---|---|---|
| 输出契约 | `agent/schemas.py` | 限定字段和数据形状 |
| 软约束 | `agent/prompts.py` | 告诉模型如何回答 |
| 硬护栏 | `domain/nutrition_rules.py`、`domain/allergen_rules.py` | 对危险或不合适内容做规则检查 |

## RAG 链路

```text
问题
  -> 查询转换
  -> 向量检索
  -> BM25 检索
  -> RRF 融合
  -> 可选 reranker
  -> 片段与来源
  -> Agent 上下文
```

相关目录和脚本：

- `rag/`：检索实现；
- `kb/`：知识库原始资料和向量库；
- `scripts/build_kb_rag.py`：构建或更新知识库；
- `.env.example`：`KB_DIR`、`KB_CORPUS_DIR` 等参数。

## 记忆边界

- `data/profile.json`：用户明确保存的长期画像；
- `data/memory_candidates.json`：等待确认的候选；
- `resources/checkpoint.db`：图运行状态；
- `sessions/*.json`：历史展示内容。

如果画像没有记录某项忌口，却在回答里出现，应先检查激活成员、历史会话、checkpoint、上下文注入和模型推断，不能直接把它当作已保存画像。
