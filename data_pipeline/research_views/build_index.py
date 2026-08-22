from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from tools.research_analysis.config import RESEARCH_MAPPING_VERSION, ResearchAnalysisConfig


SCHEMA_SQL = """
PRAGMA synchronous = NORMAL;
CREATE TABLE research_views (
    view_id TEXT PRIMARY KEY, report_id TEXT NOT NULL UNIQUE, company_id TEXT NOT NULL,
    publish_date TEXT NOT NULL, institution TEXT NOT NULL, authors_json TEXT NOT NULL,
    title TEXT NOT NULL, report_type TEXT, report_sub_type TEXT, rating TEXT,
    rating_change TEXT, target_price REAL, source_document_id TEXT NOT NULL
);
CREATE TABLE research_claims (
    claim_id TEXT PRIMARY KEY, view_id TEXT NOT NULL, company_id TEXT NOT NULL,
    publish_date TEXT NOT NULL, institution TEXT NOT NULL, claim_type TEXT NOT NULL,
    topic TEXT, stance TEXT NOT NULL, claim_text TEXT NOT NULL, source_span TEXT NOT NULL,
    source_document_id TEXT NOT NULL, chunk_id TEXT, extraction_method TEXT NOT NULL,
    confidence REAL NOT NULL, quality_flags_json TEXT NOT NULL,
    FOREIGN KEY(view_id) REFERENCES research_views(view_id)
);
CREATE INDEX idx_views_company_date ON research_views(company_id, publish_date);
CREATE INDEX idx_claims_company_date ON research_claims(company_id, publish_date);
CREATE INDEX idx_claims_company_type_date ON research_claims(company_id, claim_type, publish_date);
CREATE INDEX idx_claims_institution_date ON research_claims(institution, publish_date);
"""

SECTION_PATTERN = re.compile(
    r"(?P<label>事件|事件概述|投资要点|主要观点|核心观点|分析判断|"
    r"盈利预测与投资评级|盈利预测|投资建议|风险提示)\s*[：:]"
)
RISK_SPLIT_PATTERN = re.compile(r"[；;。]|[、，,](?=[^0-9])")
TITLE_PREFIX_PATTERN = re.compile(r"^.*?[：:]\s*")


def build_research_index(research_path: Path, chunks_path: Path, output_path: Path) -> dict:
    started = time.perf_counter()
    if not research_path.is_file():
        raise FileNotFoundError(f"Missing research source: {research_path}")
    if not chunks_path.is_file():
        raise FileNotFoundError(f"Missing chunks source: {chunks_path}")
    chunk_map = load_research_chunks(chunks_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    stats = {
        "source_rows": 0, "view_rows": 0, "claim_rows": 0, "invalid": 0,
        "claims_with_chunk": 0, "chunk_required_claims": 0,
        "chunk_mapping_failures": 0, "claim_types": {},
    }
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            connection.executescript(SCHEMA_SQL)
            with research_path.open("r", encoding="utf-8-sig") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    stats["source_rows"] += 1
                    try:
                        row = json.loads(line)
                        view = build_view(row)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        stats["invalid"] += 1
                        continue
                    connection.execute(
                        "INSERT INTO research_views VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        view,
                    )
                    stats["view_rows"] += 1
                    report_id, company_id, publish_date, institution = view[1], view[2], view[3], view[4]
                    document_id = view[-1]
                    claims = extract_claims(row)
                    for index, claim in enumerate(claims, 1):
                        chunk_required = claim["extraction_method"] == "section_rule"
                        chunk_id = (
                            locate_chunk(claim["source_span"], chunk_map.get(document_id, []))
                            if chunk_required
                            else None
                        )
                        quality_flags = []
                        if not chunk_id:
                            quality_flags.append("chunk_not_located" if chunk_required else "document_level_source")
                        digest = hashlib.sha256(
                            f"{report_id}|{claim['claim_type']}|{index}|{claim['source_span']}".encode("utf-8")
                        ).hexdigest()[:20].upper()
                        connection.execute(
                            "INSERT INTO research_claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                f"RCLAIM-{digest}", f"VIEW-{report_id}", company_id, publish_date,
                                institution, claim["claim_type"], claim.get("topic"), claim["stance"],
                                claim["claim_text"], claim["source_span"], document_id, chunk_id,
                                claim["extraction_method"], claim["confidence"],
                                json.dumps(quality_flags, ensure_ascii=False),
                            ),
                        )
                        stats["claim_rows"] += 1
                        stats["claims_with_chunk"] += bool(chunk_id)
                        stats["chunk_required_claims"] += chunk_required
                        stats["chunk_mapping_failures"] += bool(chunk_required and not chunk_id)
                        claim_type = claim["claim_type"]
                        stats["claim_types"][claim_type] = stats["claim_types"].get(claim_type, 0) + 1
            connection.commit()
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)
    manifest = {
        "status": "complete", "mapping_version": RESEARCH_MAPPING_VERSION,
        "index_path": str(output_path),
        "sources": {
            "research_reports": source_manifest(research_path),
            "chunks": source_manifest(chunks_path),
        },
        "stats": stats,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }
    write_atomic(output_path.with_name("manifest.json"), manifest)
    return manifest


