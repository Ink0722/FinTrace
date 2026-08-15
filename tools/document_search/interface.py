from datetime import date

from schemas.enums import ErrorType, ToolName, ToolStatus
from schemas.tool_calls import ToolCall
from schemas.tool_results import ToolError, ToolMetrics, ToolResult
from tools.document_search.kb_loader import knowledge_base_available, load_kb_chunks, resolve_kb_path
from tools.document_search.sample_data import load_sample_chunks
from tools.document_search.search import bm25_search, evidence_from_hits, filter_chunks
from tools.document_search.vector_search import merge_hybrid_hits, vector_index_available, vector_search


def document_search(call: ToolCall) -> ToolResult:
    query = call.arguments.get("query") or ""
    company_ids = call.arguments.get("company_ids") or []
    if not isinstance(company_ids, list) or not all(isinstance(item, str) for item in company_ids) or len(company_ids) > 1:
        return ToolResult(
            tool_call_id=call.tool_call_id,
            tool_name=ToolName.DOCUMENT_SEARCH,
            status=ToolStatus.FAILED,
            error=ToolError(
                error_type=ErrorType.INVALID_ARGUMENT,
                message="document_search currently supports zero or one company in company_ids.",
                retryable=False,
                details={"company_ids": company_ids},
            ),
        )
    company_id = company_ids[0] if company_ids else None
    document_types = call.arguments.get("document_types")
    top_k = int(call.arguments.get("top_k") or 8)
    pool_k = int(call.arguments.get("pool_k") or max(top_k * 5, 50))
    mode = call.arguments.get("mode") or "hybrid"
    start_date = date.fromisoformat(call.arguments["start_date"]) if call.arguments.get("start_date") else None
    end_date = date.fromisoformat(call.arguments["end_date"]) if call.arguments.get("end_date") else None
    kb_path = resolve_kb_path()
    kb_dir = kb_path.parent
    warnings: list[str] = []
    source = "sample"
    if knowledge_base_available(kb_path):
        chunks = load_kb_chunks(
            company_id=company_id,
            document_types=document_types,
            start_date=start_date,
            end_date=end_date,
            kb_path=kb_path,
        )
        source = "knowledge_base"
        warnings.append(f"Using local knowledge base: {kb_path}")
    else:
        chunks = filter_chunks(
            load_sample_chunks(),
            company_id=company_id,
            document_types=document_types,
            start_date=start_date,
            end_date=end_date,
        )
        warnings.append("Using built-in sample document chunks. Build data/knowledge_base/fintrace_kb.sqlite for real documents.")
    bm25_hits = bm25_search(chunks, query=query, top_k=pool_k if mode == "hybrid" else top_k)
    vector_hits = []
    vector_available = False
    if source == "knowledge_base" and mode in {"vector", "hybrid"}:
        if vector_index_available(kb_dir):
            vector_available = True
            try:
                vector_hits = vector_search(
                    query=query,
                    kb_dir=kb_dir,
                    kb_path=kb_path,
                    top_k=pool_k if mode == "hybrid" else top_k,
                    company_id=company_id,
                    document_types=document_types,
                    start_date=start_date,
                    end_date=end_date,
                )
            except Exception as exc:
                warnings.append(f"Vector search failed, falling back to BM25: {type(exc).__name__}: {exc}")
        else:
            warnings.append("Vector index not found; using BM25 only. Run build_kb with --build-vector to enable vector/hybrid search.")

    if mode == "vector":
        hits = vector_hits[:top_k]
    elif mode == "hybrid" and vector_hits:
        hits = merge_hybrid_hits(bm25_hits, vector_hits, top_k=top_k)
    else:
        hits = bm25_hits[:top_k]
    evidence = evidence_from_hits(hits, used_by=call.tool_call_id)
    return ToolResult(
        tool_call_id=call.tool_call_id,
        tool_name=ToolName.DOCUMENT_SEARCH,
        status=ToolStatus.SUCCESS,
        data={
            "company_ids": company_ids,
            "query": query,
            "top_k": top_k,
            "pool_k": pool_k,
            "mode": mode,
            "source": source,
            "hits": [hit.model_dump() for hit in hits],
            "retrieval_debug": {
                "mode": mode,
                "bm25_pool_size": pool_k if mode == "hybrid" else top_k,
                "vector_pool_size": pool_k if mode == "hybrid" else top_k,
                "candidate_chunk_count": len(chunks),
                "bm25_hit_count": len(bm25_hits),
                "vector_hit_count": len(vector_hits),
                "merged_hit_count": len({hit.chunk.chunk_id for hit in [*bm25_hits, *vector_hits]}),
                "returned_hit_count": len(hits),
                "vector_available": vector_available,
            },
            "message": f"document_search executed with {source} {mode} index",
        },
        evidence=evidence,
        warnings=warnings,
        metrics=ToolMetrics(execution_time_ms=0),
    )
