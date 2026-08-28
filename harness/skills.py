"""Unified LLM skill runner: assemble prompt, call model, validate output schema, record trace."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from harness.llm import QwenClient
from harness.streaming import emit, streaming_enabled
from harness.prompts import SKILL_REGISTRY, PromptFileError, build_system_prompt, load_prompt, elapsed_ms
from schemas.request import LlmCallRecord


CITATION_MAPPING_LIMITATION = "回答正文已生成，但证据引用映射未完成，引用关系需进一步核验。"


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
    from schemas.memory import MemoryUpdate

    output_models: dict[str, type[BaseModel]] = {
        "request_parser": ParsedRequest,
        "next_action_planner": AgentAction,
        "evidence_reviewer": EvidenceReview,
        "action_repair": ActionRepairResult,
        "final_answer": FinalAnswer,
        "memory_summarizer": MemoryUpdate,
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
            error_type="LLM_NOT_CONFIGURED",
            error_message="Qwen API key is not configured.",
        )

    system_prompt = build_system_prompt(skill)
    record_kwargs = {
        "prompt_id": skill_file.prompt_id,
        "prompt_version": skill_file.version,
        "model": active_client.model,
        "input_hash": input_hash,
        "output_schema": model_class.__name__,
    }
    total_started = time.perf_counter()
    last_error: str = ""
    last_error_type: str | None = None
    last_finish_reason: str | None = None
    last_response_chars = 0
    last_response_content = ""
    last_response_tail: str | None = None
    last_validation_stage: str | None = None
    last_validation_error: str | None = None
    last_usage: dict[str, Any] = {}
    for attempt in range(2):
        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": payload if attempt == 0 else _retry_payload(
                        runtime_context,
                        error=last_error,
                        previous_output=last_response_content,
                        skill=skill,
                    ),
                },
            ]
            if skill == "final_answer" and attempt == 0 and streaming_enabled():
                chunks = []
                for chunk in active_client.chat_json_stream(messages, temperature=0.0):
                    chunks.append(chunk)
                content = "".join(chunks)
                finish_reason = active_client.last_finish_reason
                usage = active_client.last_usage
            else:
                response = active_client.chat_json(messages, temperature=0.0)
                content = response["choices"][0]["message"]["content"]
                finish_reason = response.get("choices", [{}])[0].get("finish_reason")
                usage = response.get("usage") or active_client.last_usage
            last_finish_reason = finish_reason
            last_usage = usage or {}
            last_response_chars = len(content)
            last_response_content = content
            last_response_tail = content[-500:]
            if finish_reason not in {None, "stop"}:
                raise ValueError(f"completion ended with finish_reason={finish_reason}")
            decoded = json.loads(content)
            if skill == "request_parser":
                # The query is authoritative runtime input, not a value the
                # model should be allowed to omit or rewrite.
                decoded["raw_query"] = runtime_context["raw_query"]
            model_output = model_class.model_validate(decoded)
            if skill == "final_answer":
                _normalize_final_answer(model_output, runtime_context)
            citation_mapping_missing = _validate_output_semantics(
                skill,
                model_output,
                runtime_context,
                allow_missing_citations=skill == "final_answer" and attempt == 1,
            )
            if citation_mapping_missing:
                last_validation_stage = "final_answer_metadata"
                last_validation_error = CITATION_MAPPING_LIMITATION
            if skill == "final_answer" and streaming_enabled():
                _emit_validated_answer(model_output)
            return model_output, LlmCallRecord(
                latency_ms=elapsed_ms(total_started),
                status="recovered" if attempt else "success",
                attempt_count=attempt + 1,
                finish_reason=finish_reason,
                prompt_tokens=_usage_value(last_usage, "prompt_tokens", "input_tokens"),
                completion_tokens=_usage_value(last_usage, "completion_tokens", "output_tokens"),
                response_chars=last_response_chars,
                response_tail=last_response_tail if citation_mapping_missing else None,
                validation_stage=last_validation_stage,
                validation_error=last_validation_error,
                **record_kwargs,
            )
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            last_error_type = type(exc).__name__
            last_error = f"{type(exc).__name__}: {exc}"
            last_validation_stage = "response_validation"
            last_validation_error = last_error
        except Exception as exc:  # network / API failures
            last_error_type = type(exc).__name__
            last_error = f"{type(exc).__name__}: {exc}"
            last_validation_stage = "llm_request"
            last_validation_error = last_error
    return None, LlmCallRecord(
        latency_ms=elapsed_ms(total_started),
        status="failed",
        attempt_count=2,
        finish_reason=last_finish_reason,
        prompt_tokens=_usage_value(last_usage, "prompt_tokens", "input_tokens"),
        completion_tokens=_usage_value(last_usage, "completion_tokens", "output_tokens"),
        response_chars=last_response_chars,
        response_tail=last_response_tail,
        validation_stage=last_validation_stage,
        validation_error=last_validation_error,
        error_type=last_error_type,
        error_message=last_error[:1000] or "LLM skill failed without an error message.",
        **record_kwargs,
    )


def _retry_payload(
    runtime_context: dict[str, Any],
    *,
    error: str,
    previous_output: str,
    skill: str,
) -> str:
    retry_context = dict(runtime_context)
    repair_request: dict[str, Any] = {
        "validation_error": error,
        "previous_output": previous_output,
        "instruction": "保留上次输出中正确的内容，只修复校验错误，并重新输出完整 JSON。",
    }
    if skill == "final_answer":
        repair_request.update({
            "allowed_evidence_ids": _available_evidence_ids(runtime_context),
            "required_limitations": list(runtime_context.get("limitations") or []),
            "constraints": [
                "不得增加 supporting_evidence 之外的新事实。",
                "used_evidence_ids 只能从 allowed_evidence_ids 中选择。",
                "若 answer_status=answered 且候选证据非空，至少选择一个实际支持正文的 Evidence ID。",
                "limitations_disclosed 必须覆盖 required_limitations。",
            ],
        })
    retry_context["_repair_request"] = repair_request
    return json.dumps(retry_context, ensure_ascii=False, default=str)


def _validate_output_semantics(
    skill: str,
    output: BaseModel,
    runtime_context: dict[str, Any],
    *,
    allow_missing_citations: bool = False,
) -> bool:
    if skill != "final_answer":
        return False
    answer = str(getattr(output, "answer", "")).strip()
    if len(answer) < 8:
        raise ValueError("final answer is empty or too short")

    available_ids = set(_available_evidence_ids(runtime_context))
    used_ids = list(getattr(output, "used_evidence_ids", []))
    unknown_ids = [item for item in used_ids if item not in available_ids]
    if unknown_ids:
        raise ValueError(f"used_evidence_ids are not present in supporting_evidence: {unknown_ids}")
    if runtime_context.get("answer_status") == "answered" and available_ids and not used_ids:
        if not allow_missing_citations:
            raise ValueError("answered output must cite at least one supporting evidence id")
        output.limitations_disclosed = _deduplicate([
            *output.limitations_disclosed,
            CITATION_MAPPING_LIMITATION,
        ])
        return True
    return False


def _normalize_final_answer(output: BaseModel, runtime_context: dict[str, Any]) -> None:
    available_ids = set(_available_evidence_ids(runtime_context))
    output.used_evidence_ids = _deduplicate([
        item for item in output.used_evidence_ids if item in available_ids
    ])
    output.limitations_disclosed = _deduplicate([
        *output.limitations_disclosed,
        *(runtime_context.get("limitations") or []),
    ])


def _available_evidence_ids(runtime_context: dict[str, Any]) -> list[str]:
    return _deduplicate([
        str(item.get("evidence_id"))
        for item in runtime_context.get("supporting_evidence", [])
        if item.get("evidence_id")
    ])


def _deduplicate(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _emit_validated_answer(output: BaseModel) -> None:
    answer = str(getattr(output, "answer", ""))
    for offset in range(0, len(answer), 80):
        emit("answer.delta", {"text": answer[offset:offset + 80]})


def _usage_value(usage: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, int):
            return value
    return None


PLANNER_SKILLS = {
    "request_parser", "next_action_planner", "evidence_reviewer", "action_repair", "memory_summarizer"
}


def client_for_skill(skill: str) -> QwenClient:
    return QwenClient.for_planner() if skill in PLANNER_SKILLS else QwenClient()
