import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


load_dotenv()


def write_trace(payload: dict[str, Any]) -> None:
    trace_path = Path(os.getenv("TRACE_PATH", "./evaluation/reports/traces.jsonl"))
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"created_at": datetime.now(UTC).isoformat(), **payload}
    with trace_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
