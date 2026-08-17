import json
import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

import faiss
import numpy as np
import pytest

from data_pipeline.documents.build_index import (
    BATCH_BUILD_DIRNAME,
    build_parser,
    finalize_index,
    prepare_build,
    select_shards,
    sha256_file,
    submit_jobs,
    validate_inputs,
)
from data_pipeline.documents.embedding_client import DashScopeEmbeddingClient
from data_pipeline.documents.embedding_text import (
    estimate_embedding_corpus,
    format_embedding_text,
    load_document_metadata,
)
from data_pipeline.documents.index_artifacts import write_json_atomic
from tools.document_search.vector_search import vector_coverage_warning


@pytest.fixture
def workspace_tmp_path() -> Path:
    path = Path("tests/test_artifacts") / f"document_embedding_{uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def write_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def build_tiny_corpus(root: Path) -> tuple[Path, Path, Path]:
    documents_path = root / "documents.jsonl"
    chunks_path = root / "chunks_v2.jsonl"
    manifest_path = root / "chunk_manifest_v2.json"
    write_jsonl(
        documents_path,
        [
            {
                "document_id": "ANN-1",
                "document_type": "announcement",
                "company_id": "000001.SZ",
                "title": "监管措施公告",
                "published_date": "2025-01-02",
                "tags": ["违纪违规"],
                "text": "公司收到警示函。",
                "source_ref": "data/source/announcements/1.txt",
            },
            {
                "document_id": "RR-1",
                "document_type": "research_report",
                "company_id": "000002.SZ",
                "title": "公司盈利能力点评",
                "published_date": "2025-02-03",
                "publisher": "测试证券",
                "tags": ["业绩点评"],
                "text": "公司盈利能力改善。",
                "source_ref": "data/normalized/research_reports.jsonl#1",
            },
        ],
    )
    write_jsonl(
        chunks_path,
        [
            {
                "chunk_version": "chunks-v2",
                "chunk_id": "ANN-1-C0001",
                "document_id": "ANN-1",
                "chunk_index": 1,
                "section_title": "一、监管措施",
                "char_start": 0,
                "text": "公司收到警示函。",
            },
            {
                "chunk_version": "chunks-v2",
                "chunk_id": "RR-1-C0001",
                "document_id": "RR-1",
                "chunk_index": 1,
                "section_title": None,
                "char_start": 0,
                "text": "公司盈利能力改善。",
            },
        ],
    )
    manifest = {
        "chunk_version": "chunks-v2",
        "source_documents_sha256": sha256_file(documents_path),
        "chunks_sha256": sha256_file(chunks_path),
        "total_documents": 2,
        "total_chunks": 2,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return documents_path, chunks_path, manifest_path


def build_args(root: Path, monkeypatch: pytest.MonkeyPatch):
    documents, chunks, manifest = build_tiny_corpus(root / "corpus")
    output = root / "index"
    monkeypatch.setenv("DASHSCOPE_EMBEDDING_DIMENSION", "64")
    return build_parser().parse_args(
        [
            "prepare",
            "--documents",
            str(documents),
            "--chunks",
            str(chunks),
            "--chunk-manifest",
            str(manifest),
            "--output-dir",
            str(output),
            "--chunks-per-shard",
            "10",
            "--force",
        ]
    )


def build_partial_args(root: Path, monkeypatch: pytest.MonkeyPatch):
    corpus = root / "partial-corpus"
    documents_path = corpus / "documents.jsonl"
    chunks_path = corpus / "chunks_v2.jsonl"
    manifest_path = corpus / "chunk_manifest_v2.json"
    write_jsonl(
        documents_path,
        [
            {
                "document_id": "ANN-PARTIAL",
                "document_type": "announcement",
                "company_id": "000001.SZ",
                "title": "Partial index test",
                "published_date": "2025-01-02",
                "tags": [],
                "text": "test",
                "source_ref": "test.txt",
            }
        ],
    )
    write_jsonl(
        chunks_path,
        [
            {
                "chunk_version": "chunks-v2",
                "chunk_id": f"ANN-PARTIAL-C{index + 1:04d}",
                "document_id": "ANN-PARTIAL",
                "chunk_index": index + 1,
                "section_title": None,
                "char_start": index * 10,
                "text": f"partial chunk {index + 1}",
            }
            for index in range(11)
        ],
    )
    manifest_path.write_text(
        json.dumps(
            {
                "chunk_version": "chunks-v2",
                "source_documents_sha256": sha256_file(documents_path),
                "chunks_sha256": sha256_file(chunks_path),
                "total_documents": 1,
                "total_chunks": 11,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DASHSCOPE_EMBEDDING_DIMENSION", "64")
    args = build_parser().parse_args(
        [
            "finalize",
            "--documents",
            str(documents_path),
            "--chunks",
            str(chunks_path),
            "--chunk-manifest",
            str(manifest_path),
            "--output-dir",
            str(root / "partial-index"),
            "--chunks-per-shard",
            "20",
            "--allow-partial",
        ]
    )
    return args


def test_embedding_text_contains_search_metadata(workspace_tmp_path: Path) -> None:
    documents_path, chunks_path, manifest_path = build_tiny_corpus(workspace_tmp_path)
    documents = load_document_metadata(documents_path)
    text = format_embedding_text(documents["RR-1"], None, "公司盈利能力改善。")
    assert "文档类型：研报摘要" in text
    assert "证券代码：000002.SZ" in text
    assert "发布机构：测试证券" in text
    assert text.endswith("正文：\n公司盈利能力改善。")

    estimate = estimate_embedding_corpus(chunks_path, documents)
    assert estimate["chunk_count"] == 2
    assert estimate["embedding_text_chars"] > estimate["raw_chunk_text_chars"]
    assert estimate["estimated_tokens"]["low"] <= estimate["estimated_tokens"]["high"]
    assert validate_inputs(documents_path, chunks_path, manifest_path)["manifest"][
        "chunk_version"
    ] == "chunks-v2"


def test_query_client_requires_compatible_api() -> None:
    client = DashScopeEmbeddingClient(
        api_key="test",
        base_url="https://example.test/compatible-mode/v1",
        dimension=1024,
    )
    assert client.api_mode == "compatible"
    with pytest.raises(ValueError, match="OpenAI-compatible"):
        DashScopeEmbeddingClient(
            api_key="test",
            base_url="https://example.test/api/v1",
            dimension=1024,
        )


def test_prepare_writes_batch_embedding_request(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = build_args(workspace_tmp_path, monkeypatch)
    result = prepare_build(args)
    assert result["status"] == "prepared"
    assert result["request_count"] == 1

    build_dir = args.output_dir.resolve() / BATCH_BUILD_DIRNAME
    state = json.loads((build_dir / "state.json").read_text(encoding="utf-8"))
    request = json.loads(
        Path(state["shards"][0]["request_path"]).read_text(encoding="utf-8").strip()
    )
    assert request["url"] == "/v1/embeddings"
    assert request["body"]["model"] == "text-embedding-v4"
    assert request["body"]["dimensions"] == 64
    assert len(request["body"]["input"]) == 2


def test_finalize_restores_shuffled_vectors_and_builds_faiss(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = build_args(workspace_tmp_path, monkeypatch)
    prepare_build(args)
    build_dir = args.output_dir.resolve() / BATCH_BUILD_DIRNAME
    state_path = build_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    shard = state["shards"][0]
    custom_id = json.loads(Path(shard["mapping_path"]).read_text(encoding="utf-8"))["custom_id"]
    result_path = build_dir / "results" / "shard-0000.jsonl"
    write_jsonl(
        result_path,
        [
            {
                "custom_id": custom_id,
                "response": {
                    "status_code": 200,
                    "body": {
                        "data": [
                            {"index": 1, "embedding": axis_vector(1, 3.0)},
                            {"index": 0, "embedding": axis_vector(0, 2.0)},
                        ],
                        "usage": {"prompt_tokens": 17, "total_tokens": 17},
                    },
                },
                "error": None,
            }
        ],
    )
    shard.update(
        {
            "status": "completed",
            "result_path": result_path.resolve().as_posix(),
            "batch_id": "batch-test",
            "output_file_id": "file-output-test",
        }
    )
    state["status"] = "batch_completed"
    write_json_atomic(state_path, state)

    result = finalize_index(args)
    assert result["status"] == "complete"
    matrix = np.load(args.output_dir / "embeddings.npy", mmap_mode="r")
    np.testing.assert_allclose(matrix, np.eye(64, dtype="float32")[:2], atol=1e-6)
    assert faiss.read_index(str(args.output_dir / "vector.faiss")).ntotal == 2
    with sqlite3.connect(args.output_dir / "fintrace_kb.sqlite") as conn:
        rows = conn.execute(
            "SELECT chunk_id, company_id, vector_row FROM chunks ORDER BY vector_row"
        ).fetchall()
    assert rows == [
        ("ANN-1-C0001", "000001.SZ", 0),
        ("RR-1-C0001", "000002.SZ", 1),
    ]
    manifest = json.loads((args.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["embedding"]["provider"] == "dashscope_batch_file"
    assert manifest["embedding"]["actual_api_tokens"] == 17


def test_finalize_rejects_duplicate_result_indexes(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = build_args(workspace_tmp_path, monkeypatch)
    prepare_build(args)
    build_dir = args.output_dir.resolve() / BATCH_BUILD_DIRNAME
    state_path = build_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    shard = state["shards"][0]
    custom_id = json.loads(Path(shard["mapping_path"]).read_text(encoding="utf-8"))["custom_id"]
    result_path = build_dir / "results" / "duplicate.jsonl"
    write_jsonl(
        result_path,
        [
            {
                "custom_id": custom_id,
                "response": {
                    "status_code": 200,
                    "body": {
                        "data": [
                            {"index": 0, "embedding": axis_vector(0)},
                            {"index": 0, "embedding": axis_vector(1)},
                        ]
                    },
                },
            }
        ],
    )
    shard["result_path"] = result_path.resolve().as_posix()
    write_json_atomic(state_path, state)
    with pytest.raises(RuntimeError, match="duplicate or out-of-range"):
        finalize_index(args)


def test_finalize_explicit_partial_index_preserves_failed_chunk_for_bm25(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = build_partial_args(workspace_tmp_path, monkeypatch)
    prepare_build(args)
    build_dir = args.output_dir.resolve() / BATCH_BUILD_DIRNAME
    state_path = build_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    shard = state["shards"][0]
    mappings = [
        json.loads(line)
        for line in Path(shard["mapping_path"]).read_text(encoding="utf-8").splitlines()
    ]
    success_mapping, failed_mapping = mappings
    result_path = build_dir / "results" / "partial.jsonl"
    error_path = build_dir / "errors" / "partial.jsonl"
    write_jsonl(
        result_path,
        [
            {
                "custom_id": success_mapping["custom_id"],
                "response": {
                    "status_code": 200,
                    "body": {
                        "data": [
                            {"index": index, "embedding": axis_vector(index % 2)}
                            for index in range(10)
                        ],
                        "usage": {"prompt_tokens": 100},
                    },
                },
                "error": None,
            }
        ],
    )
    write_jsonl(
        error_path,
        [
            {
                "custom_id": failed_mapping["custom_id"],
                "response": {"status_code": 400},
                "error": {"code": "BalanceError", "message": "No service"},
            }
        ],
    )
    shard.update(
        {
            "status": "completed",
            "result_path": result_path.resolve().as_posix(),
            "error_path": error_path.resolve().as_posix(),
        }
    )
    write_json_atomic(state_path, state)

    result = finalize_index(args)
    assert result["status"] == "complete_partial"
    assert result["vector_count"] == 10
    assert result["excluded_vector_count"] == 1
    assert faiss.read_index(str(args.output_dir / "vector.faiss")).ntotal == 10
    assert len(json.loads((args.output_dir / "vector_ids.json").read_text(encoding="utf-8"))) == 10
    failures = (args.output_dir / "embedding_failures.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(failures) == 1
    assert json.loads(failures[0])["chunk_id"] == "ANN-PARTIAL-C0011"
    with sqlite3.connect(args.output_dir / "fintrace_kb.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 11
    warning = vector_coverage_warning(args.output_dir)
    assert warning and "90.9091%" in warning and "1 chunks" in warning


def test_submit_is_resumable(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = build_args(workspace_tmp_path, monkeypatch)
    prepare_build(args)

    class FakeBatchClient:
        def __init__(self) -> None:
            self.uploads = 0
            self.submissions = 0

        def upload_file(self, path: Path) -> str:
            assert path.is_file()
            self.uploads += 1
            return "file-test"

        def create_batch(self, input_file_id: str, **_: object) -> dict:
            assert input_file_id == "file-test"
            self.submissions += 1
            return {"id": "batch-test", "status": "validating"}

    client = FakeBatchClient()
    submit_jobs(args, client=client)
    submit_jobs(args, client=client)
    assert client.uploads == 1
    assert client.submissions == 1


def test_select_shards_preserves_requested_order() -> None:
    shards = [{"shard_id": "shard-0000"}, {"shard_id": "shard-0001"}]
    assert select_shards(shards, ["shard-0001", "shard-0000", "shard-0001"]) == [
        shards[1],
        shards[0],
    ]
    with pytest.raises(ValueError, match="Unknown shard id"):
        select_shards(shards, ["shared-0000"])


def axis_vector(index: int, value: float = 1.0) -> list[float]:
    vector = [0.0] * 64
    vector[index] = value
    return vector
