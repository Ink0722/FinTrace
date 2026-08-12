from schemas.enums import ErrorType, ToolName, ToolStatus
from schemas.tool_calls import ToolCall
from schemas.tool_results import ToolError, ToolMetrics, ToolResult
from tools.financial_risk.data_source import load_financial_dataset
from tools.financial_risk.metrics import calculate_metrics, evidence_from_records, latest_metric_map
from tools.financial_risk.rules import run_rules
from tools.financial_risk.validation import validate_financial_records


def financial_risk_analysis(call: ToolCall) -> ToolResult:
    company_id = call.arguments.get("company_id") or "000001.SZ"
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
    warnings = list(dataset.warnings)

    if dataset.strict and not records:
        return failed_result(
            call=call,
            error_type=ErrorType.DATA_NOT_AVAILABLE,
            message=f"No financial records found for company_id={company_id}.",
            details={"company_id": company_id, "data_source": dataset.source_name},
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
            "company_id": company_id,
            "period": sorted({record.report_period for record in records})[-1],
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
