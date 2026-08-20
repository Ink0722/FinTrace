"""Pre-Answerability (Gate B): capability existence and slot completeness, never data lookups."""
from __future__ import annotations

from schemas.request import ParsedRequest, PreAnswerability

from harness.routing.capability_registry import CAPABILITIES

UNSUPPORTED_FAMILIES = {
    "realtime_market_query": "现有数据不包含历史或实时行情。",
    "user_account_query": "系统不处理用户账户、持仓或交易。",
    "prediction_request": "系统不提供缺乏证据的确定性预测或投资建议。",
    "ownership_penetration": "多跳股权穿透（penetration）当前版本未实现。",
}

SLOT_QUESTIONS = {
    "company_ids": "请明确要查询的上市公司（名称或证券代码）。",
    "report_periods": "请明确报告期，例如 2024 年、2024 年一季度或半年报。",
    "metric_codes": "请明确要查询的财务指标，例如营业收入、净利润或经营现金流。",
    "start_date_and_end_date": "请给出比较的两个时间点或区间，例如 2024 年中和年末。",
}


def check_answerability(parsed: ParsedRequest) -> PreAnswerability:
    if parsed.requires_realtime or parsed.task_family in UNSUPPORTED_FAMILIES:
        return PreAnswerability(
            status="unsupported",
            capability=parsed.task_family,
            reason=UNSUPPORTED_FAMILIES.get(parsed.task_family, "请求超出系统能力边界。"),
        )

    if parsed.requires_investigation or parsed.task_family in {
        "financial_investigation",
        "event_investigation",
        "general_financial_explanation",
        "unknown",
    }:
        # Investigation fills slots adaptively; the planner can still clarify mid-loop if stuck.
        if not parsed.entities and not parsed.people and parsed.unresolved_references:
            return _clarify(parsed, ["company_ids"])
        return PreAnswerability(status="routeable", capability=parsed.task_family, reason="复杂调查任务，进入有界调查循环。")

    missing = _missing_slots(parsed)
    if missing:
        return _clarify(parsed, missing)
    return PreAnswerability(status="routeable", capability=parsed.task_family, reason="能力存在且必要参数完整。")


def _missing_slots(parsed: ParsedRequest) -> list[str]:
    if parsed.unresolved_references and not parsed.entities:
        return ["company_ids"]
    family = parsed.task_family
    if family in {"financial_metric_query", "financial_metric_compare"}:
        missing = []
        if not parsed.entities:
            missing.append("company_ids")
        if not parsed.metrics:
            missing.append("metric_codes")
        if not parsed.periods:
            missing.append("report_periods")
        if family == "financial_metric_compare":
            if len(parsed.periods) < 2 and len(parsed.entities) < 2:
                missing.append("report_periods")
        return missing
    if family == "ownership_snapshot":
        return [] if (parsed.entities or parsed.people) else ["company_ids"]
    if family == "ownership_compare":
        missing = []
        if not parsed.entities:
            missing.append("company_ids")
        if not (parsed.start_date and parsed.end_date) and len(parsed.periods) < 2 and len(parsed.as_of_dates) < 2:
            missing.append("start_date_and_end_date")
        return missing
    if family == "document_retrieval":
        return [] if parsed.entities or "公告" in parsed.raw_query or "研报" in parsed.raw_query else ["company_ids"]
    if family == "event_query":
        return [] if parsed.entities else ["company_ids"]
    return []


def _clarify(parsed: ParsedRequest, missing: list[str]) -> PreAnswerability:
    questions = [SLOT_QUESTIONS.get(slot, f"缺少必要条件：{slot}") for slot in missing]
    return PreAnswerability(
        status="clarification_required",
        capability=parsed.task_family,
        reason=f"缺少必要参数：{missing}",
        missing_slots=missing,
        clarification_question=" ".join(questions),
    )


def is_investigation(parsed: ParsedRequest) -> bool:
    """Gate C: direct only for unambiguous, single-capability, complete-slot requests."""
    if parsed.requires_investigation or parsed.requires_explanation or parsed.requires_realtime:
        return True
    if parsed.task_family in {"financial_investigation", "event_investigation", "general_financial_explanation", "unknown"}:
        return True
    if len(parsed.entities) > 1 and parsed.task_family in {"document_retrieval", "event_query", "ownership_snapshot"}:
        return True  # multi-entity composite -> let the planner decide
    return False
