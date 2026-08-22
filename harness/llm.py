import os
import json
from collections.abc import Iterator
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

    @classmethod
    def for_planner(cls) -> "QwenClient":
        return cls(
            api_key=os.getenv("QWEN_PLANNER_API_KEY")
            or os.getenv("DASHSCOPE_PLANNER_API_KEY")
            or os.getenv("QWEN_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY", ""),
            base_url=os.getenv("QWEN_PLANNER_BASE_URL") or os.getenv("QWEN_BASE_URL"),
            model=os.getenv("QWEN_PLANNER_MODEL")
            or os.getenv("QWEN_MODEL")
            or os.getenv("QWEN_CHAT_MODEL", "qwen-plus"),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def chat_json(self, messages: list[dict[str, str]], temperature: float = 0.0) -> dict[str, Any]:
        if not self.enabled:
            return {
                "fallback": True,
                "content": "Qwen API key is not configured. Deterministic fallback was used.",
            }

        messages = self._ensure_json_keyword(messages)
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

    def chat_json_stream(self, messages: list[dict[str, str]], temperature: float = 0.0) -> Iterator[str]:
        """Yield OpenAI-compatible content deltas while preserving JSON mode."""
        if not self.enabled:
            return
        messages = self._ensure_json_keyword(messages)
        with requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model, "messages": messages, "temperature": temperature,
                "response_format": {"type": "json_object"}, "stream": True,
            },
            timeout=120,
            stream=True,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                payload = json.loads(data)
                delta = payload.get("choices", [{}])[0].get("delta", {}).get("content")
                if delta:
                    yield str(delta)

    @staticmethod
    def _ensure_json_keyword(messages: list[dict[str, str]]) -> list[dict[str, str]]:
        """DashScope requires the word 'json' in messages when response_format=json_object."""
        if any("json" in str(message.get("content", "")).lower() for message in messages):
            return messages
        patched = [dict(message) for message in messages]
        for message in reversed(patched):
            if message.get("role") in {"system", "user"}:
                message["content"] = f"{message['content']}\n请以 JSON 格式返回结果。"
                break
        return patched
