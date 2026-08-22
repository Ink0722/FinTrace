"""Export the SQLite observability store as one JSONL record per Agent run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.tracing.store import connect, get_run


def export(output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(readonly=True) as connection:
        run_ids = [row[0] for row in connection.execute(
            "SELECT run_id FROM agent_runs ORDER BY created_at, run_id"
        )]
    with output_path.open("w", encoding="utf-8") as file:
        for run_id in run_ids:
            file.write(json.dumps(get_run(run_id), ensure_ascii=False, default=str) + "\n")
    return len(run_ids)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    count = export(args.output)
    print(json.dumps({"status": "completed", "rows": count, "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
