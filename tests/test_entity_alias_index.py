import json

from data_pipeline.entity_alias.build_index import build_alias_index
from tools.entity_resolver import EntityResolver, clear_entity_cache


def test_company_profiles_supply_short_legal_and_former_name_aliases(tmp_path) -> None:
    data_root = tmp_path / "data"
    profiles = data_root / "source" / "company_profiles" / "akshare_company_profiles.jsonl"
    profiles.parent.mkdir(parents=True)
    rows = [
        {
            "company_id": "605398.SH",
            "security_name": "新炬网络",
            "legal_name": "上海新炬网络信息技术股份有限公司",
            "former_names": [],
            "fetch_status": "success",
        },
        {
            "company_id": "300847.SZ",
            "security_name": "中船汉光",
            "legal_name": "中船汉光科技股份有限公司",
            "former_names": ["中船重工汉光科技股份有限公司"],
            "fetch_status": "success",
        },
        {
            "company_id": "600030.SH",
            "security_name": "失败记录",
            "fetch_status": "failed",
        },
    ]
    profiles.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    index_path = tmp_path / "company_aliases.sqlite"

    result = build_alias_index(data_root, index_path)
    clear_entity_cache()
    resolver = EntityResolver(index_path)

    assert result["sources"]["company_profiles"] == 2
    assert resolver.resolve_company("新炬网络").company_id == "605398.SH"
    assert resolver.resolve_company("上海新炬网络信息技术股份有限公司").company_id == "605398.SH"
    assert resolver.resolve_company("中船重工汉光科技股份有限公司").company_id == "300847.SZ"
    assert resolver.resolve_company("失败记录").status == "not_found"
