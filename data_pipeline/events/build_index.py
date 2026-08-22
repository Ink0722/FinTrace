from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from tools.event_timeline.config import EVENT_MAPPING_VERSION, EventTimelineConfig


SCHEMA_SQL = """
PRAGMA synchronous = NORMAL;
CREATE TABLE events (
    event_id TEXT PRIMARY KEY, company_id TEXT NOT NULL, event_type TEXT NOT NULL,
    event_date TEXT NOT NULL, announcement_date TEXT NOT NULL, effective_date TEXT,
    date_precision TEXT NOT NULL, event_stage TEXT NOT NULL, title TEXT NOT NULL,
    summary TEXT NOT NULL, entities_json TEXT NOT NULL, agencies_json TEXT NOT NULL,
    reference_ids_json TEXT NOT NULL, topic_signature TEXT NOT NULL, source_document_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL, source_path TEXT, extraction_method TEXT NOT NULL,
    quality_flags_json TEXT NOT NULL
);
CREATE INDEX idx_events_company_date ON events(company_id, event_date);
CREATE INDEX idx_events_company_type_date ON events(company_id, event_type, event_date);
CREATE INDEX idx_events_announcement ON events(company_id, announcement_date);
"""

EVENT_PATTERNS = (
    ("regulatory_inquiry", ("问询", "关注函", "监管函")),
    ("audit_opinion", ("审计意见", "保留意见", "无法表示意见", "否定意见")),
    ("controller_change", ("控制权变更", "实际控制人变更", "实控人变更")),
    ("share_pledge", ("股份质押", "股权质押", "解除质押")),
    ("financial_restated", ("会计差错", "财务报表更正", "前期差错更正", "更正公告")),
    ("major_litigation", ("重大诉讼", "重大仲裁", "诉讼进展", "仲裁进展")),
    ("regulatory_penalty", ("行政处罚", "处罚决定", "监管措施", "警示函", "立案", "纪律处分", "违规", "处罚")),
    ("risk_warning", ("退市风险", "风险警示", "实施其他风险警示", "撤销风险警示")),
)

NON_EVENT_PATTERNS = (
    "不存在被", "未被证券监管", "未受到证券监管", "未受过", "无被处罚", "不存在处罚",
)

STAGE_PATTERNS = (
    ("correction", ("更正", "补充公告", "修订")),
    ("remediation", ("整改报告", "整改完成", "整改情况")),
    ("response", ("回复", "答复")),
    ("resolution", ("结案", "终结", "判决", "处罚决定书", "撤销")),
    ("progress", ("进展", "延期", "听证")),
    ("initial", ("收到", "立案", "受理", "提起诉讼", "质押")),
)

AGENCY_PATTERN = re.compile(r"([\u4e00-\u9fff]{2,20}?(?:证监会|证监局|证券交易所|交易所|监管局|人民法院|法院|仲裁委员会))")
REFERENCE_PATTERNS = (
    re.compile(r"([〔\[（(]\d{4}[〕\]）)]\s*\d+号)"),
    re.compile(r"((?:问询函|关注函|监管函|警示函|处罚字)\s*[A-Za-z0-9_-]{2,24}号)"),
)
BOILERPLATE_PATTERN = re.compile(
    r"^.*?[:：]|关于|公司|收到|出具|有关|相关|公告|报告|决定书|通知书|回复|答复|整改|进展|情况|的"
)


def classify_event(title: str, categories: list[str]) -> str | None:
    text = " ".join([title, *categories])
    for event_type, patterns in EVENT_PATTERNS:
        if any(pattern in text for pattern in patterns):
            return event_type
    return None


def is_non_event_statement(title: str) -> bool:
    compact = re.sub(r"\s+", "", title)
    return any(pattern in compact for pattern in NON_EVENT_PATTERNS)


def classify_stage(title: str) -> str:
    for stage, patterns in STAGE_PATTERNS:
        if any(pattern in title for pattern in patterns):
            return stage
    return "unknown"


def extract_agencies(title: str) -> list[str]:
    values = []
    for match in AGENCY_PATTERN.finditer(title):
        value = match.group(1)
        for marker in ("收到", "来自", "由", "被", "对"):
            if marker in value:
                value = value.rsplit(marker, 1)[-1]
        values.append(value)
    return list(dict.fromkeys(values))


