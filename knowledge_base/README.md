# Knowledge Base

`knowledge_base/` 存放离线文档知识库构建代码。它不直接回答用户问题，而是把原始文档变成 `document_search` 可以在线检索的 SQLite / FAISS 产物。

## 建库入口

```python
knowledge_base.document_ingestion.build_kb.main()
```

命令：

```powershell
F:\conda_envs\FinTrace\python.exe -m knowledge_base.document_ingestion.build_kb `
  --raw-dir data/raw_documents `
  --kb-dir data/knowledge_base
```

生成向量索引：

```powershell
F:\conda_envs\FinTrace\python.exe -m knowledge_base.document_ingestion.build_kb `
  --raw-dir data/raw_documents `
  --kb-dir data/knowledge_base `
  --build-vector
```

## 工作流

```text
build_kb.main()
→ 扫描 raw-dir 下的 PDF / DOCX / TXT / MD
→ parse_metadata()
→ parse_document()
   → parse_pdf_file()
   → parse_docx_file()
   → parse_text_file()
→ chunk_pages()
   → section-aware chunking
→ connect()
→ insert_document()
→ insert_chunks()
→ write_manifest()
→ write_parse_report()
→ 如果 --build-vector：
   → build_embedding_client()
   → build_vector_index()
   → vector.faiss / vector_ids.json / embeddings.npy
```

## 数据产物

```text
data/knowledge_base/
  fintrace_kb.sqlite   # documents / chunks 表
  vector.faiss         # FAISS 向量索引
  vector_ids.json      # FAISS 行号到 chunk_id 的映射
  embeddings.npy       # 调试用 embedding 矩阵
  manifest.json        # 建库参数和向量索引状态
  parse_report.json    # 文件级解析质量报告
```

## 关键模块

- `document_ingestion/build_kb.py`：建库 CLI 和总控流程
- `document_ingestion/parsers.py`：PDF/DOCX/TXT/MD 解析和轻量表格文本化
- `document_ingestion/chunker.py`：切片和章节标题识别
- `document_ingestion/kb_store.py`：SQLite schema 和写入逻辑
- `document_ingestion/vector_index.py`：FAISS 向量索引构建
- `embeddings/client.py`：DashScope embedding 和本地 hash embedding

## Review 注意点

- 在线问答不解析 PDF/Word，只读取已经建好的 SQLite/FAISS；
- `--skip-unchanged` 只跳过未变化文件的解析，向量索引当前建议整体重建；
- OCR 尚未实现，低文本量文件会在 `parse_report.json` 中标记 `low_text_volume_may_need_ocr`。
