"""LOCAL OCR engine.

Strategy:
- PDF       : pdfplumber per-page; if a page yields fewer than `OCR_FALLBACK_THRESHOLD`
              characters it is rasterised with pdf2image and OCR'd by pytesseract.
- DOCX      : python-docx (paragraphs + tables, preserving order).
- TXT/legacy: chardet-based decode.
- Images    : pytesseract directly with light pre-processing.

Each page goes through a worker thread with a per-page timeout and tenacity
retry around transient failures.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from dataclasses import dataclass, field
from typing import Any, List

from app.utils.retry import io_retry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- DATA
@dataclass
class PageResult:
    page_number: int
    text: str
    method: str
    char_count: int = 0

    def __post_init__(self) -> None:
        self.char_count = len(self.text or "")


@dataclass
class OCRResult:
    full_text: str
    pages: List[PageResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    language: str = "und"
    page_spans: list[tuple[int, int, int]] = field(default_factory=list)
    """List of (page_number, char_start, char_end) for `full_text`."""

    @property
    def total_pages(self) -> int:
        return len(self.pages)


# ---------------------------------------------------------------- ENGINE
class OCREngine:
    OCR_FALLBACK_THRESHOLD = 50  # chars/page below which we OCR
    PAGE_TIMEOUT_SECONDS = 90
    OCR_DPI = 220
    PDF_PAGE_WORKERS = 2

    def __init__(self, tesseract_lang: str = "eng") -> None:
        self.tesseract_lang = tesseract_lang

    # ------------------------------------------------------------- PUBLIC
    async def process(self, file_path: str, mime_type: str) -> OCRResult:
        ext = self._classify(mime_type, file_path)
        if ext == "pdf":
            result = await asyncio.to_thread(self._sync_process_pdf, file_path)
        elif ext == "docx":
            result = await asyncio.to_thread(self._sync_process_docx, file_path)
        elif ext == "doc":
            result = await asyncio.to_thread(self._sync_process_txt, file_path, "doc")
        elif ext == "txt":
            result = await asyncio.to_thread(self._sync_process_txt, file_path, "txt")
        elif ext in {"png", "jpg", "jpeg", "tiff", "tif", "bmp"}:
            result = await asyncio.to_thread(self._sync_process_image, file_path)
        else:
            raise ValueError(f"Unsupported MIME for OCR: {mime_type}")
        result.language = self._detect_language(result.full_text)
        return result

    # ------------------------------------------------------------ HELPERS
    @staticmethod
    def _classify(mime_type: str, path: str) -> str:
        mime = (mime_type or "").lower()
        if "pdf" in mime:
            return "pdf"
        if "wordprocessingml" in mime or path.lower().endswith(".docx"):
            return "docx"
        if "msword" in mime or path.lower().endswith(".doc"):
            return "doc"
        if "text/plain" in mime or path.lower().endswith(".txt"):
            return "txt"
        if "image/" in mime:
            ext = os.path.splitext(path)[1].lower().lstrip(".")
            return ext or "png"
        return os.path.splitext(path)[1].lower().lstrip(".")

    @staticmethod
    def _join_pages(pages: List[PageResult]) -> tuple[str, list[tuple[int, int, int]]]:
        """Concatenate page texts, returning the full text and per-page char spans."""
        parts: list[str] = []
        spans: list[tuple[int, int, int]] = []
        offset = 0
        sep = "\n\n"
        for i, p in enumerate(pages):
            block = p.text or ""
            start = offset
            end = offset + len(block)
            spans.append((p.page_number, start, end))
            parts.append(block)
            offset = end + (len(sep) if i < len(pages) - 1 else 0)
        return sep.join(parts), spans

    # ---------------------------------------------------------------- PDF
    @io_retry(max_attempts=2)
    def _sync_process_pdf(self, file_path: str) -> OCRResult:
        import pdfplumber

        pages: List[PageResult] = []
        ocr_pages: List[int] = []

        with pdfplumber.open(file_path) as pdf:
            for idx, page in enumerate(pdf.pages, start=1):
                try:
                    text = page.extract_text() or ""
                except Exception as exc:  # noqa: BLE001
                    logger.warning("pdfplumber page %s failed: %s", idx, exc)
                    text = ""
                if len(text.strip()) < self.OCR_FALLBACK_THRESHOLD:
                    ocr_pages.append(idx)
                    pages.append(PageResult(idx, "", "pending_ocr"))
                else:
                    pages.append(PageResult(idx, text, "pdfplumber"))

        if ocr_pages:
            self._ocr_pdf_pages(file_path, pages, ocr_pages)

        full_text, spans = self._join_pages(pages)
        meta = {
            "page_count": len(pages),
            "ocr_pages": ocr_pages,
            "extraction_methods": sorted({p.method for p in pages}),
        }
        return OCRResult(
            full_text=full_text, pages=pages, metadata=meta, page_spans=spans
        )

    def _ocr_pdf_pages(
        self, file_path: str, pages: List[PageResult], ocr_pages: List[int]
    ) -> None:
        try:
            from pdf2image import convert_from_path
        except Exception as exc:  # noqa: BLE001
            logger.error("pdf2image unavailable: %s", exc)
            return

        # Convert only the pages we need (page-range is 1-based and inclusive).
        first, last = min(ocr_pages), max(ocr_pages)
        try:
            images = convert_from_path(
                file_path, dpi=self.OCR_DPI, first_page=first, last_page=last
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("pdf2image rasterisation failed: %s", exc)
            return

        with ThreadPoolExecutor(max_workers=self.PDF_PAGE_WORKERS) as pool:
            futures = {}
            for offset, image in enumerate(images):
                page_num = first + offset
                if page_num not in ocr_pages:
                    continue
                futures[pool.submit(self._tesseract_pil, image)] = page_num

            for fut, page_num in futures.items():
                try:
                    text = fut.result(timeout=self.PAGE_TIMEOUT_SECONDS)
                except FutTimeout:
                    logger.warning("OCR timeout on page %s", page_num)
                    text = ""
                except Exception as exc:  # noqa: BLE001
                    logger.warning("OCR failed on page %s: %s", page_num, exc)
                    text = ""
                for p in pages:
                    if p.page_number == page_num:
                        p.text = text
                        p.method = "tesseract"
                        p.char_count = len(text)
                        break

    # --------------------------------------------------------------- DOCX
    @io_retry(max_attempts=2)
    def _sync_process_docx(self, file_path: str) -> OCRResult:
        from docx import Document

        doc = Document(file_path)
        parts: list[str] = []
        for element in doc.element.body.iter():
            tag = element.tag.split("}", 1)[-1]
            if tag == "p":
                txt = "".join(node.text or "" for node in element.iter() if node.tag.endswith("}t"))
                if txt.strip():
                    parts.append(txt.strip())
            elif tag == "tbl":
                for row in element.iter():
                    if row.tag.endswith("}tr"):
                        cells = []
                        for cell in row.iter():
                            if cell.tag.endswith("}tc"):
                                cells.append(
                                    " ".join(
                                        (n.text or "")
                                        for n in cell.iter()
                                        if n.tag.endswith("}t")
                                    ).strip()
                                )
                        if any(cells):
                            parts.append(" | ".join(cells))

        full_text = "\n".join(parts)
        page = PageResult(page_number=1, text=full_text, method="docx")
        return OCRResult(
            full_text=full_text,
            pages=[page],
            metadata={
                "page_count": 1,
                "paragraph_count": len(doc.paragraphs),
                "table_count": len(doc.tables),
            },
            page_spans=[(1, 0, len(full_text))],
        )

    # ----------------------------------------------------------- TXT/DOC
    def _sync_process_txt(self, file_path: str, kind: str) -> OCRResult:
        import chardet

        with open(file_path, "rb") as f:
            raw = f.read()
        enc = (chardet.detect(raw) or {}).get("encoding") or "utf-8"
        try:
            text = raw.decode(enc, errors="replace")
        except Exception:  # noqa: BLE001
            text = raw.decode("utf-8", errors="replace")
        page = PageResult(page_number=1, text=text, method=kind)
        return OCRResult(
            full_text=text,
            pages=[page],
            metadata={"page_count": 1, "detected_encoding": enc},
            page_spans=[(1, 0, len(text))],
        )

    # ----------------------------------------------------------- IMAGE
    @io_retry(max_attempts=2)
    def _sync_process_image(self, file_path: str) -> OCRResult:
        from PIL import Image

        with Image.open(file_path) as img:
            text = self._tesseract_pil(img)
            page = PageResult(page_number=1, text=text, method="tesseract")
            return OCRResult(
                full_text=text,
                pages=[page],
                metadata={"page_count": 1, "image_mode": img.mode, "image_size": list(img.size)},
                page_spans=[(1, 0, len(text))],
            )

    # ---------------------------------------------------------- TESS
    def _tesseract_pil(self, image: Any) -> str:
        try:
            import pytesseract
            from PIL import ImageEnhance, ImageFilter, ImageOps
        except Exception as exc:  # noqa: BLE001
            logger.error("OCR deps unavailable: %s", exc)
            return ""
        try:
            img = image
            if img.mode != "L":
                img = img.convert("L")
            img = ImageEnhance.Contrast(img).enhance(1.5)
            img = img.filter(ImageFilter.MedianFilter(size=3))
            img = ImageOps.autocontrast(img, cutoff=2)
            return pytesseract.image_to_string(
                img, lang=self.tesseract_lang, config="--oem 3 --psm 6"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("tesseract OCR failed: %s", exc)
            return ""

    # ---------------------------------------------------------- LANGUAGE
    @staticmethod
    def _detect_language(text: str) -> str:
        if not text or len(text.strip()) < 30:
            return "und"
        try:
            from langdetect import DetectorFactory, detect

            DetectorFactory.seed = 0
            return detect(text[:5000])
        except Exception:  # noqa: BLE001
            return "und"
