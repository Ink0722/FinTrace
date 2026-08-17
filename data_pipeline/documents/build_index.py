from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sqlite3
import time
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from data_pipeline.documents.batch_embedding_client import (
    TERMINAL_BATCH_STATUSES,
    DashScopeBatchClient,
)
from data_pipeline.documents.corpus_store import (
    build_corpus_store,
    load_vector_ids,
    validate_corpus_store,
)
from data_pipeline.documents.embedding_text import (
    EMBEDDING_TEXT_VERSION,
    EmbeddingRecord,
    estimate_embedding_corpus,
    iter_embedding_records,
    load_document_metadata,
)
from data_pipeline.documents.index_artifacts import (
    build_faiss_index,
    close_memmap,
    normalize_embedding_file,
    write_json_atomic,
)


DEFAULT_DOCUMENTS_PATH = Path("data/processed/documents/documents.jsonl")
DEFAULT_CHUNKS_PATH = Path("data/processed/documents/chunks_v2.jsonl")
DEFAULT_CHUNK_MANIFEST_PATH = Path("data/processed/documents/chunk_manifest_v2.json")
DEFAULT_OUTPUT_DIR = Path("data/indexes/document_search")
BATCH_BUILD_DIRNAME = ".batch_build"
REQUEST_SIZE = 10
MAX_REQUESTS_PER_BATCH_FILE = 50_000
MAX_BATCH_FILE_BYTES = 500 * 1024 * 1024
FINAL_ARTIFACTS = (
    "fintrace_kb.sqlite",
    "embeddings.npy",
    "vector.faiss",
    "vector_ids.json",
    "build_progress.json",
    "batch_jobs.json",
    "embedding_failures.jsonl",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the FinTrace document index with DashScope Batch File."
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=("prepare", "submit", "status", "collect", "retry", "finalize", "run"),
    )
    parser.add_argument("--documents", type=Path, default=DEFAULT_DOCUMENTS_PATH)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS_PATH)
    parser.add_argument("--chunk-manifest", type=Path, default=DEFAULT_CHUNK_MANIFEST_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--chunks-per-shard",
        type=int,
        default=int(os.getenv("EMBEDDING_BATCH_CHUNKS_PER_SHARD", "20000")),
    )
    parser.add_argument(
        "--completion-window",
        default=os.getenv("EMBEDDING_BATCH_COMPLETION_WINDOW", "24h"),
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=float(os.getenv("EMBEDDING_BATCH_POLL_SECONDS", "30")),
    )
    parser.add_argument(
        "--shard-id",
        dest="shard_ids",
        action="append",
        help="Submit only this prepared shard, for example shard-0000. Repeat to select several.",
    )
    parser.add_argument(
        "--estimate-only",
        action="store_true",
        help="Estimate input size without creating files or calling DashScope.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Discard an incompatible local Batch checkpoint when preparing.",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Finalize while explicitly excluding request-level failures with complete mappings.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.estimate_only:
        if args.action:
            parser.error("--estimate-only cannot be combined with an action")
        print(json.dumps(build_estimate(args), ensure_ascii=False, indent=2))
        return 0
    if not args.action:
        parser.error("an action is required unless --estimate-only is used")
    if args.shard_ids and args.action != "submit":
        parser.error("--shard-id is only valid with the submit action")
    if args.allow_partial and args.action != "finalize":
        parser.error("--allow-partial is only valid with the finalize action")

    actions = {
        "prepare": prepare_build,
        "submit": submit_jobs,
        "status": refresh_status,
        "collect": collect_results,
        "retry": prepare_retries,
        "finalize": finalize_index,
        "run": run_build,
    }
    result = actions[args.action](args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_estimate(args: argparse.Namespace) -> dict[str, Any]:
    inputs = validate_inputs(args.documents, args.chunks, args.chunk_manifest)
    documents = load_document_metadata(args.documents)
    estimate = estimate_embedding_corpus(args.chunks, documents)
    chunk_count = int(estimate["chunk_count"])
    estimate.update(
        {
            "documents_path": display_path(args.documents),
            "chunks_path": display_path(args.chunks),
            "chunk_manifest_path": display_path(args.chunk_manifest),
            "input_hashes": inputs["hashes"],
            "planned_build": {
                "mode": "dashscope_batch_file",
                "model": embedding_model(),
                "dimension": embedding_dimension(),
                "texts_per_request": REQUEST_SIZE,
                "chunks_per_shard": args.chunks_per_shard,
                "estimated_batch_jobs": math.ceil(chunk_count / args.chunks_per_shard),
                "estimated_batch_requests": math.ceil(chunk_count / REQUEST_SIZE),
                "embedding_matrix_bytes": chunk_count * embedding_dimension() * 4,
                "embedding_and_faiss_bytes_approx": chunk_count
                * embedding_dimension()
                * 8,
            },
        }
    )
    return estimate


def prepare_build(args: argparse.Namespace) -> dict[str, Any]:
    if args.chunks_per_shard < REQUEST_SIZE:
        raise ValueError(f"--chunks-per-shard must be at least {REQUEST_SIZE}.")
    estimate = build_estimate(args)
    inputs = validate_inputs(args.documents, args.chunks, args.chunk_manifest)
    documents = load_document_metadata(args.documents)
    total_chunks = int(estimate["chunk_count"])
    if total_chunks <= 0:
        raise RuntimeError("Cannot build an index from an empty Chunk corpus.")

    output_dir = args.output_dir.resolve()
    build_dir = output_dir / BATCH_BUILD_DIRNAME
    fingerprint = build_fingerprint(inputs)
    final_manifest = output_dir / "manifest.json"
    if not args.force and completed_index_matches(final_manifest, fingerprint, total_chunks):
        return build_summary("already_complete", output_dir, total_chunks, [])
    if final_manifest.exists() and not args.force:
        raise RuntimeError(
            "A completed index exists with different inputs or embedding settings. "
            "Use --force only after confirming it should be replaced."
        )

    state_path = build_dir / "state.json"
    if state_path.exists() and not args.force:
        state = read_json(state_path)
        if state.get("fingerprint") != fingerprint:
            raise RuntimeError(
                "The local Batch checkpoint uses different inputs or embedding settings. "
                "Use prepare --force to discard it."
            )
        return state_summary(state)
    if build_dir.exists() and args.force:
        shutil.rmtree(build_dir)
    for name in ("requests", "mappings", "results", "errors"):
        (build_dir / name).mkdir(parents=True, exist_ok=True)

    sqlite_path = build_dir / "fintrace_kb.sqlite"
    inserted = build_corpus_store(
        sqlite_path,
        documents,
        iter_embedding_records(args.chunks, documents),
    )
    if inserted != total_chunks:
        raise RuntimeError(f"SQLite import count mismatch: expected {total_chunks}, got {inserted}.")
    validate_corpus_store(sqlite_path, total_chunks)

    shards = write_request_shards(
        iter_embedding_records(args.chunks, documents),
        build_dir,
        chunks_per_shard=args.chunks_per_shard,
    )
    state = {
        "schema_version": "batch-index-state-v1",
        "status": "prepared",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "documents_path": display_path(args.documents),
        "chunks_path": display_path(args.chunks),
        "chunk_manifest_path": display_path(args.chunk_manifest),
        "output_dir": display_path(output_dir),
        "document_count": len(documents),
        "chunk_count": total_chunks,
        "request_count": sum(int(shard["request_count"]) for shard in shards),
        "fingerprint": fingerprint,
        "estimate": estimate,
        "shards": shards,
    }
    write_json_atomic(state_path, state)
    return state_summary(state)


def write_request_shards(
    records: Iterable[EmbeddingRecord],
    build_dir: Path,
    *,
    chunks_per_shard: int,
) -> list[dict[str, Any]]:
    shards: list[dict[str, Any]] = []
    current: list[EmbeddingRecord] = []
    for record in records:
        if current and len(current) >= chunks_per_shard:
            shards.append(write_request_shard(current, build_dir, len(shards)))
            current = []
        current.append(record)
    if current:
        shards.append(write_request_shard(current, build_dir, len(shards)))
    return shards


def write_request_shard(
    records: list[EmbeddingRecord], build_dir: Path, shard_index: int
) -> dict[str, Any]:
    shard_id = f"shard-{shard_index:04d}"
    request_path = build_dir / "requests" / f"{shard_id}.jsonl"
    mapping_path = build_dir / "mappings" / f"{shard_id}.jsonl"
    request_tmp = request_path.with_suffix(".jsonl.tmp")
    mapping_tmp = mapping_path.with_suffix(".jsonl.tmp")
    request_count = 0
    with request_tmp.open("w", encoding="utf-8", newline="\n") as request_handle, mapping_tmp.open(
        "w", encoding="utf-8", newline="\n"
    ) as mapping_handle:
        for start in range(0, len(records), REQUEST_SIZE):
            group = records[start : start + REQUEST_SIZE]
            custom_id = f"{shard_id}-request-{request_count:05d}"
            request = {
                "custom_id": custom_id,
                "method": "POST",
                "url": "/v1/embeddings",
                "body": {
                    "model": embedding_model(),
                    "input": [record.embedding_text for record in group],
                    "dimensions": embedding_dimension(),
                    "encoding_format": "float",
                },
            }
            mapping = {
                "custom_id": custom_id,
                "vector_rows": [record.vector_row for record in group],
                "chunk_ids": [record.chunk_id for record in group],
            }
            request_handle.write(compact_json(request) + "\n")
            mapping_handle.write(compact_json(mapping) + "\n")
            request_count += 1
    os.replace(request_tmp, request_path)
    os.replace(mapping_tmp, mapping_path)
    if request_count > MAX_REQUESTS_PER_BATCH_FILE:
        raise RuntimeError(
            f"{request_path} contains {request_count} requests; the Batch File limit is "
            f"{MAX_REQUESTS_PER_BATCH_FILE}. Reduce --chunks-per-shard."
        )
    if request_path.stat().st_size > MAX_BATCH_FILE_BYTES:
        raise RuntimeError(
            f"{request_path} exceeds the 500 MB Batch File limit. "
            "Reduce --chunks-per-shard."
        )
    return new_shard_record(
        shard_id,
        request_path,
        mapping_path,
        chunk_count=len(records),
        request_count=request_count,
    )


def new_shard_record(
    shard_id: str,
    request_path: Path,
    mapping_path: Path,
    *,
    chunk_count: int,
    request_count: int,
    source_shard: str | None = None,
) -> dict[str, Any]:
    return {
        "shard_id": shard_id,
        "source_shard": source_shard,
        "request_path": display_path(request_path),
        "mapping_path": display_path(mapping_path),
        "chunk_count": chunk_count,
        "request_count": request_count,
        "status": "prepared",
        "input_file_id": None,
        "batch_id": None,
        "output_file_id": None,
        "error_file_id": None,
        "result_path": None,
        "error_path": None,
        "retry_prepared": False,
    }


def submit_jobs(
    args: argparse.Namespace, client: DashScopeBatchClient | None = None
) -> dict[str, Any]:
    state, state_path = load_state(args.output_dir)
    client = client or DashScopeBatchClient()
    selected_shards = select_shards(state["shards"], args.shard_ids)
    submitted_ids: list[str] = []
    for shard in selected_shards:
        if shard.get("batch_id"):
            continue
        request_path = resolve_state_path(shard["request_path"])
        if not shard.get("input_file_id"):
            shard["input_file_id"] = client.upload_file(request_path)
            shard["status"] = "uploaded"
            touch_state(state, state_path)
        payload = client.create_batch(
            shard["input_file_id"],
            completion_window=args.completion_window,
            name=f"FinTrace-{shard['shard_id']}",
        )
        batch_id = payload.get("id")
        if not isinstance(batch_id, str) or not batch_id:
            raise RuntimeError(f"Batch response for {shard['shard_id']} has no job id.")
        shard["batch_id"] = batch_id
        shard["status"] = str(payload.get("status") or "submitted")
        copy_batch_fields(shard, payload)
        submitted_ids.append(shard["shard_id"])
        touch_state(state, state_path)
    state["status"] = aggregate_status(state["shards"])
    touch_state(state, state_path)
    return {
        **state_summary(state),
        "selected_shards": [shard["shard_id"] for shard in selected_shards],
        "submitted_shards": submitted_ids,
    }


def select_shards(
    shards: list[dict[str, Any]], shard_ids: list[str] | None
) -> list[dict[str, Any]]:
    if not shard_ids:
        return shards
    requested = list(dict.fromkeys(shard_ids))
    by_id = {str(shard["shard_id"]): shard for shard in shards}
    unknown = [shard_id for shard_id in requested if shard_id not in by_id]
    if unknown:
        available = ", ".join(sorted(by_id))
        raise ValueError(
            f"Unknown shard id(s): {', '.join(unknown)}. Available shards: {available}."
        )
    return [by_id[shard_id] for shard_id in requested]


def refresh_status(
    args: argparse.Namespace, client: DashScopeBatchClient | None = None
) -> dict[str, Any]:
    state, state_path = load_state(args.output_dir)
    client = client or DashScopeBatchClient()
    for shard in state["shards"]:
        batch_id = shard.get("batch_id")
        if not batch_id:
            continue
        payload = client.get_batch(batch_id)
        shard["status"] = str(payload.get("status") or shard.get("status") or "unknown")
        copy_batch_fields(shard, payload)
    state["status"] = aggregate_status(state["shards"])
    touch_state(state, state_path)
    return state_summary(state)


def collect_results(
    args: argparse.Namespace, client: DashScopeBatchClient | None = None
) -> dict[str, Any]:
    state, state_path = load_state(args.output_dir)
    client = client or DashScopeBatchClient()
    for shard in state["shards"]:
        batch_id = shard.get("batch_id")
        if batch_id and shard.get("status") not in TERMINAL_BATCH_STATUSES:
            payload = client.get_batch(batch_id)
            shard["status"] = str(payload.get("status") or shard.get("status"))
            copy_batch_fields(shard, payload)
        if shard.get("output_file_id") and not shard.get("result_path"):
            destination = build_path(args.output_dir, "results", f"{shard['shard_id']}.jsonl")
            client.download_file(shard["output_file_id"], destination)
            shard["result_path"] = display_path(destination)
            touch_state(state, state_path)
        if shard.get("error_file_id") and not shard.get("error_path"):
            destination = build_path(args.output_dir, "errors", f"{shard['shard_id']}.jsonl")
            client.download_file(shard["error_file_id"], destination)
            shard["error_path"] = display_path(destination)
            touch_state(state, state_path)
    state["status"] = aggregate_status(state["shards"])
    touch_state(state, state_path)
    return state_summary(state)


def prepare_retries(args: argparse.Namespace) -> dict[str, Any]:
    state, state_path = load_state(args.output_dir)
    retry_shards: list[dict[str, Any]] = []
    for shard in list(state["shards"]):
        if shard.get("retry_prepared"):
            continue
        failed_ids = read_error_custom_ids(shard.get("error_path"))
        if not failed_ids:
            if shard.get("status") in {"failed", "expired", "cancelled"}:
                if shard.get("result_path"):
                    raise RuntimeError(
                        f"{shard['shard_id']} has partial output but no request-level error ids; "
                        "automatic retry would risk duplicate vectors."
                    )
                failed_ids = set(read_jsonl_by_id(resolve_state_path(shard["request_path"])))
            else:
                continue
        retry_shard = create_retry_shard(shard, failed_ids, args.output_dir, state)
        state["shards"].append(retry_shard)
        retry_shards.append(retry_shard)
        shard["retry_prepared"] = True
    if not retry_shards:
        return {**state_summary(state), "message": "No unresolved Batch requests need retry."}
    state["status"] = "retry_prepared"
    touch_state(state, state_path)
    return state_summary(state)


def create_retry_shard(
    source: dict[str, Any],
    custom_ids: set[str],
    output_dir: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    retry_index = sum(1 for item in state["shards"] if str(item["shard_id"]).startswith("retry-"))
    shard_id = f"retry-{retry_index:04d}"
    requests = read_jsonl_by_id(resolve_state_path(source["request_path"]))
    mappings = read_jsonl_by_id(resolve_state_path(source["mapping_path"]))
    unknown = custom_ids - requests.keys()
    if unknown:
        raise RuntimeError(f"Retry errors reference unknown custom_ids: {sorted(unknown)[:3]}")
    request_path = build_path(output_dir, "requests", f"{shard_id}.jsonl")
    mapping_path = build_path(output_dir, "mappings", f"{shard_id}.jsonl")
    write_jsonl(request_path, (requests[item] for item in sorted(custom_ids)))
    write_jsonl(mapping_path, (mappings[item] for item in sorted(custom_ids)))
    chunk_count = sum(len(mappings[item]["vector_rows"]) for item in custom_ids)
    return new_shard_record(
        shard_id,
        request_path,
        mapping_path,
        chunk_count=chunk_count,
        request_count=len(custom_ids),
        source_shard=source["shard_id"],
    )


def finalize_index(args: argparse.Namespace) -> dict[str, Any]:
    state, state_path = load_state(args.output_dir)
    inputs = validate_inputs(args.documents, args.chunks, args.chunk_manifest)
    if state.get("fingerprint") != build_fingerprint(inputs):
        raise RuntimeError("Current inputs do not match the prepared Batch build.")
    total_chunks = int(state["chunk_count"])
    dimension = int(state["fingerprint"]["dimension"])
    mappings = load_all_mappings(state["shards"])
    original_request_count = int(state["request_count"])
    if len(mappings) != original_request_count:
        raise RuntimeError(
            f"Request mapping count mismatch: expected {original_request_count}, got {len(mappings)}."
        )

    final_dir = build_path(args.output_dir, "final")
    final_dir.mkdir(parents=True, exist_ok=True)
    raw_embeddings_path = final_dir / "embeddings.raw.npy"
    embeddings_path = final_dir / "embeddings.npy"
    embeddings = np.lib.format.open_memmap(
        raw_embeddings_path,
        mode="w+",
        dtype="float32",
        shape=(total_chunks, dimension),
    )
    embeddings[:] = np.nan
    filled = np.zeros(total_chunks, dtype=bool)
    successful_requests: set[str] = set()
    actual_tokens = 0
    try:
        for shard in state["shards"]:
            if not shard.get("result_path"):
                continue
            result_path = resolve_state_path(shard["result_path"])
            for line_number, result in iter_jsonl(result_path):
                custom_id = result.get("custom_id")
                if not isinstance(custom_id, str) or custom_id not in mappings:
                    raise RuntimeError(
                        f"Unknown custom_id in {result_path}:{line_number}: {custom_id!r}."
                    )
                response = result.get("response")
                if not isinstance(response, dict) or int(response.get("status_code", 0)) != 200:
                    continue
                body = response.get("body")
                if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                    raise RuntimeError(
                        f"Embedding response has no data list in {result_path}:{line_number}."
                    )
                write_response_vectors(
                    embeddings,
                    filled,
                    mappings[custom_id],
                    body["data"],
                    dimension,
                    custom_id,
                )
                successful_requests.add(custom_id)
                actual_tokens += usage_tokens(body)
        embeddings.flush()
    finally:
        close_memmap(embeddings)

    error_records = load_batch_errors(state["shards"])
    unresolved_errors = set(error_records)
    for shard in state["shards"]:
        unresolved_errors.update(read_error_custom_ids(shard.get("error_path")))
    unresolved_errors -= successful_requests
    if unresolved_errors and not args.allow_partial:
        raise RuntimeError(
            f"{len(unresolved_errors)} Batch requests still failed. Run retry, submit, collect, then finalize."
        )
    missing_rows = np.flatnonzero(~filled)
    excluded_rows = sorted(
        {
            int(row)
            for custom_id in unresolved_errors
            for row in mappings[custom_id]["vector_rows"]
        }
    )
    if set(int(row) for row in missing_rows) != set(excluded_rows):
        raise RuntimeError(
            "Batch results contain missing vectors that are not fully explained by recorded "
            f"request failures: missing={len(missing_rows)}, explained={len(excluded_rows)}."
        )
    if excluded_rows and not args.allow_partial:
        raise RuntimeError("Partial vector exclusion requires --allow-partial.")

    sqlite_source = build_path(args.output_dir, "fintrace_kb.sqlite")
    validate_corpus_store(sqlite_source, total_chunks)
    all_vector_ids = load_vector_ids(sqlite_source)
    included_rows = np.flatnonzero(filled)
    vector_count = len(included_rows)
    if vector_count <= 0:
        raise RuntimeError("Cannot build a FAISS index without any successful vectors.")
    compact_embedding_file(raw_embeddings_path, embeddings_path, included_rows)
    raw_embeddings_path.unlink()
    normalize_embedding_file(embeddings_path)
    build_faiss_index(embeddings_path, final_dir / "vector.faiss", dimension)
    shutil.copy2(sqlite_source, final_dir / "fintrace_kb.sqlite")
    vector_ids = [all_vector_ids[int(row)] for row in included_rows]
    if len(vector_ids) != vector_count:
        raise RuntimeError("vector_ids count does not match successful vector count.")
    write_json_atomic(final_dir / "vector_ids.json", vector_ids)
    failure_records = build_embedding_failure_records(
        unresolved_errors,
        mappings,
        error_records,
        sqlite_source,
    )
    if len(failure_records) != len(excluded_rows):
        raise RuntimeError("Embedding failure artifact does not match excluded vector count.")
    write_jsonl(final_dir / "embedding_failures.jsonl", failure_records)

    completed_at = now_iso()
    partial_index = bool(excluded_rows)
    vector_coverage = vector_count / total_chunks
    progress = {
        "status": "complete_partial" if partial_index else "complete",
        "completed_rows": vector_count,
        "total_rows": total_chunks,
        "excluded_rows": len(excluded_rows),
        "vector_coverage": vector_coverage,
        "actual_api_tokens": actual_tokens or None,
        "updated_at": completed_at,
        "fingerprint": state["fingerprint"],
    }
    write_json_atomic(final_dir / "build_progress.json", progress)
    write_json_atomic(
        final_dir / "batch_jobs.json",
        {
            "created_at": state["created_at"],
            "completed_at": completed_at,
            "jobs": [public_job_record(shard) for shard in state["shards"]],
        },
    )
    manifest = {
        "status": "complete",
        "created_at": completed_at,
        "documents_path": state["documents_path"],
        "chunks_path": state["chunks_path"],
        "chunk_manifest_path": state["chunk_manifest_path"],
        "document_count": state["document_count"],
        "chunk_count": total_chunks,
        "vector_count": vector_count,
        "excluded_vector_count": len(excluded_rows),
        "vector_coverage": vector_coverage,
        "partial_index": partial_index,
        "embedding": {
            "provider": "dashscope_batch_file",
            "api_mode": "compatible",
            "model": embedding_model(),
            "dimension": dimension,
            "normalized": True,
            "similarity": "cosine_via_inner_product",
            "actual_api_tokens": actual_tokens or None,
        },
        "estimate": state["estimate"],
        "fingerprint": state["fingerprint"],
        "artifacts": {
            "sqlite": "fintrace_kb.sqlite",
            "embeddings": "embeddings.npy",
            "faiss": "vector.faiss",
            "vector_ids": "vector_ids.json",
            "batch_jobs": "batch_jobs.json",
            "embedding_failures": "embedding_failures.jsonl",
        },
    }
    write_json_atomic(final_dir / "manifest.json", manifest)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in FINAL_ARTIFACTS:
        os.replace(final_dir / name, output_dir / name)
    os.replace(final_dir / "manifest.json", output_dir / "manifest.json")
    final_dir.rmdir()
    state["status"] = "complete_partial" if partial_index else "complete"
    state["actual_api_tokens"] = actual_tokens or None
    state["vector_count"] = vector_count
    state["excluded_vector_count"] = len(excluded_rows)
    state["updated_at"] = completed_at
    write_json_atomic(state_path, state)
    return {
        **build_summary(
            "complete_partial" if partial_index else "complete",
            output_dir,
            total_chunks,
            state["shards"],
            actual_tokens,
        ),
        "vector_count": vector_count,
        "excluded_vector_count": len(excluded_rows),
        "vector_coverage": vector_coverage,
    }


def write_response_vectors(
    embeddings: np.ndarray,
    filled: np.ndarray,
    mapping: dict[str, Any],
    data: list[Any],
    dimension: int,
    custom_id: str,
) -> None:
    rows = mapping["vector_rows"]
    if len(data) != len(rows):
        raise RuntimeError(
            f"{custom_id} returned {len(data)} vectors for {len(rows)} input texts."
        )
    seen_indexes: set[int] = set()
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("index"), int):
            raise RuntimeError(f"{custom_id} contains an invalid embedding index.")
        index = item["index"]
        if index < 0 or index >= len(rows) or index in seen_indexes:
            raise RuntimeError(f"{custom_id} contains duplicate or out-of-range index {index}.")
        vector = np.asarray(item.get("embedding"), dtype="float32")
        if vector.shape != (dimension,) or not np.isfinite(vector).all():
            raise RuntimeError(f"{custom_id}[{index}] is not a finite {dimension}-dimensional vector.")
        vector_row = int(rows[index])
        if vector_row < 0 or vector_row >= len(filled) or filled[vector_row]:
            raise RuntimeError(f"Duplicate or invalid vector row {vector_row} from {custom_id}.")
        embeddings[vector_row] = vector
        filled[vector_row] = True
        seen_indexes.add(index)


def compact_embedding_file(
    source_path: Path,
    destination_path: Path,
    included_rows: np.ndarray,
    *,
    rows_per_batch: int = 10_000,
) -> None:
    source = np.load(source_path, mmap_mode="r")
    destination = np.lib.format.open_memmap(
        destination_path,
        mode="w+",
        dtype="float32",
        shape=(len(included_rows), source.shape[1]),
    )
    for start in range(0, len(included_rows), rows_per_batch):
        rows = included_rows[start : start + rows_per_batch]
        destination[start : start + len(rows)] = source[rows]
    destination.flush()
    close_memmap(destination)
    close_memmap(source)


def load_batch_errors(shards: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    errors: dict[str, dict[str, Any]] = {}
    for shard in shards:
        value = shard.get("error_path")
        if not value:
            continue
        path = resolve_state_path(value)
        if not path.is_file():
            continue
        for line_number, item in iter_jsonl(path):
            custom_id = item.get("custom_id")
            if not isinstance(custom_id, str):
                raise RuntimeError(f"Batch error has no custom_id in {path}:{line_number}.")
            error = item.get("error") or {}
            record = {
                "custom_id": custom_id,
                "source_shard": shard["shard_id"],
                "error_code": str(error.get("code") or "unknown"),
                "error_message": str(error.get("message") or error or "unknown error"),
            }
            previous = errors.get(custom_id)
            if previous is not None and previous != record:
                raise RuntimeError(f"Conflicting Batch errors for custom_id {custom_id}.")
            errors[custom_id] = record
    return errors


def build_embedding_failure_records(
    unresolved_errors: set[str],
    mappings: dict[str, dict[str, Any]],
    errors: dict[str, dict[str, Any]],
    sqlite_path: Path,
) -> list[dict[str, Any]]:
    unknown = unresolved_errors - mappings.keys()
    if unknown:
        raise RuntimeError(f"Batch errors reference unknown mappings: {sorted(unknown)[:3]}")
    chunk_ids = [
        chunk_id
        for custom_id in unresolved_errors
        for chunk_id in mappings[custom_id]["chunk_ids"]
    ]
    metadata: dict[str, tuple[str, str, str]] = {}
    with sqlite3.connect(sqlite_path) as conn:
        for start in range(0, len(chunk_ids), 500):
            batch = chunk_ids[start : start + 500]
            placeholders = ",".join("?" for _ in batch)
            rows = conn.execute(
                f"SELECT chunk_id, doc_id, company_id, title FROM chunks "
                f"WHERE chunk_id IN ({placeholders})",
                batch,
            ).fetchall()
            metadata.update(
                {
                    str(chunk_id): (str(doc_id), str(company_id), str(title))
                    for chunk_id, doc_id, company_id, title in rows
                }
            )
    if len(metadata) != len(set(chunk_ids)):
        raise RuntimeError("Some excluded Chunk metadata is missing from SQLite.")

    records: list[dict[str, Any]] = []
    for custom_id in unresolved_errors:
        mapping = mappings[custom_id]
        error = errors.get(custom_id)
        if error is None:
            raise RuntimeError(f"Missing error details for excluded request {custom_id}.")
        for vector_row, chunk_id in zip(mapping["vector_rows"], mapping["chunk_ids"]):
            doc_id, company_id, title = metadata[chunk_id]
            records.append(
                {
                    "chunk_id": chunk_id,
                    "document_id": doc_id,
                    "company_id": company_id,
                    "title": title,
                    "vector_row_original": int(vector_row),
                    "custom_id": custom_id,
                    "source_shard": error["source_shard"],
                    "error_code": error["error_code"],
                    "error_message": error["error_message"],
                }
            )
    records.sort(key=lambda item: item["vector_row_original"])
    return records


def load_all_mappings(shards: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mappings: dict[str, dict[str, Any]] = {}
    for shard in shards:
        for _, mapping in iter_jsonl(resolve_state_path(shard["mapping_path"])):
            custom_id = mapping.get("custom_id")
            if not isinstance(custom_id, str):
                raise RuntimeError(f"Mapping in {shard['mapping_path']} has no custom_id.")
            previous = mappings.get(custom_id)
            if previous is not None and previous != mapping:
                raise RuntimeError(f"Conflicting mappings for custom_id {custom_id}.")
            mappings[custom_id] = mapping
    return mappings


def run_build(args: argparse.Namespace) -> dict[str, Any]:
    prepared = prepare_build(args)
    if prepared["status"] == "already_complete":
        return prepared
    submit_jobs(args)
    while True:
        status = refresh_status(args)
        if status["all_terminal"]:
            break
        time.sleep(max(1.0, args.poll_seconds))
    collected = collect_results(args)
    if collected["failed_jobs"]:
        return {**collected, "next_action": "Run retry, then submit/status/collect/finalize."}
    return finalize_index(args)


def validate_inputs(documents_path: Path, chunks_path: Path, manifest_path: Path) -> dict[str, Any]:
    for path in (documents_path, chunks_path, manifest_path):
        if not path.is_file():
            raise FileNotFoundError(f"Required input does not exist: {path}")
    manifest = read_json(manifest_path)
    if manifest.get("chunk_version") != "chunks-v2":
        raise RuntimeError(f"Expected chunks-v2 manifest, got {manifest.get('chunk_version')!r}.")
    documents_sha256 = sha256_file(documents_path)
    chunks_sha256 = sha256_file(chunks_path)
    if manifest.get("source_documents_sha256") != documents_sha256:
        raise RuntimeError("documents.jsonl SHA-256 does not match chunk_manifest_v2.json.")
    if manifest.get("chunks_sha256") != chunks_sha256:
        raise RuntimeError("chunks_v2.jsonl SHA-256 does not match chunk_manifest_v2.json.")
    return {
        "manifest": manifest,
        "hashes": {
            "documents_sha256": documents_sha256,
            "chunks_sha256": chunks_sha256,
            "chunk_manifest_sha256": sha256_file(manifest_path),
        },
    }


def build_fingerprint(inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        **inputs["hashes"],
        "chunk_version": inputs["manifest"]["chunk_version"],
        "embedding_text_version": EMBEDDING_TEXT_VERSION,
        "provider": "dashscope_batch_file",
        "model": embedding_model(),
        "dimension": embedding_dimension(),
        "api_mode": "compatible",
        "request_size": REQUEST_SIZE,
    }


def completed_index_matches(path: Path, fingerprint: dict[str, Any], total_chunks: int) -> bool:
    if not path.is_file():
        return False
    try:
        manifest = read_json(path)
    except (OSError, json.JSONDecodeError):
        return False
    return (
        manifest.get("status") == "complete"
        and manifest.get("fingerprint") == fingerprint
        and manifest.get("chunk_count") == total_chunks
        and all((path.parent / name).exists() for name in FINAL_ARTIFACTS)
    )


def copy_batch_fields(shard: dict[str, Any], payload: dict[str, Any]) -> None:
    for key in ("output_file_id", "error_file_id"):
        if payload.get(key):
            shard[key] = payload[key]
    if isinstance(payload.get("request_counts"), dict):
        shard["request_counts"] = payload["request_counts"]


def aggregate_status(shards: list[dict[str, Any]]) -> str:
    statuses = [str(shard.get("status") or "prepared") for shard in shards]
    if statuses and all(status == "completed" for status in statuses):
        return "batch_completed"
    if any(status in {"failed", "expired", "cancelled"} for status in statuses):
        return "batch_attention_required"
    if any(status not in TERMINAL_BATCH_STATUSES for status in statuses):
        return "batch_in_progress"
    return "batch_terminal"


def state_summary(state: dict[str, Any]) -> dict[str, Any]:
    shards = state.get("shards") or []
    statuses: dict[str, int] = {}
    for shard in shards:
        status = str(shard.get("status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "status": state.get("status"),
        "chunk_count": state.get("chunk_count"),
        "request_count": state.get("request_count"),
        "job_count": len(shards),
        "job_statuses": statuses,
        "failed_jobs": sum(
            statuses.get(item, 0) for item in ("failed", "expired", "cancelled")
        ),
        "all_terminal": bool(shards)
        and all(str(shard.get("status")) in TERMINAL_BATCH_STATUSES for shard in shards),
    }


def build_summary(
    status: str,
    output_dir: Path,
    chunk_count: int,
    shards: list[dict[str, Any]],
    actual_tokens: int | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "output_dir": display_path(output_dir),
        "chunk_count": chunk_count,
        "job_count": len(shards),
        "model": embedding_model(),
        "dimension": embedding_dimension(),
        "actual_api_tokens": actual_tokens or None,
    }


def public_job_record(shard: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "shard_id",
        "source_shard",
        "chunk_count",
        "request_count",
        "status",
        "input_file_id",
        "batch_id",
        "output_file_id",
        "error_file_id",
        "request_counts",
    )
    return {key: shard.get(key) for key in keys if shard.get(key) is not None}


def load_state(output_dir: Path) -> tuple[dict[str, Any], Path]:
    state_path = build_path(output_dir, "state.json")
    if not state_path.is_file():
        raise RuntimeError("No prepared Batch build exists. Run the prepare action first.")
    state = read_json(state_path)
    if state.get("schema_version") != "batch-index-state-v1":
        raise RuntimeError(f"Unsupported Batch state version: {state.get('schema_version')!r}.")
    return state, state_path


def touch_state(state: dict[str, Any], path: Path) -> None:
    state["updated_at"] = now_iso()
    write_json_atomic(path, state)


def build_path(output_dir: Path, *parts: str) -> Path:
    return output_dir.resolve() / BATCH_BUILD_DIRNAME / Path(*parts)


def resolve_state_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (Path.cwd() / path).resolve()


def read_error_custom_ids(value: str | None) -> set[str]:
    if not value:
        return set()
    path = resolve_state_path(value)
    if not path.is_file() or path.stat().st_size == 0:
        return set()
    result: set[str] = set()
    for _, item in iter_jsonl(path):
        custom_id = item.get("custom_id")
        if isinstance(custom_id, str):
            result.add(custom_id)
    return result


def read_jsonl_by_id(path: Path) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for line_number, value in iter_jsonl(path):
        custom_id = value.get("custom_id")
        if not isinstance(custom_id, str) or custom_id in values:
            raise RuntimeError(f"Invalid or duplicate custom_id in {path}:{line_number}.")
        values[custom_id] = value
    return values


def iter_jsonl(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid JSON in {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise RuntimeError(f"Expected a JSON object in {path}:{line_number}.")
            yield line_number, value


def write_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(compact_json(value) + "\n")
    os.replace(temporary, path)


def usage_tokens(body: dict[str, Any]) -> int:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return 0
    value = usage.get("prompt_tokens", usage.get("total_tokens", 0))
    return int(value) if isinstance(value, (int, float)) else 0


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected a JSON object in {path}.")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def embedding_model() -> str:
    return os.getenv("DASHSCOPE_EMBEDDING_MODEL", "text-embedding-v4")


def embedding_dimension() -> int:
    dimension = int(os.getenv("DASHSCOPE_EMBEDDING_DIMENSION", "1024"))
    if dimension not in {64, 128, 256, 512, 768, 1024, 1536, 2048}:
        raise ValueError(f"Unsupported text-embedding-v4 dimension: {dimension}")
    return dimension


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
