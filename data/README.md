# FinTrace Data Layout

`data/` contains datasets and generated artifacts only. Processing code lives in
`data_pipeline/`; online query code lives in `tools/`.

```text
data/
  source/                 Downloaded documents and frozen external snapshots
  normalized/             Normalized competition JSONL datasets
  processed/
    competition/          Conversion and download quality reports
    documents/            Frozen Document and Chunk corpora
    events/               Generated event and cluster records
    ownership/            Generated ownership relations
    financial/            Generated financial features
    entity_resolution/    Unified company-code universe and quality manifest
  indexes/
    document_search/      SQLite, embeddings and FAISS artifacts
    event_timeline/       Event query indexes
    research_analysis/    Attributed research-view SQLite index
    entity_resolution/    Canonical entities, aliases and reviewed mappings
    ownership_analysis/  Shareholder holdings SQLite index
    financial_analysis/   Financial query indexes
  evaluation/             Evaluation questions and annotations
```

The authoritative text Chunk corpus is
`processed/documents/chunks_v2.jsonl`. Runtime indexes are derived artifacts and
must be rebuildable from files under `normalized/` and `processed/`.

`indexes/research_analysis/research_views.sqlite` is derived from normalized
research reports and the frozen Chunk corpus. It stores attributed claims for
fast online filtering; `document_search` remains the source-text retrieval layer.
