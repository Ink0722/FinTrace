"""Unified LLM skill runner: assemble prompt, call model, validate output schema, record trace."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from harness.llm import QwenClient
from harness.streaming import JsonAnswerDeltaParser, emit, streaming_enabled
from harness.prompts import SKILL_REGISTRY, PromptFileError, build_system_prompt, load_prompt, elapsed_ms
from schemas.request import LlmCallRecord


def run_skill(
    skill: str,
    runtime_context: dict[str, Any],
    *,
    client: QwenClient | None = None,
) -> tuple[BaseModel | None, LlmCallRecord]:
    """Run one prompt skill. Returns (validated output | None, trace record).

    The output model is validated against the skill's registered schema; one retry
    with the validation error appended is attempted before giving up.
    """
    from schemas.request import (  # local import keeps the model registry in one place
        ActionRepairResult,
        AgentAction,
        EvidenceReview,
        FinalAnswer,
        ParsedRequest,
    )

    output_models: dict[str, type[BaseModel]] = {
        "request_parser": ParsedRequest,
        "next_action_planner": AgentAction,
        "evidence_reviewer": EvidenceReview,
        "action_repair": ActionRepairResult,
        "final_answer": FinalAnswer,
    }
    model_class = output_models.get(skill)
    if model_class is None:
        # P2 skills (planner/reviewer/repair/final_answer) register their models on arrival.
        raise PromptFileError(f"Skill has no output model registered yet: {skill}")

    skill_file = load_prompt(SKILL_REGISTRY[skill][0])
    payload = json.dumps(runtime_context, ensure_ascii=False, default=str)
    input_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    active_client = client or client_for_skill(skill)
    if not active_client.enabled:
        return None, LlmCallRecord(
            prompt_id=skill_file.prompt_id,
            prompt_version=skill_file.version,
            model=active_client.model,
            input_hash=input_hash,
            output_schema=model_class.__name__,
            status="failed",
        )

    system_prompt = build_system_prompt(skill)
    record_kwargs = {
        "prompt_id": skill_file.prompt_id,
        "prompt_version": skill_file.version,
        "model": active_client.model,
        "input_hash": input_hash,
        "output_schema": model_class.__name__,
    }
    status = "success"
    last_error: str = ""
    for attempt in range(2):
        started = time.perf_counter()
        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": payload if attempt == 0 else payload + _retry_hint(last_error)},
            ]
            if skill == "final_answer" and attempt == 0 and streaming_enabled():
                parser = JsonAnswerDeltaParser()
                chunks = []
                for chunk in active_client.chat_json_stream(messages, temperature=0.0):
                    chunks.append(chunk)
                    delta = parser.feed(chunk)
                    if delta:
                        emit("answer.delta", {"text": delta})
                content = "".join(chunks)
            else:
                response = active_client.chat_json(messages, temperature=0.0)
                content = response["choices"][0]["message"]["content"]
            model_output = model_class.model_validate(json.loads(content))
            if attempt > 0:
                status = "recovered"
            return model_output, LlmCallRecord(latency_ms=elapsed_ms(started), status=status, **record_kwargs)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # network / API failures
            last_error = f"{type(exc).__name__}: {exc}"
    return None, LlmCallRecord(status="failed", **record_kwargs)


def _retry_hint(error: str) -> str:
    return (
        "\n\n--- 上一次输出未通过 Schema 校验，错误如下，请修正后重新输出符合 Schema 的 JSON ---\n"
        f"{error}"
    )


PLANNER_SKILLS = {"request_parser", "next_action_planner", "evidence_reviewer", "action_repair"}


def client_for_skill(skill: str) -> QwenClient:
    return QwenClient.for_planner() if skill in PLANNER_SKILLS else QwenClient()
