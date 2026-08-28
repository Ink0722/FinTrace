import sqlite3

from fastapi.testclient import TestClient

from app.api.main import app
from deployment.bootstrap_showcase import bootstrap
from deployment.build_showcase_seed import build_seed
from evaluation.runner.repository import create_batch, mark_case_completed
from harness.tracing.store import SHOWCASE_USER_ID, connect, import_payload
from harness.tracing.users import claim_session, create_user, set_session_immutable


def test_showcase_mode_requires_internal_api_key(monkeypatch) -> None:
    monkeypatch.setenv("FINTRACE_DEPLOYMENT_MODE", "showcase")
    monkeypatch.setenv("FINTRACE_INTERNAL_API_KEY", "test-internal-key")
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/showcase/sessions").status_code == 401
    response = client.get(
        "/showcase/sessions",
        headers={"X-FinTrace-Internal-Key": "test-internal-key"},
    )
    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_showcase_evaluation_session_rejects_all_mutations(monkeypatch) -> None:
    monkeypatch.setenv("FINTRACE_DEPLOYMENT_MODE", "showcase")
    monkeypatch.setenv("FINTRACE_INTERNAL_API_KEY", "test-internal-key")
    claim_session(SHOWCASE_USER_ID, "SESSION-READONLY", "评测会话")
    assert set_session_immutable(SHOWCASE_USER_ID, "SESSION-READONLY")
    client = TestClient(app)
    headers = {"X-FinTrace-Internal-Key": "test-internal-key"}

    assert client.post(
        "/chat", headers=headers,
        json={"query": "继续提问", "session_id": "SESSION-READONLY"},
    ).status_code == 403
    assert client.patch(
        "/showcase/sessions/SESSION-READONLY", headers=headers,
        json={"title": "new title"},
    ).status_code == 403
    assert client.delete(
        "/showcase/sessions/SESSION-READONLY", headers=headers,
    ).status_code == 403


def test_build_showcase_seed_keeps_only_final_batch(monkeypatch, tmp_path) -> None:
    source = tmp_path / "runtime.sqlite3"
    output = tmp_path / "showcase.sqlite3"
    monkeypatch.setenv("FINTRACE_RUNTIME_DB", str(source))
    evaluation_user = create_user("评测用户")
    claim_session(evaluation_user["user_id"], "SESSION-EVAL", "最终评测")
    claim_session(evaluation_user["user_id"], "SESSION-OTHER", "其他会话")
    import_payload({
        "run_id": "RUN-EVAL", "trace_id": "TRACE-EVAL",
        "user_id": evaluation_user["user_id"], "session_id": "SESSION-EVAL",
        "turn_id": 1, "query": "评测问题", "answer": "评测回答",
    })
    import_payload({
        "run_id": "RUN-OTHER", "trace_id": "TRACE-OTHER",
        "user_id": evaluation_user["user_id"], "session_id": "SESSION-OTHER",
        "turn_id": 1, "query": "其他问题", "answer": "其他回答",
    })
    create_batch({
        "batch_id": "EVAL-TEST", "dataset_path": "questions.jsonl",
        "dataset_sha256": "abc", "evaluation_user_id": evaluation_user["user_id"],
        "knowledge_cutoff": "2026-05-28", "agent_version": "test",
        "created_at": "2026-08-29T00:00:00+00:00",
    }, [{
        "case_id": "CASE-1", "source_session_id": "1",
        "agent_session_id": "SESSION-EVAL", "expected_turn_id": 1,
        "question": "评测问题", "annotation": {},
    }])
    mark_case_completed("EVAL-TEST", "CASE-1", "RUN-EVAL")

    result = build_seed(source=source, output=output, batch_id="EVAL-TEST")
    assert result["session_count"] == 1
    assert result["run_count"] == 1
    with sqlite3.connect(output) as connection:
        connection.row_factory = sqlite3.Row
        session = connection.execute("SELECT * FROM user_sessions").fetchone()
        assert session["session_id"] == "SESSION-EVAL"
        assert session["user_id"] == SHOWCASE_USER_ID
        assert session["immutable"] == 1
        assert connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 1
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_bootstrap_preserves_existing_demo_sessions(tmp_path) -> None:
    seed = tmp_path / "seed.sqlite3"
    runtime = tmp_path / "runtime.sqlite3"
    connection = connect(path=seed)
    connection.close()

    assert bootstrap(seed=seed, runtime=runtime) == "initialized"
    with sqlite3.connect(runtime) as connection:
        connection.execute(
            "INSERT INTO user_sessions(session_id, user_id, title, created_at, updated_at, immutable) "
            "VALUES ('SESSION-DEMO', ?, 'demo', '2026-08-29', '2026-08-29', 0)",
            (SHOWCASE_USER_ID,),
        )
    with sqlite3.connect(seed) as connection:
        connection.execute("UPDATE users SET display_name = 'changed seed'")
    assert bootstrap(seed=seed, runtime=runtime) == "preserved"
    with sqlite3.connect(runtime) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM user_sessions WHERE session_id = 'SESSION-DEMO'"
        ).fetchone()[0] == 1
