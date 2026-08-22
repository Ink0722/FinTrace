# FinTrace Evaluation Runtime

`runtime/fintrace.sqlite3` is the single source of truth for Agent session memory,
run logs and evaluation batches. It is generated locally and ignored by Git.

The database stores one row per run plus related tool executions, file and non-file evidence, workflow node events, and LLM call metadata. It does not store API keys or private model reasoning.

## Runtime configuration

```dotenv
FINTRACE_EVAL_LOG_ENABLED=true
FINTRACE_RUNTIME_DB=./runtime/fintrace.sqlite3
```

## Read through FastAPI

```http
GET /runs?session_id=SESSION-001&limit=50&offset=0
GET /runs/{run_id}
```

The frontend should use these endpoints instead of opening SQLite directly.

Live chat execution uses `POST /chat/stream`. Incremental SSE events are displayed immediately, while the completed turn is persisted to this same database exactly once.

## Export JSONL when needed

```powershell
F:\conda_envs\FinTrace\python.exe -m harness.tracing.export_jsonl evaluation\exports\agent_runs.jsonl
```

JSONL is an export format only and is not updated during normal Agent execution.

## Legacy migration

```powershell
F:\conda_envs\FinTrace\python.exe -m harness.tracing.migrate_jsonl `
  --traces <legacy-traces.jsonl> `
  --turns <legacy-agent-turns.jsonl>
```

The original project logs have already been migrated and removed. The migration command remains for importing other legacy copies.

The former session and observability databases are retained under
`backups/runtime-premerge/` as migration backups.
They are no longer written by CLI, API, frontend or evaluation execution.

## Dataset execution

The resumable multi-turn runner is documented in
[`runner/README.md`](runner/README.md). Evaluation batches use dedicated local users,
fixed knowledge cutoffs and `run_id` links into the same observability database.
