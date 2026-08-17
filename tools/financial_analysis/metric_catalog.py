from __future__ import annotations

from dataclasses import dataclass


MAPPING_VERSION = "financial-metrics-v1"


@dataclass(frozen=True)
class MetricDefinition:
    metric_code: str
    name: str
    statement_name: str
    source_column: str
    value_nature: str


METRIC_DEFINITIONS = (
    MetricDefinition("TOTAL_ASSETS", "总资产", "balance_sheet", "tot_assets", "instant"),
    MetricDefinition("TOTAL_LIABILITIES", "总负债", "balance_sheet", "tot_liab", "instant"),
    MetricDefinition("CURRENT_ASSETS", "流动资产合计", "balance_sheet", "tot_cur_assets", "instant"),
    MetricDefinition("CURRENT_LIABILITIES", "流动负债合计", "balance_sheet", "tot_cur_liab", "instant"),
    MetricDefinition("INVENTORY", "存货", "balance_sheet", "inventories", "instant"),
    MetricDefinition("ACCOUNTS_RECEIVABLE", "应收账款", "balance_sheet", "acct_rcv", "instant"),
    MetricDefinition("MONETARY_CAPITAL", "货币资金", "balance_sheet", "monetary_cap", "instant"),
    MetricDefinition("REVENUE", "营业收入", "income_statement", "oper_rev", "year_to_date"),
    MetricDefinition("OPERATING_COST", "营业成本", "income_statement", "less_oper_cost", "year_to_date"),
    MetricDefinition(
        "NET_PROFIT_PARENT",
        "归属于母公司股东的净利润",
        "income_statement",
        "net_profit_excl_min_int_inc",
        "year_to_date",
    ),
    MetricDefinition("OPERATING_PROFIT", "营业利润", "income_statement", "oper_profit", "year_to_date"),
    MetricDefinition("R_AND_D_EXPENSE", "研发费用", "income_statement", "rd_expense", "year_to_date"),
    MetricDefinition(
        "OPERATING_CASHFLOW",
        "经营活动现金流量净额",
        "cashflow_statement",
        "net_cash_flows_oper_act",
        "year_to_date",
    ),
    MetricDefinition(
        "CASH_RECEIVED_FROM_SALES",
        "销售商品、提供劳务收到的现金",
        "cashflow_statement",
        "cash_recp_sg_and_rs",
        "year_to_date",
    ),
)

METRIC_CATALOG = {definition.metric_code: definition for definition in METRIC_DEFINITIONS}

SOURCE_FILES = {
    "balance_sheet": "balance_sheets.jsonl",
    "income_statement": "income_statements.jsonl",
    "cashflow_statement": "cashflows.jsonl",
}


def period_type(report_period: str) -> str:
    suffix = report_period[5:]
    return {
        "03-31": "Q1",
        "06-30": "H1",
        "09-30": "Q3_YTD",
        "12-31": "FY",
    }.get(suffix, "NON_STANDARD")
