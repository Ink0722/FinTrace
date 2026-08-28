"""LLM-based answer-quality first review for a completed evaluation batch.

Reads ``answer_review.csv`` plus the runtime database (read-only), asks a
frozen LLM evaluator to judge every turn's answer against its run evidence,
and writes back ONLY the five review columns:

- ``llm_label``            yes / no (provisional; final_correct stays empty)
- ``required_facts``       JSON array of minimal necessary facts
- ``necessary_fact_count`` len(required_facts)
- ``hit_fact_count``       facts correctly expressed and evidence-supported
- ``reviewer_notes``       short auditable rationale

Stages: ``extract`` (build judge inputs) -> ``evaluate`` (LLM, resumable by
case_id into a side JSONL) -> ``apply`` (merge into CSV, atomic replace,
resumable by existing llm_label) -> ``summarize`` (validation + summary JSON).
The CSV is backed up once as ``answer_review.before_llm_review.csv``.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from harness.llm import QwenClient
from harness.runtime_db import PROJECT_ROOT
from harness.tracing.store import connect

from evaluation.analysis.tool_llm_review import (
    KNOWLEDGE_CUTOFF_DEFAULT,
    file_sha256,
    parse_llm_json,
    sqlite_row_factory,
)

DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "evaluation" / "results"
PROMPT_VERSION = "answer-llm-review-v3"
REVIEW_COLUMNS = ("llm_label", "required_facts", "necessary_fact_count", "hit_fact_count", "reviewer_notes")
MAX_PARSE_RETRIES = 2
PROGRESS_EVERY = 25
APPLY_FLUSH_EVERY = 50

SYSTEM_PROMPT = """你是金融智能体回答质量的初审员。你评审FinTrace智能体在一个多轮会话轮次中的最终回答，判断其正确性并为可回答问题提炼必要事实。你的结论是"LLM初审结果"，供人工复核，不是最终结论。

## 评审输入
每轮包含：问题、同会话此前问题与已解析上下文（用于消解指代）、参考可回答性标注（可能不准，仅供参考）、Agent的回答与回答状态、该轮全部证据（evidence，含是否被引用）、工具调用概要（工具、操作、状态）。知识截止日2026-05-28，回答不得使用晚于该日披露的信息。

## 关键约束
- answer_status字段语义：answered=已回答；partially_answered=部分回答；clarification_required=要求澄清；insufficient_evidence=证据不足说明；unsupported=能力边界拒答。这些都不是失败状态，绝不能仅因answer_status取值而判no。只有回答被截断、生成失败（如failed、内容残缺无法阅读）才适用失败判no。
- 判断只依据回答内容与证据，不得依据answer_status字段值得出结论。
- 禁止用常识、公开事实、模型记忆评判答案（如"上市日期是公开信息"不构成判no理由——数据集中没有的证据就不能要求Agent给出）。
- 参考标注的可回答性可能错误，不得作为判断依据；以系统边界规则为准。
- reviewer_notes必须非空、以判断结论开头、与llm_label一致：若你的分析结论是应判yes，llm_label必须填yes。先定结论再写一句依据。

## 正确性标准（llm_label，只能填yes或no）
同时满足以下条件才填yes：
1. 回答准确回应用户核心问题；
2. 主体、股票代码和报告期正确（多轮中当前轮明确指定的主体/时间优先于历史，明显切换话题后不得继承旧主体）；
3. 核心结论与本轮证据一致；
4. 重要数字、比例、日期和关系无实质错误；
5. 未把风险信号写成已确认的违法或造假事实；未把研报观点、预测写成公司客观事实；
6. 未引用知识截止日之后的信息；
7. 对数据范围之外的问题给出了明确且恰当的能力边界说明。

出现以下任一情况填no：
1. 主体、股票代码或时间范围错误；
2. 核心结论错误、答非所问或遗漏问题主要部分；
3. 引用了证据中不存在的重要事实（无证据断言）；
4. 将推测、研报观点或风险信号写成确定事实；
5. 数据能支持回答但Agent无正当理由拒绝回答（不当拒答）；
6. 数据无法支持回答但Agent仍给出确定结论（编造）；
7. 回答被截断、生成失败，或只有错误信息而没有完成回答（answer_status为failed/截断时属此类）；
8. 多轮对话中错误继承或覆盖当前轮明确指定的主体与时间。

