from __future__ import annotations

import sqlite3
import time
from datetime import date
from typing import Literal

import requests
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from schemas.enums import ErrorType, ToolName, ToolStatus
from schemas.tool_calls import ToolCall
from schemas.tool_results import ToolError, ToolMetrics, ToolResult
from tools.document_search.config import DocumentSearchConfig
from tools.document_search.fts5_search import (
    LexicalSearchOutcome,
    bm25_index_available,
    fts5_search,
    validate_bm25_index_snapshot,
)
from tools.document_search.kb_loader import knowledge_base_available, load_filtered_chunk_ids
from tools.document_search.sample_data import load_sample_chunks
from tools.document_search.search import bm25_search, evidence_from_hits, filter_chunks
from tools.document_search.vector_search import (
    VectorSearchOutcome,
    limit_chunks_per_document,
    merge_hybrid_hits,
    vector_coverage_warning,
    vector_index_available,
    vector_search,
)


BM25_BUILD_COMMAND = "python -m data_pipeline.documents.build_bm25_index"


class DocumentSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    company_ids: list[str] = Field(default_factory=list, max_length=1)
    document_types: list[str] | None = None
    start_date: date | None = None
    end_date: date | None = None
    top_k: int = Field(default=8, ge=1)
    pool_k: int | None = Field(default=None, ge=1)
    mode: Literal["bm25", "vector", "hybrid"] = "hybrid"

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value

    @field_validator("company_ids")
    @classmethod
    def validate_company_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("company_ids must contain non-empty strings")
        return normalized

    @field_validator("document_types")
    @classmethod
    def validate_document_types(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        normalized = [value.strip() for value in values]
        if not normalized or any(not value for value in normalized):
            raise ValueError("document_types must contain non-empty strings")
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def validate_dates(self) -> "DocumentSearchArguments":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be later than end_date")
        return self


def document_search(call: ToolCall) -> ToolResult:
    started = time.perf_counter()
    try:
        config = DocumentSearchConfig.from_env()
        raw_arguments = dict(call.arguments)
        raw_arguments.setdefault("mode", config.default_mode)
        raw_arguments.setdefault("top_k", config.default_top_k)
        arguments = DocumentSearchArguments.model_validate(raw_arguments)
        pool_k = arguments.pool_k or max(arguments.top_k * 5, 50)
        if arguments.top_k > config.max_top_k:
            raise ValueError(f"top_k must be <= {config.max_top_k}")
        if pool_k < arguments.top_k:
            raise ValueError("pool_k must be greater than or equal to top_k")
        if pool_k > config.max_pool_k:
            raise ValueError(f"pool_k must be <= {config.max_pool_k}")
    except (ValidationError, TypeError, ValueError) as exc:
        return _error_result(
            call,
            started,
            ErrorType.INVALID_ARGUMENT,
            f"Invalid document_search arguments or configuration: {exc}",
            details={"arguments": call.arguments},
        )

    kb_path = config.kb_path
    kb_dir = kb_path.parent
    warnings: list[str] = []
    source = "knowledge_base"
    metadata_time_ms = 0
    lexical_time_ms = 0
    vector_outcome = VectorSearchOutcome(hits=[])
    lexical_outcome = LexicalSearchOutcome(hits=[])
    chunks: list = []

    if knowledge_base_available(kb_path):
        allowed_types = _kb_document_types(kb_path)
        invalid_types = sorted(set(arguments.document_types or []) - allowed_types)
        if allowed_types and invalid_types:
            return _error_result(
                call,
                started,
                ErrorType.INVALID_ARGUMENT,
                f"document_types must be one of {sorted(allowed_types)}, got {invalid_types}",
                details={"document_types": arguments.document_types},
            )
        if arguments.mode in {"bm25", "hybrid"}:
            if not bm25_index_available(config.bm25_index_path):
                return _error_result(
                    call,
                    started,
                    ErrorType.DATA_NOT_AVAILABLE,
                    f"BM25 FTS5 index not found: {config.bm25_index_path}",
                    details={
                        "bm25_index_path": str(config.bm25_index_path),
                        "build_command": BM25_BUILD_COMMAND,
                    },
                )
            index_errors = validate_bm25_index_snapshot(config.bm25_index_path, kb_path)
            if index_errors:
                return _error_result(
                    call,
                    started,
                    ErrorType.DATA_NOT_AVAILABLE,
                    "BM25 FTS5 index is stale or incomplete; rebuild it from the knowledge base.",
                    details={"errors": index_errors, "build_command": BM25_BUILD_COMMAND},
                )
        try:
            metadata_started = time.perf_counter()
            allowed_chunk_ids = None
            if _has_metadata_filter(arguments):
                allowed_chunk_ids = load_filtered_chunk_ids(
                    company_id=_company_id(arguments),
                    document_types=arguments.document_types,
                    start_date=arguments.start_date,
                    end_date=arguments.end_date,
                    kb_path=kb_path,
                )
            metadata_time_ms = _elapsed_ms(metadata_started)
        except (OSError, sqlite3.Error, ValueError) as exc:
            return _error_result(
                call,
                started,
                ErrorType.TEMPORARY_DATABASE_ERROR,
                f"Unable to read document knowledge base: {type(exc).__name__}: {exc}",
                retryable=isinstance(exc, sqlite3.OperationalError),
            )
    elif config.demo_mode:
        source = "sample"
        chunks = filter_chunks(
            load_sample_chunks(),
            company_id=_company_id(arguments),
            document_types=arguments.document_types,
            start_date=arguments.start_date,
            end_date=arguments.end_date,
        )
        allowed_chunk_ids = None
        warnings.append("Demo mode is enabled; results come from built-in sample chunks.")
    else:
        return _error_result(
            call,
            started,
            ErrorType.DATA_NOT_AVAILABLE,
            f"Document knowledge base not found: {kb_path}",
            details={"kb_path": str(kb_path), "demo_mode": False},
        )

    lexical_started = time.perf_counter()
    if arguments.mode not in {"bm25", "hybrid"}:
        bm25_hits = []
    elif source == "knowledge_base":
        try:
            lexical_outcome = fts5_search(
                query=arguments.query,
                index_path=config.bm25_index_path,
                kb_path=kb_path,
                top_k=pool_k,
                company_id=_company_id(arguments),
                document_types=arguments.document_types,
                start_date=arguments.start_date,
                end_date=arguments.end_date,
            )
        except (OSError, sqlite3.Error, ValueError) as exc:
            return _error_result(
                call,
                started,
                ErrorType.TEMPORARY_DATABASE_ERROR,
                f"BM25 FTS5 index query failed: {type(exc).__name__}: {exc}",
                retryable=isinstance(exc, sqlite3.OperationalError),
                metadata_time_ms=metadata_time_ms,
            )
        bm25_hits = lexical_outcome.hits
    else:
        bm25_hits = bm25_search(chunks, query=arguments.query, top_k=pool_k)
    lexical_time_ms = _elapsed_ms(lexical_started)

    vector_available = source == "knowledge_base" and vector_index_available(kb_dir)
    if arguments.mode in {"vector", "hybrid"}:
        if not vector_available:
            message = (
                "Vector index artifacts are unavailable. "
                "Run data_pipeline.documents.build_index Batch workflow."
            )
            if arguments.mode == "vector":
                return _error_result(
                    call,
                    started,
                    ErrorType.DATA_NOT_AVAILABLE,
                    message,
                    metadata_time_ms=metadata_time_ms,
                )
            warnings.append(f"{message} Falling back to BM25.")
        else:
            coverage_warning = vector_coverage_warning(kb_dir)
            if coverage_warning:
                warnings.append(coverage_warning)
            try:
                vector_outcome = vector_search(
                    query=arguments.query,
                    kb_dir=kb_dir,
                    kb_path=kb_path,
                    top_k=pool_k,
                    allowed_chunk_ids=allowed_chunk_ids,
                    exact_batch_size=config.exact_search_batch_size,
                )
            except Exception as exc:
                message = f"Vector search failed: {type(exc).__name__}: {exc}"
                if arguments.mode == "vector":
                    return _error_result(
                        call,
                        started,
                        ErrorType.EXTERNAL_SERVICE_ERROR
                        if _is_external_error(exc)
                        else ErrorType.DATA_NOT_AVAILABLE,
                        message,
                        retryable=_is_external_error(exc),
                        metadata_time_ms=metadata_time_ms,
                        lexical_time_ms=lexical_time_ms,
                    )
                warnings.append(f"{message}. Falling back to BM25.")

    rerank_started = time.perf_counter()
    if arguments.mode == "vector":
        hits = limit_chunks_per_document(
            vector_outcome.hits,
            top_k=arguments.top_k,
            max_chunks_per_document=config.max_chunks_per_document,
        )
    elif arguments.mode == "hybrid" and vector_outcome.hits:
        hits = merge_hybrid_hits(
            bm25_hits,
            vector_outcome.hits,
            top_k=arguments.top_k,
            rrf_k=config.rrf_k,
            max_chunks_per_document=config.max_chunks_per_document,
        )
    else:
        hits = limit_chunks_per_document(
            bm25_hits,
            top_k=arguments.top_k,
            max_chunks_per_document=config.max_chunks_per_document,
        )
    rerank_time_ms = _elapsed_ms(rerank_started)
    evidence = evidence_from_hits(hits, used_by=call.tool_call_id)
    return ToolResult(
        tool_call_id=call.tool_call_id,
        tool_name=ToolName.DOCUMENT_SEARCH,
        status=ToolStatus.SUCCESS,
        data={
            "company_ids": arguments.company_ids,
            "query": arguments.query,
            "top_k": arguments.top_k,
            "pool_k": pool_k,
            "mode": arguments.mode,
            "source": source,
            "hits": [hit.model_dump() for hit in hits],
            "retrieval_debug": {
                "mode": arguments.mode,
                "fusion": "rrf" if arguments.mode == "hybrid" and vector_outcome.hits else None,
                "candidate_chunk_count": (
                    lexical_outcome.candidate_count
                    if arguments.mode in {"bm25", "hybrid"} and source == "knowledge_base"
                    else len(chunks)
                    if arguments.mode in {"bm25", "hybrid"}
                    else vector_outcome.candidate_count
                ),
                "lexical_strategy": (
                    lexical_outcome.strategy if source == "knowledge_base" else "in_memory_sample"
                ),
                "bm25_hit_count": len(bm25_hits),
                "vector_hit_count": len(vector_outcome.hits),
                "vector_strategy": vector_outcome.strategy,
                "vector_candidate_count": vector_outcome.candidate_count,
                "returned_hit_count": len(hits),
                "vector_available": vector_available,
            },
            "message": f"document_search executed with {source} {arguments.mode} retrieval",
        },
        evidence=evidence,
        warnings=warnings,
        metrics=ToolMetrics(
            execution_time_ms=_elapsed_ms(started),
            metadata_time_ms=metadata_time_ms,
            lexical_search_time_ms=lexical_time_ms,
            embedding_time_ms=vector_outcome.embedding_time_ms,
            vector_search_time_ms=vector_outcome.search_time_ms,
            rerank_time_ms=rerank_time_ms,
        ),
    )


def _company_id(arguments: DocumentSearchArguments) -> str | None:
    return arguments.company_ids[0] if arguments.company_ids else None


def _kb_document_types(kb_path) -> set[str]:
    """The vocabulary is data-driven: upload KBs derive types from filenames."""
    import sqlite3 as _sqlite3

    try:
        with _sqlite3.connect(kb_path) as connection:
            rows = connection.execute("SELECT DISTINCT document_type FROM chunks").fetchall()
    except sqlite3.Error:
        return set()
    return {str(row[0]) for row in rows if row[0]}


def _has_metadata_filter(arguments: DocumentSearchArguments) -> bool:
    return bool(
        arguments.company_ids
        or arguments.document_types
        or arguments.start_date
        or arguments.end_date
    )


def _is_external_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, requests.RequestException):
            return True
        current = current.__cause__
    return False


def _error_result(
    call: ToolCall,
    started: float,
    error_type: ErrorType,
    message: str,
    *,
    retryable: bool = False,
    details: dict | None = None,
    metadata_time_ms: int = 0,
    lexical_time_ms: int = 0,
) -> ToolResult:
    return ToolResult(
        tool_call_id=call.tool_call_id,
        tool_name=ToolName.DOCUMENT_SEARCH,
        status=ToolStatus.FAILED,
        error=ToolError(
            error_type=error_type,
            message=message,
            retryable=retryable,
            details=details or {},
        ),
        metrics=ToolMetrics(
            execution_time_ms=_elapsed_ms(started),
            metadata_time_ms=metadata_time_ms,
            lexical_search_time_ms=lexical_time_ms,
        ),
    )


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))
