from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


load_dotenv()

RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
TERMINAL_BATCH_STATUSES = {"completed", "failed", "expired", "cancelled"}


class DashScopeBatchClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else (
            os.getenv("DASHSCOPE_EMBEDDING_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY", "")
        )
        self.base_url = (
            base_url
            or os.getenv(
                "DASHSCOPE_EMBEDDING_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            )
        ).rstrip("/")
        if "/compatible-mode/" not in self.base_url:
            raise ValueError("Batch File requires a DashScope OpenAI-compatible base URL.")
        self.timeout_seconds = timeout_seconds or float(
            os.getenv("EMBEDDING_TIMEOUT_SECONDS", "120")
        )
        self.max_retries = max_retries if max_retries is not None else int(
            os.getenv("EMBEDDING_MAX_RETRIES", "5")
        )
        if not self.api_key:
            raise RuntimeError(
                "DASHSCOPE_EMBEDDING_API_KEY or DASHSCOPE_API_KEY is required for Batch File."
            )

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def upload_file(self, path: Path) -> str:
        with path.open("rb") as handle:
            response = requests.post(
                f"{self.base_url}/files",
                headers=self.headers,
                data={"purpose": "batch"},
                files={"file": (path.name, handle, "application/jsonl")},
                timeout=self.timeout_seconds,
            )
        payload = self._decode_response(response, operation="upload Batch file")
        file_id = payload.get("id")
        if not isinstance(file_id, str) or not file_id:
            raise RuntimeError("Batch upload response does not contain a file id.")
        return file_id

    def create_batch(
        self,
        input_file_id: str,
        *,
        completion_window: str = "24h",
        name: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "input_file_id": input_file_id,
            "endpoint": "/v1/embeddings",
            "completion_window": completion_window,
        }
        if name:
            body["metadata"] = {"ds_name": name}
        response = requests.post(
            f"{self.base_url}/batches",
            headers={**self.headers, "Content-Type": "application/json"},
            json=body,
            timeout=self.timeout_seconds,
        )
        return self._decode_response(response, operation="create Batch job")

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/batches/{batch_id}")

    def download_file(self, file_id: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f"{destination.name}.tmp")
        response = self._request("GET", f"/files/{file_id}/content", stream=True)
        with temporary.open("wb") as handle:
            for block in response.iter_content(chunk_size=1024 * 1024):
                if block:
                    handle.write(block)
        os.replace(temporary, destination)

    def _request_json(self, method: str, path: str) -> dict[str, Any]:
        response = self._request(method, path)
        return self._decode_response(response, operation=f"{method} {path}")

    def _request(self, method: str, path: str, *, stream: bool = False) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=self.headers,
                    timeout=self.timeout_seconds,
                    stream=stream,
                )
                if response.status_code in RETRYABLE_STATUS_CODES and attempt < self.max_retries:
                    response.close()
                    time.sleep(min(2**attempt, 16))
                    continue
                response.raise_for_status()
                return response
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(min(2**attempt, 16))
            except requests.HTTPError as exc:
                raise RuntimeError(self._http_error_message(exc.response)) from exc
        raise RuntimeError(f"DashScope request failed: {last_error}") from last_error

    @staticmethod
    def _decode_response(response: requests.Response, *, operation: str) -> dict[str, Any]:
        try:
            response.raise_for_status()
            payload = response.json()
        except requests.HTTPError as exc:
            raise RuntimeError(DashScopeBatchClient._http_error_message(response)) from exc
        except ValueError as exc:
            raise RuntimeError(f"DashScope could not {operation}: invalid JSON response.") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"DashScope could not {operation}: response is not an object.")
        if isinstance(payload.get("error"), dict):
            error = payload["error"]
            raise RuntimeError(str(error.get("message") or error))
        return payload

    @staticmethod
    def _http_error_message(response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or error)
            if payload.get("message"):
                return str(payload["message"])
        return f"DashScope HTTP {response.status_code}: {response.text[:500]}"
