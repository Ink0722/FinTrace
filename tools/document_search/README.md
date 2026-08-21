# document_search

`document_search` 从本地文档知识库召回原文 Chunk，并把最终命中转换为可追溯的 `Evidence`。在线检索不会切分文档或生成文档向量；这些工作由 `data_pipeline.documents` 离线完成。

## 入口函数

```python
tools.document_search.interface.document_search(call: ToolCall) -> ToolResult
```

工具始终返回 `ToolResult`。参数、SQLite、向量服务或索引异常不会直接从工具入口抛出，避免中断整个 Agent 工作流。

## 调用参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `query` | `str` | 必填 | 非空自然语言检索问题 |
| `company_ids` | `list[str]` | `[]` | 当前支持零个或一个证券代码 |
| `document_types` | `list[str]` | 不限制 | 当前正式语料包括 `announcement`、`research_report` |
| `start_date` | `YYYY-MM-DD` | 不限制 | 发布日期下界 |
| `end_date` | `YYYY-MM-DD` | 不限制 | 发布日期上界，不能早于 `start_date` |
| `knowledge_cutoff` | `YYYY-MM-DD` | 不限制 | 系统允许使用的信息最晚披露日，由工作流注入，Planner 不得生成或修改 |
| `top_k` | `int` | `8` | 最终返回数量 |
| `pool_k` | `int` | `max(top_k * 5, 50)` | BM25 和向量候选池大小 |
| `mode` | `bm25/vector/hybrid` | `hybrid` | 检索模式 |

多公司、空问题、非法日期、未知参数以及超出配置上限的 `top_k/pool_k` 返回 `INVALID_ARGUMENT`。

实际检索上界为 `effective_end_date = min(end_date, knowledge_cutoff)`；任一参数缺失时使用另一项，两者都缺失时不设置上界。所有日期过滤均比较 Chunk 的 `publish_date`。如果 `start_date` 晚于有效结束日期，工具返回 `INVALID_ARGUMENT`。返回数据同时回显 `knowledge_cutoff` 和 `effective_end_date`，用于审计防前视约束。

Agent 调用时，如果请求解析阶段已经唯一确定公司或文档类型，而 Planner 未显式填写相应过滤条件，Action Validator 会确定性补入 `company_ids` 和 `document_types`，避免退化为无关的全库搜索。同一轮调查最多执行两次文档检索，即一次初始查询和一次实质不同的 Query 改写；仍有缺口时以限制说明收束。

## 在线工作流

```text
document_search(call)
→ 校验参数并加载配置
→ 检查 SQLite 知识库
   ├ 不存在且 Demo 关闭 → DATA_NOT_AVAILABLE
   └ 不存在且 Demo 开启 → 显式使用内置样例
→ 合并 end_date 与 knowledge_cutoff，得到有效披露截止日
→ 根据公司、文档类型和发布日期确定候选 Chunk
→ BM25 分支：中文双字 token 召回
→ Vector 分支：Qwen 生成查询向量
   ├ 有元数据过滤 → 在过滤后的向量子集上精确余弦搜索
   └ 无元数据过滤 → 全库 FAISS IndexFlatIP 搜索
→ Hybrid 分支：使用 RRF 融合两路排名
→ 限制同一文档最多返回指定数量的 Chunk
→ 生成 DocumentSearchHit 和 Evidence
→ 返回结果、警告、调试信息及分阶段耗时
```

## 检索模式与失败语义

### `bm25`

只执行关键词检索，不调用 Qwen Embedding API。连续中文使用双字 token，孤立汉字保留单字 token，适配“存货、现金流、关联交易”等金融词汇并减少高频单字噪声。

### `vector`

只执行语义检索。向量索引缺失、查询 embedding 失败或索引配置不匹配时返回失败，不用 BM25 伪装成向量结果。

### `hybrid`

默认模式，同时执行 BM25 和向量检索。向量分支不可用时会给出 warning，并降级为 BM25；最终回答可以据此披露检索限制。

## 过滤感知向量检索

知识库包含数千家公司，不能先取全库前几十条再过滤公司，否则目标公司的结果很可能已经被丢弃。

当前策略是：

```text
指定 company_ids/document_types/date
→ SQLite 确定允许的 chunk_id
→ chunk_id 映射为紧凑向量行号
→ 分批读取 embeddings.npy
→ 只对允许行计算 query_vector 点积
→ 返回该过滤范围内真正的 Top-K
```

索引向量和查询向量均已归一化，因此点积就是余弦相似度。精确搜索按批读取 `embeddings.npy`，不会一次复制整个向量矩阵。

完全不带过滤条件时，工具使用全库 `vector.faiss`。FAISS 索引、`vector_ids.json` 和只读 `embeddings.npy` 会按文件路径及修改时间在进程内缓存；索引文件更新后会使用新的缓存键加载。

## Hybrid 融合

BM25 分数和余弦相似度不在同一尺度，当前不直接加权相加，而是使用 Reciprocal Rank Fusion：

```text
RRF(chunk) = 1 / (k + bm25_rank) + 1 / (k + vector_rank)
```

默认 `k=60`。最终 `score` 归一化到 `0～1`，原始 `bm25_score`、`vector_score` 和融合方式保留在 `retrieval` 中。