## 分类问题处理
- 数据集标记unanswerable的问题：Agent准确说明能力边界且没有编造 → yes；仍给出无数据支持的事实性答案 → no。
- 部分可回答问题：Agent回答了有证据支持的部分并清楚说明其余限制 → 可以yes；缺失内容导致核心问题未回答 → no。
- 需要澄清的问题：Agent给出已有部分结果、指出缺失参数及其影响且表达清楚 → yes；直接编造参数作答 → no。
- 正确拒答的判断依据：问题超出系统边界（实时/历史行情、涨跌排行、盘口资金流、自选股持仓账户、基金产品数据、数据集外互联网事实、未来价格预测），或边界内但证据确实缺失且Agent说明了缺口。

## 复合问题的处理
一部分在边界内、一部分在边界外的问题（如"XX表现如何+明天走势预测"、"最新公告+当前股价"、"XX怎么样+能买吗"）：
- Agent用证据回答了边界内部分，并明确说明边界外部分无法提供 → yes（这是正确处理，不要因问题含有预测/行情成分而整体判no）；
- 完全忽略边界内可回答部分、只拒答 → 视边界内部分是否为问题主体决定，通常no（遗漏主要部分）；
- 对边界外部分编造确定性结论 → no。
"表现如何/怎么样"类表述在无行情语境时按业务与财务表现理解，可用财务数据回答。

## 必要事实（required_facts）
- 定义：正确回答当前问题必须覆盖的最小事实集合；不是证据全文，不是所有可用信息。
- 每项只表达一个可独立核验的事实；优先保留直接回答用户问题的主体、时间、指标、事件或关系。
- 不把修饰性描述、免责声明和非必要背景列为必要事实。
- 数值允许与原始证据等价的单位换算。
- 通常每轮1至5项，复杂比较或多跳关系最多8项。
- 必须来自本轮evidence或问题本身已有的明确条件；禁止用常识、模型记忆或互联网资料补造。
- 正确的拒答或能力边界回答：required_facts留空数组[]，fact_hits留空数组[]。
- 不当拒答（本可回答但被拒）：正常提炼必要事实，fact_hits按回答实际命中记（通常全false）。
- unanswerable但Agent编造的：required_facts留空（正确行为是边界说明，无数值事实可提炼），llm_label=no。
- 对需要澄清的问题若Agent正确要求补充参数：required_facts留空。

## 命中规则（fact_hits与required_facts等长）
- 回答正确表达且证据支持 → true；语义等价、合理简称、等价单位换算可命中。
- 事实被提到但主体、期间、方向或数值错误 → false。
- 同一事实重复出现只算一次。

