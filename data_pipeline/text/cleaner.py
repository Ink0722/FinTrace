from __future__ import annotations

import re
import unicodedata


CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
INLINE_WHITESPACE_RE = re.compile(r"[ \t]+")
EXCESS_NEWLINES_RE = re.compile(r"\n{3,}")


def clean_text(value: object) -> str:
    """Apply conservative whitespace cleanup without rewriting source content."""

    text = "" if value is None else str(value)
    text = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    text = CONTROL_CHAR_RE.sub("", text)
    lines = [INLINE_WHITESPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    return EXCESS_NEWLINES_RE.sub("\n\n", "\n".join(lines)).strip()


def clean_tags(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = clean_text(value)
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def remove_leading_title_lines(text: str, title: str) -> tuple[str, int]:
    """Remove complete title lines only from the start of a document body."""

    cleaned_text = clean_text(text)
    cleaned_title = clean_text(title)
    if not cleaned_text or not cleaned_title:
        return cleaned_text, 0

    lines = cleaned_text.split("\n")
    expected = _title_comparison_key(cleaned_title)
    removed = 0
    while lines and _title_comparison_key(lines[0]) == expected:
        lines.pop(0)
        removed += 1
        while lines and not lines[0]:
            lines.pop(0)

    result = clean_text("\n".join(lines))
    if not result:
        return cleaned_text, 0
    return result, removed


def _title_comparison_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", clean_text(value))
    normalized = re.sub(r"\s+", "", normalized)
    return normalized.rstrip("。.!！?？")
