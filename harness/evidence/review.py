"""Deterministic evidence sufficiency review (Phase 1). LLM reviewer skill (04) lands in Phase 2."""
from __future__ import annotations

from schemas.agent_state import AgentState
from schemas.request import CoveredAspect, EvidenceGap, EvidenceReview


def review_evidence(state: AgentState) -> EvidenceReview:
    results = state.tool_results
    if not results:
        return EvidenceReview(
            status="insufficient",
            evidence_gaps=[
                EvidenceGap(
                    gap_id="GAP-NO-TOOL",
                    description="没有任何工具执行成功，未获得证据",
                    priority="high",
                    resolvable=False,
                )
            ],
            reason="无工具结果",
        )

    succeeded = [result for result in results if result.status.value == "success"]
    failed = [result for result in results if result.status.value != "success"]
    succeeded_with_evidence = [result for result in succeeded if result.evidence]

    covered = [
        CoveredAspect(
            aspect=f"{result.tool_name.value}.{(result.data or {}).get('operation', '')}".rstrip("."),
            evidence_ids=[item.evidence_id for item in result.evidence][:10],
        )
        for result in succeeded_with_evidence
    ]
    gaps = [
        EvidenceGap(
            gap_id=f"GAP-{result.tool_call_id}",
            description=(
                f"{result.tool_name.value} 调用失败：{result.error.message if result.error else '未知错误'}"
            ),
            priority="high",
            candidate_capabilities=[],
            resolvable=False,
        )
        for result in failed
    ]
    gaps.extend(
        EvidenceGap(
            gap_id=f"GAP-EMPTY-{result.tool_call_id}",
            description=f"{result.tool_name.value} 执行成功但未返回证据（可能无数据命中）",
            priority="medium",
            candidate_capabilities=[],
            resolvable=False,
        )
        for result in succeeded
        if not result.evidence
    )

    if succeeded_with_evidence and not failed and not gaps:
        return EvidenceReview(status="sufficient", covered_aspects=covered, reason="所有调用成功且均有证据。")
    if succeeded_with_evidence:
        return EvidenceReview(
            status="partial",
            covered_aspects=covered,
            evidence_gaps=gaps,
            reason="部分调用已有证据，其余未能补充。",
        )
    return EvidenceReview(
        status="insufficient",
        evidence_gaps=gaps or [EvidenceGap(gap_id="GAP-EMPTY", description="所有调用均未返回证据", priority="high", resolvable=False)],
        reason="没有获得任何可用证据。",
    )


def answer_status_from_review(review: EvidenceReview) -> str:
    return {
        "sufficient": "answered",
        "partial": "partially_answered",
        "insufficient": "insufficient_evidence",
    }[review.status]
