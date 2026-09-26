"""Processes uploaded papers in the background. The `papers` table is the queue: rows in 'processing'."""
import logging
import os
import threading

from services.moderation import DUPLICATE_CANDIDATES, review_reasons
from services.paper_metadata import to_paper_fields
from services.paper_store import (MAX_ATTEMPTS, PAPERS_DIR, REVIEW_NOTE, Paper, PostgresPaperStore,
                                  resolve_paper_path)

logger = logging.getLogger(__name__)

REQUIRED_METADATA = ("subjectCode", "subjectName", "monthYear", "time", "marks")
METADATA_ATTEMPTS = 2
IDLE_SECONDS = 10


def worker_enabled() -> bool:
    return os.getenv("PAPER_WORKER_ENABLED", "true").strip().lower() != "false"


def extract_metadata_with_retry(processor, file_path: str) -> dict:
    """Header OCR + LLM metadata extraction, asking the LLM again if required fields come back empty."""
    result: dict = {"text": "", "metadata": None}
    for attempt in range(1, METADATA_ATTEMPTS + 1):
        result = processor.process_pdf(file_path)
        metadata = result.get("metadata") or {}
        if all(metadata.get(field) for field in REQUIRED_METADATA):
            break
        logger.info("Metadata incomplete for %s on attempt %d", file_path, attempt)
    return result


def process_paper(paper: Paper, store, processor, vectors) -> None:
    """Runs one claimed paper through OCR, metadata, embedding and the checks, then marks it live or review.
    Never raises: a failure is retried later, and the third one marks the paper failed."""
    pdf_path = resolve_paper_path(paper.file_path)
    try:
        if not os.path.exists(pdf_path):
            logger.error("Paper %s has no file at %s", paper.id, pdf_path)
            store.mark_failed(paper.id)
            return
        header = extract_metadata_with_retry(processor, pdf_path)
        fields = to_paper_fields(header.get("metadata"))
        full_text = processor.extract_full_text(pdf_path)
        vector = vectors.get_embedding(full_text.strip() or paper.filename, input_type="passage")
        if not vector:
            raise RuntimeError("embedding failed")
        nearest = vectors.nearest_papers(vector, DUPLICATE_CANDIDATES, exclude_id=paper.id)
        candidates = [(point.id, (point.payload or {}).get("full_text", "")) for point in nearest]
        reasons = review_reasons(full_text, fields, candidates)
        status = "review" if reasons else "live"
        metadata = {"subject_code": fields["subject_code"], "subject_name": fields["subject_name"],
                    "year": fields["year"], "filename": paper.filename}
        if not vectors.upsert_paper_vector(paper.id, vector, full_text, metadata, status):
            raise RuntimeError("storing the vector failed")
        store.finish(paper.id, fields, status, reasons, REVIEW_NOTE if reasons else None)
        logger.info("Paper %s processed: %s %s", paper.id, status, [r["code"] for r in reasons])
    except Exception:
        logger.exception("Processing paper %s failed (attempt %d of %d)", paper.id, paper.attempts, MAX_ATTEMPTS)
        try:
            if paper.attempts >= MAX_ATTEMPTS:
                store.mark_failed(paper.id)
            else:
                store.schedule_retry(paper.id)
        except Exception:
            logger.exception("Couldn't record the failure of paper %s; it's retried when its lease runs out", paper.id)


def _default_processor():
    from services.ocr_service import DocumentProcessor

    return DocumentProcessor(upload_dir=resolve_paper_path(PAPERS_DIR))


def _default_vectors():
    from services.vector_service import VectorService

    return VectorService()


class PaperWorker:
    """One background thread that processes uploads one at a time."""

    def __init__(self, store_factory=PostgresPaperStore, processor_factory=_default_processor,
                 vectors_factory=_default_vectors, idle_seconds: float = IDLE_SECONDS):
        self._store_factory = store_factory
        self._processor_factory = processor_factory
        self._vectors_factory = vectors_factory
        self._idle_seconds = idle_seconds
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="paper-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)
            self._thread = None

    def wake(self) -> None:
        """Called after an upload so it's processed now rather than at the next idle check."""
        self._wake.set()

    def run_once(self, store, processor, vectors) -> bool:
        paper = store.claim_next()
        if paper is None:
            return False
        process_paper(paper, store, processor, vectors)
        return True

    def _run(self) -> None:
        deps = None
        while not self._stop.is_set():
            try:
                if deps is None:
                    deps = (self._store_factory(), self._processor_factory(), self._vectors_factory())
                if self.run_once(*deps):
                    continue
            except Exception:
                logger.exception("Paper worker error; trying again shortly")
            self._wake.wait(self._idle_seconds)
            self._wake.clear()


paper_worker = PaperWorker()