为避免同一份研报的相邻 Chunk 占满上下文，默认每个文档最多保留 3 条结果。

## 数据来源与安全边界

正式数据路径按以下顺序确定：

```text
1. FINTRACE_KB_PATH
2. <项目根目录>/data/indexes/document_search/fintrace_kb.sqlite
```

相对路径始终从项目根目录解析，而不是从启动命令所在目录解析。

内置 `sample_data.py` 默认禁用。只有明确设置以下变量时才会使用：

```dotenv
FINTRACE_DOCUMENT_SEARCH_DEMO_MODE=true
```

Demo 结果会标记 `source=sample` 并附带 warning，不应参与正式评测。

## 环境配置

```dotenv
# 查询 embedding，必须与离线索引模型和维度一致
DASHSCOPE_EMBEDDING_API_KEY=
DASHSCOPE_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_EMBEDDING_MODEL=text-embedding-v4
DASHSCOPE_EMBEDDING_DIMENSION=1024

# 知识库与在线检索
FINTRACE_KB_PATH=data/indexes/document_search/fintrace_kb.sqlite
FINTRACE_DOCUMENT_SEARCH_DEMO_MODE=false
FINTRACE_DOCUMENT_SEARCH_DEFAULT_MODE=hybrid
FINTRACE_DOCUMENT_SEARCH_DEFAULT_TOP_K=8
FINTRACE_DOCUMENT_SEARCH_MAX_TOP_K=20
FINTRACE_DOCUMENT_SEARCH_MAX_POOL_K=500
FINTRACE_DOCUMENT_SEARCH_RRF_K=60
FINTRACE_DOCUMENT_SEARCH_MAX_CHUNKS_PER_DOCUMENT=3
FINTRACE_DOCUMENT_SEARCH_EXACT_BATCH_SIZE=4096
```

配置说明：

- `DEFAULT_TOP_K`：调用参数未指定 `top_k` 时使用。
- `MAX_TOP_K`：限制最终传给 LLM 的 Chunk 数量。
- `MAX_POOL_K`：限制两路召回池，防止异常参数造成过大计算。
- `RRF_K`：控制 RRF 排名衰减，通常不需要频繁调整。
- `MAX_CHUNKS_PER_DOCUMENT`：限制单文档占用的结果槽位。
- `EXACT_BATCH_SIZE`：过滤后精确向量计算的批大小，主要影响内存和速度。

## 输出字段

`ToolResult.data` 主要字段：

- `hits`：最终命中的 `DocumentSearchHit`。
- `source`：`knowledge_base` 或显式 Demo 下的 `sample`。
- `mode`：请求的检索模式。
- `retrieval_debug`：候选数量、两路命中数、向量策略、融合方式和返回数。

每个 Hit 的 `retrieval` 示例：

```json
{
  "source": "hybrid",
  "matched_by": ["bm25", "vector"],
  "bm25_score": 0.91,
  "vector_score": 0.78,
  "final_score": 0.85,
  "fusion": "rrf"
}
```

`ToolResult.metrics` 记录：

- `execution_time_ms`：完整工具执行时间。
- `metadata_time_ms`：SQLite 候选过滤和 Chunk 加载时间。
- `lexical_search_time_ms`：BM25 时间。
- `embedding_time_ms`：在线生成查询向量的时间。
- `vector_search_time_ms`：FAISS 或过滤后精确向量计算时间。
- `rerank_time_ms`：融合和同文档限流时间。

比赛中的“工具调用响应延迟”应采用 `execution_time_ms`，不是整个 Agent 生成最终答案的时间。

## 索引产物

正式目录 `data/indexes/document_search/` 包含：

- `fintrace_kb.sqlite`：Chunk 正文与元数据。
- `embeddings.npy`：归一化后的紧凑向量矩阵。
- `vector.faiss`：无过滤全库检索索引。
- `vector_ids.json`：紧凑向量行到 `chunk_id` 的映射。
- `bm25_index.sqlite`：contentless FTS5 词法索引 + `chunk_meta` 过滤元数据（`python -m data_pipeline.documents.build_bm25_index` 离线构建，`bm25/hybrid` 模式必需）。
- `bm25_manifest.json`：词法索引的 KB 指纹与分词器版本，失配时在线查询要求重建。
- `manifest.json`：模型、维度、覆盖率和输入指纹。
- `embedding_failures.jsonl`：未进入向量索引的 Chunk。

如果 `manifest.json` 标记为部分索引，工具会返回向量覆盖率 warning。缺失向量的 Chunk 仍保存在 SQLite 中，可以被 BM25 召回。

## 测试

```powershell
F:\conda_envs\FinTrace\python.exe -m pytest tests\test_document_search.py tests\test_document_embedding.py -q
```

测试使用临时知识库和本地假查询向量，不依赖正式索引内容，也不会调用 DashScope。

## 关键文件

- `config.py`：环境配置和项目根目录路径解析。
- `interface.py`：参数校验、模式语义、错误边界和结果组装。
- `kb_loader.py`：SQLite Chunk 与过滤候选读取。
- `search.py`：中文 BM25 和 Evidence 转换。
- `vector_search.py`：过滤感知精确搜索、FAISS、缓存、RRF 和结果限流。
- `sample_data.py`：仅供显式 Demo 使用的样例数据。
