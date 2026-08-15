"""Retry only announcement downloads that previously failed due to timeouts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from data_pipeline.competition.preprocess import is_timeout_failure, repair_announcements


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Preprocessed data root")
    parser.add_argument("--workers", type=int, default=3, help="Concurrent downloads")
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Maximum wall-clock seconds for each downloaded document",
    )
    parser.add_argument("--retries", type=int, default=1, help="HTTP retries per source URL")
    parser.add_argument("--dry-run", action="store_true", help="Count eligible records without downloading")
    return parser


def count_timeout_records(data_dir: Path) -> int:
    announcements = data_dir / "jsonl" / "announcements.jsonl"
    if not announcements.is_file():
        raise FileNotFoundError(f"Announcement JSONL does not exist: {announcements}")
    count = 0
    with announcements.open("r", encoding="utf-8") as handle:
        for line in handle:
            if is_timeout_failure(json.loads(line)):
                count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.workers < 1:
        raise ValueError("--workers must be at least 1")
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")
    if args.retries < 0:
        raise ValueError("--retries cannot be negative")

    data_dir = args.data_dir.resolve()
    if args.dry_run:
        print(f"Eligible timeout records: {count_timeout_records(data_dir)}")
        return 0

    report = repair_announcements(
        data_dir=data_dir,
        workers=args.workers,
        timeout=args.timeout,
        retries=args.retries,
        timeout_only=True,
    )
    print(
        f"Timeout retry complete: selected={report['selected']}, "
        f"results={report['repair_statuses']}, "
        f"remaining_timeouts={report['remaining_timeouts']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
