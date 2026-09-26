"""Checks on an uploaded file before it's accepted: name, size, page count, and exact copies."""
import hashlib
import logging
import os

from pdf2image import pdfinfo_from_path

from errors import ApiError
from services.paper_store import Paper

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PAGES = 20
_CHUNK = 1024 * 1024
_INVALID_FILENAME_CHARS = set('<>:"|?*/\\')

DUPLICATE_MESSAGES = {
    "live": "This paper is already on PrepWise.",
    "processing": "This paper was already uploaded and is being checked.",
    "review": "This paper was already uploaded and is being checked.",
    "rejected": "This file was already reviewed and not accepted.",
}


class FileTooLarge(Exception):
    pass


def filename_problem(name: str) -> str | None:
    """Why `name` can't be used as a paper's display name, or None when it's fine."""
    if not name.lower().endswith(".pdf"):
        return "Only PDF files are supported."
    stem = name[:-len(".pdf")]
    if (not stem.strip()
            or len(name) > 200
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)
            or any(ch in _INVALID_FILENAME_CHARS for ch in name)):
        return "Please rename the file using letters, numbers and simple punctuation, then upload it again."
    return None


def remove_quietly(path) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def save_upload(source, destination, max_bytes: int) -> str:
    """Copies `source` (a binary file object) to `destination` and returns its SHA-256 hex digest.
    Past `max_bytes` it stops, removes the partial file and raises FileTooLarge."""
    digest = hashlib.sha256()
    size = 0
    try:
        with open(destination, "wb") as out:
            while chunk := source.read(_CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise FileTooLarge()
                digest.update(chunk)
                out.write(chunk)
    except BaseException:
        remove_quietly(destination)
        raise
    return digest.hexdigest()


def count_pdf_pages(path) -> int | None:
    """Page count from poppler's pdfinfo, or None when the PDF can't be read."""
    try:
        return int(pdfinfo_from_path(str(path), timeout=30)["Pages"])
    except Exception as error:
        logger.warning("Couldn't count the pages of %s: %s", path, error)
        return None


def duplicate_paper_error(existing: Paper | None) -> ApiError:
    status = existing.status if existing is not None else "processing"
    return ApiError(409, "duplicate_paper", DUPLICATE_MESSAGES.get(status, DUPLICATE_MESSAGES["processing"]),
                    extra={"paperId": existing.id if existing is not None else None, "paperStatus": status})
