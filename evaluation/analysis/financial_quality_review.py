"""Prepare and aggregate independent reviews for existing financial risk cases.

The module reuses the successful ``financial_analysis.risk_scan`` calls in one
evaluation batch.  It does not create cases, call an LLM, rerun the Agent, or
change whitepaper tables.  ``prepare`` materializes two blind-review packets;
``aggregate`` validates externally produced JSONL files and computes metrics.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from evaluation.analysis.report_batch import DEFAULT_OUTPUT_ROOT, load_batch
from tools.financial_analysis.risk_catalog import RISK_RULES, RISK_RULE_VERSION


RISK_INPUT_FILENAME = "financial_risk_label_input.jsonl"
RISK_PROMPT_FILENAME = "financial_risk_label_prompt.md"
RISK_REVIEW_FILENAME = "financial_risk_label_result.jsonl"
REPORT_INPUT_FILENAME = "financial_report_review_input.jsonl"
REPORT_PROMPT_FILENAME = "financial_report_review_prompt.md"
REPORT_REVIEW_FILENAME = "financial_report_review_result.jsonl"
RISK_SCORES_FILENAME = "financial_risk_scores.csv"
REPORT_SCORES_FILENAME = "financial_report_scores.csv"
SUMMARY_FILENAME = "financial_quality_summary.json"
PROMPT_VERSION = "financial-quality-review-v2"

REFERENCE_LABELS = {"positive", "negative", "not_evaluable"}
SYSTEM_POSITIVE = "positive"
SYSTEM_NEGATIVE = "negative"
SYSTEM_NOT_EVALUABLE = "not_evaluable"
VETO_ERRORS = {
    "wrong_entity",
    "wrong_period",
    "wrong_core_value",
    "unsupported_citation",
    "cutoff_violation",
    "fraud_overstatement",
}
REPORT_DIMENSIONS = (
    "data_and_citations",
    "logical_consistency",
    "financial_professionalism",
    "completeness_and_usability",
)

RULE_APPLICABILITY = {
    "CASH_PROFIT_DIVERGENCE": (
        "逐相邻期间判断；两期归母净利润均须大于0，否则该期间对不适用。"
        "增长率=(本期-上期)/abs(上期)，上期值为0时增长率不可计算。"
    ),
    "RECEIVABLE_REVENUE_DIVERGENCE": (
        "逐相邻期间判断；上期应收账款和上期营业收入均须大于0，否则该期间对不适用。"
        "增长率=(本期-上期)/abs(上期)。"
    ),
    "INVENTORY_REVENUE_DIVERGENCE": (
        "逐相邻期间判断；上期存货和上期营业收入均须大于0，否则该期间对不适用。"
        "增长率=(本期-上期)/abs(上期)。"
    ),
    "LIQUIDITY_PRESSURE": "逐报告期判断；流动负债须大于0，否则该期间不适用。",
    "MARGIN_VOLATILITY": (
        "逐相邻期间判断；两期营业收入均须大于0，否则该期间对不适用。"
        "毛利率=(营业收入-营业成本)/营业收入，营业利润率=营业利润/营业收入。"
    ),
    "NEGATIVE_OPERATING_CASHFLOW_PERSISTENCE": (
        "按输入期间顺序判断连续负值；至少需要阈值规定数量的期间。缺失期间会中断连续序列。"
    ),
    "SALES_CASH_REVENUE_DIVERGENCE": (
        "逐报告期判断；当期营业收入须大于0，否则该期不适用，并重置上一期比率。"
        "比率变化仅在相邻两个适用期间之间计算。"
    ),
    "LEVERAGE_PRESSURE": (
        "逐报告期判断；当期总资产须大于0，否则该期不适用，并重置上一期资产负债率。"
        "比率变化仅在相邻两个适用期间之间计算。"
    ),
}

RISK_PROMPT = """# FinTrace 财务风险标签独立复核提示词

