"""Streaming file uploads with SHA-256, MIME detection, dedupe and safe paths.

All disk writes are streamed in fixed-size chunks so we never load full
files into memory. Files for a given owner/document live under:
    {UPLOAD_DIR}/{owner_id}/{document_id}/original{ext}
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import UploadFile

from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- CONSTANTS
_CHUNK_SIZE = 1024 * 1024  # 1 MiB

ALLOWED_MIME_TO_EXT: dict[str, str] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
    "text/plain": "txt",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/tiff": "tiff",
    "image/x-tiff": "tiff",
}

EXT_TO_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

# python-magic is optional in Windows dev; sniff with extension fallback.
try:  # pragma: no cover - optional dep
    import magic  # type: ignore

    _HAS_MAGIC = True
except Exception:  # noqa: BLE001
    _HAS_MAGIC = False


# ---------------------------------------------------------------- DATA
@dataclass
class StoredUpload:
    stored_path: str
    size_bytes: int
    sha256: str
    mime_type: str
    extension: str


# ---------------------------------------------------------------- ERRORS
class UploadValidationError(ValueError):
    """Raised when an upload fails validation (size, MIME, etc.)."""


# ---------------------------------------------------------------- HELPERS
def _sanitize_filename(name: str) -> str:
    name = os.path.basename(name or "")
    name = re.sub(r"[^A-Za-z0-9._\- ]", "_", name).strip()
    return name[:255] or "upload"


def _ext_from_mime(mime: str, original_filename: str) -> str:
    if mime in ALLOWED_MIME_TO_EXT:
        return ALLOWED_MIME_TO_EXT[mime]
    ext = Path(original_filename).suffix.lower().lstrip(".")
    return ext or "bin"


def _detect_mime(head_bytes: bytes, filename: str) -> str:
    if _HAS_MAGIC:
        try:
            mime = magic.from_buffer(head_bytes, mime=True)  # type: ignore[union-attr]
            if mime:
                return mime
        except Exception as exc:  # noqa: BLE001
            logger.debug("magic.from_buffer failed: %s", exc)
    return EXT_TO_MIME.get(Path(filename).suffix.lower(), "application/octet-stream")


def _safe_path(base_dir: str, owner_id: uuid.UUID, document_id: uuid.UUID, ext: str) -> str:
    base = Path(base_dir).resolve()
    target_dir = (base / str(owner_id) / str(document_id)).resolve()
    if not str(target_dir).startswith(str(base) + os.sep) and target_dir != base:
        raise UploadValidationError("Invalid upload path")
    target_dir.mkdir(parents=True, exist_ok=True)
    return str(target_dir / f"original.{ext.lstrip('.')}")


# ---------------------------------------------------------------- API
class FileHandler:
    """High-level upload / lookup operations."""

    @staticmethod
    def sanitize_filename(name: str) -> str:
        return _sanitize_filename(name)

    @staticmethod
    def detect_mime(head_bytes: bytes, filename: str) -> str:
        return _detect_mime(head_bytes, filename)

    @staticmethod
    async def stream_upload_to_disk(
        upload: UploadFile,
        owner_id: uuid.UUID,
        document_id: uuid.UUID,
        *,
        max_bytes: Optional[int] = None,
        upload_dir: Optional[str] = None,
    ) -> StoredUpload:
        """Stream `upload` to disk, computing SHA-256 in flight.

        Validates MIME (sniffs first chunk), enforces max size, prevents
        path traversal. Returns the stored path + checksum + sniffed MIME.
        """
        max_bytes = max_bytes or settings.max_file_size_bytes
        base_dir = upload_dir or settings.UPLOAD_DIR
        original_name = _sanitize_filename(upload.filename or "upload")

        hasher = hashlib.sha256()
        size_bytes = 0
        sniffed_mime: Optional[str] = None
        ext: Optional[str] = None
        target_path: Optional[str] = None
        out_handle = None

        try:
            while True:
                chunk = await upload.read(_CHUNK_SIZE)
                if not chunk:
                    break

                if sniffed_mime is None:
                    sniffed_mime = _detect_mime(chunk[:4096], original_name)
                    if sniffed_mime not in ALLOWED_MIME_TO_EXT:
                        raise UploadValidationError(
                            f"Unsupported MIME type: {sniffed_mime}"
                        )
                    ext = _ext_from_mime(sniffed_mime, original_name)
                    target_path = _safe_path(base_dir, owner_id, document_id, ext)
                    out_handle = await aiofiles.open(target_path, "wb")

                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise UploadValidationError(
                        f"File exceeds max size of {max_bytes} bytes"
                    )
                hasher.update(chunk)
                assert out_handle is not None  # for type-checkers
                await out_handle.write(chunk)
        finally:
            if out_handle is not None:
                await out_handle.close()

        if size_bytes == 0:
            # Clean up empty placeholder files.
            if target_path and os.path.exists(target_path):
                try:
                    os.remove(target_path)
                except OSError:
                    pass
            raise UploadValidationError("Empty file")

        assert sniffed_mime and ext and target_path  # narrow types
        return StoredUpload(
            stored_path=target_path,
            size_bytes=size_bytes,
            sha256=hasher.hexdigest(),
            mime_type=sniffed_mime,
            extension=ext,
        )

    @staticmethod
    def remove_document_dir(owner_id: uuid.UUID, document_id: uuid.UUID) -> None:
        """Recursively remove a document's storage directory."""
        target = Path(settings.UPLOAD_DIR).resolve() / str(owner_id) / str(document_id)
        if not target.exists():
            return
        try:
            for root, dirs, files in os.walk(target, topdown=False):
                for f in files:
                    try:
                        os.remove(os.path.join(root, f))
                    except OSError:
                        pass
                for d in dirs:
                    try:
                        os.rmdir(os.path.join(root, d))
                    except OSError:
                        pass
            os.rmdir(target)
        except OSError as exc:
            logger.warning("Failed to remove %s: %s", target, exc)
