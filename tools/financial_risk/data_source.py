import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from schemas.financial import FinancialRecord
from tools.financial_risk.csv_loader import CsvFinancialDataSource
from tools.financial_risk.sample_data import load_sample_financial_records


DEFAULT_FINANCIAL_RECORDS_PATH = Path("data/financial/financial_records.csv")


class FinancialDataSource(Protocol):
    name: str

    def load_records(self, company_id: str) -> list[FinancialRecord]:
        ...


@dataclass
class FinancialDataset:
    records: list[FinancialRecord]
    source_name: str
    warnings: list[str]
    strict: bool = False


class SampleFinancialDataSource:
    name = "sample"

    def load_records(self, company_id: str) -> list[FinancialRecord]:
        return load_sample_financial_records(company_id=company_id)


def load_financial_dataset(company_id: str) -> FinancialDataset:
    source = os.getenv("FINANCIAL_DATA_SOURCE", "auto").lower()
    records_path = Path(os.getenv("FINANCIAL_RECORDS_PATH", str(DEFAULT_FINANCIAL_RECORDS_PATH)))

    if source in {"csv", "auto"} and records_path.exists():
        data_source = CsvFinancialDataSource(records_path=records_path)
        return FinancialDataset(
            records=data_source.load_records(company_id=company_id),
            source_name=data_source.name,
            warnings=[f"Using CSV financial data: {records_path}"],
            strict=True,
        )

    if source == "csv":
        return FinancialDataset(
            records=[],
            source_name="csv",
            warnings=[f"CSV financial records file not found: {records_path}"],
            strict=True,
        )

    data_source = SampleFinancialDataSource()
    return FinancialDataset(
        records=data_source.load_records(company_id=company_id),
        source_name=data_source.name,
        warnings=["Using built-in sample financial data. Configure FINANCIAL_DATA_SOURCE=csv for real financial records."],
        strict=False,
    )