你是一名具备财务分析基础的独立复核人员。请逐行读取
`financial_risk_label_input.jsonl`。每行是既有评测批次中的一个“公司－风险规则”案例，
只包含规则定义、适用期间和原始财务指标证据，不包含 FinTrace 的原判定。

请仅依据输入完成判断，不联网，不读取项目中的其他结果文件，不使用常识补充缺失数据，
也不要把风险信号直接表述为财务造假事实。判断口径如下：

- `positive`：现有指标足以按给定公式和阈值确认规则被触发；
- `negative`：现有指标足以完成规则计算，且规则未被触发；
- `not_evaluable`：缺少必要指标、期间不可比、分母无效，或现有证据不足以执行规则。

增长率、比率和连续期间必须按 `rule.formula` 与 `rule.thresholds` 计算。不得自行修改阈值。
`supporting_metric_keys` 只能引用当前行 `metrics` 中已有的 `metric_key`；应列出足以复核
结论的关键指标，不能填写无关指标。

每个输入行只输出一个 JSON 对象，并逐行写入
`financial_risk_label_result.jsonl`。不得输出 Markdown 代码块或额外说明。格式如下：

{
  "case_id": "SESSION-001-TURN-001",
  "rule_id": "CASH_PROFIT_DIVERGENCE",
  "review_packet_id": "...",
  "reference_label": "positive",
  "supporting_metric_keys": ["NET_PROFIT_PARENT@2024-12-31"],
  "reason": "依据哪些数值、计算过程和阈值得出该结论"
}

严格要求：

- `case_id`、`rule_id` 必须原样返回，且不得遗漏或重复案例；
- `review_packet_id` 必须从输入原样返回，用于核对评审版本；
- `reference_label` 只能是 `positive`、`negative` 或 `not_evaluable`；
- `reason` 必须写明关键数值及其与阈值的关系，不能只重复标签；
- 数据不足时选择 `not_evaluable`，不得猜测或补值。
"""

REPORT_PROMPT = """# FinTrace 财务风险报告独立盲评提示词

你是一名学习金融或会计的独立评审人员。请逐行读取
`financial_report_review_input.jsonl`。每行包含用户问题、FinTrace 的最终报告、结构化风险
扫描结果以及本轮财务指标证据。请核对报告是否准确使用这些材料，不联网，不读取项目中
的其他文件，不根据模型记忆补充事实。

请按以下四个维度分别给出 1 至 5 的整数分数：

1. `data_and_citations`：报告中的主体、期间、数值、风险规则和证据引用是否与输入一致；
2. `logical_consistency`：从指标、计算结果到风险结论的推导是否连贯，是否区分事实、风险信号与推测；
3. `financial_professionalism`：财务概念、公式含义、风险措辞和专业边界是否恰当；
4. `completeness_and_usability`：是否回答用户问题，覆盖主要触发信号、必要限制，并形成可理解的分析。

完整性必须相对于用户的实际问题判断，而不是要求机械罗列全部八条风险规则。输入中的
`supporting_evidence` 汇集了本轮所有工具产生的证据；某项结构化信号未在报告中出现，只能
影响完整性评分，不能单独构成 `wrong_core_value`。只有报告明确写出的数值或触发状态与输入
冲突时，才能使用该否决项。

统一评分尺度：5 分为准确完整且无实质问题；4 分为总体优秀，仅有轻微遗漏；3 分为基本可用
但存在明显不足；2 分为存在重要错误或较大缺失；1 分为核心内容错误或基本不可用。

如发现下列严重问题，将对应代码写入 `veto_errors`：

- `wrong_entity`：分析了错误公司；
- `wrong_period`：核心分析期间错误；
- `wrong_core_value`：关键财务数值或规则触发状态错误；
- `unsupported_citation`：报告声称有证据支持，但输入证据并不支持；
- `cutoff_violation`：使用了信息截止日之后的数据；
- `fraud_overstatement`：把风险信号直接断言为造假事实。

