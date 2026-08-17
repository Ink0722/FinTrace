import re
from pathlib import Path


def chunk_pages(
    pages: list[dict],
    *,
    doc_id: str,
    max_chars: int = 900,
    overlap_chars: int = 120,
) -> list[dict]:
    chunks: list[dict] = []
    chunk_index = 1
    for page in pages:
        text = normalize_text(str(page.get("text") or ""))
        if not text:
            continue
        for part, section_title in split_text_with_sections(text, max_chars=max_chars, overlap_chars=overlap_chars):
            chunks.append(
                {
                    "chunk_id": f"{doc_id}-C{chunk_index:04d}",
                    "chunk_index": chunk_index,
                    "page_start": page.get("page"),
                    "page_end": page.get("page"),
                    "section_title": section_title,
                    "text": part,
                }
            )
            chunk_index += 1
    return chunks


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_text(text: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    return [chunk for chunk, _ in split_text_with_sections(text, max_chars=max_chars, overlap_chars=overlap_chars)]


def split_text_with_sections(text: str, *, max_chars: int, overlap_chars: int) -> list[tuple[str, str | None]]:
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
    if not paragraphs:
        return []
    sections: list[tuple[str | None, list[str]]] = []
    current_title: str | None = None
    current_parts: list[str] = []
    for paragraph in paragraphs:
        first_line = paragraph.splitlines()[0].strip()
        if looks_like_heading(first_line):
            if current_parts:
                sections.append((current_title, current_parts))
                current_parts = []
            current_title = first_line
            continue
        current_parts.append(paragraph)
    if current_parts:
        sections.append((current_title, current_parts))

    chunks: list[tuple[str, str | None]] = []
    for section_title, parts in sections:
        section_text = "\n\n".join(parts)
        if len(section_text) <= max_chars:
            chunks.append((section_text, section_title))
        else:
            chunks.extend((part, section_title) for part in split_text_without_sections(section_text, max_chars=max_chars, overlap_chars=overlap_chars))
    return chunks


def split_text_without_sections(text: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(split_long_text(paragraph, max_chars=max_chars, overlap_chars=overlap_chars))
            continue
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = paragraph
    if current:
        chunks.append(current)
    return chunks


def looks_like_heading(line: str) -> bool:
    line = line.strip()
    if not line or len(line) > 60:
        return False
    patterns = [
        r"^[一二三四五六七八九十]+[、.．]\s*.+",
        r"^（[一二三四五六七八九十]+）\s*.+",
        r"^\([一二三四五六七八九十]+\)\s*.+",
        r"^第[一二三四五六七八九十0-9]+[章节]\s*.+",
        r"^问题[一二三四五六七八九十0-9]+[：:]\s*.+",
        r"^[0-9]+[.．、]\s*.+",
        r"^关键审计事项$",
        r"^管理层讨论与分析$",
    ]
    return any(re.match(pattern, line) for pattern in patterns)


def split_long_text(text: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(0, end - overlap_chars)
    return [chunk for chunk in chunks if chunk]


def stable_doc_id(path: Path, company_id: str, published_date: str, document_type: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", path.stem).strip("-")
    return f"DOC-{company_id}-{published_date}-{document_type}-{stem}"
