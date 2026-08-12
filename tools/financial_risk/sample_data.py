from schemas.financial import FinancialRecord


def load_sample_financial_records(company_id: str = "000001.SZ") -> list[FinancialRecord]:
    rows = [
        ("2020A", "INCOME_STATEMENT", "REVENUE", "营业收入", 1000, "ANNUAL-2020", 10),
        ("2020A", "INCOME_STATEMENT", "NET_PROFIT", "净利润", 100, "ANNUAL-2020", 11),
        ("2020A", "CASH_FLOW", "OPERATING_CASHFLOW", "经营活动现金流量净额", 90, "ANNUAL-2020", 20),
        ("2020A", "BALANCE_SHEET", "INVENTORY", "存货", 120, "ANNUAL-2020", 30),
        ("2020A", "BALANCE_SHEET", "ACCOUNTS_RECEIVABLE", "应收账款", 160, "ANNUAL-2020", 31),
        ("2020A", "INCOME_STATEMENT", "GROSS_PROFIT", "毛利", 300, "ANNUAL-2020", 12),
        ("2020A", "INCOME_STATEMENT", "NON_RECURRING_PROFIT", "非经常性损益", 8, "ANNUAL-2020", 13),
        ("2021A", "INCOME_STATEMENT", "REVENUE", "营业收入", 1100, "ANNUAL-2021", 10),
        ("2021A", "INCOME_STATEMENT", "NET_PROFIT", "净利润", 120, "ANNUAL-2021", 11),
        ("2021A", "CASH_FLOW", "OPERATING_CASHFLOW", "经营活动现金流量净额", 70, "ANNUAL-2021", 20),
        ("2021A", "BALANCE_SHEET", "INVENTORY", "存货", 180, "ANNUAL-2021", 30),
        ("2021A", "BALANCE_SHEET", "ACCOUNTS_RECEIVABLE", "应收账款", 230, "ANNUAL-2021", 31),
        ("2021A", "INCOME_STATEMENT", "GROSS_PROFIT", "毛利", 330, "ANNUAL-2021", 12),
        ("2021A", "INCOME_STATEMENT", "NON_RECURRING_PROFIT", "非经常性损益", 20, "ANNUAL-2021", 13),
        ("2022A", "INCOME_STATEMENT", "REVENUE", "营业收入", 1180, "ANNUAL-2022", 10),
        ("2022A", "INCOME_STATEMENT", "NET_PROFIT", "净利润", 150, "ANNUAL-2022", 11),
        ("2022A", "CASH_FLOW", "OPERATING_CASHFLOW", "经营活动现金流量净额", 45, "ANNUAL-2022", 20),
        ("2022A", "BALANCE_SHEET", "INVENTORY", "存货", 310, "ANNUAL-2022", 30),
        ("2022A", "BALANCE_SHEET", "ACCOUNTS_RECEIVABLE", "应收账款", 360, "ANNUAL-2022", 31),
        ("2022A", "INCOME_STATEMENT", "GROSS_PROFIT", "毛利", 350, "ANNUAL-2022", 12),
        ("2022A", "INCOME_STATEMENT", "NON_RECURRING_PROFIT", "非经常性损益", 45, "ANNUAL-2022", 13),
    ]
    return [
        FinancialRecord(
            company_id=company_id,
            report_period=period,
            statement_type=statement_type,
            item_code=item_code,
            item_name_raw=item_name,
            value_raw=value,
            unit_raw="万元",
            value_cny=value * 10000,
            source_document_id=document_id,
            source_page=page,
        )
        for period, statement_type, item_code, item_name, value, document_id, page in rows
    ]
