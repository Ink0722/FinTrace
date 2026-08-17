from harness.routing.router import route_query
from schemas.enums import ToolName


def test_financial_query_routes_to_financial_tool() -> None:
    plan = route_query("分析一下这家公司的存货和现金流风险")
    assert plan.tool_calls[0].tool_name == ToolName.FINANCIAL_ANALYSIS


def test_ownership_query_routes_to_graph_tool() -> None:
    plan = route_query("张某通过哪些主体控制这家公司")
    assert plan.tool_calls[0].tool_name == ToolName.OWNERSHIP_PENETRATION


def test_rule_planner_extracts_structured_arguments() -> None:
    plan = route_query("分析000001.SZ在2022年的存货和现金流风险，并结合问询函")
    tool_names = [call.tool_name for call in plan.tool_calls]
    assert ToolName.FINANCIAL_ANALYSIS in tool_names
    assert ToolName.DOCUMENT_SEARCH in tool_names
    financial_call = next(call for call in plan.tool_calls if call.tool_name == ToolName.FINANCIAL_ANALYSIS)
    document_call = next(call for call in plan.tool_calls if call.tool_name == ToolName.DOCUMENT_SEARCH)
    assert financial_call.arguments["company_ids"] == ["000001.SZ"]
    assert financial_call.arguments["report_periods"] == ["2022-12-31"]
    assert financial_call.arguments["operation"] == "metric_query"
    assert financial_call.arguments["metric_codes"] == ["INVENTORY", "OPERATING_CASHFLOW"]
    assert document_call.arguments["document_types"] == ["inquiry_letter"]


def test_comprehensive_analysis_proactively_routes_financial_and_ownership_tools() -> None:
    plan = route_query("请对000001.SZ做一次综合分析")
    tool_names = [call.tool_name for call in plan.tool_calls]
    assert tool_names == [ToolName.FINANCIAL_ANALYSIS, ToolName.OWNERSHIP_PENETRATION]


def test_llm_planner_uses_separate_model_env(monkeypatch) -> None:
    from harness.routing import planner

    created_clients = []

    class FakeClient:
        def __init__(self, api_key=None, base_url=None, model=None):
            self.api_key = api_key
            self.base_url = base_url
            self.model = model
            created_clients.append(self)

        @property
        def enabled(self):
            return True

        def chat_json(self, messages, temperature=0.0):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"user_intent":"financial_document_analysis",'
                                '"tool_calls":[{"tool_name":"document_search","arguments":{"top_k":3},"reason":"LLM planner"}]}'
                            )
                        }
                    }
                ]
            }

    monkeypatch.setenv("QWEN_API_KEY", "answer-key")
    monkeypatch.setenv("QWEN_MODEL", "answer-model")
    monkeypatch.setenv("QWEN_PLANNER_API_KEY", "planner-key")
    monkeypatch.setenv("QWEN_PLANNER_MODEL", "planner-model")
    monkeypatch.setattr(planner, "QwenClient", FakeClient)

    plan = planner.build_plan("查一下问询函")

    assert created_clients[0].api_key == "planner-key"
    assert created_clients[0].model == "planner-model"
    assert plan.tool_calls[0].tool_name == ToolName.DOCUMENT_SEARCH
    assert plan.tool_calls[0].arguments["top_k"] == 3
