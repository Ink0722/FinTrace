"""Prepare, execute and inspect resumable FinTrace evaluation batches."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from evaluation.runner import repository
from harness.graph.workflow import knowledge_cutoff_from_env, run_agent
from harness.tracing.users import claim_session, ensure_user


HEARTBEAT_SECONDS = 30


class ProgressReporter:
    """Thread-safe human-readable progress on stderr; stdout stays machine-readable."""

    def __init__(self, *, batch_id: str, total_cases: int, concurrency: int) -> None:
        self.batch_id = batch_id
        self.total_cases = total_cases
        self.concurrency = max(1, concurrency)
        self.started = time.perf_counter()
        self.processed = 0
        self.durations: list[float] = []
        self.lock = threading.Lock()

    def batch_started(self, session_count: int) -> None:
        self._write(
            f"[评测] 批次 {self.batch_id} 开始 | "
            f"会话 {session_count} | 题目 {self.total_cases} | 并发 {self.concurrency}"
        )

    def session_started(self, session_id: str, case_count: int) -> None:
        self._write(f"[会话 {session_id}] 开始 | 本次执行 {case_count} 轮")

    def turn_started(self, case: dict, index: int, session_total: int) -> None:
        question = " ".join(str(case["question"]).split())
        if len(question) > 90:
            question = f"{question[:87]}..."
        self._write(
            f"[会话 {case['source_session_id']} | {index}/{session_total}] "
            f"开始 {case['case_id']} | {question}"
        )

    def start_heartbeat(self, case: dict) -> tuple[threading.Event, threading.Thread]:
        stop = threading.Event()
        started = time.perf_counter()

        def report() -> None:
            while not stop.wait(HEARTBEAT_SECONDS):
                elapsed = time.perf_counter() - started
                self._write(
                    f"[会话 {case['source_session_id']} | {case['case_id']}] "
                    f"仍在处理 | 已耗时 {elapsed:.0f} 秒"
                )

        thread = threading.Thread(target=report, name=f"heartbeat-{case['case_id']}", daemon=True)
        thread.start()
        return stop, thread

    def stop_heartbeat(self, heartbeat: tuple[threading.Event, threading.Thread]) -> None:
        stop, thread = heartbeat
        stop.set()
        thread.join(timeout=0.2)

    def turn_completed(self, case: dict, state, duration: float) -> None:
        answer_status = getattr(state, "answer_status", None) or "completed"
        routing_mode = getattr(state, "routing_mode", None) or "no_tool"
        tool_count = len(getattr(state, "tool_call_history", []) or [])
        evidence_count = len(getattr(state, "evidence_ledger", []) or [])
        llm_count = len(getattr(state, "llm_calls", []) or [])
        progress, eta = self._record_duration(duration)
        self._write(
            f"[会话 {case['source_session_id']} | {case['case_id']}] 完成 {answer_status} | "
            f"路径 {routing_mode} | 工具 {tool_count} | 证据 {evidence_count} | "
            f"LLM {llm_count} | {duration:.1f} 秒"
        )
        self._write(f"[评测] 总进度 {progress}{eta}")

    def turn_failed(self, case: dict, exc: Exception, duration: float) -> None:
        progress, eta = self._record_duration(duration)
        self._write(
            f"[会话 {case['source_session_id']} | {case['case_id']}] 失败 "
            f"{type(exc).__name__}: {exc} | {duration:.1f} 秒"
        )
        self._write(f"[评测] 总进度 {progress}{eta} | 本会话停止，可用 --retry-failed 重试")

    def turn_agent_failed(self, case: dict, state, duration: float, message: str) -> None:
        progress, eta = self._record_duration(duration)
        self._write(
            f"[会话 {case['source_session_id']} | {case['case_id']}] Agent 失败 "
            f"{message} | {duration:.1f} 秒"
        )
        self._write(f"[评测] 总进度 {progress}{eta} | 本会话停止，可用 --retry-failed 重试")

    def batch_finished(self, status: str) -> None:
        elapsed = time.perf_counter() - self.started
        self._write(f"[评测] 本次执行结束 | 批次状态 {status} | 总耗时 {elapsed:.1f} 秒")

    def _record_duration(self, duration: float) -> tuple[str, str]:
        with self.lock:
            self.processed += 1
            self.durations.append(duration)
            progress = f"{self.processed}/{self.total_cases} ({self.processed / self.total_cases:.1%})"
            remaining = self.total_cases - self.processed
            if not remaining:
                return progress, ""
            average = sum(self.durations) / len(self.durations)
            eta_seconds = average * remaining / self.concurrency
            return progress, f" | 预计剩余约 {_format_duration(eta_seconds)}"

    def _write(self, message: str) -> None:
        with self.lock:
            print(message, file=sys.stderr, flush=True)


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{max(1, round(seconds))} 秒"
    if seconds < 3600:
        return f"{max(1, round(seconds / 60))} 分钟"
    return f"{seconds / 3600:.1f} 小时"


def prepare(dataset: Path, knowledge_cutoff: str, agent_version: str | None = None) -> dict:
    date.fromisoformat(knowledge_cutoff)
    raw = dataset.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    rows = _load_and_validate(dataset)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    batch_id = f"EVAL-{stamp}-{digest[:8].upper()}-{uuid4().hex[:4].upper()}"
    user_id = f"USER-{batch_id}"
    ensure_user(user_id, f"评测 {dataset.stem} {stamp}", "#B45F36")
    cases = [{
        "case_id": row["case_id"],
        "source_session_id": str(row["session_id"]),
        "agent_session_id": f"{batch_id}-SESSION-{int(row['session_id']):03d}",
        "expected_turn_id": int(row["turn_id"]),
        "question": row["question"],
        "annotation": {key: value for key, value in row.items() if key != "question"},
    } for row in rows]
    batch = {
        "batch_id": batch_id,
        "dataset_path": str(dataset.resolve()),
        "dataset_sha256": digest,
        "evaluation_user_id": user_id,
        "knowledge_cutoff": knowledge_cutoff,
        "agent_version": agent_version,
        "created_at": datetime.now(UTC).isoformat(),
    }
    repository.create_batch(batch, cases)
    return {**batch, "case_count": len(cases), "session_count": len({item["source_session_id"] for item in cases})}


def execute(
    batch_id: str, *, concurrency: int = 1, session_id: str | None = None,
    max_cases: int | None = None, retry_failed: bool = False,
) -> dict:
    batch = repository.get_batch(batch_id)
    if batch is None:
        raise ValueError(f"Unknown batch_id: {batch_id}")
    repository.reset_interrupted(batch_id)
    cases = repository.list_cases(
        batch_id, session_id=session_id, include_failed=retry_failed, max_cases=max_cases,
    )
    grouped: dict[str, list[dict]] = defaultdict(list)
    for case in cases:
        grouped[case["source_session_id"]].append(case)
    reporter = ProgressReporter(
        batch_id=batch_id, total_cases=len(cases), concurrency=concurrency,
    )
    reporter.batch_started(len(grouped))
    repository.mark_batch_running(batch_id)
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [pool.submit(_run_session, batch, items, reporter) for items in grouped.values()]
        for future in as_completed(futures):
            future.result()
    result = repository.refresh_batch_status(batch_id)
    reporter.batch_finished(result["status"])
    return result


def _run_session(batch: dict, cases: list[dict], reporter: ProgressReporter) -> None:
    ordered = sorted(cases, key=lambda item: item["expected_turn_id"])
    reporter.session_started(ordered[0]["source_session_id"], len(ordered))
    for index, case in enumerate(ordered, 1):
        if not repository.prior_turns_completed(
            batch["batch_id"], case["source_session_id"], case["expected_turn_id"],
        ):
            break
        repository.mark_case_running(batch["batch_id"], case["case_id"])
        reporter.turn_started(case, index, len(ordered))
        started = time.perf_counter()
        heartbeat = reporter.start_heartbeat(case)
        try:
            claim_session(
                batch["evaluation_user_id"], case["agent_session_id"],
                f"评测会话 {case['source_session_id']}",
            )
            state = run_agent(
                case["question"], session_id=case["agent_session_id"],
                knowledge_cutoff=batch["knowledge_cutoff"],
            )
            if not state.run_id:
                raise RuntimeError("Agent completed without a run_id")
            if _agent_execution_failed(state):
                message = _agent_failure_message(state)
                repository.mark_case_failed(
                    batch["batch_id"], case["case_id"], message, run_id=state.run_id,
                )
                reporter.turn_agent_failed(case, state, time.perf_counter() - started, message)
                break
            repository.mark_case_completed(batch["batch_id"], case["case_id"], state.run_id)
            reporter.turn_completed(case, state, time.perf_counter() - started)
        except Exception as exc:
            repository.mark_case_failed(
                batch["batch_id"], case["case_id"], f"{type(exc).__name__}: {exc}",
            )
            reporter.turn_failed(case, exc, time.perf_counter() - started)
            break  # Never execute a later turn after an infrastructure failure.
        finally:
            reporter.stop_heartbeat(heartbeat)


def _agent_execution_failed(state) -> bool:
    return (
        getattr(state, "answer_status", None) == "failed"
        or getattr(state, "workflow_status", None) in {"failed", "llm_failed"}
    )


def _agent_failure_message(state) -> str:
    errors = getattr(state, "errors", None) or []
    if errors:
        latest = errors[-1]
        if isinstance(latest, dict):
            stage = str(latest.get("stage") or "agent")
            error_type = str(latest.get("error_type") or "FAILED")
            detail = str(latest.get("message") or "Agent returned a failed state.")
            return f"[{stage}] {error_type}: {detail}"
        return str(latest)
    status = getattr(state, "workflow_status", None) or getattr(state, "answer_status", None) or "failed"
    return f"Agent returned failed status: {status}"


def _load_and_validate(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    required = {"case_id", "session_id", "turn_id", "question"}
    seen_cases: set[str] = set()
    sessions: dict[str, list[int]] = defaultdict(list)
    for number, row in enumerate(rows, 1):
        missing = required - row.keys()
        if missing:
            raise ValueError(f"Line {number} missing fields: {sorted(missing)}")
        if row["case_id"] in seen_cases:
            raise ValueError(f"Duplicate case_id: {row['case_id']}")
        seen_cases.add(row["case_id"])
        sessions[str(row["session_id"])].append(int(row["turn_id"]))
    for session, turns in sessions.items():
        if turns != list(range(1, len(turns) + 1)):
            raise ValueError(f"Session {session} turn_id must be contiguous and ordered")
    return rows


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--dataset", type=Path, required=True)
    prepare_parser.add_argument(
        "--knowledge-cutoff", default=knowledge_cutoff_from_env(),
        help="Fixed ISO date; defaults to FINTRACE_KNOWLEDGE_CUTOFF",
    )
    prepare_parser.add_argument("--agent-version")
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--batch-id", required=True)
    run_parser.add_argument("--concurrency", type=int, default=1)
    run_parser.add_argument("--session-id")
    run_parser.add_argument("--max-cases", type=int)
    run_parser.add_argument("--retry-failed", action="store_true")
    status_parser = commands.add_parser("status")
    status_parser.add_argument("--batch-id", required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        if not args.knowledge_cutoff:
            parser.error("--knowledge-cutoff or FINTRACE_KNOWLEDGE_CUTOFF is required")
        result = prepare(args.dataset, args.knowledge_cutoff, args.agent_version)
    elif args.command == "run":
        result = execute(
            args.batch_id, concurrency=args.concurrency, session_id=args.session_id,
            max_cases=args.max_cases, retry_failed=args.retry_failed,
        )
    else:
        result = repository.refresh_batch_status(args.batch_id, update=False)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
