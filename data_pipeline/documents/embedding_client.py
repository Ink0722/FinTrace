from __future__ import annotations

import os
import time
from typing import Any

import numpy as np
import requests
from dotenv import load_dotenv


load_dotenv()

RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}


class EmbeddingClient:
    model: str
    dimension: int
    last_usage_tokens: int = 0

    def embed_query(self, text: str) -> np.ndarray:
        raise NotImplementedError


class DashScopeEmbeddingClient(EmbeddingClient):
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        dimension: int | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else (
            os.getenv("DASHSCOPE_EMBEDDING_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY", "")
        )
        configured_base_url = base_url or os.getenv(
            "DASHSCOPE_EMBEDDING_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.base_url = configured_base_url.rstrip("/")
        if "/compatible-mode/" not in self.base_url:
            raise ValueError("Document retrieval requires a DashScope OpenAI-compatible base URL.")
        self.api_mode = "compatible"
        self.model = model or os.getenv("DASHSCOPE_EMBEDDING_MODEL", "text-embedding-v4")
        self.dimension = dimension or int(os.getenv("DASHSCOPE_EMBEDDING_DIMENSION", "1024"))
        self.timeout_seconds = timeout_seconds or float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "120"))
        self.max_retries = max_retries if max_retries is not None else int(os.getenv("EMBEDDING_MAX_RETRIES", "5"))
        if self.dimension not in {64, 128, 256, 512, 768, 1024, 1536, 2048}:
            raise ValueError(f"Unsupported text-embedding-v4 dimension: {self.dimension}")
        self.last_usage_tokens = 0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def embed_query(self, text: str) -> np.ndarray:
        if not self.enabled:
            raise RuntimeError(
                "DASHSCOPE_EMBEDDING_API_KEY or DASHSCOPE_API_KEY is required for Qwen embedding."
            )
        payload = self._request([text])
        vectors = read_embedding_data(payload)
        if len(vectors) != 1:
            raise RuntimeError(f"Expected one query embedding, got {len(vectors)}.")
        matrix = np.asarray(vectors, dtype="float32")
        if matrix.shape != (1, self.dimension):
            raise RuntimeError(
                f"Embedding dimension mismatch: expected {(1, self.dimension)}, got {matrix.shape}."
            )
        self.last_usage_tokens = read_usage_tokens(payload)
        return normalize_embeddings(matrix)[0]

    def _request(self, texts: list[str]) -> dict[str, Any]:
        url = f"{self.base_url}/embeddings"
        body = {
            "model": self.model,
            "input": texts,
            "dimensions": self.dimension,
            "encoding_format": "float",
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=self.timeout_seconds,
                )
                if response.status_code in RETRYABLE_STATUS_CODES and attempt < self.max_retries:
                    time.sleep(min(2**attempt, 16))
                    continue
                response.raise_for_status()
                payload = response.json()
                validate_embedding_response(payload)
                return payload
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(min(2**attempt, 16))
            except (requests.HTTPError, ValueError, RuntimeError) as exc:
                last_error = exc
                break
        raise RuntimeError(
            f"DashScope embedding request failed after {self.max_retries + 1} attempts: {last_error}"
        ) from last_error

def build_embedding_client() -> EmbeddingClient:
    return DashScopeEmbeddingClient()


def validate_embedding_response(payload: dict[str, Any]) -> None:
    if isinstance(payload.get("error"), dict):
        raise RuntimeError(str(payload["error"].get("message") or payload["error"]))
    if not isinstance(payload.get("data"), list):
        raise RuntimeError("Embedding response missing data list.")


def read_embedding_data(payload: dict[str, Any]) -> list[list[float]]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("Embedding response missing data list.")
    ordered = sorted(data, key=lambda item: item.get("index", 0))
    embeddings = [item.get("embedding") for item in ordered]
    if not all(isinstance(item, list) for item in embeddings):
        raise RuntimeError("Embedding response contains invalid embedding values.")
    return embeddings


def read_usage_tokens(payload: dict[str, Any]) -> int:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return 0
    value = usage.get("prompt_tokens", usage.get("total_tokens", 0))
    return int(value) if isinstance(value, (int, float)) else 0


def normalize_embeddings(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (vectors / norms).astype("float32")
