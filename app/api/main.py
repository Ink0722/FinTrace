import json
import queue
import threading

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import uvicorn

from harness.graph.workflow import run_agent, stream_agent
from harness.tracing.store import get_run, list_runs


app = FastAPI(title="FinTrace API", version="0.1.0")


class ChatRequest(BaseModel):
    query: str
    session_id: str = "SESSION-001"


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat")
def chat(request: ChatRequest) -> dict:
    result = run_agent(request.query, session_id=request.session_id)
    return result.model_dump()


@app.post("/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    """Stream LangGraph progress and genuine Qwen answer deltas over SSE."""
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
    answer_status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """List persisted Agent turns for the frontend observability view."""
    items = list_runs(
        session_id=session_id, answer_status=answer_status, limit=limit, offset=offset,
    )
    return {"items": items, "limit": limit, "offset": offset}


@app.get("/runs/{run_id}")
def run_detail(run_id: str) -> dict:
    """Return one complete, structured Agent execution record."""
    result = get_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return result


if __name__ == "__main__":
    uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000, reload=False)
