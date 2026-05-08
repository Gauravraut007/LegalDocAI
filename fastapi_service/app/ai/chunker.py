"""Legal-aware recursive chunker.

Strategy
========
1. Split the OCR'd full text on legal section headers via regex; track a
   running ``section_path`` (e.g. "Article 3 > Section 3.2 > (a)").
2. Within each section, segment into sentences with ``pysbd`` (English).
3. Pack sentences into chunks targeting ~CHUNK_SIZE_TOKENS tokens with
   CHUNK_OVERLAP_TOKENS overlap; never split a sentence; never exceed
   1.6 * CHUNK_SIZE_TOKENS.
4. Map char offsets back to page numbers using OCR's ``page_spans``.
5. Drop empty / whitespace-only / duplicate (sha256) chunks.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

from app.config import settings
from app.ai.ocr_engine import OCRResult

logger = logging.getLogger(__name__)


# =========================================================================
# Token counter
# =========================================================================
class _TokenCounter:
    def __init__(self) -> None:
        self._enc = None
        try:
            import tiktoken

            self._enc = tiktoken.get_encoding("cl100k_base")
        except Exception as exc:  # noqa: BLE001
            logger.warning("tiktoken unavailable; falling back to char/4: %s", exc)

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._enc is not None:
            try:
                return len(self._enc.encode(text))
            except Exception:  # noqa: BLE001
                pass
        return max(1, len(text) // 4)

    def slice_by_tokens(self, text: str, max_tokens: int, overlap: int) -> List[str]:
        if not text:
            return []
        if self._enc is None:
            char_max = max_tokens * 4
            char_overlap = overlap * 4
            step = max(1, char_max - char_overlap)
            return [text[i : i + char_max] for i in range(0, len(text), step)]
        ids = self._enc.encode(text)
        if len(ids) <= max_tokens:
            return [text]
        step = max(1, max_tokens - overlap)
        out: list[str] = []
        for start in range(0, len(ids), step):
            end = start + max_tokens
            out.append(self._enc.decode(ids[start:end]))
            if end >= len(ids):
                break
        return out


# =========================================================================
# Section splitting
# =========================================================================
@dataclass
class _Section:
    header: Optional[str]
    section_path: str
    text: str
    char_offset: int


# Top-level legal headers. Captured group 1 = header text.
_LEGAL_HEADER_RE = re.compile(
    r"^[ \t]*("
    r"(?:Article|Section|Clause|Schedule|Annex|Exhibit|Appendix|Part|Chapter)"
    r"\s+[IVXLCDM0-9]+(?:\.[0-9]+)*"
    r"(?:[ \t]*[\-:.][ \t]*[^\n]{0,200})?"
    r"|§\s*[0-9]+(?:\.[0-9]+)*(?:[ \t]*[\-:.][ \t]*[^\n]{0,200})?"
    r"|WHEREAS\b[^\n]{0,200}"
    r"|NOW[, ]+THEREFORE\b[^\n]{0,200}"
    r"|RECITALS?\b[^\n]{0,200}"
    r"|DEFINITIONS?\b[^\n]{0,200}"
    r")[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)

# Numbered subsection: "1.", "1.1", "1.1.2", "(a)", "(i)"
_NUM_HEADING_RE = re.compile(
    r"^[ \t]*("
    r"\d+(?:\.\d+){0,4}\.?"
    r"|\([a-z]\)"
    r"|\([ivx]+\)"
    r")[ \t]+(?=[A-Z\"'])",
    re.MULTILINE,
)

# ALL-CAPS line of 3-80 chars (a likely header).
_CAPS_HEADER_RE = re.compile(r"^[ \t]*([A-Z][A-Z0-9 \-,&/.]{2,80})[ \t]*$", re.MULTILINE)


def _build_section_path(stack: list[str], new_header: str) -> tuple[list[str], str]:
    """Append `new_header` to `stack` based on its depth; return new stack and joined path."""
    # Determine depth from leading numeric pattern, else assume top-level.
    depth_match = re.match(r"\s*(\d+(?:\.\d+){0,4})", new_header)
    if depth_match:
        depth = depth_match.group(1).count(".") + 1
    elif new_header.lower().startswith(
        ("article", "section", "clause", "part", "chapter", "schedule", "exhibit", "annex")
    ):
        depth = 1
    elif new_header.lower().startswith(("whereas", "now therefore", "now, therefore", "recital")):
        depth = 1
    else:
        depth = max(1, len(stack))
    new_stack = stack[: depth - 1] + [new_header.strip()]
    return new_stack, " > ".join(new_stack)


def split_sections(text: str) -> list[_Section]:
    """Split full text into sections using legal headers; preserve section_path."""
    if not text:
        return []

    # Collect (start, header_line) tuples for all heading kinds, then sort by position.
    matches: list[tuple[int, str]] = []
    for rx in (_LEGAL_HEADER_RE, _NUM_HEADING_RE, _CAPS_HEADER_RE):
        for m in rx.finditer(text):
            header = m.group(1).strip()
            if 1 <= len(header) <= 240:
                matches.append((m.start(), header))

    if not matches:
        return [_Section(header=None, section_path="", text=text, char_offset=0)]

    # Deduplicate by start position, keep the longest header at the same offset.
    matches.sort(key=lambda t: (t[0], -len(t[1])))
    seen: dict[int, str] = {}
    for start, hdr in matches:
        seen.setdefault(start, hdr)
    ordered = sorted(seen.items())

    # First slice (anything before the first header) goes under empty path.
    sections: list[_Section] = []
    if ordered[0][0] > 0:
        sections.append(
            _Section(header=None, section_path="", text=text[: ordered[0][0]], char_offset=0)
        )

    stack: list[str] = []
    for i, (start, header) in enumerate(ordered):
        end = ordered[i + 1][0] if i + 1 < len(ordered) else len(text)
        stack, path = _build_section_path(stack, header)
        sections.append(
            _Section(
                header=header,
                section_path=path,
                text=text[start:end],
                char_offset=start,
            )
        )
    return sections


# =========================================================================
# Sentence splitter
# =========================================================================
class _SentenceSplitter:
    def __init__(self) -> None:
        self._segmenter = None
        try:
            import pysbd

            self._segmenter = pysbd.Segmenter(language="en", clean=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("pysbd unavailable; using regex sentence splitter: %s", exc)

    def split(self, text: str) -> list[str]:
        if not text:
            return []
        if self._segmenter is not None:
            try:
                return [s for s in self._segmenter.segment(text) if s.strip()]
            except Exception as exc:  # noqa: BLE001
                logger.debug("pysbd failed: %s", exc)
        return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


# =========================================================================
# Chunk
# =========================================================================
@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    ordinal: int
    text: str
    token_count: int
    char_start: int
    char_end: int
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    section_path: Optional[str] = None
    hash: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "ordinal": self.ordinal,
            "text": self.text,
            "token_count": self.token_count,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "section_path": self.section_path,
            "hash": self.hash,
        }


# =========================================================================
# Chunker
# =========================================================================
class DocumentChunker:
    HARD_TOKEN_CAP = 800  # never exceed this per chunk

    def __init__(
        self,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        min_tokens: int | None = None,
    ) -> None:
        self.chunk_size = chunk_size or settings.CHUNK_SIZE_TOKENS or 500
        self.chunk_overlap = chunk_overlap or settings.CHUNK_OVERLAP_TOKENS or 80
        self.min_tokens = min_tokens or settings.MIN_CHUNK_TOKENS or 50
        self.tokens = _TokenCounter()
        self.sentences = _SentenceSplitter()

    # ------------------------------------------------------------- PUBLIC
    def chunk(self, ocr_result: OCRResult, document_id: str) -> List[Chunk]:
        text = ocr_result.full_text or ""
        if not text.strip():
            return []

        sections = split_sections(text)
        page_spans = ocr_result.page_spans or [(1, 0, len(text))]
        chunks: List[Chunk] = []
        seen_hashes: set[str] = set()
        ordinal = 0

        for section in sections:
            for piece, char_start, char_end in self._pack_section(section):
                clean = piece.strip()
                if not clean:
                    continue
                tokens = self.tokens.count(clean)
                if tokens < self.min_tokens and chunks:
                    # Merge into the previous chunk if it stays under the cap.
                    prev = chunks[-1]
                    if prev.token_count + tokens <= self.HARD_TOKEN_CAP and (
                        prev.section_path == section.section_path
                    ):
                        prev.text = (prev.text + "\n\n" + clean).strip()
                        prev.token_count = self.tokens.count(prev.text)
                        prev.char_end = char_end
                        prev.page_end = self._lookup_page(char_end, page_spans)
                        prev.hash = hashlib.sha256(prev.text.encode("utf-8")).hexdigest()
                        seen_hashes.discard(prev.hash)
                        seen_hashes.add(prev.hash)
                        continue

                h = hashlib.sha256(clean.encode("utf-8")).hexdigest()
                if h in seen_hashes:
                    continue
                seen_hashes.add(h)

                chunks.append(
                    Chunk(
                        chunk_id=str(uuid.uuid4()),
                        document_id=str(document_id),
                        ordinal=ordinal,
                        text=clean,
                        token_count=tokens,
                        char_start=char_start,
                        char_end=char_end,
                        page_start=self._lookup_page(char_start, page_spans),
                        page_end=self._lookup_page(max(char_start, char_end - 1), page_spans),
                        section_path=section.section_path or None,
                        hash=h,
                    )
                )
                ordinal += 1

        logger.info("chunked document %s into %d chunks", document_id, len(chunks))
        return chunks

    # ------------------------------------------------------------- PACK
    def _pack_section(self, section: _Section) -> list[tuple[str, int, int]]:
        """Yield (text, abs_char_start, abs_char_end) chunks for one section."""
        sentences = self.sentences.split(section.text)
        if not sentences:
            return []

        # Compute (sentence, abs_char_start, abs_char_end) per sentence.
        sent_spans: list[tuple[str, int, int]] = []
        cursor = 0
        for sent in sentences:
            idx = section.text.find(sent, cursor)
            if idx == -1:
                idx = cursor
            abs_start = section.char_offset + idx
            abs_end = abs_start + len(sent)
            sent_spans.append((sent, abs_start, abs_end))
            cursor = idx + len(sent)

        out: list[tuple[str, int, int]] = []
        cur_text: list[str] = []
        cur_tokens = 0
        cur_start: Optional[int] = None
        cur_end: Optional[int] = None

        def flush() -> None:
            nonlocal cur_text, cur_tokens, cur_start, cur_end
            if cur_text and cur_start is not None and cur_end is not None:
                joined = " ".join(cur_text).strip()
                if joined:
                    out.append((joined, cur_start, cur_end))
            cur_text, cur_tokens, cur_start, cur_end = [], 0, None, None

        for sent, s_start, s_end in sent_spans:
            sent_tokens = self.tokens.count(sent)

            # Sentence is itself larger than the hard cap → split it directly.
            if sent_tokens > self.HARD_TOKEN_CAP:
                flush()
                for sub in self.tokens.slice_by_tokens(
                    sent, self.chunk_size, self.chunk_overlap
                ):
                    out.append((sub.strip(), s_start, s_end))
                continue

            if cur_tokens + sent_tokens > self.chunk_size and cur_text:
                flush()
                # Carry overlap: re-include the tail of the previous chunk.
                if out and self.chunk_overlap > 0:
                    tail_text = self.tokens.slice_by_tokens(
                        out[-1][0], self.chunk_overlap, 0
                    )[-1]
                    cur_text = [tail_text]
                    cur_tokens = self.tokens.count(tail_text)
                    cur_start = out[-1][2] - len(tail_text)
                    cur_end = out[-1][2]

            if cur_start is None:
                cur_start = s_start
            cur_text.append(sent)
            cur_tokens += sent_tokens
            cur_end = s_end

            if cur_tokens >= self.HARD_TOKEN_CAP:
                flush()

        flush()
        return out

    # -------------------------------------------------------- PAGE LOOKUP
    @staticmethod
    def _lookup_page(char_pos: int, page_spans: list[tuple[int, int, int]]) -> Optional[int]:
        if not page_spans:
            return None
        for page_num, start, end in page_spans:
            if start <= char_pos < end:
                return page_num
        # Past the end: return last known page.
        return page_spans[-1][0]
