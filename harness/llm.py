import os
import json
from collections.abc import Iterator
from typing import Any

import requests
from dotenv import load_dotenv


load_dotenv()
_UNSET = object()


class QwenClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_output_tokens: int | None | object = _UNSET,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
        self.base_url = (
            base_url if base_url is not None else os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        ).rstrip("/")
        self.model = model if model is not None else os.getenv("QWEN_MODEL") or os.getenv("QWEN_CHAT_MODEL", "qwen-plus")
        self.max_output_tokens = (
            _optional_positive_int(os.getenv("QWEN_MAX_OUTPUT_TOKENS"))
            if max_output_tokens is _UNSET
            else max_output_tokens
        )
        self.last_finish_reason: str | None = None
        self.last_usage: dict[str, Any] = {}

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
            max_output_tokens=_optional_positive_int(os.getenv("QWEN_PLANNER_MAX_OUTPUT_TOKENS")),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def chat_json(self, messages: list[dict[str, str]], temperature: float = 0.0) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("Qwen API key is not configured.")

        messages = self._ensure_json_keyword(messages)
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        if self.max_output_tokens is not None:
            body["max_tokens"] = self.max_output_tokens
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        choice = payload.get("choices", [{}])[0]
        self.last_finish_reason = choice.get("finish_reason")
        self.last_usage = payload.get("usage") or {}
        return payload

    def chat_json_stream(self, messages: list[dict[str, str]], temperature: float = 0.0) -> Iterator[str]:
        """Yield OpenAI-compatible content deltas while preserving JSON mode."""
        if not self.enabled:
            raise RuntimeError("Qwen API key is not configured.")
        messages = self._ensure_json_keyword(messages)
        self.last_finish_reason = None
        self.last_usage = {}
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "stream": True,
        }
        if self.max_output_tokens is not None:
            body["max_tokens"] = self.max_output_tokens
        with requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=body,
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
                choices = payload.get("choices") or []
                choice = choices[0] if choices else {}
                if choice.get("finish_reason") is not None:
                    self.last_finish_reason = choice["finish_reason"]
                if payload.get("usage"):
                    self.last_usage = payload["usage"]
                delta = choice.get("delta", {}).get("content")
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


def _optional_positive_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("LLM output token limits must be positive integers.")
    return parsed
