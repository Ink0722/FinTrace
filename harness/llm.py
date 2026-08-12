import os
from typing import Any

import requests
from dotenv import load_dotenv


load_dotenv()


class QwenClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
        self.base_url = (
            base_url if base_url is not None else os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        ).rstrip("/")
        self.model = model if model is not None else os.getenv("QWEN_MODEL") or os.getenv("QWEN_CHAT_MODEL", "qwen-plus")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def chat_json(self, messages: list[dict[str, str]], temperature: float = 0.0) -> dict[str, Any]:
        if not self.enabled:
            return {
                "fallback": True,
                "content": "Qwen API key is not configured. Deterministic fallback was used.",
            }

        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "response_format": {"type": "json_object"},
            },
            timeout=120,
        )
        response.raise_for_status()
        return response.json()
