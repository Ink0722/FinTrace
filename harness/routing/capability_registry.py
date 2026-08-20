"""Static capability registry. `implemented` must reflect real code, not target interfaces (docs/13 §10)."""
from __future__ import annotations

from schemas.request import CapabilityDescriptor

CAPABILITIES: dict[str, CapabilityDescriptor] = {
    descriptor.name: descriptor
    for descriptor in (
        CapabilityDescriptor(
            name="financial_metric_query",
            implemented=True,
            tool="financial_analysis",
            operation="metric_query",
            required_slots=["company_ids", "metric_codes", "report_periods"],
            description="查询指定公司、报告期的财务指标原始值。",
        ),
        CapabilityDescriptor(
            name="financial_metric_compare",
            implemented=True,
            tool="financial_analysis",
            operation="metric_compare",
            required_slots=["company_ids", "metric_codes", "report_periods"],
            description="单公司跨期或多公司单期确定性比较；不允许多公司×多期间。",
        ),
        CapabilityDescriptor(
            name="financial_risk_scan",
            implemented=False,
            description="财务风险规则扫描，目标态能力。",
        ),
        CapabilityDescriptor(
            name="ownership_snapshot",
            implemented=True,
            tool="ownership_analysis",
            operation="holding_query",
            required_slots=["company_ids_or_holder_ids"],
            description="主要股东快照查询（正向/反向/交叉）与集中度。",
        ),
        CapabilityDescriptor(
            name="ownership_compare",
            implemented=True,
            tool="ownership_analysis",
            operation="holding_compare",
            required_slots=["company_ids", "start_date", "end_date"],
            description="同一公司两个观察时点的股东进入/退出/增减持比较。",
        ),
        CapabilityDescriptor(
            name="ownership_penetration",
            implemented=False,
            description="多跳股权穿透，目标态能力。",
        ),
        CapabilityDescriptor(
            name="document_retrieval",
            implemented=True,
            tool="document_search",
            operation="search",
            required_slots=["query"],
            description="公告正文与研报摘要的混合检索。",
        ),
        CapabilityDescriptor(
            name="event_query",
            implemented=True,
            tool="event_timeline",
            operation="event_query",
            required_slots=["entity_ids"],
            description="结构化事件筛选与排序。",
        ),
        CapabilityDescriptor(
            name="event_cluster",
            implemented=True,
            tool="event_timeline",
            operation="event_cluster",
            required_slots=["entity_ids"],
            description="相关事件聚合为事件簇。",
        ),
        CapabilityDescriptor(
            name="realtime_market_price",
            implemented=False,
            description="历史或实时行情，数据源不存在。",
        ),
        CapabilityDescriptor(
            name="user_portfolio",
            implemented=False,
            description="用户账户与持仓，不属于系统边界。",
        ),
    )
}


def get_capability(name: str) -> CapabilityDescriptor | None:
    return CAPABILITIES.get(name)


def implemented_operations() -> set[tuple[str, str]]:
    return {
        (descriptor.tool, descriptor.operation)
        for descriptor in CAPABILITIES.values()
        if descriptor.implemented and descriptor.tool and descriptor.operation
    }


def candidate_capabilities(task_family: str) -> list[str]:
    """Shrink the capability set the planner is allowed to see (docs/13 §12)."""
    by_family = {
        "financial_metric_query": ["financial_metric_query"],
        "financial_metric_compare": ["financial_metric_compare", "financial_metric_query"],
        "financial_investigation": [
            "financial_metric_query",
            "financial_metric_compare",
            "document_retrieval",
            "event_query",
        ],
        "ownership_snapshot": ["ownership_snapshot"],
        "ownership_compare": ["ownership_compare", "ownership_snapshot"],
        "ownership_penetration": ["ownership_snapshot", "ownership_compare"],
        "document_retrieval": ["document_retrieval"],
        "event_query": ["event_query", "event_cluster"],
        "event_investigation": ["event_query", "event_cluster", "document_retrieval"],
        "general_financial_explanation": ["document_retrieval"],
        "prediction_request": [],
        "realtime_market_query": [],
        "user_account_query": [],
        "unknown": [
            "financial_metric_query",
            "ownership_snapshot",
            "document_retrieval",
            "event_query",
        ],
    }
    candidates = [name for name in by_family.get(task_family, []) if CAPABILITIES[name].implemented]
    return candidates or [name for name, descriptor in CAPABILITIES.items() if descriptor.implemented][:1] or [
        "document_retrieval"
    ]
