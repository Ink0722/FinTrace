from __future__ import annotations

import re
import unicodedata


LEGAL_SUFFIXES = (
    "集团股份有限公司",
    "股份有限公司",
    "集团有限公司",
    "有限责任公司",
    "有限公司",
    "股份公司",
)


def normalize_name(value: str) -> str:
    """Return a conservative comparison key without changing legal semantics."""
    value = unicodedata.normalize("NFKC", value or "").upper()
    return re.sub(r"[\s·•・,，。.:：;；()（）\[\]【】]", "", value)


def legal_core_name(value: str) -> str:
    normalized = normalize_name(value)
    for suffix in LEGAL_SUFFIXES:
        normalized_suffix = normalize_name(suffix)
        if normalized.endswith(normalized_suffix) and len(normalized) > len(normalized_suffix):
            return normalized[: -len(normalized_suffix)]
    return normalized
