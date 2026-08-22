# FinTrace Data Pipeline

`data_pipeline/` is the single home for all offline data preparation code. It
does not answer Agent requests directly. Source data and generated artifacts are
stored under `data/`; online tool implementations remain under `tools/`.

## Layout

```text
data_pipeline/
  common/       Shared JSONL, hashing, manifest and retry helpers
  competition/  Competition file conversion and announcement recovery
  documents/    Document normalization, Chunk construction and vector indexing
  events/       Event normalization, clustering and timeline construction
  entity_resolution/ Canonical legal-entity matching and review candidates
  ownership/    Shareholder normalization and ownership graph construction
  financial/    Statement normalization and financial feature construction
```

The last four packages are separated by data product, while common mechanics
belong in `common/`. A function used during an Agent request belongs in
`tools/`, even when its offline index was produced here.

## Data Flow

```text
Competition files
  -> data_pipeline.competition
  -> data/normalized + data/source + data/evaluation
  -> data_pipeline.documents/events/ownership/financial
  -> data/processed
  -> data/indexes
  -> tools
```

See [data/README.md](../data/README.md) for the data directory contract.

## Documents

Inputs:

```text
data/normalized/announcements.jsonl
data/source/announcements/*.txt
data/normalized/research_reports.jsonl
```

Build normalized Documents:

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.cli build-documents `
  --data-dir data
```

Outputs:

```text
data/processed/documents/documents.jsonl
data/processed/documents/document_quality.json
```

Build the frozen V2 Chunk corpus:

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.cli build-chunks `
  --data-dir data `
  --version chunks-v2 `
  --output data\processed\documents\chunks_v2.jsonl `
  --report data\processed\documents\chunk_quality_v2.json `
  --manifest data\processed\documents\chunk_manifest_v2.json
```

The authoritative corpus is `chunks_v2.jsonl`. `chunks.jsonl` is retained only
as the historical V1 comparison corpus. Embedding and indexes must record the V2
manifest hash and may not silently rebuild or replace its Chunk IDs.

`documents/parsers.py`, `uploaded_file_chunker.py` and `build_file_index.py`
support future PDF, DOCX, TXT or Markdown uploads. They are not used to rebuild
the competition corpus.

## Generated Indexes

Runtime artifacts are written outside this code package:

```text
data/indexes/document_search/
  fintrace_kb.sqlite
  embeddings.npy
  vector.faiss
  vector_ids.json
  build_progress.json
  batch_jobs.json
  embedding_failures.jsonl
  manifest.json
```

Estimate the full embedding input without calling Qwen:

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index --estimate-only
```

Prepare Batch File requests locally without calling the API:

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index prepare
```

Submit, monitor, collect and finalize the formal index:

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index submit
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index status
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index collect
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index finalize
```

Submit one prepared shard when a staged rollout is preferred:

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.documents.build_index submit --shard-id shard-0000
```

Repeat `--shard-id` to select several shards. With no selector, `submit` handles
all shards that do not already have a Batch job ID.

The builder validates the frozen input hashes, imports searchable metadata into
SQLite, groups at most ten texts in each `/v1/embeddings` request and splits the
corpus into resumable DashScope Batch File jobs. Results are restored by
`custom_id` and response `data.index`; missing, duplicate, malformed or failed
vectors block finalization. Valid vectors are normalized before an exact
`IndexFlatIP` index is created. Use the `retry` action for request-level failures
and `run` only when waiting in one process is convenient. `--force` is accepted
by `prepare` only and replaces an incompatible local checkpoint or completed
index after explicit confirmation.

`finalize --allow-partial` is an explicit exception for small, reviewed
request-level failures. It excludes only missing rows fully explained by Batch
error records, keeps every Chunk in SQLite for BM25, writes successful vectors
to a compact FAISS index and records exclusions in `embedding_failures.jsonl`.
Unknown missing rows and malformed vectors still fail the build.

`build_file_index.py` remains a parsing and SQLite compatibility path for future
uploaded files. It does not generate vectors; offline vector construction has a
single Batch File implementation.

## Financial metric index

Build the narrow online financial index from the three normalized statement files:

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.financial.build_index
```

The builder extracts only versioned metrics declared in
`tools.financial_analysis.metric_catalog` and writes
`data/indexes/financial_analysis/financial_metrics.sqlite` plus `manifest.json`.
The normalized JSONL files remain the source of truth.

## Ownership holdings and path index

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.entity_resolution.build_company_universe
F:\conda_envs\FinTrace\python.exe -m data_pipeline.entity_resolution.fetch_company_profiles --estimate
F:\conda_envs\FinTrace\python.exe -m data_pipeline.entity_resolution.fetch_company_profiles --code 600030
F:\conda_envs\FinTrace\python.exe -m data_pipeline.entity_resolution.fetch_company_profiles
F:\conda_envs\FinTrace\python.exe -m data_pipeline.entity_resolution.build_index
F:\conda_envs\FinTrace\python.exe -m data_pipeline.ownership.build_index
```

The universe step takes the union of company codes observed in shareholders,
research, announcements and financial statements. The AKShare step is
resumable and freezes legal names under
`data/source/company_profiles/`; it is never called online. The entity builder
creates the auditable `entity_master.sqlite`. Exact,
unambiguous normalized names and legal-core names unique on both sides become
confirmed same-entity links; ambiguous matches remain review candidates and are not used online. The v3
ownership builder imports only confirmed links into `holder_company_links` for
bounded penetration searches. Fuzzy or LLM-generated links are never promoted
without review.

## Announcement event index

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.events.build_index
```

The builder classifies normalized announcement titles with versioned deterministic
rules and writes `data/indexes/event_timeline/events.sqlite` plus `manifest.json`.
Unclassified or invalid rows are counted instead of being force-labeled. Online
event queries never fall back to CSV or sample records.

## Reproducibility

Every generated corpus or index must record its input paths, SHA-256 hashes,
schema version, parameters, record counts and creation time. Builders write to a
temporary file before replacing a completed artifact. Failed API batches must be
recorded and must never be replaced with fake or hash-based production vectors.
