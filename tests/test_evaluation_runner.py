import json
from types import SimpleNamespace

from evaluation.runner import repository
from evaluation.runner.run_dataset import execute, prepare


def _dataset(tmp_path):
    path = tmp_path / "questions.jsonl"
    rows = [
        {"case_id": "S1-T1", "session_id": "1", "turn_id": 1, "question": "问题一", "answerability": "answerable"},
        {"case_id": "S1-T2", "session_id": "1", "turn_id": 2, "question": "问题二", "answerability": "unanswerable"},
        {"case_id": "S2-T1", "session_id": "2", "turn_id": 1, "question": "问题三", "answerability": "clarification_required"},
    ]
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return path


def test_prepare_creates_dedicated_user_and_cases(monkeypatch, tmp_path) -> None:
    result = prepare(_dataset(tmp_path), "2025-06-30", "test")
    assert result["case_count"] == 3
    assert result["session_count"] == 2
    assert result["evaluation_user_id"].startswith("USER-EVAL-")
    cases = repository.list_cases(result["batch_id"])
    assert cases[0]["agent_session_id"].endswith("SESSION-001")
    annotation = json.loads(cases[0]["annotation_json"])
    assert "question" not in annotation
    assert annotation["answerability"] == "answerable"


def test_execute_preserves_turn_order_and_records_run_ids(monkeypatch, tmp_path) -> None:
    prepared = prepare(_dataset(tmp_path), "2025-06-30")
    calls = []

    def fake_run_agent(question, session_id, *, knowledge_cutoff):
        calls.append((question, session_id, knowledge_cutoff))
        return SimpleNamespace(run_id=f"RUN-{len(calls)}")

    monkeypatch.setattr("evaluation.runner.run_dataset.run_agent", fake_run_agent)
    result = execute(prepared["batch_id"], concurrency=2)
    assert result["case_statuses"] == {"completed": 3}
    session_one = [item for item in calls if item[1].endswith("SESSION-001")]
    assert [item[0] for item in session_one] == ["问题一", "问题二"]
    assert all(item[2] == "2025-06-30" for item in calls)


def test_failure_blocks_later_turn_until_retry(monkeypatch, tmp_path) -> None:
    prepared = prepare(_dataset(tmp_path), "2025-06-30")

    def failing(question, session_id, *, knowledge_cutoff):
        if question == "问题一":
            raise TimeoutError("model timeout")
        return SimpleNamespace(run_id="RUN-OK")

    monkeypatch.setattr("evaluation.runner.run_dataset.run_agent", failing)
    result = execute(prepared["batch_id"], session_id="1")
    assert result["case_statuses"]["failed"] == 1
    assert result["case_statuses"]["pending"] == 2
