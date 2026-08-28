from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field


BLANK_LINE_RE = re.compile(r"\n[ \t]*\n+")
PRIMARY_BREAK_RE = re.compile(r"[。！？!?；;](?:[”’」』】）》])?")
SECONDARY_BREAK_RE = re.compile(r"[，,、：:](?:[”’」』】）》])?")
RESEARCH_HEADING_RE = re.compile(
    r"(?<![\u4e00-\u9fffA-Za-z0-9])"
    r"(?P<title>盈利预测与投资评级|事件概述|投资要点|主要观点|核心观点|"
    r"分析判断|盈利预测|投资建议|风险提示|事件)\s*[：:]"
)
CHINESE_NUMERALS = "一二三四五六七八九十百"
CIRCLED_NUMERALS = "①②③④⑤⑥⑦⑧⑨⑩"
STRUCTURAL_RESIDUE_RE = re.compile(
    rf"^[\s?？!！:：;；,.，。、…·\-—_()（）\[\]【】/\\0-9{CHINESE_NUMERALS}{CIRCLED_NUMERALS}]*$"
)


@dataclass(frozen=True)
class ChunkingConfig:
    target_chars: int = 600
    min_chars: int = 200
    soft_max_chars: int = 900
    hard_max_chars: int = 1200

    def __post_init__(self) -> None:
        if not 0 < self.min_chars <= self.target_chars <= self.soft_max_chars:
            raise ValueError("Expected min_chars <= target_chars <= soft_max_chars")
        if self.soft_max_chars > self.hard_max_chars:
            raise ValueError("soft_max_chars cannot exceed hard_max_chars")


@dataclass(frozen=True)
class ChunkPiece:
    section_title: str | None
    char_start: int
    text: str


@dataclass
class ChunkingResult:
    pieces: list[ChunkPiece]
    stats: Counter[str] = field(default_factory=Counter)


@dataclass(frozen=True)
class HeadingMarker:
    start: int
    title: str
    level: int


def chunk_text(
    text: str,
    *,
    document_type: str,
    config: ChunkingConfig | None = None,
) -> ChunkingResult:
    config = config or ChunkingConfig()
    if not text or not text.strip():
        return ChunkingResult([])

    stats: Counter[str] = Counter()
    markers = find_heading_markers(text, document_type=document_type)
    stats["headings_detected"] = len(markers)
    regions = build_regions(text, markers, min_chars=config.min_chars, stats=stats)
    pieces: list[ChunkPiece] = []
    for start, end, section_title in regions:
        region_pieces = chunk_region(
            text,
            start=start,
            end=end,
            section_title=section_title,
            config=config,
            stats=stats,
        )
        pieces.extend(region_pieces)

    stats["chunks"] = len(pieces)
    stats["chunks_with_section_title"] = sum(piece.section_title is not None for piece in pieces)
    stats["short_chunks"] = sum(len(piece.text) < config.min_chars for piece in pieces)
    return ChunkingResult(pieces, stats)


def find_heading_markers(text: str, *, document_type: str) -> list[HeadingMarker]:
    raw_markers: dict[int, tuple[str, int]] = {}
    offset = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        stripped = content.strip()
        if stripped:
            classified = classify_line_heading(stripped)
            if classified is not None:
                level, title = classified
                start = offset + len(content) - len(content.lstrip())
                raw_markers[start] = (title, level)
        offset += len(line)

    if document_type == "research_report":
        for match in RESEARCH_HEADING_RE.finditer(text):
            start = match.start("title")
            raw_markers.setdefault(start, (match.group("title"), 1))

    levels: dict[int, str] = {}
    markers: list[HeadingMarker] = []
    for start, (title, level) in sorted(raw_markers.items()):
        for existing_level in [value for value in levels if value >= level]:
            del levels[existing_level]
        levels[level] = title.rstrip("：:").strip()
        section_path = " / ".join(levels[value] for value in sorted(levels))
        markers.append(HeadingMarker(start=start, title=section_path, level=level))
    return markers


def classify_line_heading(line: str) -> tuple[int, str] | None:
    if not line or len(line) > 80:
        return None

    patterns: list[tuple[int, str]] = [
        (1, rf"^[{CHINESE_NUMERALS}]+[、.．]\s*.+"),
        (2, rf"^[（(][{CHINESE_NUMERALS}]+[）)]\s*.+"),
        (1, rf"^第[{CHINESE_NUMERALS}0-9]+章\s*.*"),
        (2, rf"^第[{CHINESE_NUMERALS}0-9]+节\s*.*"),
        (3, rf"^[{CIRCLED_NUMERALS}]\s*.+"),
    ]
    for level, pattern in patterns:
        if re.match(pattern, line):
            if level == 3 and line.endswith(("。", "；", ";")):
                return None
            return level, line

    numeric_heading = re.match(
        r"^(?:[0-9]{1,2}(?:\.[0-9]{1,2})+(?:[、．]|\.\s+|\s+)|"
        r"[0-9]{1,2}(?:[、．]|\.\s*))(?P<title>.+)$",
        line,
    )
    if numeric_heading is not None:
        title = numeric_heading.group("title").strip()
        if title and not title[0].isdigit() and not line.endswith(("。", "；", ";")):
            return 3, line

    explicit = line.rstrip("：:").strip()
    if explicit in {
        "事件",
        "事件概述",
        "投资要点",
        "主要观点",
        "核心观点",
        "分析判断",
        "盈利预测",
        "盈利预测与投资评级",
        "投资建议",
        "风险提示",
    }:
        return 1, explicit
    return None


