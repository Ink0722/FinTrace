"""Initialize a persistent showcase runtime database without overwriting user data."""
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path
from typing import Sequence

from harness.tracing.store import connect


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = PROJECT_ROOT / "deployment" / "assets" / "fintrace-showcase-seed.sqlite3"
DEFAULT_RUNTIME = PROJECT_ROOT / "runtime" / "fintrace.sqlite3"


def bootstrap(*, seed: Path, runtime: Path) -> str:
    seed = seed.resolve()
    runtime = runtime.resolve()
    if runtime.exists():
        connection = connect(path=runtime)
        connection.close()
        return "preserved"
    if not seed.is_file():
        raise FileNotFoundError(f"Showcase seed not found: {seed}")

    runtime.parent.mkdir(parents=True, exist_ok=True)
    temporary = runtime.with_suffix(runtime.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        shutil.copy2(seed, temporary)
        connection = connect(path=temporary)
        connection.close()
        os.replace(temporary, runtime)
    finally:
        temporary.unlink(missing_ok=True)
    return "initialized"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(bootstrap(seed=args.seed, runtime=args.runtime))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