def build_view(row: dict) -> tuple:
    report_id = clean(row.get("report_id"))
    code = clean(row.get("sec_code"))
    exchange = clean(row.get("exchange_code")).upper()
    suffix = {"XSHG": ".SH", "XSHE": ".SZ", "XBSE": ".BJ"}.get(exchange)
    publish_date = clean(row.get("publish_date"))
    institution = clean(row.get("org_name"))
    title = clean(row.get("title"))
    if not report_id or not re.fullmatch(r"\d{6}", code) or not suffix:
        raise ValueError("invalid identity")
    time.strptime(publish_date, "%Y-%m-%d")
    if not institution or not title or not clean(row.get("abstract")):
        raise ValueError("missing research content")
    target = row.get("tar_price_val") if row.get("tar_price_val") is not None else row.get("tar_price")
    try:
        target = float(target) if target not in (None, "") else None
    except (TypeError, ValueError):
        target = None
    return (
        f"VIEW-{report_id}", report_id, f"{code}{suffix}", publish_date, institution,
        json.dumps(split_authors(row.get("author")), ensure_ascii=False), title,
        clean(row.get("report_type")) or None, clean(row.get("report_sub_type")) or None,
        clean(row.get("rating_org")) or None, clean(row.get("rating_change")) or None,
        target, f"RR-{report_id}",
    )


def extract_claims(row: dict) -> list[dict]:
    title = clean(row.get("title"))
    abstract = clean(row.get("abstract"))
    claims: list[dict] = []
    rating = clean(row.get("rating_org"))
    if rating:
        change = clean(row.get("rating_change"))
        span = first_matching_span(abstract, (f"维持“{rating}”评级", f"维持\"{rating}\"评级", f"{rating}评级")) or rating
        claims.append(claim("investment_rating", "投资评级", rating_stance(rating), f"{change + '，' if change else ''}{rating}评级", span, "metadata", 1.0))

    sections = split_sections(abstract)
    forecast = sections.get("盈利预测与投资评级") or sections.get("盈利预测")
    if forecast:
        claims.append(claim("earnings_forecast", "盈利预测", "neutral", compact(forecast), forecast, "section_rule", 1.0))
    risks = sections.get("风险提示")
    if risks:
        for item in split_risks(risks):
            claims.append(claim("risk_opinion", risk_topic(item), "negative", item, item, "section_rule", 1.0))

    judgment = TITLE_PREFIX_PATTERN.sub("", title).strip() or title
    if judgment:
        claims.append(claim("analyst_judgment", "核心判断", infer_stance(judgment), judgment, title, "title_rule", 0.9))

    event_text = sections.get("事件") or sections.get("事件概述")
    if event_text:
        claims.append(claim("cited_fact", "研报引用事实", "neutral", compact(event_text), event_text, "section_rule", 0.8))
    return dedupe_claims(claims)


