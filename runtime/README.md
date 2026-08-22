# FinTrace Runtime

`fintrace.sqlite3` is the generated local database shared by Agent session memory,
users, conversation ownership, observability traces and evaluation batches. It is
configured through `FINTRACE_RUNTIME_DB` and ignored by Git.

The legacy databases are retained under `backups/runtime-premerge/` for migration
recovery only. New application writes must use this directory.
