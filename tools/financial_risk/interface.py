from schemas.enums import ErrorType, ToolName, ToolStatus
from schemas.tool_calls import ToolCall
from schemas.tool_results import ToolError, ToolMetrics, ToolResult
from tools.financial_risk.data_source import load_financial_dataset
from tools.financial_risk.metrics import calculate_metrics, evidence_from_records, latest_metric_map
from tools.financial_risk.rules import run_rules
from tools.financial_risk.validation import validate_financial_records


def financial_risk_analysis(call: ToolCall) -> ToolResult:
    company_ids = call.arguments.get("company_ids")
    if not isinstance(company_ids, list) or len(company_ids) != 1 or not isinstance(company_ids[0], str):
        return failed_result(
            call=call,
            error_type=ErrorType.INVALID_ARGUMENT,
            message="financial_risk_analysis currently requires company_ids with exactly one company.",
            details={"company_ids": company_ids},
            warnings=[],
        )
    company_id = company_ids[0]
    report_periods = call.arguments.get("report_periods")
    if report_periods is not None and (
        not isinstance(report_periods, list) or len(report_periods) != 1 or not isinstance(report_periods[0], str)
    ):
        return failed_result(
            call=call,
            error_type=ErrorType.INVALID_ARGUMENT,
            message="financial_risk_analysis currently requires report_periods with exactly one target period when provided.",
            details={"report_periods": report_periods},
            warnings=[],
        )
    try:
        dataset = load_financial_dataset(company_id=company_id)
    except Exception as exc:
        return failed_result(
            call=call,
            error_type=ErrorType.VALIDATION_FAILED,
            message="Financial records could not be loaded.",
            details={"error": f"{type(exc).__name__}: {exc}"},
            warnings=[],
        )
    records = dataset.records
    if report_periods:
        target_period = report_periods[0]
        available_periods = {record.report_period for record in records}
        if target_period not in available_periods:
            records = []
        else:
            records = [record for record in records if record.report_period <= target_period]
    warnings = list(dataset.warnings)

    if not records:
        return failed_result(
            call=call,
            error_type=ErrorType.DATA_NOT_AVAILABLE,
            message=f"No financial records found for company_ids={company_ids} and report_periods={report_periods}.",
            details={"company_ids": company_ids, "report_periods": report_periods, "data_source": dataset.source_name},
            warnings=warnings,
        )

    validation = validate_financial_records(records)
    warnings.extend(validation.warnings)
    if validation.errors:
        return failed_result(
            call=call,
            error_type=ErrorType.VALIDATION_FAILED,
            message="Financial records validation failed.",
            details={"errors": validation.errors},
            warnings=warnings,
        )

    metrics = calculate_metrics(records)
    latest_metrics = latest_metric_map(metrics)
    signals = run_rules(latest_metrics)
    triggered = [signal for signal in signals if signal.triggered]
    total_score = sum(signal.score for signal in triggered)
    evidence = evidence_from_records(records)
    for item in evidence:
        item.used_by = [signal.rule_id for signal in triggered if item.evidence_id in signal.evidence_ids]

    return ToolResult(
        tool_call_id=call.tool_call_id,
        tool_name=ToolName.FINANCIAL_RISK_ANALYSIS,
        status=ToolStatus.SUCCESS,
        data={
            "company_ids": company_ids,
            "report_periods": report_periods or [sorted({record.report_period for record in records})[-1]],
            "data_source": dataset.source_name,
            "risk_score": total_score,
            "risk_level": "high" if total_score >= 30 else "medium" if total_score >= 15 else "low",
            "metrics": [metric.model_dump() for metric in metrics],
            "risk_signals": [signal.model_dump() for signal in signals],
            "triggered_rule_ids": [signal.rule_id for signal in triggered],
            "message": f"financial_risk_analysis executed with {dataset.source_name} financial records",
        },
        evidence=evidence,
        warnings=warnings,
        metrics=ToolMetrics(execution_time_ms=0),
    )


def failed_result(call: ToolCall, error_type: ErrorType, message: str, details: dict, warnings: list[str]) -> ToolResult:
    return ToolResult(
        tool_call_id=call.tool_call_id,
        tool_name=ToolName.FINANCIAL_RISK_ANALYSIS,
        status=ToolStatus.FAILED,
        data={},
        evidence=[],
        warnings=warnings,
        error=ToolError(error_type=error_type, message=message, retryable=False, details=details),
        metrics=ToolMetrics(execution_time_ms=0),
    )
