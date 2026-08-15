# document_search

文档检索工具负责从本地文档知识库或内置样例中召回原文片段，并把命中结果转成 `Evidence`。

## 入口函数

```python
tools.document_search.interface.document_search(call: ToolCall) -> ToolResult
```

## 工作流

```text
document_search(call)
→ 解析 call.arguments
   - company_ids：零个或一个公司的数组
   - query
   - document_types
   - start_date / end_date
   - top_k / pool_k
   - mode=bm25|vector|hybrid
→ resolve_kb_path()
→ 如果 SQLite 知识库存在：
   → load_kb_chunks()
   → BM25 检索
   → 如果 mode=vector/hybrid 且 vector.faiss 存在：
      → vector_search()
      → merge_hybrid_hits()
→ 如果知识库不存在：
   → load_sample_chunks()
   → filter_chunks()
   → bm25_search()
→ evidence_from_hits()
→ ToolResult
```

不限定公司时传空数组 `[]`；当前在线实现收到多个公司时返回 `INVALID_ARGUMENT`，不静默丢弃公司。

## 数据来源

优先级：

```text
1. FINTRACE_KB_PATH 指向的 SQLite 知识库
2. data/knowledge_base/fintrace_kb.sqlite
3. tools/document_search/sample_data.py
```

SQLite 中的 chunk 由 `knowledge_base.document_ingestion.build_kb` 离线生成。

## 检索模式

```text
bm25    关键词检索
vector  FAISS 语义检索
hybrid  默认，BM25 + FAISS 合并
```

如果选择 `vector/hybrid` 但没有 `vector.faiss` 或 embedding 配置不可用，工具会 warning 并回退 BM25。

## 输出重点

`ToolResult.data`：

- `hits`：命中的 `DocumentSearchHit`
- `retrieval_debug`：召回池、命中数、合并数、最终返回数
- `source`：`knowledge_base` 或 `sample`
- `mode`：实际请求的检索模式

每个 hit 包含 `retrieval`：

```json
{
  "source": "hybrid",
  "matched_by": ["bm25", "vector"],
  "bm25_score": 0.91,
  "vector_score": 0.78,
  "final_score": 0.85
}
```

## 关键文件

- `interface.py`：工具入口和回退策略
- `kb_loader.py`：从 SQLite 读取 chunk
- `search.py`：BM25、过滤和 Evidence 转换
- `vector_search.py`：FAISS 检索和 hybrid 合并
- `sample_data.py`：无知识库时的样例数据
