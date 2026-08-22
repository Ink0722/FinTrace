from __future__ import annotations

import argparse
import json
import math
import os
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from data_pipeline.entity_alias.build_index import canonical_company_id
from data_pipeline.entity_resolution.build_company_universe import (
    DEFAULT_OUTPUT as DEFAULT_UNIVERSE,
    load_company_universe,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "source" / "company_profiles" / "akshare_company_profiles.jsonl"
CNINFO_SOURCE = "akshare.stock_profile_cninfo"
XUEQIU_SOURCE = "akshare.stock_individual_basic_info_xq"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch and freeze A-share legal company profiles through AKShare.")
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--code", action="append", default=[], help="Fetch one stock code; repeat for several codes.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of pending codes attempted in this run.")
    parser.add_argument("--delay", type=float, default=0.8, help="Seconds between requests.")
    parser.add_argument("--retries", type=int, default=2, help="Retries after the first failed request.")
    parser.add_argument("--retry-delay", type=float, default=3.0)
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--read-timeout", type=float, default=30.0)
    parser.add_argument("--force", action="store_true", help="Fetch codes even if a successful record already exists.")
    parser.add_argument("--source", choices=("xueqiu", "cninfo"), default="xueqiu")
    parser.add_argument("--xq-token", default=os.getenv("FINTRACE_XQ_TOKEN"))
    parser.add_argument("--estimate", action="store_true", help="Report pending requests without importing AKShare or using the network.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.estimate:
        result = estimate_company_profiles(args.universe, args.output, args.code)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    try:
        import akshare as ak
    except ImportError as exc:
        raise SystemExit("AKShare is not installed. Install akshare==1.18.94 in the FinTrace environment.") from exc

    source_name = XUEQIU_SOURCE if args.source == "xueqiu" else CNINFO_SOURCE
    result = fetch_company_profiles(
        universe_path=args.universe,
        output_path=args.output,
        requested_codes=args.code,
        limit=args.limit,
        delay=args.delay,
        retries=args.retries,
        retry_delay=args.retry_delay,
        force=args.force,
        fetch_one=lambda code: _fetch_akshare_profile(
            ak,
            code,
            source=args.source,
            xq_token=args.xq_token,
            connect_timeout=args.connect_timeout,
            read_timeout=args.read_timeout,
        ),
        akshare_version=ak.__version__,
        source_name=source_name,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["failed"] == 0 else 2


def fetch_company_profiles(
    *,
    universe_path: Path,
    output_path: Path,
    requested_codes: list[str] | None,
    limit: int | None,
    delay: float,
    retries: int,
    retry_delay: float,
    force: bool,
    fetch_one: Callable[[str], object],
    akshare_version: str,
    source_name: str = CNINFO_SOURCE,
) -> dict:
    if retries < 0 or delay < 0 or retry_delay < 0:
        raise ValueError("retries and delays must be non-negative")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")

    universe = load_company_universe(universe_path, eligible_only=True)
    if requested_codes:
        codes = [canonical_company_id(code) for code in requested_codes]
        unknown = sorted(code for code in codes if code not in universe)
        if unknown:
            raise ValueError(f"Requested codes are absent from the eligible A-share universe: {unknown}")
    else:
        codes = sorted(universe)

    successful = _successful_codes(output_path)
    successful_in_scope = successful.intersection(codes)
    pending = [code for code in codes if force or code not in successful]
    if limit is not None:
        pending = pending[:limit]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    succeeded = failed = 0
    with output_path.open("a", encoding="utf-8", newline="\n") as handle:
        for index, company_id in enumerate(pending):
            print(f"[{index + 1}/{len(pending)}] fetching {company_id} ...", flush=True)
            record = _fetch_with_retries(
                company_id,
                _security_name(universe[company_id], company_id),
                fetch_one,
                retries=retries,
                retry_delay=retry_delay,
                akshare_version=akshare_version,
                source_name=source_name,
            )
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            if record["fetch_status"] == "success":
                succeeded += 1
                print(f"  success: {record['legal_name']}", flush=True)
            else:
                failed += 1
                print(
                    f"  failed after {record['attempts']} attempts: "
                    f"{record['error_type']}: {record['error_message']}",
                    flush=True,
                )
            if index + 1 < len(pending) and delay:
                time.sleep(delay)

    manifest = {
        "status": "complete" if failed == 0 else "completed_with_failures",
        "source": source_name,
        "akshare_version": akshare_version,
        "output_path": str(output_path),
        "universe_size": len(universe),
        "already_successful": len(successful_in_scope),
        "attempted": len(pending),
        "succeeded": succeeded,
        "failed": failed,
        "remaining": max(0, len(codes) - len(successful_in_scope) - succeeded),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }
    _write_json_atomic(output_path.with_name("fetch_manifest.json"), manifest)
    return manifest


def estimate_company_profiles(
    universe_path: Path, output_path: Path, requested_codes: list[str] | None = None
) -> dict:
    universe = load_company_universe(universe_path, eligible_only=True)
    if requested_codes:
        codes = [canonical_company_id(code) for code in requested_codes]
        unknown = sorted(code for code in codes if code not in universe)
        if unknown:
            raise ValueError(f"Requested codes are absent from the eligible A-share universe: {unknown}")
    else:
        codes = sorted(universe)
    successful = _successful_codes(output_path)
    pending = [code for code in codes if code not in successful]
    return {
        "status": "estimate",
        "universe_path": str(universe_path),
        "profile_path": str(output_path),
        "eligible_a_share_codes": len(codes),
        "already_successful": len(set(codes).intersection(successful)),
        "pending_requests": len(pending),
        "pending_examples": pending[:20],
    }


def _fetch_akshare_profile(
    akshare_module,
    code: str,
    *,
    source: str,
    xq_token: str | None,
    connect_timeout: float,
    read_timeout: float,
):
    if connect_timeout <= 0 or read_timeout <= 0:
        raise ValueError("connect_timeout and read_timeout must be positive")
    import requests

    original_post = requests.post

    def post_with_timeout(*args, **kwargs):
        kwargs.setdefault("timeout", (connect_timeout, read_timeout))
        return original_post(*args, **kwargs)

    with _temporary_attribute(requests, "post", post_with_timeout):
        if source == "cninfo":
            return akshare_module.stock_profile_cninfo(symbol=code)
        symbol = _xueqiu_symbol(code)
        return akshare_module.stock_individual_basic_info_xq(
            symbol=symbol, token=xq_token, timeout=read_timeout
        )


@contextmanager
def _temporary_attribute(target, name: str, value):
    original = getattr(target, name)
    setattr(target, name, value)
    try:
        yield
    finally:
        setattr(target, name, original)


def _fetch_with_retries(
    company_id: str,
    security_name: str,
    fetch_one: Callable[[str], object],
    *,
    retries: int,
    retry_delay: float,
    akshare_version: str,
    source_name: str,
) -> dict:
    bare_code = company_id.split(".", 1)[0]
    last_error: Exception | None = None
    for attempt in range(1, retries + 2):
        try:
            frame = fetch_one(bare_code)
            raw = _frame_record(frame)
            legal_name = str(raw.get("公司名称") or raw.get("org_name_cn") or "").strip()
            if not legal_name:
                raise ValueError("AKShare response has no 公司名称")
            return {
                "company_id": company_id,
                "legal_name": legal_name,
                "security_name": str(
                    raw.get("A股简称") or raw.get("org_short_name_cn") or security_name
                ).strip(),
                "former_names": _split_former_names(
                    raw.get("曾用简称") or raw.get("pre_name_cn")
                ),
                "source": source_name,
                "akshare_version": akshare_version,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "fetch_status": "success",
                "attempts": attempt,
                "raw": raw,
            }
        except Exception as exc:  # The upstream raises several requests/parser exceptions.
            last_error = exc
            if attempt <= retries and retry_delay:
                time.sleep(retry_delay)
    assert last_error is not None
    return {
        "company_id": company_id,
        "security_name": security_name,
        "source": source_name,
        "akshare_version": akshare_version,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fetch_status": "failed",
        "attempts": retries + 1,
        "error_type": type(last_error).__name__,
        "error_message": str(last_error)[:1000],
    }


def _frame_record(frame: object) -> dict:
    if not hasattr(frame, "empty") or not hasattr(frame, "iloc"):
        raise TypeError("AKShare response is not a pandas DataFrame")
    if frame.empty:
        raise ValueError("AKShare response is empty")
    columns = {str(column) for column in frame.columns}
    if {"item", "value"}.issubset(columns):
        return {
            str(row["item"]): _json_value(row["value"])
            for _, row in frame.iterrows()
        }
    return {str(key): _json_value(value) for key, value in frame.iloc[0].to_dict().items()}


def _xueqiu_symbol(company_id: str) -> str:
    code = company_id.split(".", 1)[0]
    suffix = company_id.split(".", 1)[1] if "." in company_id else ""
    if suffix == "SH" or code.startswith("6"):
        return f"SH{code}"
    if suffix == "BJ" or code.startswith(("4", "8", "9")):
        return f"BJ{code}"
    return f"SZ{code}"


def _json_value(value):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value if isinstance(value, (str, int, float, bool, list, dict)) else str(value)


def _split_former_names(value) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text or text in {"-", "--", "无"}:
        return []
    for delimiter in ("、", ",", "，", ";", "；"):
        text = text.replace(delimiter, "|")
    return list(dict.fromkeys(part.strip() for part in text.split("|") if part.strip()))


def _security_name(row: dict, company_id: str) -> str:
    names = row.get("security_names")
    if isinstance(names, list):
        for name in names:
            if str(name).strip():
                return str(name).strip()
    return company_id


def _successful_codes(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    successful: set[str] = set()
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            company_id = canonical_company_id(str(row.get("company_id") or ""))
            if company_id and row.get("fetch_status") == "success":
                successful.add(company_id)
    return successful


def _write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


if __name__ == "__main__":
    raise SystemExit(main())
