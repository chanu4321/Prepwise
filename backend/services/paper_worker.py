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
IDLE_FALLBACK_SECONDS = 3600
MIN_IDLE_SECONDS = 1.0


def worker_enabled() -> bool:
    return os.getenv("PAPER_WORKER_ENABLED", "").strip().lower() == "true"


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


def _delete_vector_quietly(vectors, paper_id: int) -> None:
    """Best-effort vector cleanup for a paper that isn't live. Ignores the result and never raises."""
    try:
        vectors.delete_paper(paper_id)
    except Exception:
        logger.exception("Couldn't delete the vector for paper %s", paper_id)


def process_paper(paper: Paper, store, processor, vectors) -> None:
    """Runs one claimed paper through OCR, metadata, embedding and the checks, then marks it live or review.
    Never raises: a failure is retried later, and the third one marks the paper failed."""
    pdf_path = resolve_paper_path(paper.file_path)
    try:
        if paper.attempts > MAX_ATTEMPTS:
            logger.error("Paper %s was claimed %d times without finishing; marking it failed", paper.id, paper.attempts)
            store.mark_failed(paper.id)
            _delete_vector_quietly(vectors, paper.id)
            return
        if not os.path.exists(pdf_path):
            logger.error("Paper %s has no file at %s", paper.id, pdf_path)
            store.mark_failed(paper.id)
            _delete_vector_quietly(vectors, paper.id)
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
        if not store.finish(paper.id, fields, status, reasons, REVIEW_NOTE if reasons else None):
            logger.warning("Paper %s was deleted or changed before it finished; removing the vector it just wrote",
                           paper.id)
            _delete_vector_quietly(vectors, paper.id)
            return
        logger.info("Paper %s processed: %s %s", paper.id, status, [r["code"] for r in reasons])
    except Exception:
        logger.exception("Processing paper %s failed (attempt %d of %d)", paper.id, paper.attempts, MAX_ATTEMPTS)
        try:
            if paper.attempts >= MAX_ATTEMPTS:
                store.mark_failed(paper.id)
                _delete_vector_quietly(vectors, paper.id)
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
                 vectors_factory=_default_vectors, idle_seconds: float = IDLE_FALLBACK_SECONDS):
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

    def idle_wait(self, store) -> float:
        """How long to sleep before checking the queue again: as soon as the next retry or lease is due
        (at least MIN_IDLE_SECONDS), but never longer than self._idle_seconds."""
        try:
            due = store.seconds_until_next_due()
        except Exception:
            logger.exception("Couldn't check when the next paper is due; using the idle fallback")
            return self._idle_seconds
        if due is None:
            return self._idle_seconds
        return max(MIN_IDLE_SECONDS, min(self._idle_seconds, due))

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
            wait = self.idle_wait(deps[0]) if deps is not None else min(60, self._idle_seconds)
            self._wake.wait(wait)
            self._wake.clear()


paper_worker = PaperWorker()
