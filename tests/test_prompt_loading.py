import pytest
from pathlib import Path

from harness.answering import load_system_prompt
from harness.routing import planner
from harness.routing.planner import load_planner_prompt


def test_load_system_prompt_uses_prompt_file() -> None:
    prompt = load_system_prompt()
    assert "FinTrace" in prompt
    assert "answer" in prompt
    assert "limitations" in prompt
    assert "主动分析原则" in prompt
    assert "股权穿透" in prompt


def test_load_planner_prompt_uses_prompt_file() -> None:
    prompt = load_planner_prompt()
    assert "工具计划生成器" in prompt
    assert "tool_calls" in prompt
    assert "financial_risk_analysis" in prompt
    assert "ownership_penetration" in prompt
    assert "综合分析" in prompt


def test_missing_planner_prompt_raises(monkeypatch) -> None:
    monkeypatch.setattr(planner, "PLANNER_PROMPT_PATH", Path("prompts/__missing_planner_prompt__.md"))
    with pytest.raises(RuntimeError, match="Planner prompt file is required"):
        planner.load_planner_prompt()
