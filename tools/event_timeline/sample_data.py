from datetime import date

from schemas.event import EventRecord


def load_sample_events() -> list[EventRecord]:
    return [
        EventRecord(
            event_id="EVENT-001",
            company_id="000001.SZ",
            event_type="regulatory_inquiry",
            event_date=date(2023, 5, 18),
            entities=["示例上市公司", "交易所"],
            title="交易所发出年报问询函",
            summary="监管机构要求公司说明存货增长、库龄结构和跌价准备计提是否充分。",
            source_document_ids=["INQUIRY-2023"],
        ),
        EventRecord(
            event_id="EVENT-002",
            company_id="000001.SZ",
            event_type="regulatory_inquiry",
            event_date=date(2023, 5, 30),
            entities=["示例上市公司", "交易所"],
            title="公司回复年报问询函",
            summary="公司回复称存货增加与订单备货有关，并说明了跌价准备测试过程。",
            source_document_ids=["INQUIRY-REPLY-2023"],
        ),
        EventRecord(
            event_id="EVENT-003",
            company_id="000001.SZ",
            event_type="audit_opinion",
            event_date=date(2023, 4, 28),
            entities=["示例上市公司", "审计师"],
            title="审计报告披露关键审计事项",
            summary="审计报告将收入确认和存货跌价准备列为关键审计事项。",
            source_document_ids=["AUDIT-2022"],
        ),
        EventRecord(
            event_id="EVENT-004",
            company_id="000001.SZ",
            event_type="controller_change",
            event_date=date(2022, 1, 10),
            entities=["张某", "示例控股集团", "示例上市公司"],
            title="控制关系生效",
            summary="示例控股集团对示例上市公司的控制关系生效，张某通过示例控股集团形成控制链。",
            source_document_ids=["SHAREHOLDER-2022"],
        ),
        EventRecord(
            event_id="EVENT-005",
            company_id="000001.SZ",
            event_type="risk_warning",
            event_date=date(2022, 12, 20),
            entities=["示例上市公司"],
            title="公司回应存货增长风险",
            summary="公司称存货增加与订单备货有关，后续将持续评估减值风险。",
            source_document_ids=["NEWS-2022-001"],
        ),
        EventRecord(
            event_id="EVENT-006",
            company_id="000002.SZ",
            event_type="regulatory_inquiry",
            event_date=date(2023, 6, 1),
            entities=["其他公司"],
            title="其他公司收到问询函",
            summary="其他公司收到年报问询函。",
            source_document_ids=["OTHER-INQUIRY-2023"],
        ),
    ]
