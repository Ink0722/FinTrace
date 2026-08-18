# FinTrace Data Layout

`data/` contains datasets and generated artifacts only. Processing code lives in
`data_pipeline/`; online query code lives in `tools/`.

```text
data/
  source/                 Downloaded source documents
  normalized/             Normalized competition JSONL datasets
  processed/
    competition/          Conversion and download quality reports
    documents/            Frozen Document and Chunk corpora
    events/               Generated event and cluster records
    ownership/            Generated ownership relations
    financial/            Generated financial features
  indexes/
    document_search/      SQLite, embeddings and FAISS artifacts
    event_timeline/       Event query indexes
    ownership_analysis/  Shareholder holdings SQLite index
    financial_analysis/   Financial query indexes
  evaluation/             Evaluation questions and annotations
```

The authoritative text Chunk corpus is
`processed/documents/chunks_v2.jsonl`. Runtime indexes are derived artifacts and
must be rebuildable from files under `normalized/` and `processed/`.
