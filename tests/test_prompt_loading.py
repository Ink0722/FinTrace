from harness.prompts import (
    PromptFileError,
    SKILL_REGISTRY,
    build_system_prompt,
    core_skill_files,
    load_prompt,
)
from harness.skills import run_skill
from schemas.request import FinalAnswer, ParsedRequest
from schemas.memory import MemoryUpdate


def test_global_policy_has_valid_header() -> None:
    policy = load_prompt("01_global_policy.md")
    assert policy.prompt_id == "fintrace.global_policy"
    assert policy.version == "1.3.1"
    assert "Evidence 边界" in policy.body


def test_active_agent_prompts_use_evidence_without_generic_claim_contract() -> None:
    for filename in (
        "03_next_action_planner.md",
        "04_evidence_reviewer.md",
        "06_final_answer.md",
    ):
        assert "verified_claims" not in load_prompt(filename).body

    assert "claim_ids" not in load_prompt("04_evidence_reviewer.md").body
    assert "used_claim_ids" not in load_prompt("06_final_answer.md").body


def test_skill_registry_files_exist_with_headers() -> None:
    for filename in core_skill_files():
        prompt = load_prompt(filename)
        assert prompt.prompt_id.startswith("fintrace.")
        assert prompt.body, f"{filename} has an empty body"


def test_action_repair_prompt_matches_output_schema() -> None:
    prompt = load_prompt("05_action_repair.md")
    assert prompt.version == "1.3.0"
    assert '"error_class"' not in prompt.body


def test_build_system_prompt_joins_policy_and_skill() -> None:
    system = build_system_prompt("request_parser")
    assert "证据驱动的金融研究 Agent" in system
    assert "--- CURRENT SKILL ---" in system
    assert "Request Parser" in system
    # Skills never inline each other's bodies.
    assert "Next Action Planner" not in system


def test_unknown_skill_rejected() -> None:
    try:
        build_system_prompt("no_such_skill")
    except PromptFileError:
        return
    raise AssertionError("expected PromptFileError")


def test_prompt_file_without_header_rejected(tmp_path, monkeypatch) -> None:
    from harness.prompts import PROMPTS_DIR, clear_prompt_cache

    bad = PROMPTS_DIR / "99_bad_no_header.md"
    bad.write_text("没有版本头的 prompt", encoding="utf-8")
    clear_prompt_cache()
    try:
        try:
            load_prompt("99_bad_no_header.md")
        except PromptFileError:
            pass
        else:
            raise AssertionError("expected PromptFileError for missing header")
    finally:
        bad.unlink(missing_ok=True)
        clear_prompt_cache()


def test_run_skill_records_failure_without_llm() -> None:
    output, record = run_skill("request_parser", {"raw_query": "测试"})
    assert output is None
    assert record.status == "failed"
    assert record.prompt_id == "fintrace.request_parser"
    assert record.input_hash


def test_run_skill_validates_output_schema() -> None:
    from harness.llm import QwenClient

    class FakeClient(QwenClient):
        def __init__(self):  # noqa: D107
            super().__init__(api_key="test-key")

        @property
        def enabled(self):
            return True

        def chat_json(self, messages, temperature=0.0):
            return {"choices": [{"message": {"content": '{"raw_query": "为什么利润涨了但现金流下降", "task_family": "financial_investigation"}'}}]}

    output, record = run_skill(
        "request_parser",
        {"raw_query": "为什么利润涨了但现金流下降"},
        client=FakeClient(),
    )
    assert isinstance(output, ParsedRequest)
    assert output.task_family == "financial_investigation"
    assert record.status == "success"