def build_regions(
    text: str,
    markers: list[HeadingMarker],
    *,
    min_chars: int,
    stats: Counter[str],
) -> list[tuple[int, int, str | None]]:
    if not markers:
        start, end = trim_span(text, 0, len(text))
        return [(start, end, None)] if start < end else []

    regions: list[tuple[int, int, str | None]] = []
    first_start, first_end = trim_span(text, 0, markers[0].start)
    pending_start: int | None = None
    if first_start < first_end:
        if first_end - first_start < min_chars:
            pending_start = first_start
        else:
            regions.append((first_start, first_end, None))
    for index, marker in enumerate(markers):
        raw_end = markers[index + 1].start if index + 1 < len(markers) else len(text)
        own_start, own_end = trim_span(text, marker.start, raw_end)
        start = pending_start if pending_start is not None else own_start
        end = own_end
        structural_fragment = is_structural_fragment(
            text[own_start:own_end],
            section_title=marker.title,
        )
        if structural_fragment:
            stats["structural_fragments_merged"] += 1
        if structural_fragment and index + 1 < len(markers):
            pending_start = start
            continue
        if structural_fragment and regions:
            previous_start, _, previous_title = regions[-1]
            regions[-1] = (previous_start, end, previous_title)
            pending_start = None
            continue
        if start < end:
            regions.append((start, end, marker.title))
        pending_start = None
    return regions


def is_structural_fragment(value: str, *, section_title: str) -> bool:
    stripped = value.strip()
    leaf_title = section_title.rsplit(" / ", maxsplit=1)[-1].strip()
    if stripped.startswith(leaf_title):
        stripped = stripped[len(leaf_title) :].lstrip()
    stripped = stripped.lstrip("：:").strip()
    for entity in ("&gt;", "&lt;", "&nbsp;"):
        stripped = stripped.replace(entity, "")
    return STRUCTURAL_RESIDUE_RE.fullmatch(stripped) is not None


def chunk_region(
    text: str,
    *,
    start: int,
    end: int,
    section_title: str | None,
    config: ChunkingConfig,
    stats: Counter[str],
) -> list[ChunkPiece]:
    paragraphs = paragraph_spans(text, start=start, end=end)
    stats["paragraphs_detected"] += len(paragraphs)
    atomic_spans: list[tuple[int, int]] = []
    for paragraph_start, paragraph_end in paragraphs:
        if paragraph_end - paragraph_start <= config.hard_max_chars:
            atomic_spans.append((paragraph_start, paragraph_end))
            continue
        stats["long_paragraphs_split"] += 1
        atomic_spans.extend(
            split_long_span(
                text,
                start=paragraph_start,
                end=paragraph_end,
                config=config,
                stats=stats,
            )
        )

    packed = pack_spans(text, atomic_spans, config=config)
    return [
        ChunkPiece(
            section_title=section_title,
            char_start=piece_start,
            text=text[piece_start:piece_end],
        )
        for piece_start, piece_end in packed
    ]


def paragraph_spans(text: str, *, start: int, end: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = start
    for match in BLANK_LINE_RE.finditer(text, start, end):
        paragraph_start, paragraph_end = trim_span(text, cursor, match.start())
        if paragraph_start < paragraph_end:
            spans.append((paragraph_start, paragraph_end))
        cursor = match.end()
    paragraph_start, paragraph_end = trim_span(text, cursor, end)
    if paragraph_start < paragraph_end:
        spans.append((paragraph_start, paragraph_end))
    return spans


def split_long_span(
    text: str,
    *,
    start: int,
    end: int,
    config: ChunkingConfig,
    stats: Counter[str],
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = start
    while end - cursor > config.hard_max_chars:
        lower = min(cursor + config.min_chars, end)
        upper = min(cursor + config.soft_max_chars, end)
        target = min(cursor + config.target_chars, end)
        boundary = choose_boundary(text, lower=lower, upper=upper, target=target, pattern=PRIMARY_BREAK_RE)
        if boundary is not None:
            stats["sentence_boundaries_used"] += 1
        else:
            boundary = choose_boundary(
                text,
                lower=lower,
                upper=upper,
                target=target,
                pattern=SECONDARY_BREAK_RE,
            )
            if boundary is not None:
                stats["secondary_boundaries_used"] += 1
        if boundary is None:
            boundary = target
            stats["forced_boundaries_used"] += 1
        piece_start, piece_end = trim_span(text, cursor, boundary)
        if piece_start < piece_end:
            spans.append((piece_start, piece_end))
        cursor = boundary

    piece_start, piece_end = trim_span(text, cursor, end)
    if piece_start < piece_end:
        spans.append((piece_start, piece_end))
    return spans


def choose_boundary(
    text: str,
    *,
    lower: int,
    upper: int,
    target: int,
    pattern: re.Pattern[str],
) -> int | None:
    candidates = [match.end() for match in pattern.finditer(text, lower, upper)]
    if not candidates:
        return None
    return min(candidates, key=lambda value: (abs(value - target), -value))


def pack_spans(
    text: str,
    spans: list[tuple[int, int]],
    *,
    config: ChunkingConfig,
) -> list[tuple[int, int]]:
    if not spans:
        return []
    packed: list[tuple[int, int]] = []
    current_start, current_end = spans[0]
    for next_start, next_end in spans[1:]:
        current_length = current_end - current_start
        next_length = next_end - next_start
        merged_length = next_end - current_start
        should_merge = (
            merged_length <= config.soft_max_chars
            and (current_length < config.target_chars or next_length < config.min_chars)
        ) or (current_length < config.min_chars and merged_length <= config.hard_max_chars)
        if should_merge and not text[current_end:next_start].strip():
            current_end = next_end
            continue
        packed.append((current_start, current_end))
        current_start, current_end = next_start, next_end
    packed.append((current_start, current_end))
    return packed


def trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end
