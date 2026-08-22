"""Request-local event bridge for streaming Agent execution."""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Callable


EventEmitter = Callable[[str, dict[str, Any]], None]
_EMITTER: ContextVar[EventEmitter | None] = ContextVar("fintrace_stream_emitter", default=None)


def set_emitter(emitter: EventEmitter) -> Token:
    return _EMITTER.set(emitter)


def reset_emitter(token: Token) -> None:
    _EMITTER.reset(token)


def emit(event: str, payload: dict[str, Any]) -> None:
    emitter = _EMITTER.get()
    if emitter is not None:
        emitter(event, payload)


def streaming_enabled() -> bool:
    return _EMITTER.get() is not None


class JsonAnswerDeltaParser:
    """Incrementally decode only the JSON `answer` string for user display."""

    def __init__(self) -> None:
        self.raw = ""
        self.sent = ""

    def feed(self, chunk: str) -> str:
        self.raw += chunk
        marker = self.raw.find('"answer"')
        if marker < 0:
            return ""
        colon = self.raw.find(":", marker + 8)
        quote = self.raw.find('"', colon + 1) if colon >= 0 else -1
        if quote < 0:
            return ""
        decoded = _decode_partial_json_string(self.raw[quote + 1:])
        delta = decoded[len(self.sent):]
        self.sent = decoded
        return delta


def _decode_partial_json_string(value: str) -> str:
    output: list[str] = []
    index = 0
    escapes = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f"}
    while index < len(value):
        char = value[index]
        if char == '"':
            break
        if char != "\\":
            output.append(char)
            index += 1
            continue
        if index + 1 >= len(value):
            break
        escaped = value[index + 1]
        if escaped == "u":
            if index + 6 > len(value):
                break
            try:
                output.append(chr(int(value[index + 2:index + 6], 16)))
            except ValueError:
                pass
            index += 6
            continue
        output.append(escapes.get(escaped, escaped))
        index += 2
    return "".join(output)