def split_sections(text: str) -> dict[str, str]:
    matches = list(SECTION_PATTERN.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[match.end():end].strip()
        if value:
            sections.setdefault(match.group("label"), value)
    return sections


def split_risks(text: str) -> list[str]:
    values = [item.strip(" ：:，,。；;") for item in RISK_SPLIT_PATTERN.split(text)]
    return [item for item in values if 4 <= len(item) <= 200][:20]


def claim(claim_type, topic, stance, claim_text, source_span, method, confidence) -> dict:
    return {
        "claim_type": claim_type, "topic": topic, "stance": stance,
        "claim_text": claim_text[:500], "source_span": source_span[:1500],
        "extraction_method": method, "confidence": confidence,
    }


def load_research_chunks(path: Path) -> dict[str, list[tuple[str, str]]]:
    values: dict[str, list[tuple[str, str]]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if '"document_id":"RR-' not in line and '"document_id": "RR-' not in line:
                continue
            row = json.loads(line)
            if str(row.get("document_id", "")).startswith("RR-"):
                values[row["document_id"]].append((row["chunk_id"], clean(row.get("text"))))
    return values


def locate_chunk(span: str, chunks: list[tuple[str, str]]) -> str | None:
    if not chunks:
        return None
    needle = compact(span)
    for chunk_id, text in chunks:
        if span in text or (needle and needle in compact(text)):
            return chunk_id
    tokens = set(needle[index:index + 2] for index in range(max(0, len(needle) - 1)))
    best_id, best_score = None, 0.0
    for chunk_id, text in chunks:
        candidate = compact(text)
        candidate_tokens = set(candidate[index:index + 2] for index in range(max(0, len(candidate) - 1)))
        score = len(tokens & candidate_tokens) / len(tokens | candidate_tokens) if tokens and candidate_tokens else 0.0
        if score > best_score:
            best_id, best_score = chunk_id, score
    return best_id if best_score >= 0.25 else None


def dedupe_claims(claims: list[dict]) -> list[dict]:
    result, seen = [], set()
    for item in claims:
        key = (item["claim_type"], compact(item["claim_text"]))
        if key not in seen:
            result.append(item)
            seen.add(key)
    return result


def split_authors(value) -> list[str]:
    return [item.strip() for item in re.split(r"[,，、;/]", clean(value)) if item.strip()]


def rating_stance(value: str) -> str:
    if any(word in value for word in ("买入", "增持", "推荐", "强烈推荐")):
        return "positive"
    if any(word in value for word in ("卖出", "减持", "回避")):
        return "negative"
    return "neutral"


def infer_stance(value: str) -> str:
    positive = sum(word in value for word in ("增长", "改善", "提升", "向好", "高增", "超预期"))
    negative = sum(word in value for word in ("下降", "承压", "低于预期", "风险", "放缓"))
    return "positive" if positive > negative else "negative" if negative > positive else "neutral"


def risk_topic(value: str) -> str:
    for topic in ("政策", "需求", "价格", "竞争", "项目", "汇率", "原材料", "减值", "现金流"):
        if topic in value:
            return topic
    return "风险提示"


def first_matching_span(text: str, candidates: tuple[str, ...]) -> str | None:
    return next((item for item in candidates if item in text), None)


def compact(value: str) -> str:
    return re.sub(r"\s+", "", clean(value))


def clean(value) -> str:
    return "" if value is None else str(value).strip()


def source_manifest(path: Path) -> dict:
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": sha256(path)}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_atomic(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main(argv=None) -> int:
    config = ResearchAnalysisConfig.from_env()
    parser = argparse.ArgumentParser(description="Build the FinTrace research-view index.")
    parser.add_argument("--research", type=Path, default=config.research_path)
    parser.add_argument("--chunks", type=Path, default=config.chunks_path)
    parser.add_argument("--output", type=Path, default=config.index_path)
    args = parser.parse_args(argv)
    print(json.dumps(build_research_index(args.research, args.chunks, args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
