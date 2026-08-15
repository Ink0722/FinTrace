# FinTrace Data Pipeline

`data_pipeline/` contains offline dataset preparation code. It does not run inside the Agent request path.

## Layout

```text
data_pipeline/
  competition/  Raw competition file conversion and announcement recovery
  text/         Normalized announcement and research-report documents
```

`competition/` is the relocated home of the original preprocessing scripts. `text/` implements normalized Document construction and paragraph-aware Chunk construction. Embedding and vector-index construction remain separate downstream steps.

## Build Normalized Documents

Inputs:

```text
data/jsonl/announcements.jsonl
data/documents/announcements/*.txt
data/jsonl/research_reports.jsonl
```

Command:

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.text.cli build-documents `
  --data-dir data
```

V1 outputs retained for comparison:

```text
data/text_corpus/documents.jsonl
data/text_corpus/document_quality.json
```

Announcement fields:

```text
document_id, document_type, company_id, title, published_date,
tags, text, source_ref
```

Research-report Documents additionally include `publisher`. Announcements use the downloaded TXT body as `text`; research reports use the JSONL `abstract`. Non-A-share research records and announcements without an indexed text layer are reported and excluded.

For announcements, the builder removes complete title lines repeated at the very start of the body. It does not perform fuzzy deletion elsewhere. The quality report records both the number of affected announcements and the number of removed title lines. Research-report `text` remains an abstract, not the full report.

The builder streams both source files, rejects duplicate Document IDs, and writes through a temporary file before replacing the output. It does not modify any source JSONL or TXT file.

The JSONL output is UTF-8 without a BOM. In Windows PowerShell, specify the encoding when previewing it:

```powershell
Get-Content -Encoding utf8 data\text_corpus\documents.jsonl -TotalCount 1
```

## Build Chunks

The Chunk builder consumes only the frozen normalized Document corpus. The historical V1 files remain under their original names for comparison:

Default boundaries are `min=200`, `target=600`, `soft_max=900`, and `hard_max=1200` characters. Paragraphs are kept intact when possible. Only a paragraph longer than the hard maximum is split, first at sentence punctuation, then secondary punctuation, and finally at a forced character boundary. Chunk overlap is zero.

```text
data/text_corpus/chunks.jsonl
data/text_corpus/chunk_quality.json
data/text_corpus/chunk_manifest.json
```

Do not overwrite those files while comparing versions. Generate the improved V2 corpus independently:

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.text.cli build-chunks `
  --data-dir data `
  --version chunks-v2 `
  --output data\text_corpus\chunks_v2.jsonl `
  --report data\text_corpus\chunk_quality_v2.json `
  --manifest data\text_corpus\chunk_manifest_v2.json
```

Each Chunk contains only:

```text
chunk_version, chunk_id, document_id, chunk_index, section_title, char_start, text
```

Document-level metadata is joined from `documents.jsonl` through `document_id`; it is not duplicated into every Chunk. `chunk_version` prevents records from different corpora being confused when exported independently. `section_title` is inherited from explicit headings and is `null` when the source has no reliable heading. `char_start` points to the exact start position in the source Document text.

The quality report records length distributions, short Chunks, heading coverage, forced boundaries, duplicate IDs, hard-limit violations, and text-coverage failures. The manifest freezes source/output SHA-256 hashes, parameters, schema, version, and counts. Because changing the source or split parameters can change every downstream ID, freeze the manifest before embedding or manually annotating `required_chunk_ids`.

Implementation details and the current full-corpus quality results are documented in [Chunk 构建技术白皮书](../docs/11-Chunk构建技术白皮书.md).

## Retrieval Boundary

Chunks intentionally do not duplicate `document_type`; retrieval joins it from the Document metadata table by `document_id`. Retrieval should filter to one type when the question explicitly asks for announcements or research views. Mixed queries should retrieve each type independently before reranking; the initial budget is announcement Top 5 plus research-report Top 5, reranked to a final Top 6. This policy belongs to the retrieval stage and is not implemented by either builder.
