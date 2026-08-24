"""Phase 2 skill integration: planner / reviewer / repair / final_answer wired via run_skill."""
import json

from harness.graph.workflow import run_agent
from harness.prompts import load_prompt
from schemas.request import ActionRepairResult, AgentAction, EvidenceReview, FinalAnswer, LlmCallRecord


def _record(skill: str) -> LlmCallRecord:
    prompt = load_prompt(
        {
            "next_action_planner": "03_next_action_planner.md",
            "evidence_reviewer": "04_evidence_reviewer.md",
            "action_repair": "05_action_repair.md",
            "final_answer": "06_final_answer.md",
        }[skill]
    )
    return LlmCallRecord(
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.version,
        model="fake",
        input_hash="0" * 16,
        output_schema="fake",
        status="success",
    )


def test_all_seven_core_prompts_materialized() -> None:
    from harness.prompts import core_skill_files

    assert set(core_skill_files()) == {
        "02_request_parser.md",
        "03_next_action_planner.md",
        "04_evidence_reviewer.md",
        "05_action_repair.md",
        "06_final_answer.md",
        "07_memory_summarizer.md",
    }


def test_llm_planner_and_reviewer_drive_investigation(monkeypatch) -> None:
    planner_rounds = {"count": 0}

    def fake_run_skill(skill, runtime, **kwargs):
        if skill == "next_action_planner":
            planner_rounds["count"] += 1
            if planner_rounds["count"] == 1:
                action = AgentAction(
                    action="call_tool",
                    capability="document_retrieval",
                    tool_name="document_search",
                    operation="search",
                    arguments={"query": runtime["raw_query"], "company_ids": ["600519.SH"], "top_k": 3},
                    target_gap_id="GAP-1",
                    reason="先检索文本证据",
                )
            else:
                action = AgentAction(action="finish", reason="证据已足够")
            return action, _record(skill)
        if skill == "evidence_reviewer":
            return (
                EvidenceReview(
                    status="sufficient",
                    covered_aspects=[
                        {"aspect": "文本证据", "claim_ids": [], "evidence_ids": ["EVID-FAKE"]}
                    ],
                    evidence_gaps=[],
                    reason="检索已覆盖核心问题",
                ),
                _record(skill),
            )
        if skill == "final_answer":
            return (
                FinalAnswer(
                    answer="基于检索的结构化回答。",
                    used_evidence_ids=["EVID-FAKE"],
                    limitations_disclosed=["示例限制"],
                ),
                _record(skill),
            )
        return None, None

    monkeypatch.setattr("harness.graph.nodes.run_skill", fake_run_skill)
    state = run_agent("结合公告分析600519.SH的存货风险", session_id="TEST-P2-1")

    assert state.routing_mode == "investigation"
    assert state.total_tool_calls == 1
    assert state.tool_call_history[0].tool_name == "document_search"
    assert state.review_status == "sufficient"
    assert state.answer_status == "answered"
    payload = json.loads(state.final_answer)
    assert payload["answer"] == "基于检索的结构化回答。"
    assert payload["limitations_disclosed"] == ["示例限制"]
    assert [record.prompt_id for record in state.llm_calls] == [
        "fintrace.next_action_planner",
        "fintrace.evidence_reviewer",
        "fintrace.final_answer",
    ]


def test_llm_planner_invalid_output_falls_back_to_rule_queue(monkeypatch) -> None:
    def fake_run_skill(skill, runtime, **kwargs):
        if skill == "next_action_planner":
            return None, _record(skill)
        return None, None

    monkeypatch.setattr("harness.graph.nodes.run_skill", fake_run_skill)
    state = run_agent("结合公告分析600519.SH的存货风险", session_id="TEST-P2-2")
    assert state.routing_mode == "investigation"
    assert state.tool_call_history  # rule queue still investigated
    assert any("规则调查队列" in warning for warning in state.warnings)


def test_repair_skill_fixes_invalid_action(monkeypatch) -> None:
    planner_rounds = {"count": 0}

    def fake_run_skill(skill, runtime, **kwargs):
        if skill == "next_action_planner":
            planner_rounds["count"] += 1
            if planner_rounds["count"] == 1:
                # Missing report_periods: deterministic repair cannot fix this.
                return (
                    AgentAction(
                        action="call_tool",
                        capability="financial_metric_query",
                        tool_name="financial_analysis",
                        operation="metric_query",
                        arguments={
                            "operation": "metric_query",
                            "company_ids": ["600519.SH"],
                            "metric_codes": ["REVENUE"],
                        },
                        reason="缺少报告期",
                    ),
                    _record(skill),
                )
            return AgentAction(action="finish", reason="完成"), _record(skill)
        if skill == "action_repair":
            failed = runtime["failed_action"]
            repaired_arguments = dict(failed["arguments"])
            repaired_arguments["report_periods"] = ["2024-12-31"]
            return (
                ActionRepairResult(
                    status="repaired",
                    error_class="missing_argument",
                    repaired_action=AgentAction.model_validate({**failed, "arguments": repaired_arguments}),
                    reason="从 parsed_request 恢复报告期",
                ),
                _record(skill),
            )
        if skill == "evidence_reviewer":
            return EvidenceReview(status="sufficient", reason="已获得指标"), _record(skill)
        if skill == "final_answer":
            return FinalAnswer(answer="修复后完成查询。"), _record(skill)
        return None, None

    monkeypatch.setattr("harness.graph.nodes.run_skill", fake_run_skill)
    state = run_agent("结合公告分析600519.SH 2024年营业收入", session_id="TEST-P2-3")
    assert state.repair_count == 1
    assert state.tool_call_history and state.tool_call_history[0].tool_name == "financial_analysis"
    assert state.tool_call_history[0].status == "success"
    assert "repair_action" in state.executed_nodes