## 输出格式
只输出一个JSON对象，不要输出其他文字或代码块标记：
{
  "case_id": "...",
  "llm_label": "yes|no",
  "required_facts": ["..."],
  "fact_hits": [true],
  "reviewer_notes": "不超过80字的可审计判断依据，如：回答正确，主体、报告期及核心指标均与证据一致。/ 主体错误：问题问600519.SH，回答用了601555.SH。/ 不当拒答：现有财务证据足以回答核心问题。"
}"""


# ---------------------------------------------------------------- extraction

def _loads(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _clip(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value


def load_answer_inputs(batch_id: str) -> list[dict[str, Any]]:
    """One judge input per case: question + context + answer + evidence."""
    connection = connect(readonly=True)
    connection.row_factory = sqlite_row_factory
    try:
        cases = connection.execute(
            "SELECT case_id, source_session_id, agent_session_id, expected_turn_id, "
            "question, annotation_json, run_id FROM evaluation_cases "
            "WHERE batch_id = ? ORDER BY agent_session_id, expected_turn_id",
            (batch_id,),
        ).fetchall()
        run_ids = [case["run_id"] for case in cases]
        runs: dict[str, dict[str, Any]] = {}
        for run_id in run_ids:
            run = connection.execute(
                "SELECT answer, answer_status, current_context_json FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            runs[run_id] = run or {}
        placeholders = ",".join("?" * len(run_ids))
        evidence_by_run: dict[str, list[dict[str, Any]]] = {}
        for record in connection.execute(
            f"SELECT run_id, evidence_id, evidence_type, fact_json, source_json "
            f"FROM evidence_records WHERE run_id IN ({placeholders}) ORDER BY run_id, sequence",
            run_ids,
        ):
            evidence_by_run.setdefault(record["run_id"], []).append(
                {
                    "evidence_id": record["evidence_id"],
                    "type": record["evidence_type"],
                    "fact": _clip(_loads(record["fact_json"], record["fact_json"]), 700),
                    "source": _clip(_loads(record["source_json"], record["source_json"]), 220),
                }
            )
        tools_by_run: dict[str, list[dict[str, Any]]] = {}
        for record in connection.execute(
            f"SELECT run_id, tool_name, operation, status FROM tool_executions "
            f"WHERE run_id IN ({placeholders}) ORDER BY run_id, sequence",
            run_ids,
        ):
            tools_by_run.setdefault(record["run_id"], []).append(
                {
                    "tool": record["tool_name"],
                    "operation": record["operation"],
                    "status": record["status"],
                }
            )
    finally:
        connection.close()

    inputs: list[dict[str, Any]] = []
    session_questions: dict[str, list[str]] = {}
    for case in cases:
        run_id = case["run_id"]
        run = runs.get(run_id) or {}
        session_key = case["agent_session_id"]
        evidence = evidence_by_run.get(run_id, [])
        inputs.append(
            {
                "case_id": case["case_id"],
                "session": case["source_session_id"],
                "turn": case["expected_turn_id"],
                "question": case["question"],
                "recent_prior_questions": session_questions.get(session_key, [])[-3:],
                "resolved_context": _loads(run.get("current_context_json"), {}),
                "reference_annotation_answerability": (
                    _loads(case["annotation_json"], {}).get("answerability")
                ),
                "answer_status": run.get("answer_status"),
                "answer": run.get("answer") or "",
                "tool_calls": tools_by_run.get(run_id, []),
                "evidence": evidence,
                "evidence_used_hint": "证据按evidence_id列出；回答应只用这些证据支撑事实",
            }
        )
        session_questions.setdefault(session_key, []).append(case["question"])
    return inputs


# ---------------------------------------------------------------- evaluation

def normalise_judgment(judgment: dict[str, Any], case_input: dict[str, Any]) -> dict[str, Any]:
    """Validate the LLM judgment; counts are derived, never taken verbatim."""
    case_id = case_input["case_id"]
    label = str(judgment.get("llm_label", "")).strip().lower()
    if label not in {"yes", "no"}:
        label = "no"
    facts = judgment.get("required_facts") or []
    if isinstance(facts, str):
        facts = _loads(facts, [])
    facts = [str(fact)[:200] for fact in facts if str(fact).strip()][:8]
    hits = judgment.get("fact_hits")
    if not isinstance(hits, list):
        hits = []
    hits = [bool(hit) for hit in hits][: len(facts)]
    while len(hits) < len(facts):
        hits.append(False)
    return {
        "case_id": case_id,
        "llm_label": label,
        "required_facts": facts,
        "fact_hits": hits,
        "necessary_fact_count": len(facts),
        "hit_fact_count": sum(hits),
        "reviewer_notes": str(judgment.get("reviewer_notes", ""))[:160],
    }


def judge_case(client: QwenClient, case_input: dict[str, Any]) -> tuple[dict[str, Any], str]:
    user_payload = json.dumps(case_input, ensure_ascii=False)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"知识截止日：{KNOWLEDGE_CUTOFF_DEFAULT}。请评审以下轮次的回答：\n{user_payload}"
            ),
        },
    ]
    last_error = ""
    for _ in range(MAX_PARSE_RETRIES + 1):
        try:
            reply = client.chat_json(messages, temperature=0.0)
        except Exception as exc:  # noqa: BLE001 - network/API failure, retry
            last_error = f"API_ERROR:{type(exc).__name__}"
            time.sleep(3)
            continue
        text = reply
        if isinstance(reply, dict):
            choices = reply.get("choices") or [{}]
            text = (choices[0].get("message") or {}).get("content", "")
        try:
            judgment = parse_llm_json(text)
            return normalise_judgment(judgment, case_input), "ok"
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = f"PARSE_ERROR:{exc}"
            continue
    placeholder = normalise_judgment({}, case_input)
    placeholder.update(
        {
            "reviewer_notes": f"评审输出解析失败（{last_error}），需人工评审",
            "needs_human_review": True,
        }
    )
    return placeholder, "parse_failed"


def run_evaluation(
    batch_id: str,
    output_dir: Path,
    limit: int | None = None,
    concurrency: int = 6,
) -> dict[str, Any]:
    inputs = load_answer_inputs(batch_id)
    input_path = output_dir / "answer_llm_review_input.jsonl"
    with open(input_path, "w", encoding="utf-8") as handle:
        for case_input in inputs:
            handle.write(json.dumps(case_input, ensure_ascii=False) + "\n")

    judgments_path = output_dir / "answer_llm_review.judgments.jsonl"
    done: set[str] = set()
    if judgments_path.exists():
        with open(judgments_path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    done.add(json.loads(line)["case_id"])
                except (ValueError, KeyError):
                    continue
    pending = [item for item in inputs if item["case_id"] not in done]
    if limit is not None:
        pending = pending[:limit]
    print(
        f"batch={batch_id} total={len(inputs)} done={len(done)} pending={len(pending)}",
        file=sys.stderr,
    )
    client = QwenClient()
    if not client.enabled:
        raise SystemExit("QWEN/DASHSCOPE API key not configured")
    started = time.time()
    processed = failures = 0
    with open(judgments_path, "a", encoding="utf-8") as handle, ThreadPoolExecutor(
        max_workers=concurrency
    ) as pool:
        futures = {
            pool.submit(judge_case, client, case_input): case_input["case_id"]
            for case_input in pending
        }
        for future in as_completed(futures):
            judgment, status = future.result()
            handle.write(json.dumps(judgment, ensure_ascii=False) + "\n")
            handle.flush()
            processed += 1
            if status != "ok":
                failures += 1
            if processed % PROGRESS_EVERY == 0:
                print(
                    f"progress {processed}/{len(pending)} elapsed={time.time() - started:.0f}s "
                    f"failures={failures}",
                    file=sys.stderr,
                )
    return {
        "judged": processed,
        "parse_failures": failures,
        "elapsed_seconds": round(time.time() - started, 1),
        "judgments_path": str(judgments_path),
    }


def rejudge(
    batch_id: str,
    output_dir: Path,
    concurrency: int = 8,
) -> dict[str, Any]:
    """Second-pass adjudication for suspect rows: llm_label=no or empty notes.

    Removes those cases from the side judgments file, re-judges them with the
    current (patched) prompt, clears their five CSV review fields, re-applies.
    """
    csv_path = output_dir / "answer_review.csv"
    backup_path = output_dir / "answer_review.before_llm_review.csv"
    if not backup_path.exists():
        shutil.copyfile(csv_path, backup_path)
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    targets = {
        row["case_id"]
        for row in rows
        if row.get("llm_label") == "no" or not row.get("reviewer_notes", "").strip()
    }

    judgments_path = output_dir / "answer_llm_review.judgments.jsonl"
    kept_lines = []
    with open(judgments_path, encoding="utf-8") as handle:
        for line in handle:
            try:
                if json.loads(line)["case_id"] not in targets:
                    kept_lines.append(line)
            except ValueError:
                continue
    with open(judgments_path, "w", encoding="utf-8") as handle:
        handle.writelines(kept_lines)
    print(f"rejudge targets: {len(targets)}", file=sys.stderr)

    meta = run_evaluation(batch_id, output_dir, limit=None, concurrency=concurrency)

    for row in rows:
        if row["case_id"] in targets:
            for column in REVIEW_COLUMNS:
                row[column] = ""
    temp_path = csv_path.with_suffix(".csv.tmp")
    _write_csv_atomic(temp_path, csv_path, fieldnames, rows)
    apply_meta = apply_judgments(batch_id, output_dir)
    return {"targets": len(targets), "evaluate": meta, "apply": apply_meta}


# ---------------------------------------------------------------- apply to csv

def apply_judgments(batch_id: str, output_dir: Path) -> dict[str, Any]:
    """Merge side-file judgments into answer_review.csv (atomic, resumable)."""
    csv_path = output_dir / "answer_review.csv"
    backup_path = output_dir / "answer_review.before_llm_review.csv"
    if not backup_path.exists():
        shutil.copyfile(csv_path, backup_path)

    judgments: dict[str, dict[str, Any]] = {}
    judgments_path = output_dir / "answer_llm_review.judgments.jsonl"
    with open(judgments_path, encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            judgments[record["case_id"]] = record

    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    missing_columns = [column for column in REVIEW_COLUMNS if column not in fieldnames]
    if missing_columns:
        raise SystemExit(f"answer_review.csv missing columns: {missing_columns}")

    applied = 0
    already = 0
    temp_path = csv_path.with_suffix(".csv.tmp")
    for index, row in enumerate(rows):
        if row.get("llm_label"):
            already += 1
            continue
        judgment = judgments.get(row["case_id"])
        if not judgment:
            continue
        facts_json = json.dumps(judgment["required_facts"], ensure_ascii=False)
        row["llm_label"] = judgment["llm_label"]
        row["required_facts"] = facts_json if judgment["required_facts"] else ""
        row["necessary_fact_count"] = str(judgment["necessary_fact_count"]) if judgment["required_facts"] else ""
        row["hit_fact_count"] = str(judgment["hit_fact_count"]) if judgment["required_facts"] else ""
        row["reviewer_notes"] = judgment["reviewer_notes"]
        applied += 1
        if applied % APPLY_FLUSH_EVERY == 0:
            _write_csv_atomic(temp_path, csv_path, fieldnames, rows)

    _write_csv_atomic(temp_path, csv_path, fieldnames, rows)
    return {"applied": applied, "already_labelled": already, "total_rows": len(rows)}


def _write_csv_atomic(temp_path: Path, csv_path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with open(temp_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, csv_path)


# ---------------------------------------------------------------- validation

def validate_csv(output_dir: Path) -> tuple[list[str], dict[str, Any]]:
    csv_path = output_dir / "answer_review.csv"
    backup_path = output_dir / "answer_review.before_llm_review.csv"
    errors: list[str] = []
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with open(backup_path, encoding="utf-8-sig", newline="") as handle:
        backup = list(csv.DictReader(handle))
    if len(rows) != 1410:
        errors.append(f"row count {len(rows)} != 1410")
    case_ids = [row["case_id"] for row in rows]
    if len(set(case_ids)) != len(case_ids):
        errors.append("duplicate case_id")
    if {row["case_id"] for row in rows} != {row["case_id"] for row in backup}:
        errors.append("case_id set differs from backup")

    protected = [
        column
        for column in (rows[0] if rows else {}).keys()
        if column not in REVIEW_COLUMNS
    ]
    for index, (row, original) in enumerate(zip(rows, backup)):
        for column in protected:
            if row.get(column) != original.get(column):
                errors.append(f"row {index} column {column} modified")
                break

    stats: dict[str, Any] = {
        "reviewed": 0,
        "correct": 0,
        "incorrect": 0,
        "fact_cases": 0,
        "necessary": 0,
        "hit": 0,
        "unanswerable_reviewed": 0,
        "boundary_correct": 0,
    }
    for row in rows:
        label = row.get("llm_label")
        if label not in {"yes", "no", ""}:
            errors.append(f"{row['case_id']}: invalid llm_label {label!r}")
            continue
        if not label:
            continue
        stats["reviewed"] += 1
        if label == "yes":
            stats["correct"] += 1
        else:
            stats["incorrect"] += 1
        facts_raw = row.get("required_facts", "")
        if row.get("dataset_answerability") == "unanswerable":
            stats["unanswerable_reviewed"] += 1
            if label == "yes":
                stats["boundary_correct"] += 1
        if not facts_raw:
            if row.get("necessary_fact_count") or row.get("hit_fact_count"):
                errors.append(f"{row['case_id']}: counts set without required_facts")
            continue
        try:
            facts = json.loads(facts_raw)
            if not isinstance(facts, list):
                raise ValueError("not a list")
        except ValueError:
            errors.append(f"{row['case_id']}: required_facts not valid JSON array")
            continue
        necessary = row.get("necessary_fact_count", "")
        hit = row.get("hit_fact_count", "")
        if necessary != str(len(facts)):
            errors.append(f"{row['case_id']}: necessary_fact_count {necessary} != {len(facts)}")
        try:
            hit_value = int(hit)
            if not 0 <= hit_value <= len(facts):
                raise ValueError("out of range")
        except ValueError:
            errors.append(f"{row['case_id']}: hit_fact_count invalid {hit!r}")
            continue
        stats["fact_cases"] += 1
        stats["necessary"] += len(facts)
        stats["hit"] += hit_value
    return errors, stats


def summarise(batch_id: str, output_dir: Path, eval_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    errors, stats = validate_csv(output_dir)
    reviewed = stats["reviewed"]
    summary = {
        "batch_id": batch_id,
        "note": "LLM初审结果，供人工复核；未填写final_correct，不是最终实验结论",
        "prompt_version": PROMPT_VERSION,
        "evaluation_model": os.getenv("QWEN_MODEL") or os.getenv("QWEN_CHAT_MODEL", "qwen-plus"),
        "temperature": 0.0,
        "knowledge_cutoff": KNOWLEDGE_CUTOFF_DEFAULT,
        "total_cases": 1410,
        "reviewed_cases": reviewed,
        "correct_cases": stats["correct"],
        "incorrect_cases": stats["incorrect"],
        "fact_reviewed_cases": stats["fact_cases"],
        "necessary_fact_count": stats["necessary"],
        "hit_fact_count": stats["hit"],
        "provisional_answer_accuracy": (
            round(stats["correct"] / reviewed, 4) if reviewed else None
        ),
        "provisional_key_fact_recall": (
            round(stats["hit"] / stats["necessary"], 4) if stats["necessary"] else None
        ),
        "unanswerable_cases": stats["unanswerable_reviewed"],
        "correct_boundary_responses": stats["boundary_correct"],
        "validation_errors": errors[:50],
        "provenance": {
            "question_set_sha256": file_sha256(
                PROJECT_ROOT / "evaluation" / "questions" / "questions_annotated_v1.jsonl"
            ),
            "database": "runtime/fintrace.sqlite3 (read-only)",
            "backup": "answer_review.before_llm_review.csv",
        },
        "evaluation_run": eval_meta or {},
    }
    with open(output_dir / "answer_llm_review_summary.json", "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    return summary


# ---------------------------------------------------------------- cli

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", default="EVAL-20260825T204302Z-FA59F317-2CB3")
    parser.add_argument(
        "--stage", choices=("extract", "evaluate", "apply", "rejudge", "summarize", "all"), required=True
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--eval-meta", default=None)
    args = parser.parse_args()
    output_dir = DEFAULT_OUTPUT_ROOT / args.batch_id
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.stage in {"extract", "all"}:
        inputs = load_answer_inputs(args.batch_id)
        with open(output_dir / "answer_llm_review_input.jsonl", "w", encoding="utf-8") as handle:
            for case_input in inputs:
                handle.write(json.dumps(case_input, ensure_ascii=False) + "\n")
        print(json.dumps({"cases": len(inputs)}))
    if args.stage in {"evaluate", "all"}:
        meta = run_evaluation(args.batch_id, output_dir, limit=args.limit, concurrency=args.concurrency)
        print(json.dumps(meta, ensure_ascii=False))
    if args.stage in {"apply", "all"}:
        meta = apply_judgments(args.batch_id, output_dir)
        print(json.dumps(meta, ensure_ascii=False))
    if args.stage == "rejudge":
        meta = rejudge(args.batch_id, output_dir, concurrency=args.concurrency)
        print(json.dumps(meta, ensure_ascii=False))
    if args.stage in {"summarize", "all"}:
        eval_meta = json.loads(args.eval_meta) if args.eval_meta else None
        summary = summarise(args.batch_id, output_dir, eval_meta)
        print(
            json.dumps(
                {
                    "reviewed": summary["reviewed_cases"],
                    "correct": summary["correct_cases"],
                    "accuracy": summary["provisional_answer_accuracy"],
                    "recall": summary["provisional_key_fact_recall"],
                    "validation_errors": summary["validation_errors"][:5],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
