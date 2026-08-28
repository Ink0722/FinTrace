import json
import hmac
import os
import queue
import threading

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import uvicorn

from harness.graph.workflow import run_agent, stream_agent
from harness.tracing.store import get_run, list_runs
from harness.tracing.store import DEFAULT_USER_ID, SHOWCASE_USER_ID
from harness.tracing.users import (
    ReadOnlySessionError, claim_session, delete_session, get_user_session_detail,
    list_user_sessions, rename_session,
)


app = FastAPI(title="FinTrace API", version="0.1.0")


def showcase_mode() -> bool:
    return os.getenv("FINTRACE_DEPLOYMENT_MODE", "").strip().lower() == "showcase"


def showcase_user_id() -> str:
    return os.getenv("FINTRACE_SHOWCASE_USER_ID", SHOWCASE_USER_ID).strip() or SHOWCASE_USER_ID


@app.middleware("http")
async def protect_internal_api(request: Request, call_next):
    if showcase_mode() and request.url.path != "/health":
        expected = os.getenv("FINTRACE_INTERNAL_API_KEY", "")
        supplied = request.headers.get("X-FinTrace-Internal-Key", "")
        if not expected or not hmac.compare_digest(supplied, expected):
            return JSONResponse(status_code=401, content={"detail": "Internal API authentication failed"})
    return await call_next(request)


class ChatRequest(BaseModel):
    query: str
    session_id: str = "SESSION-001"
    user_id: str = DEFAULT_USER_ID


class SessionRequest(BaseModel):
    title: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat")
def chat(request: ChatRequest) -> dict:
    _claim_chat_session(request)
    result = run_agent(request.query, session_id=request.session_id)
    return result.model_dump()


@app.post("/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    """Stream LangGraph progress and genuine Qwen answer deltas over SSE."""
    _claim_chat_session(request)
    events: queue.Queue[tuple[str, dict] | None] = queue.Queue()

    def emit(event: str, payload: dict) -> None:
        events.put((event, payload))

    def worker() -> None:
        try:
            stream_agent(request.query, request.session_id, emit)
        except Exception as exc:
            emit("workflow.failed", {"error_type": type(exc).__name__, "message": str(exc)})
        finally:
            events.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def event_stream():
        while True:
            try:
                item = events.get(timeout=15)
            except queue.Empty:
                yield ": heartbeat\n\n"
                continue
            if item is None:
                break
            event, payload = item
            yield f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(
        event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.get("/runs")
def runs(
    session_id: str | None = None,
    user_id: str | None = None,
    answer_status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """List persisted Agent turns for the frontend observability view."""
    items = list_runs(
        session_id=session_id, user_id=user_id, answer_status=answer_status,
        limit=limit, offset=offset,
    )
    return {"items": items, "limit": limit, "offset": offset}


@app.get("/runs/{run_id}")
def run_detail(run_id: str) -> dict:
    """Return one complete, structured Agent execution record."""
    result = get_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return result


@app.get("/showcase/sessions")
def showcase_sessions() -> dict:
    """Return the single public showcase workspace without exposing user APIs."""
    return {"items": list_user_sessions(showcase_user_id())}


@app.get("/showcase/sessions/{session_id}")
def showcase_session_detail(
    session_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    before_turn: int | None = Query(default=None, ge=1),
) -> dict:
    result = get_user_session_detail(
        showcase_user_id(), session_id, limit=limit, before_turn=before_turn,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return result


@app.delete("/showcase/sessions/{session_id}", status_code=204)
def remove_showcase_session(session_id: str) -> None:
    try:
        deleted = delete_session(showcase_user_id(), session_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")


@app.patch("/showcase/sessions/{session_id}")
def edit_showcase_session(session_id: str, request: SessionRequest) -> dict:
    if not request.title.strip():
        raise HTTPException(status_code=422, detail="title cannot be empty")
    try:
        result = rename_session(showcase_user_id(), session_id, request.title)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return result


def _claim_chat_session(request: ChatRequest) -> None:
    user_id = showcase_user_id() if showcase_mode() else request.user_id
    try:
        claim_session(user_id, request.session_id, request.query)
    except ReadOnlySessionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


if __name__ == "__main__":
    uvicorn.run(
        "app.api.main:app",
        host=os.getenv("FINTRACE_API_HOST", "127.0.0.1"),
        port=int(os.getenv("FINTRACE_API_PORT", "8000")),
        reload=False,
    )