每个输入行只输出一个 JSON 对象，并逐行写入
`financial_report_review_result.jsonl`。不得输出 Markdown 代码块或额外说明。格式如下：

{
  "case_id": "SESSION-001-TURN-001",
  "review_packet_id": "...",
  "scores": {
    "data_and_citations": 4,
    "logical_consistency": 4,
    "financial_professionalism": 5,
    "completeness_and_usability": 4
  },
  "veto_errors": [],
  "review_reason": "简要说明评分依据、主要优点和不足"
}

严格要求：

- `case_id` 必须原样返回，且不得遗漏或重复案例；
- `review_packet_id` 必须从输入原样返回，用于核对评审版本；
- 四项分数必须全部填写 1 至 5 的整数；
- `veto_errors` 只能使用上述六种代码，没有严重问题时填空数组；
- 不因行文风格偏好扣分，重点判断财务事实、推理、证据和回答完整性。
"""


def prepare_inputs(
    batch_id: str, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> dict[str, Any]:
    context = load_batch(batch_id)
    risk_records: list[dict[str, Any]] = []
    report_records: list[dict[str, Any]] = []

    for case in context.cases:
        run_id = str(case.get("final_run_id") or "")
        risk_tools = [
            tool
            for tool in context.tools.get(run_id, [])
            if tool.get("tool_name") == "financial_analysis"
            and tool.get("operation") == "risk_scan"
            and tool.get("status") == "success"
        ]
        if not risk_tools:
            continue
        if len(risk_tools) != 1:
            raise ValueError(f"{case['case_id']} has {len(risk_tools)} successful risk scans")

        tool = risk_tools[0]
        result = _loads_object(tool.get("result_json"), label="risk result")
        data = result.get("data") or {}
        signals = data.get("signals") or []
        if not signals:
            raise ValueError(f"{case['case_id']} risk scan has no signals")
        evidence = _compact_financial_evidence(result.get("evidence") or [])
        evidence_by_metric: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in evidence:
            metric_code = str(item.get("metric_code") or "")
            if metric_code:
                evidence_by_metric[metric_code].append(item)

        case_id = str(case["case_id"])
        for signal in signals:
            rule_id = str(signal.get("rule_id") or "")
            rule = RISK_RULES.get(rule_id)
            if rule is None:
                raise ValueError(f"{case_id} contains unknown risk rule {rule_id!r}")
            metrics = [
                item
                for metric_code in rule.required_metrics
                for item in evidence_by_metric.get(metric_code, [])
            ]
            risk_record = {
                    "case_id": case_id,
                    "rule_id": rule_id,
                    "company_id": data.get("company_id"),
                    "periods": data.get("periods_used") or [],
                    "knowledge_cutoff": data.get("knowledge_cutoff")
                    or case.get("run_knowledge_cutoff"),
                    "rule": {
                        "name": rule.name,
                        "topic": rule.topic,
                        "required_metrics": list(rule.required_metrics),
                        "formula": rule.formula,
                        "thresholds": rule.thresholds,
                        "threshold_basis": rule.threshold_basis,
                        "applicability": RULE_APPLICABILITY[rule_id],
                    },
                    "metrics": metrics,
                }
            risk_record["review_packet_id"] = _packet_id(risk_record)
            risk_records.append(risk_record)

        report_record = {
                "case_id": case_id,
                "question": case.get("question"),
                "company_id": data.get("company_id"),
                "periods": data.get("periods_used") or [],
                "knowledge_cutoff": data.get("knowledge_cutoff")
                or case.get("run_knowledge_cutoff"),
                "final_report": case.get("answer") or "",
                "risk_scan": {
                    "rule_version": data.get("rule_version"),
                    "coverage": data.get("coverage"),
                    "overall_score": data.get("overall_score"),
                    "scoring_status": data.get("scoring_status"),
                    "signals": signals,
                },
                "financial_evidence": evidence,
                "supporting_evidence": _compact_run_evidence(
                    context.evidence.get(run_id, [])
                ),
                "tool_execution_summary": [
                    {
                        "tool_name": item.get("tool_name"),
                        "operation": item.get("operation"),
                        "status": item.get("status"),
                        "reason": item.get("reason"),
                    }
                    for item in context.tools.get(run_id, [])
                ],
            }
        report_record["review_packet_id"] = _packet_id(report_record)
        report_records.append(report_record)

    _validate_prepared_inputs(risk_records, report_records)
    output_dir = output_root / batch_id
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / RISK_INPUT_FILENAME, risk_records)
    _write_jsonl(output_dir / REPORT_INPUT_FILENAME, report_records)
    (output_dir / RISK_PROMPT_FILENAME).write_text(RISK_PROMPT, encoding="utf-8")
    (output_dir / REPORT_PROMPT_FILENAME).write_text(REPORT_PROMPT, encoding="utf-8")
    return {
        "batch_id": batch_id,
        "financial_case_count": len(report_records),
        "case_rule_count": len(risk_records),
        "rule_counts": dict(sorted(Counter(x["rule_id"] for x in risk_records).items())),
        "risk_input_path": str(output_dir / RISK_INPUT_FILENAME),
        "risk_prompt_path": str(output_dir / RISK_PROMPT_FILENAME),
        "expected_risk_review_path": str(output_dir / RISK_REVIEW_FILENAME),
        "report_input_path": str(output_dir / REPORT_INPUT_FILENAME),
        "report_prompt_path": str(output_dir / REPORT_PROMPT_FILENAME),
        "expected_report_review_path": str(output_dir / REPORT_REVIEW_FILENAME),
    }


def aggregate_results(
    batch_id: str, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> dict[str, Any]:
    output_dir = output_root / batch_id
    risk_inputs = _read_jsonl(output_dir / RISK_INPUT_FILENAME)
    report_inputs = _read_jsonl(output_dir / REPORT_INPUT_FILENAME)
    risk_reviews = _read_jsonl(output_dir / RISK_REVIEW_FILENAME)
    report_reviews = _read_jsonl(output_dir / REPORT_REVIEW_FILENAME)
    if not risk_inputs or not report_inputs:
        raise ValueError("financial review inputs are missing; run prepare first")

    validated_risk = validate_risk_reviews(risk_inputs, risk_reviews)
    validated_reports = validate_report_reviews(report_inputs, report_reviews)
    system_predictions = _load_system_predictions(batch_id)

    risk_rows = score_risk_reviews(validated_risk, system_predictions)
    report_rows = score_report_reviews(validated_reports)
    _write_csv(output_dir / RISK_SCORES_FILENAME, risk_rows)
    _write_csv(output_dir / REPORT_SCORES_FILENAME, report_rows)

    summary = {
        "batch_id": batch_id,
        "prompt_version": PROMPT_VERSION,
        "risk_rule_version": RISK_RULE_VERSION,
        "risk_warning_classification": summarize_risk_scores(risk_rows),
        "financial_report_review": summarize_report_scores(report_rows),
        "files": {
            "risk_scores": RISK_SCORES_FILENAME,
            "report_scores": REPORT_SCORES_FILENAME,
        },
    }
    (output_dir / SUMMARY_FILENAME).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def validate_risk_reviews(
    inputs: list[dict[str, Any]], reviews: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    input_by_key = _index_case_rules(inputs, "risk label input")
    review_by_key = _index_case_rules(reviews, "risk label review")
    _require_same_keys(input_by_key, review_by_key, "risk label")
    validated: list[dict[str, Any]] = []
    for key, source in input_by_key.items():
        review = review_by_key[key]
        if review.get("review_packet_id") != source.get("review_packet_id"):
            raise ValueError(f"{key}: review_packet_id does not match current input")
        label = str(review.get("reference_label") or "")
        if label not in REFERENCE_LABELS:
            raise ValueError(f"{key}: invalid reference_label {label!r}")
        reason = str(review.get("reason") or "").strip()
        if not reason:
            raise ValueError(f"{key}: reason is required")
        available = {str(item["metric_key"]) for item in source.get("metrics") or []}
        supporting = review.get("supporting_metric_keys")
        if not isinstance(supporting, list):
            raise ValueError(f"{key}: supporting_metric_keys must be a list")
        unknown = sorted({str(item) for item in supporting} - available)
        if unknown:
            raise ValueError(f"{key}: unknown supporting_metric_keys {unknown}")
        validated.append(
            {
                **review,
                "case_id": key[0],
                "rule_id": key[1],
                "review_packet_id": source["review_packet_id"],
                "reference_label": label,
                "supporting_metric_keys": [str(item) for item in supporting],
                "reason": reason,
            }
        )
    return validated


def validate_report_reviews(
    inputs: list[dict[str, Any]], reviews: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    input_by_id = _index_cases(inputs, "financial report input")
    review_by_id = _index_cases(reviews, "financial report review")
    _require_same_keys(input_by_id, review_by_id, "financial report")
    validated: list[dict[str, Any]] = []
    for case_id in input_by_id:
        source = input_by_id[case_id]
        review = review_by_id[case_id]
        if review.get("review_packet_id") != source.get("review_packet_id"):
            raise ValueError(f"{case_id}: review_packet_id does not match current input")
        scores = review.get("scores")
        if not isinstance(scores, dict):
            raise ValueError(f"{case_id}: scores must be an object")
        normalized_scores: dict[str, int] = {}
        for dimension in REPORT_DIMENSIONS:
            value = scores.get(dimension)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
                raise ValueError(f"{case_id}: {dimension} must be an integer from 1 to 5")
            normalized_scores[dimension] = value
        vetoes = review.get("veto_errors")
        if not isinstance(vetoes, list):
            raise ValueError(f"{case_id}: veto_errors must be a list")
        normalized_vetoes = [str(item) for item in vetoes]
        unknown = sorted(set(normalized_vetoes) - VETO_ERRORS)
        if unknown:
            raise ValueError(f"{case_id}: unknown veto_errors {unknown}")
        reason = str(review.get("review_reason") or "").strip()
        if not reason:
            raise ValueError(f"{case_id}: review_reason is required")
        validated.append(
            {
                "case_id": case_id,
                "review_packet_id": source["review_packet_id"],
                "scores": normalized_scores,
                "veto_errors": sorted(set(normalized_vetoes)),
                "review_reason": reason,
            }
        )
    return validated


def score_risk_reviews(
    reviews: list[dict[str, Any]], system_predictions: dict[tuple[str, str], str]
) -> list[dict[str, Any]]:
    review_keys = {(item["case_id"], item["rule_id"]) for item in reviews}
    _require_same_keys(review_keys, set(system_predictions), "system risk predictions")
    rows: list[dict[str, Any]] = []
    for review in reviews:
        key = (review["case_id"], review["rule_id"])
        reference = review["reference_label"]
        prediction = system_predictions[key]
        tp = int(reference == SYSTEM_POSITIVE and prediction == SYSTEM_POSITIVE)
        fp = int(reference == SYSTEM_NEGATIVE and prediction == SYSTEM_POSITIVE)
        fn = int(reference == SYSTEM_POSITIVE and prediction != SYSTEM_POSITIVE)
        tn = int(reference == SYSTEM_NEGATIVE and prediction == SYSTEM_NEGATIVE)
        rows.append(
            {
                "case_id": key[0],
                "rule_id": key[1],
                "reference_label": reference,
                "system_prediction": prediction,
                "scored": "yes" if reference != SYSTEM_NOT_EVALUABLE else "no",
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
                "supporting_metric_keys": " | ".join(review["supporting_metric_keys"]),
                "review_reason": review["reason"],
            }
        )
    return rows


def score_report_reviews(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for review in reviews:
        scores = review["scores"]
        average = sum(scores.values()) / len(REPORT_DIMENSIONS)
        vetoes = review["veto_errors"]
        excellent = all(scores[item] >= 4 for item in REPORT_DIMENSIONS) and average >= 4 and not vetoes
        rows.append(
            {
                "case_id": review["case_id"],
                **scores,
                "average_score": round(average, 4),
                "veto_errors": " | ".join(vetoes),
                "excellent": "yes" if excellent else "no",
                "review_reason": review["review_reason"],
            }
        )
    return rows


def summarize_risk_scores(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "micro": _binary_summary(rows),
        "by_rule": {
            rule_id: _binary_summary([item for item in rows if item["rule_id"] == rule_id])
            for rule_id in RISK_RULES
        },
    }


def summarize_report_scores(rows: list[dict[str, Any]]) -> dict[str, Any]:
    dimensions: dict[str, Any] = {}
    for dimension in REPORT_DIMENSIONS:
        values = [int(item[dimension]) for item in rows]
        dimensions[dimension] = {
            "mean": round(statistics.fmean(values), 4) if values else None,
            "standard_deviation": round(statistics.pstdev(values), 4) if values else None,
            "score_at_least_4_count": sum(value >= 4 for value in values),
            "score_at_least_4_rate": _ratio(sum(value >= 4 for value in values), len(values)),
        }
    veto_counts = Counter(
        veto
        for item in rows
        for veto in str(item.get("veto_errors") or "").split(" | ")
        if veto
    )
    excellent_count = sum(item.get("excellent") == "yes" for item in rows)
    return {
        "case_count": len(rows),
        "excellent_count": excellent_count,
        "excellent_rate": _ratio(excellent_count, len(rows)),
        "veto_case_count": sum(bool(item.get("veto_errors")) for item in rows),
        "veto_error_counts": dict(sorted(veto_counts.items())),
        "dimensions": dimensions,
    }


def _binary_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tp = sum(int(item["tp"]) for item in rows)
    fp = sum(int(item["fp"]) for item in rows)
    fn = sum(int(item["fn"]) for item in rows)
    tn = sum(int(item["tn"]) for item in rows)
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    f1 = (
        round(2 * precision * recall / (precision + recall), 4)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    return {
        "case_rule_count": len(rows),
        "scored_count": sum(item["scored"] == "yes" for item in rows),
        "excluded_not_evaluable_count": sum(item["scored"] == "no" for item in rows),
        "reference_positive_count": sum(item["reference_label"] == SYSTEM_POSITIVE for item in rows),
        "reference_negative_count": sum(item["reference_label"] == SYSTEM_NEGATIVE for item in rows),
        "system_not_evaluable_count": sum(item["system_prediction"] == SYSTEM_NOT_EVALUABLE for item in rows),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _load_system_predictions(batch_id: str) -> dict[tuple[str, str], str]:
    context = load_batch(batch_id)
    predictions: dict[tuple[str, str], str] = {}
    for case in context.cases:
        run_id = str(case.get("final_run_id") or "")
        for tool in context.tools.get(run_id, []):
            if not (
                tool.get("tool_name") == "financial_analysis"
                and tool.get("operation") == "risk_scan"
                and tool.get("status") == "success"
            ):
                continue
            result = _loads_object(tool.get("result_json"), label="risk result")
            for signal in (result.get("data") or {}).get("signals") or []:
                key = (str(case["case_id"]), str(signal.get("rule_id") or ""))
                if key in predictions:
                    raise ValueError(f"duplicate system prediction {key}")
                predictions[key] = _normalize_system_status(signal.get("status"))
    return predictions


def _normalize_system_status(status: Any) -> str:
    value = str(status or "")
    if value == "triggered":
        return SYSTEM_POSITIVE
    if value == "not_triggered":
        return SYSTEM_NEGATIVE
    if value in {"insufficient_data", "not_applicable"}:
        return SYSTEM_NOT_EVALUABLE
    raise ValueError(f"unsupported system risk status {value!r}")


def _compact_financial_evidence(evidence: list[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict) or item.get("evidence_type") != "financial_statement_metric":
            continue
        fact = item.get("fact") or {}
        metric_code = str(fact.get("metric_code") or "")
        period = str(fact.get("report_period") or "")
        if not metric_code or not period:
            continue
        metric_key = f"{metric_code}@{period}"
        if metric_key in seen:
            continue
        seen.add(metric_key)
        records.append(
            {
                "metric_key": metric_key,
                "metric_code": metric_code,
                "report_period": period,
                "value": fact.get("value"),
                "currency": fact.get("currency"),
                "value_nature": fact.get("value_nature"),
                "announcement_date": fact.get("announcement_date"),
                "evidence_id": item.get("evidence_id"),
            }
        )
    return sorted(records, key=lambda item: (item["report_period"], item["metric_code"]))


def _compact_run_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in evidence:
        evidence_id = str(item.get("evidence_id") or "")
        if not evidence_id:
            continue
        records.append(
            {
                "evidence_id": evidence_id,
                "evidence_type": item.get("evidence_type"),
                "support_level": item.get("support_level"),
                "source": _loads_json_value(item.get("source_json")),
                "fact": _loads_json_value(item.get("fact_json")),
            }
        )
    return records


def _packet_id(record: dict[str, Any]) -> str:
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "FIN-REVIEW-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20].upper()


def _loads_json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _validate_prepared_inputs(
    risk_records: list[dict[str, Any]], report_records: list[dict[str, Any]]
) -> None:
    if not report_records:
        raise ValueError("batch contains no successful financial risk cases")
    report_ids = {str(item["case_id"]) for item in report_records}
    if len(report_ids) != len(report_records):
        raise ValueError("financial report inputs contain duplicate cases")
    risk_index = _index_case_rules(risk_records, "risk label input")
    if {key[0] for key in risk_index} != report_ids:
        raise ValueError("risk label and financial report case sets differ")
    unknown_rules = sorted({key[1] for key in risk_index} - set(RISK_RULES))
    if unknown_rules:
        raise ValueError(f"risk label inputs contain unknown rules {unknown_rules}")


def _index_case_rules(
    records: list[dict[str, Any]], label: str
) -> dict[tuple[str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for item in records:
        key = (str(item.get("case_id") or ""), str(item.get("rule_id") or ""))
        if not all(key):
            raise ValueError(f"{label} has an empty case_id or rule_id")
        if key in indexed:
            raise ValueError(f"{label} contains duplicate {key}")
        indexed[key] = item
    return indexed


def _index_cases(records: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in records:
        case_id = str(item.get("case_id") or "")
        if not case_id:
            raise ValueError(f"{label} has an empty case_id")
        if case_id in indexed:
            raise ValueError(f"{label} contains duplicate {case_id}")
        indexed[case_id] = item
    return indexed


def _require_same_keys(left: Any, right: Any, label: str) -> None:
    left_keys = set(left)
    right_keys = set(right)
    if left_keys != right_keys:
        missing = sorted(left_keys - right_keys)
        extra = sorted(right_keys - left_keys)
        raise ValueError(f"{label} coverage mismatch; missing={missing[:5]}, extra={extra[:5]}")


def _loads_object(value: Any, *, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {label} JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object")
    return parsed


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path}:{line_number}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"JSONL row must be an object at {path}:{line_number}")
            records.append(item)
    return records


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty score file {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--stage", choices=("prepare", "aggregate"), required=True)
    args = parser.parse_args()
    result = (
        prepare_inputs(args.batch_id, args.output_root)
        if args.stage == "prepare"
        else aggregate_results(args.batch_id, args.output_root)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
