# Local backups

This directory separates retired but potentially valuable artifacts from files used by
the active FinTrace runtime. Backup payloads are ignored by Git; `manifest.json` records
their original locations, destinations, sizes, and checksums for recovery.

- `runtime-premerge/`: SQLite databases retained from before the unified runtime migration.
- `embedding-batches/`: DashScope Batch requests, results, mappings, errors, and checkpoints.
- `competition-source/`: the original competition delivery archive.
- `frontend-assets/`: superseded source assets.
- `document-corpus-v1/`: the retired V1 chunk corpus and its reports.

Nothing under this directory is read by the running Agent. Restore an artifact to its
recorded source path before using the corresponding migration or rebuild command.
