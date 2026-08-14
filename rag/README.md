# RAG 模块边界

```text
语料文件
  -> ingest.py
  -> cleaning.py
  -> chunking.py
  -> embeddings.py
  -> store.py
  -> retriever.py
  -> Agent 工具
```

模块职责：

- `cleaning.py`：去噪、保留 Markdown 结构、提取来源。
- `chunking.py`：按标题和段落切片，支持长度限制与少量重叠。
- `embeddings.py`：统一创建 `bge` 或 Chroma 默认 embedding。
- `store.py`：唯一接触 Chroma 的地方。
- `ingest.py`：离线建库流程，可输出清洗预览。
- `retriever.py`：在线检索的统一入口，向业务返回 `SearchResult`。

构建索引：

```powershell
uv run python build_kb_rag.py --preview
```

清洗预览位于：

```text
resources/cleaned_preview/
```