def test_request_parser_restores_authoritative_raw_query_without_retry() -> None:
    from harness.llm import QwenClient

    class FakeClient(QwenClient):
        def __init__(self):
            super().__init__(api_key="test-key")
            self.calls = 0

        def chat_json(self, messages, temperature=0.0):
            self.calls += 1
            return {
                "choices": [{
                    "message": {"content": '{"raw_query":"被模型改写的问题","entities":[],"task_family":"unknown"}'},
                    "finish_reason": "stop",
                }]
            }

    client = FakeClient()
    output, record = run_skill(
        "request_parser",
        {"raw_query": "原始用户问题"},
        client=client,
    )

    assert isinstance(output, ParsedRequest)
    assert output.raw_query == "原始用户问题"
    assert client.calls == 1
    assert record.status == "success"


def test_request_parser_fills_omitted_raw_query_without_retry() -> None:
    from harness.llm import QwenClient

    class FakeClient(QwenClient):
        def __init__(self):
            super().__init__(api_key="test-key")
            self.calls = 0

        def chat_json(self, messages, temperature=0.0):
            self.calls += 1
            return {
                "choices": [{
                    "message": {"content": '{"entities":[],"task_family":"realtime_market_query"}'},
                    "finish_reason": "stop",
                }]
            }

    client = FakeClient()
    output, record = run_skill(
        "request_parser",
        {"raw_query": "连续横盘30个交易日"},
        client=client,
    )

    assert isinstance(output, ParsedRequest)
    assert output.raw_query == "连续横盘30个交易日"
    assert client.calls == 1
    assert record.status == "success"


def test_memory_summarizer_prompt_and_schema_are_active() -> None:
    prompt = load_prompt("07_memory_summarizer.md")
    assert prompt.prompt_id == "fintrace.memory_summarizer"

    from harness.llm import QwenClient

    class FakeClient(QwenClient):
        def __init__(self):
            super().__init__(api_key="test-key")

        def chat_json(self, messages, temperature=0.0):
            return {"choices": [{"message": {"content": '{"summary":"摘要","open_questions":[]}'}}]}

    output, record = run_skill("memory_summarizer", {"messages_to_compress": []}, client=FakeClient())
    assert isinstance(output, MemoryUpdate)
    assert output.summary == "摘要"
    assert record.status == "success"


def test_final_answer_retries_semantically_truncated_json() -> None:
    from harness.llm import QwenClient

    class FakeClient(QwenClient):
        def __init__(self):
            super().__init__(api_key="test-key")
            self.calls = 0

        def chat_json(self, messages, temperature=0.0):
            self.calls += 1
            answer = "机构观点显示需求" if self.calls == 1 else "机构观点显示相关产品需求增长。"
            return {
                "choices": [{"message": {"content": '{"answer":"' + answer + '","used_evidence_ids":["E1"],"limitations_disclosed":[]}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            }

    client = FakeClient()
    output, record = run_skill(
        "final_answer",
        {
            "answer_status": "answered",
            "supporting_evidence": [{"evidence_id": "E1"}],
            "limitations": [],
        },
        client=client,
    )
    assert isinstance(output, FinalAnswer)
    assert output.answer.endswith("。")
    assert client.calls == 2
    assert record.status == "recovered"
    assert record.attempt_count == 2
    assert record.prompt_tokens == 12
    assert record.completion_tokens == 8


def test_final_answer_records_length_truncation_failure() -> None:
    from harness.llm import QwenClient

    class FakeClient(QwenClient):
        def __init__(self):
            super().__init__(api_key="test-key")

        def chat_json(self, messages, temperature=0.0):
            return {
                "choices": [{"message": {"content": '{"answer":"分析结果仍包括","used_evidence_ids":[],"limitations_disclosed":[]}'}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 5},
            }

    output, record = run_skill(
        "final_answer",
        {"answer_status": "answered", "supporting_evidence": [], "limitations": []},
        client=FakeClient(),
    )
    assert output is None
    assert record.status == "failed"
    assert record.attempt_count == 2
    assert record.finish_reason == "length"
    assert record.error_type == "ValueError"
    assert "finish_reason=length" in record.error_message
    assert record.latency_ms >= 0
