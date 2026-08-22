from harness.prompts import (
    PromptFileError,
    SKILL_REGISTRY,
    build_system_prompt,
    core_skill_files,
    load_prompt,
)
from harness.skills import run_skill
from schemas.request import ParsedRequest


def test_global_policy_has_valid_header() -> None:
    policy = load_prompt("01_global_policy.md")
    assert policy.prompt_id == "fintrace.global_policy"
    assert policy.version == "1.2.0"
    assert "Evidence 边界" in policy.body


def test_skill_registry_files_exist_with_headers() -> None:
    for filename in core_skill_files():
        prompt = load_prompt(filename)
        assert prompt.prompt_id.startswith("fintrace.")
        assert prompt.body, f"{filename} has an empty body"


def test_action_repair_prompt_matches_output_schema() -> None:
    prompt = load_prompt("05_action_repair.md")
    assert prompt.version == "1.2.0"
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