def extract_reference_ids(title: str) -> list[str]:
    values = []
    for pattern in REFERENCE_PATTERNS:
        values.extend(match.group(1).strip() for match in pattern.finditer(title))
    return list(dict.fromkeys(values))


def build_topic_signature(title: str) -> str:
    value = BOILERPLATE_PATTERN.sub("", title)
    value = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", value).lower()
    return value[:160]


def build_event_index(normalized_dir: Path, output_path: Path) -> dict:
    started = time.perf_counter()
    source_path = normalized_dir / "announcements.jsonl"
    if not source_path.is_file():
        raise FileNotFoundError(f"Missing normalized announcement file: {source_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    stats = {"source_rows": 0, "event_rows": 0, "non_event_statements": 0, "unclassified": 0, "invalid": 0, "with_agencies": 0, "with_reference_ids": 0, "event_types": {}, "event_stages": {}}
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            connection.executescript(SCHEMA_SQL)
            batch = []
            with source_path.open("r", encoding="utf-8-sig") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    stats["source_rows"] += 1
                    try:
                        row = json.loads(line)
                        company_id = str(row.get("s_info_windcode") or "").strip().upper()
                        announcement_date = str(row.get("ann_dt") or "").strip()
                        title = str(row.get("n_info_title") or "").strip()
                        document_id = str(row.get("id") or row.get("object_id") or "").strip()
                        if not company_id or not announcement_date or not title or not document_id:
                            raise ValueError
                        time.strptime(announcement_date, "%Y-%m-%d")
                    except (json.JSONDecodeError, ValueError):
                        stats["invalid"] += 1
                        continue
                    if is_non_event_statement(title):
                        stats["non_event_statements"] += 1
                        continue
                    event_type = classify_event(title, [str(item) for item in row.get("category_names") or []])
                    if event_type is None:
                        stats["unclassified"] += 1
                        continue
                    digest = hashlib.sha256(f"{company_id}|{announcement_date}|{document_id}|{event_type}".encode("utf-8")).hexdigest()[:24].upper()
                    event_stage = classify_stage(title)
                    agencies = extract_agencies(title)
                    reference_ids = extract_reference_ids(title)
                    quality_flags = ["event_date_uses_announcement_date", "title_level_fact_only"]
                    batch.append((
                        f"EVT-ANN-{digest}", company_id, event_type, announcement_date, announcement_date,
                        None, "announcement_only", event_stage, title, title,
                        json.dumps([company_id], ensure_ascii=False),
                        json.dumps(agencies, ensure_ascii=False),
                        json.dumps(reference_ids, ensure_ascii=False),
                        build_topic_signature(title), document_id, f"EVID-EVT-{digest}",
                        row.get("document_path"), "announcement_title_rule_v3",
                        json.dumps(quality_flags, ensure_ascii=False),
                    ))
                    stats["event_rows"] += 1
                    stats["with_agencies"] += bool(agencies)
                    stats["with_reference_ids"] += bool(reference_ids)
                    stats["event_types"][event_type] = stats["event_types"].get(event_type, 0) + 1
                    stats["event_stages"][event_stage] = stats["event_stages"].get(event_stage, 0) + 1
                    if len(batch) >= 5000:
                        connection.executemany("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
                        batch.clear()
            if batch:
                connection.executemany("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
            connection.commit()
            stats["inserted_rows"] = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)
    stat = source_path.stat()
    manifest = {"status": "complete", "mapping_version": EVENT_MAPPING_VERSION, "index_path": str(output_path), "source": {"path": str(source_path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": _sha256(source_path)}, "stats": stats, "elapsed_ms": round((time.perf_counter() - started) * 1000)}
    _write_atomic(output_path.with_name("manifest.json"), manifest)
    return manifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_atomic(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main(argv=None) -> int:
    config = EventTimelineConfig.from_env()
    parser = argparse.ArgumentParser(description="Build the FinTrace event timeline index.")
    parser.add_argument("--normalized-dir", type=Path, default=config.normalized_dir)
    parser.add_argument("--output", type=Path, default=config.index_path)
    args = parser.parse_args(argv)
    print(json.dumps(build_event_index(args.normalized_dir, args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
