"""Resume storage, extraction and deletion for the AI job workbench.

Handles file validation, safe path construction, text extraction from
TXT/PDF/DOCX, content hashing and deletion.  All storage paths are
relative to the resume directory to avoid leaking absolute filesystem
locations.  Extracted text is never written to logs.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import uuid


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_FORMATS = {"txt", "pdf", "docx"}
MAX_TEXT_LENGTH = 50_000
DEFAULT_MAX_BYTES = 10_000_000  # 10 MB


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_format(filename) -> str:
    """Validate that *filename* has a txt/pdf/docx extension.

    Returns the canonical format string (lower-case, no dot).  Raises
    ``ValueError`` for any other extension or a filename without one.
    """
    if not filename or not isinstance(filename, str):
        raise ValueError("文件名不能为空")
    ext = pathlib.Path(filename).suffix.lower().lstrip(".")
    if ext not in ALLOWED_FORMATS:
        raise ValueError(f"不支持的简历格式: {ext or '无扩展名'}")
    return ext


def validate_size(file_bytes, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
    """Raise ``ValueError`` when *file_bytes* exceeds *max_bytes*."""
    if file_bytes is None:
        raise ValueError("文件内容不能为空")
    size = len(file_bytes)
    if size > max_bytes:
        raise ValueError(f"文件大小 {size} 超过限制 {max_bytes}")
    if size == 0:
        raise ValueError("文件内容不能为空")


# ---------------------------------------------------------------------------
# Safe path construction
# ---------------------------------------------------------------------------

def build_safe_path(resume_dir, resume_id, fmt: str) -> str:
    """Build a safe relative storage path inside *resume_dir*.

    Returns a relative path string (e.g. ``"abc123.pdf"``) that is
    guaranteed to resolve inside *resume_dir*.  Raises ``ValueError`` on
    any attempt to escape the resume directory via *resume_id* or *fmt*.
    """
    if fmt not in ALLOWED_FORMATS:
        raise ValueError(f"不支持的简历格式: {fmt}")
    rid = str(resume_id)
    # resume_id must be purely alphanumeric — rejects ../, separators, etc.
    if not rid or not rid.isalnum():
        raise ValueError("resume_id 含非法字符")
    relative_name = f"{rid}.{fmt}"
    base = pathlib.Path(os.fspath(resume_dir)).resolve()
    full = (base / relative_name).resolve()
    try:
        full.relative_to(base)
    except ValueError:
        raise ValueError("路径穿越攻击检测：存储路径超出简历目录")
    return relative_name


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text(path, fmt: str) -> str:
    """Extract text from a TXT/PDF/DOCX file at *path*.

    The result is truncated to ``MAX_TEXT_LENGTH`` characters.  The
    extracted text is never logged.
    """
    file_path = pathlib.Path(os.fspath(path))
    if fmt == "txt":
        text = file_path.read_text(encoding="utf-8", errors="replace")
    elif fmt == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(file_path))
        parts: list[str] = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        text = "\n".join(parts)
    elif fmt == "docx":
        from docx import Document

        doc = Document(str(file_path))
        text = "\n".join(para.text for para in doc.paragraphs)
    else:
        raise ValueError(f"不支持的简历格式: {fmt}")
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH]
    return text


# ---------------------------------------------------------------------------
# Save / delete
# ---------------------------------------------------------------------------

def save_resume(profile_id, file_bytes, filename, fmt, resume_dir, store) -> dict:
    """Validate, persist and extract a resume file.

    Steps:
      1. Validate the filename extension and file size.
      2. Build a safe storage path inside *resume_dir*.
      3. Write the raw bytes to disk.
      4. Extract text (truncated to ``MAX_TEXT_LENGTH``).
      5. Compute a sha256 content hash.
      6. Persist via ``store.save_resume``.

    Returns the resume record produced by the store.
    """
    validated_fmt = validate_format(filename)
    validate_size(file_bytes)
    file_id = uuid.uuid4().hex
    relative_path = build_safe_path(resume_dir, file_id, validated_fmt)
    base = pathlib.Path(os.fspath(resume_dir)).resolve()
    absolute_path = base / relative_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    absolute_path.write_bytes(file_bytes)
    extracted_text = extract_text(absolute_path, validated_fmt)
    content_hash = hashlib.sha256(file_bytes).hexdigest()
    # Store only the basename to avoid retaining directory components from
    # user-supplied filenames (path-traversal hardening).
    original_filename = pathlib.Path(filename).name
    return store.save_resume(
        profile_id,
        relative_path,
        validated_fmt,
        extracted_text,
        content_hash,
        original_filename=original_filename,
    )


def delete_resume(resume_id, store, resume_dir=None) -> bool:
    """Delete a resume's file, text, hash and filename.

    Removes the physical file from *resume_dir* (when provided) and then
    delegates to ``store.delete_resume`` which wipes ``extracted_text``,
    ``content_hash``, ``original_filename`` and sets ``deleted_at``.
    """
    resume = store.get_resume(resume_id)
    storage_path = resume.get("storage_path") or ""
    if resume_dir and storage_path:
        base = pathlib.Path(os.fspath(resume_dir)).resolve()
        file_path = (base / storage_path).resolve()
        try:
            file_path.relative_to(base)
        except ValueError:
            # Refuse to touch a path that escaped the resume directory.
            pass
        else:
            if file_path.is_file():
                file_path.unlink()
    return store.delete_resume(resume_id)
