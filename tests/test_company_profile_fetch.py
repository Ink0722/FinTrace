import json
from pathlib import Path

import pandas as pd

from data_pipeline.entity_resolution.fetch_company_profiles import fetch_company_profiles


def test_fetch_profiles_writes_success_and_resumes(tmp_path: Path) -> None:
    universe = tmp_path / "universe.jsonl"
    output = tmp_path / "profiles.jsonl"
    _write_universe(universe, "600030.SH", "CITIC Securities")
    calls: list[str] = []

    def fetch_one(code: str):
        calls.append(code)
        return pd.DataFrame(
            [{"org_name_cn": "CITIC Securities Co Ltd", "org_short_name_cn": "CITIC Securities", "pre_name_cn": "Old CITIC"}]
        )

    first = _fetch(universe, output, fetch_one)
    second = _fetch(universe, output, fetch_one)

    assert first["succeeded"] == 1
    assert second["attempted"] == 0
    assert calls == ["600030"]
    row = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert row["company_id"] == "600030.SH"
    assert row["legal_name"] == "CITIC Securities Co Ltd"
    assert row["former_names"] == ["Old CITIC"]


def test_fetch_profiles_records_failure_after_retries(tmp_path: Path) -> None:
    universe = tmp_path / "universe.jsonl"
    output = tmp_path / "profiles.jsonl"
    _write_universe(universe, "000001.SZ", "Ping An Bank")
    attempts = 0

    def fail(_code: str):
        nonlocal attempts
        attempts += 1
        raise TimeoutError("upstream timeout")

    result = _fetch(universe, output, fail, retries=2)

    assert result["failed"] == 1
    assert attempts == 3
    row = json.loads(output.read_text(encoding="utf-8"))
    assert row["fetch_status"] == "failed"
    assert row["error_type"] == "TimeoutError"


def test_fetch_profiles_accepts_xueqiu_item_value_shape(tmp_path: Path) -> None:
    universe = tmp_path / "universe.jsonl"
    output = tmp_path / "profiles.jsonl"
    _write_universe(universe, "600030.SH", "CITIC Securities")

    result = _fetch(
        universe,
        output,
        lambda _code: pd.DataFrame(
            [
                {"item": "org_name_cn", "value": "CITIC Securities Co Ltd"},
                {"item": "org_short_name_cn", "value": "CITIC Securities"},
                {"item": "pre_name_cn", "value": "Old CITIC"},
            ]
        ),
        source_name="akshare.stock_individual_basic_info_xq",
    )

    row = json.loads(output.read_text(encoding="utf-8"))
    assert result["succeeded"] == 1
    assert row["legal_name"] == "CITIC Securities Co Ltd"
    assert row["former_names"] == ["Old CITIC"]
    assert row["source"] == "akshare.stock_individual_basic_info_xq"


def _fetch(universe: Path, output: Path, fetch_one, *, retries: int = 0, source_name: str = "test") -> dict:
    return fetch_company_profiles(
        universe_path=universe,
        output_path=output,
        requested_codes=[],
        limit=None,
        delay=0,
        retries=retries,
        retry_delay=0,
        force=False,
        fetch_one=fetch_one,
        akshare_version="test",
        source_name=source_name,
    )


def _write_universe(path: Path, company_id: str, name: str) -> None:
    row = {
        "company_id": company_id,
        "code_type": "a_share",
        "profile_eligible": True,
        "sources": ["research_reports"],
        "security_names": [name],
        "first_observed_date": None,
        "last_observed_date": None,
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
