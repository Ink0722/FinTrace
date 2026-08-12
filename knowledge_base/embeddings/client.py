import hashlib
import os
from typing import Any

import numpy as np
import requests
from dotenv import load_dotenv


load_dotenv()


class EmbeddingClient:
    def embed_documents(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_documents([text])[0]


class DashScopeEmbeddingClient(EmbeddingClient):
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        batch_size: int | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("DASHSCOPE_EMBEDDING_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
        self.base_url = (base_url or os.getenv("DASHSCOPE_EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")).rstrip("/")
        self.model = model or os.getenv("DASHSCOPE_EMBEDDING_MODEL", "text-embedding-v4")
        self.batch_size = batch_size or int(os.getenv("EMBEDDING_BATCH_SIZE", "16"))

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not self.enabled:
            raise RuntimeError("DASHSCOPE_EMBEDDING_API_KEY or DASHSCOPE_API_KEY is required to build vector index.")

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            response = requests.post(
                f"{self.base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "input": batch,
                    "encoding_format": "float",
                },
                timeout=120,
            )
            response.raise_for_status()
            payload = response.json()
            vectors.extend(read_embedding_data(payload))
        return normalize_embeddings(np.asarray(vectors, dtype="float32"))


class HashEmbeddingClient(EmbeddingClient):
    """Deterministic local embeddings for tests and offline smoke checks."""

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim
        self.model = f"hash-{dim}"

    @property
    def enabled(self) -> bool:
        return True

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        vectors = np.zeros((len(texts), self.dim), dtype="float32")
        for row, text in enumerate(texts):
            for token in text.lower().split():
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dim
                vectors[row, index] += 1.0
        return normalize_embeddings(vectors)


def build_embedding_client() -> EmbeddingClient:
    provider = os.getenv("EMBEDDING_PROVIDER", "dashscope").lower()
    if provider == "hash":
        return HashEmbeddingClient(dim=int(os.getenv("HASH_EMBEDDING_DIM", "64")))
    if provider != "dashscope":
        raise RuntimeError(f"Unsupported EMBEDDING_PROVIDER: {provider}")
    return DashScopeEmbeddingClient()


def read_embedding_data(payload: dict[str, Any]) -> list[list[float]]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("Embedding response missing data list.")
    ordered = sorted(data, key=lambda item: item.get("index", 0))
    embeddings = [item.get("embedding") for item in ordered]
    if not all(isinstance(item, list) for item in embeddings):
        raise RuntimeError("Embedding response contains invalid embedding values.")
    return embeddings


def normalize_embeddings(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (vectors / norms).astype("float32")
