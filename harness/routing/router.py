from harness.routing.planner import build_plan
from schemas.tool_calls import ExecutionPlan


def route_query(query: str) -> ExecutionPlan:
    return build_plan(query)
