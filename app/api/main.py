import json
import queue
import threading

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import uvicorn

from harness.graph.workflow import run_agent, stream_agent
from harness.tracing.store import get_run, list_runs
from harness.tracing.store import DEFAULT_USER_ID
from harness.tracing.users import (
    claim_session, create_user, delete_session, delete_user, get_user,
    get_user_session_detail, list_user_sessions, list_users, rename_session, update_user,
)


app = FastAPI(title="FinTrace API", version="0.1.0")


class ChatRequest(BaseModel):
    query: str
    session_id: str = "SESSION-001"
    user_id: str = DEFAULT_USER_ID


class UserRequest(BaseModel):
    display_name: str
    avatar_color: str = "#078b98"


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


@app.get("/users")
def users() -> dict:
    return {"items": list_users()}


@app.post("/users", status_code=201)
def add_user(request: UserRequest) -> dict:
    if not request.display_name.strip():
        raise HTTPException(status_code=422, detail="display_name cannot be empty")
    return create_user(request.display_name, request.avatar_color)


@app.patch("/users/{user_id}")
def edit_user(user_id: str, request: UserRequest) -> dict:
    result = update_user(user_id, request.display_name, request.avatar_color)
    if result is None:
        raise HTTPException(status_code=404, detail="User not found")
    return result


@app.delete("/users/{user_id}", status_code=204)
def remove_user(user_id: str) -> None:
    if not delete_user(user_id):
        raise HTTPException(status_code=409, detail="Default or last user cannot be deleted")


@app.get("/users/{user_id}/sessions")
def user_sessions(user_id: str) -> dict:
    if get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {"items": list_user_sessions(user_id)}


@app.get("/users/{user_id}/sessions/{session_id}")
def user_session_detail(
    user_id: str,
    session_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    before_turn: int | None = Query(default=None, ge=1),
) -> dict:
    try:
        result = get_user_session_detail(
            user_id, session_id, limit=limit, before_turn=before_turn,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return result


@app.delete("/users/{user_id}/sessions/{session_id}", status_code=204)
def remove_session(user_id: str, session_id: str) -> None:
    try:
        deleted = delete_session(user_id, session_id)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")


@app.patch("/users/{user_id}/sessions/{session_id}")
def edit_session(user_id: str, session_id: str, request: SessionRequest) -> dict:
    if not request.title.strip():
        raise HTTPException(status_code=422, detail="title cannot be empty")
    try:
        result = rename_session(user_id, session_id, request.title)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return result


def _claim_chat_session(request: ChatRequest) -> None:
    try:
        claim_session(request.user_id, request.session_id, request.query)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


if __name__ == "__main__":
    uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000, reload=False)
