import re
from datetime import date

from rank_bm25 import BM25Okapi

from schemas.document import DocumentChunk, DocumentSearchHit
from schemas.evidence import Evidence, EvidenceSource


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for value in re.findall(r"[A-Za-z0-9_.]+|[\u4e00-\u9fff]+", text.lower()):
        if not re.fullmatch(r"[\u4e00-\u9fff]+", value):
            tokens.append(value)
            continue
        if len(value) == 1:
            tokens.append(value)
        else:
            tokens.extend(value[index : index + 2] for index in range(len(value) - 1))
    return tokens


def filter_chunks(
    chunks: list[DocumentChunk],
    company_id: str | None,
    document_types: list[str] | None,
    start_date: date | None,
    end_date: date | None,
) -> list[DocumentChunk]:
    filtered: list[DocumentChunk] = []
    allowed_types = set(document_types or [])
    for chunk in chunks:
        if company_id and chunk.company_id != company_id:
            continue
        if allowed_types and chunk.document_type not in allowed_types:
            continue
        if start_date and chunk.publish_date < start_date:
            continue
        if end_date and chunk.publish_date > end_date:
            continue
        filtered.append(chunk)
    return filtered


def bm25_search(chunks: list[DocumentChunk], query: str, top_k: int = 8) -> list[DocumentSearchHit]:
    if not chunks:
        return []
    tokenized_docs = [tokenize(f"{chunk.title} {chunk.section or ''} {chunk.text}") for chunk in chunks]
    tokenized_query = tokenize(query)
    if not tokenized_query:
        return []
    bm25 = BM25Okapi(tokenized_docs)
    scores = bm25.get_scores(tokenized_query)
    max_score = max(scores) if len(scores) else 0
    hits: list[DocumentSearchHit] = []
    for chunk, score in sorted(zip(chunks, scores), key=lambda item: item[1], reverse=True)[:top_k]:
        normalized = float(score / max_score) if max_score else 0.0
        if normalized <= 0:
            continue
        hits.append(
            DocumentSearchHit(
                chunk=chunk,
                score=round(normalized, 6),
                evidence_id=f"EVID-{chunk.chunk_id}",
                retrieval={
                    "source": "bm25",
                    "matched_by": ["bm25"],
                    "bm25_score": round(normalized, 6),
                    "vector_score": None,
                    "final_score": round(normalized, 6),
                },
            )
        )
    return hits


def evidence_from_hits(hits: list[DocumentSearchHit], used_by: str) -> list[Evidence]:
    evidence: list[Evidence] = []
    for hit in hits:
        chunk = hit.chunk
        evidence.append(
            Evidence(
                evidence_id=hit.evidence_id,
                evidence_type="document_chunk",
                source=EvidenceSource(
                    document_id=chunk.document_id,
                    company_id=chunk.company_id,
                    document_type=chunk.document_type,
                    page=chunk.page,
                    source_path=chunk.source_path,
                ),
                fact={
                    "chunk_id": chunk.chunk_id,
                    "title": chunk.title,
                    "publish_date": chunk.publish_date.isoformat(),
                    "section": chunk.section,
                    "text": chunk.text,
                    "score": hit.score,
                    "retrieval": hit.retrieval,
                },
                used_by=[used_by],
            )
        )
    return evidence
