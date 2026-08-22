"""Prepare, execute and inspect resumable FinTrace evaluation batches."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from evaluation.runner import repository
from harness.graph.workflow import knowledge_cutoff_from_env, run_agent
from harness.tracing.users import claim_session, ensure_user


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
    repository.mark_batch_running(batch_id)
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [pool.submit(_run_session, batch, items) for items in grouped.values()]
        for future in as_completed(futures):
            future.result()
    return repository.refresh_batch_status(batch_id)


def _run_session(batch: dict, cases: list[dict]) -> None:
    for case in sorted(cases, key=lambda item: item["expected_turn_id"]):
        if not repository.prior_turns_completed(
            batch["batch_id"], case["source_session_id"], case["expected_turn_id"],
        ):
            break
        repository.mark_case_running(batch["batch_id"], case["case_id"])
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
            repository.mark_case_completed(batch["batch_id"], case["case_id"], state.run_id)
        except Exception as exc:
            repository.mark_case_failed(
                batch["batch_id"], case["case_id"], f"{type(exc).__name__}: {exc}",
            )
            break  # Never execute a later turn after an infrastructure failure.


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
