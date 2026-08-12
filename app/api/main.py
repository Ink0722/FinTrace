from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

from harness.graph.workflow import run_agent


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


if __name__ == "__main__":
    uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000, reload=False)
