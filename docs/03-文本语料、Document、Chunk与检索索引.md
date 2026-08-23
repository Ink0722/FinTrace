# FinTrace 文本语料、Document、Chunk 与检索索引

本文件定义竞赛文本从原始材料到在线可检索证据的唯一目标流程。文本标准化、Chunk 构建和索引构建是三个独立阶段，任何阶段不得重写上游事实。

## 代码审查索引

| 环节 | 实现文件 | 核心检查 |
| --- | --- | --- |
| Document 统一 | `data_pipeline/documents/document_builder.py`、`data_pipeline/documents/cleaner.py` | 字段映射、正文不改写、质量标记 |
| Chunk 切分 | `data_pipeline/documents/chunk_builder.py`、`data_pipeline/documents/chunker.py`、`data_pipeline/documents/embedding_text.py` | 段落边界、稳定 ID、Embedding 输入版本 |
| 语料入库 | `data_pipeline/documents/corpus_store.py`、`data_pipeline/documents/sqlite_store.py` | Chunk/Document 对应与事务写入 |
| Batch Embedding | `data_pipeline/documents/build_index.py`、`data_pipeline/documents/batch_embedding_client.py` | `custom_id` 复原、失败记录、输入哈希 |
| BM25/FTS5 | `data_pipeline/documents/build_bm25_index.py`、`tools/document_search/fts5_search.py` | tokenizer 版本和 contentless FTS5 |
| 在线混合检索 | `tools/document_search/interface.py`、`tools/document_search/search.py`、`tools/document_search/vector_search.py` | 过滤、防前视、RRF、证据定位 |

构建命令和产物目录以 `data_pipeline/README.md` 为准；字段模型以 `schemas/document.py` 及构建器真实输出为准。

## 输入、边界与产物

输入包括公告索引及授权正文、财报附注/审计报告/问询函、研报元数据与摘要，以及后续合法上传的 PDF、DOCX、TXT、Markdown。公告无正文时只保留标题、日期、类型和来源，不能伪造正文；研报摘要不能被扩写为研报全文。

```text
raw/source + normalized metadata
  -> Document corpus
  -> frozen Chunk corpus
  -> FTS/BM25 metadata index + embedding/vector index
  -> document_search evidence
```

核心产物为 `documents.jsonl`、质量报告、`chunks_v2.jsonl`、Chunk manifest，以及 SQLite、BM25、向量索引及其 manifest。竞赛语料以冻结的 `chunks_v2` 为唯一输入；上传文件通道不得重建或覆盖其 Chunk ID。

## Document 契约

每个 Document 至少包含稳定 `document_id`、`source_type`、公司/证券标识、标题、发布日期、原始来源定位、正文、文本语言、解析状态、质量标记、schema 版本和内容哈希。可选元数据包括公告类型、报告期、作者/机构、页码范围和下载时间。

- 标识、来源和日期优先取原始元数据；缺失时标为未知，不从正文猜测。
- `content_hash` 基于规范化后正文计算；相同内容可去重，但必须保留所有原始来源。
- 清理仅移除格式噪声、重复空白、不可打印控制符和明确的页眉页脚重复；不得改写数值、否定词、主体、日期和法律措辞。
- 失败记录必须携带原因、源路径和可重试性；失败文本不可进入正式索引。

## Chunk 契约与切分策略

Chunk 是证据引用的最小检索单元，至少包含 `chunk_id`、`document_id`、顺序号、标题/章节路径、原文文本、字符或 Token 范围、来源定位、内容哈希和语料版本。

1. 优先按标题、段落、列表和表格语义边界切分；
2. 短段落合并至目标窗口，保留父标题与必要前文；
3. 超长段落采用带重叠的窗口切分，重叠只为上下文连续，不能制造重复证据；
4. 表格、公告编号、金额、日期和否定句不可被任意截断；
5. Chunk ID 由语料版本、Document ID 和稳定序号确定，冻结后不得因重建而漂移。

每个 Chunk 必须能反向定位到源文件、页码或行区间；答案引用 Chunk 时同时暴露其来源元数据，不将检索片段误表述为完整原文。

## 索引与检索

检索采用结构化过滤与混合召回：先按公司、文档类型、报告期、发布日期和知识截止日过滤，再融合 FTS/BM25 词法分数与向量相似度。向量归一化、索引参数、嵌入模型、输入 manifest 哈希、失败条目和构建时间必须被记录。

- 词法与向量索引均缺失或 manifest 不匹配时，系统返回明确的建库/数据错误。
- Batch 嵌入需按 `custom_id` 与响应索引复原；缺失、重复、畸形向量会阻断正式完成。
- 部分向量索引只能在明确审查并登记排除记录后生成；SQLite/BM25 仍保留全部合法 Chunk。
- `knowledge_cutoff` 由调用上下文注入，检索层必须过滤截止日后披露的材料。

## 复现与验收

构建报告至少给出输入数、成功数、失败数、去重数、Document/Chunk 长度分布、空文本数、孤儿 Chunk 数、哈希与版本。任何下游索引都必须验证输入版本和 manifest；不匹配时拒绝静默复用。

标准构建命令、批量嵌入和索引恢复步骤以 `data_pipeline/README.md` 及 `data_pipeline/documents/` 脚本为准。
