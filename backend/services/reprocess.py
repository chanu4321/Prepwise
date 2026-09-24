"""Re-runs stored papers through the upload pipeline (OCR, metadata, embedding)."""
import logging
import os
from collections import Counter
from dataclasses import dataclass, field

from services.ocr_service import DocumentProcessor
from services.paper_metadata import PAPER_FIELDS, to_paper_fields
from services.paper_store import PaperRow, get_papers, update_paper_metadata

logger = logging.getLogger(__name__)

STATUSES = ("preview", "updated", "skipped", "failed")
_MARKERS = {"preview": "*", "updated": "+", "skipped": "-", "failed": "x"}


@dataclass
class ReprocessResult:
    paper_id: int
    filename: str
    status: str
    changes: dict = field(default_factory=dict)  # field -> (old, new)
    message: str = ""


def merge_fields(old: dict, new: dict) -> dict:
    """New non-empty values win; an empty new value never wipes out existing data."""
    return {k: new.get(k) if new.get(k) else old.get(k) for k in PAPER_FIELDS}


def diff_fields(old: dict, merged: dict) -> dict:
    return {k: (old.get(k), merged[k]) for k in PAPER_FIELDS if old.get(k) != merged[k]}


def reprocess_paper(row: PaperRow, processor, vector_service, update_metadata, apply: bool) -> ReprocessResult:
    if not os.path.exists(row.file_path):
        return ReprocessResult(row.id, row.filename, "skipped", message=f"PDF not found at {row.file_path}")
    try:
        extracted = processor.process_pdf(row.file_path)
        merged = merge_fields(row.fields, to_paper_fields(extracted.get("metadata")))
        changes = diff_fields(row.fields, merged)
        if not apply:
            return ReprocessResult(row.id, row.filename, "preview", changes)

        full_text = processor.extract_full_text(row.file_path)
        vector = vector_service.get_embedding(full_text, input_type="passage")
        if not vector:
            return ReprocessResult(row.id, row.filename, "failed", changes, "embedding failed; nothing was changed")
        payload = {"subject_code": merged["subject_code"], "subject_name": merged["subject_name"],
                   "year": merged["year"], "filename": row.filename}
        if not vector_service.upsert_paper_vector(row.id, vector, full_text, payload):
            return ReprocessResult(row.id, row.filename, "failed", changes, "Qdrant update failed; nothing was changed")
        if changes:
            update_metadata(row.id, merged)
        return ReprocessResult(row.id, row.filename, "updated", changes)
    except Exception as error:
        logger.exception("Reprocessing paper %s failed", row.id)
        return ReprocessResult(row.id, row.filename, "failed", message=str(error))


def reprocess_many(rows, processor, vector_service, update_metadata, apply: bool, out=print) -> list[ReprocessResult]:
    results = []
    for row in rows:
        result = reprocess_paper(row, processor, vector_service, update_metadata, apply)
        out(f"{_MARKERS[result.status]} {result.paper_id}: {result.filename} [{result.status}] {result.message}".rstrip())
        for name, (old, new) in result.changes.items():
            out(f"    {name}: {old!r} -> {new!r}")
        results.append(result)
    return results


def run_reprocess(ids: list[int] | None, apply: bool, out=print) -> int:
    from services.vector_service import VectorService

    rows = get_papers(ids)
    for missing in sorted(set(ids or []) - {row.id for row in rows}):
        out(f"- {missing}: no paper with this id")
    if not rows:
        out("Nothing to reprocess.")
        return 1

    results = reprocess_many(rows, DocumentProcessor(upload_dir="backend/papers"), VectorService(),
                             update_paper_metadata, apply, out)
    counts = Counter(result.status for result in results)
    out("Summary: " + ", ".join(f"{counts[s]} {s}" for s in STATUSES if counts[s]))
    if not apply:
        out("Preview only: nothing was written. Re-run with --apply to save these changes.")
    return 1 if counts["failed"] else 0
