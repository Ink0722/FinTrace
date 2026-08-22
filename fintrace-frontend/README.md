# FinTrace Frontend Prototype

A standalone UI prototype for the FinTrace financial research agent.

## Included

- Light-mode FinTrace visual system using the provided SVG logo
- Multi-conversation sidebar with per-user localStorage persistence
- Local user workspace switcher with create, rename and delete controls
- Chat interface connected to the real FinTrace FastAPI Agent
- Tool-call timeline with pending/running/completed states
- Expandable tool arguments and result summaries
- Evidence / Trace drawer and clickable inline citations
- Backend adapter boundary in `lib/chat-service.ts`
- Responsive layout for desktop and mobile

## Run

```bash
npm install
npm run dev
```

Open http://localhost:3000.

## Backend integration

The existing FinTrace API accepts:

```json
{
  "query": "...",
  "session_id": "SESSION-001",
  "user_id": "USER-DEFAULT"
}
```

Local users and session ownership are stored by FastAPI in the observability SQLite
database. This workspace switcher does not implement passwords or authentication.
The backend session list is authoritative. Browser storage only preserves unsent new
conversations and richer temporary UI state, and is merged after the backend response.

Set the server-side backend address in `.env.local`:

```dotenv
FINTRACE_API_BASE_URL=http://127.0.0.1:8000
```

The browser calls `/api/fintrace/chat/stream`; the Next.js route handler forwards the streaming body from FastAPI `POST /chat/stream`. This avoids browser CORS configuration and keeps the backend address on the server. The non-streaming `POST /chat` remains available for CLI and automated callers.

Start FastAPI from the repository root:

```powershell
F:\conda_envs\FinTrace\python.exe -m app.api.main
```

Start the frontend in another terminal:

```powershell
cd fintrace-frontend
npm install
npm run dev
```

Open `http://localhost:3000`. A browser conversation ID is passed as `session_id`, so follow-up questions in one conversation use the backend multi-turn memory.

The backend persists sessions and turns in `runtime/fintrace.sqlite3`. The browser must not open that file directly. Use the read-only observability endpoints instead:

```text
GET /runs?session_id=SESSION-001&limit=50&offset=0
GET /runs/{run_id}
```

The list endpoint supplies run summaries. The detail endpoint supplies request parsing, tool executions, file and non-file evidence, workflow nodes, LLM call metadata, warnings and errors. SQLite is the sole runtime log source; JSONL is generated only when an evaluation export is needed.

## Streaming API events

The frontend currently consumes these SSE events:

- `turn.started`
- `request.resolved`
- `route.selected`
- `workflow.node`
- `tool.started`
- `tool.completed`
- `evidence.added`
- `answer.delta`
- `answer.completed`
- `turn.completed`
- `workflow.failed`

The backend sends a heartbeat every 15 seconds. `answer.delta` contains only decoded user-facing answer text, never the surrounding structured JSON. `turn.completed` includes the authoritative final state so the UI can reconcile incremental events.

## Session deletion

Each conversation has rename and delete commands in its three-dot menu. Renamed titles
are persisted in SQLite and remain unchanged after refresh. Persisted sessions are
deleted transactionally with their memory, Agent runs, tool calls, evidence, workflow
events, and LLM call records. Unsent blank conversations are removed only from browser
state. Evaluation conversations follow the same deletion rules as ordinary conversations.
