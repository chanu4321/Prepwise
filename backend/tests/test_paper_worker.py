import threading
import time
from datetime import timedelta

import pytest

from services.paper_store import FAILED_NOTE, REVIEW_NOTE
from services.paper_worker import PaperWorker, process_paper
from tests.fakes import FakePaperStore, FakeProcessor, FakeVectorIndex, moderation_fixture

EXAM = moderation_fixture("ai_major_2023")
METADATA = {"subjectCode": "cse 401", "subjectName": "Artificial Intelligence", "monthYear": "April-May, 2023",
            "time": "3 Hrs", "marks": "60"}


@pytest.fixture
def store():
    return FakePaperStore()


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "abc.pdf"
    path.write_bytes(b"%PDF-1.4 x")
    return path


def claimed(store, pdf):
    store.create_upload("AI Major 2023.pdf", str(pdf), "f" * 64, "k" * 32, None)
    return store.claim_next()


def test_clean_paper_goes_live_with_its_details_and_vector(store, pdf, tmp_path):
    vectors = FakeVectorIndex()
    paper = claimed(store, pdf)
    process_paper(paper, store, FakeProcessor(tmp_path, METADATA, full_text=EXAM), vectors)
    done = store.get(paper.id)
    assert (done.status, done.review_reasons, done.status_note) == ("live", [], None)
    assert done.fields["subject_code"] == "CSE401" and done.fields["year"] == "April-May, 2023"
    assert vectors.points[paper.id]["status"] == "live"
    assert vectors.points[paper.id]["filename"] == "AI Major 2023.pdf"
    assert vectors.points[paper.id]["full_text"] == EXAM
    assert store.claim_next() is None


def test_flagged_paper_waits_for_review(store, pdf, tmp_path):
    vectors = FakeVectorIndex()
    vectors.neighbours = [(41, moderation_fixture("ai_major_2023_rescan"))]
    paper = claimed(store, pdf)
    process_paper(paper, store, FakeProcessor(tmp_path, {}, full_text=EXAM), vectors)
    done = store.get(paper.id)
    assert done.status == "review" and done.status_note == REVIEW_NOTE
    assert [r["code"] for r in done.review_reasons] == ["possible_duplicate", "missing_details"]
    assert done.review_reasons[0]["paperId"] == 41
    assert vectors.points[paper.id]["status"] == "review"


def test_blank_scan_is_flagged_unreadable_and_still_indexed(store, pdf, tmp_path):
    vectors = FakeVectorIndex()
    paper = claimed(store, pdf)
    process_paper(paper, store, FakeProcessor(tmp_path, METADATA, full_text=""), vectors)
    done = store.get(paper.id)
    assert done.status == "review" and done.review_reasons == [{"code": "unreadable", "letters": 0}]
    assert vectors.embedded == ["AI Major 2023.pdf"]  # never embeds an empty string


@pytest.mark.parametrize("vectors", [FakeVectorIndex(vector=None), FakeVectorIndex(fail_nearest=True),
                                     FakeVectorIndex(fail_upsert=True)])
def test_a_failed_step_is_retried_later_not_published(store, pdf, tmp_path, vectors):
    paper = claimed(store, pdf)
    process_paper(paper, store, FakeProcessor(tmp_path, METADATA, full_text=EXAM), vectors)
    assert store.get(paper.id).status == "processing"
    assert store.claim_next() is None
    store.now += timedelta(seconds=121)
    assert store.claim_next().id == paper.id


def test_third_failure_marks_the_paper_failed(store, pdf, tmp_path):
    processor = FakeProcessor(tmp_path, METADATA, fail=True)
    paper = claimed(store, pdf)
    for attempt in range(1, 4):
        assert paper.attempts == attempt
        process_paper(paper, store, processor, FakeVectorIndex())
        store.now += timedelta(minutes=3)
        paper = store.claim_next()
    assert paper is None
    failed = store.list_all()[0]
    assert (failed.status, failed.status_note) == ("failed", FAILED_NOTE)


def test_missing_file_fails_at_once(store, tmp_path):
    store.create_upload("gone.pdf", str(tmp_path / "gone.pdf"), "f" * 64, "k" * 32, None)
    paper = store.claim_next()
    process_paper(paper, store, FakeProcessor(tmp_path, METADATA, full_text=EXAM), FakeVectorIndex())
    assert store.get(paper.id).status == "failed"


def test_run_once_reports_whether_there_was_work(store, pdf, tmp_path):
    worker = PaperWorker()
    processor, vectors = FakeProcessor(tmp_path, METADATA, full_text=EXAM), FakeVectorIndex()
    assert worker.run_once(store, processor, vectors) is False
    store.create_upload("a.pdf", str(pdf), "f" * 64, "k" * 32, None)
    assert worker.run_once(store, processor, vectors) is True
    assert store.list_all()[0].status == "live"


class LockedStore(FakePaperStore):
    """The worker thread and the test both use this store; the lock stops a dict resize mid-iteration."""

    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()

    def create_upload(self, *args):
        with self.lock:
            return super().create_upload(*args)

    def claim_next(self):
        with self.lock:
            return super().claim_next()


def test_thread_processes_an_upload_when_woken(pdf, tmp_path):
    store = LockedStore()
    worker = PaperWorker(store_factory=lambda: store,
                         processor_factory=lambda: FakeProcessor(tmp_path, METADATA, full_text=EXAM),
                         vectors_factory=FakeVectorIndex, idle_seconds=30)
    worker.start()
    try:
        store.create_upload("a.pdf", str(pdf), "f" * 64, "k" * 32, None)
        worker.wake()
        deadline = time.monotonic() + 5
        while store.list_all()[0].status == "processing" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert store.list_all()[0].status == "live"
    finally:
        worker.stop()


def test_app_starts_and_stops_the_worker_when_enabled(monkeypatch):
    from fastapi.testclient import TestClient

    import main

    events = []
    monkeypatch.setenv("PAPER_WORKER_ENABLED", "true")
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main.paper_worker, "start", lambda: events.append("start"))
    monkeypatch.setattr(main.paper_worker, "stop", lambda: events.append("stop"))
    with TestClient(main.app):
        assert events == ["start"]
    assert events == ["start", "stop"]


def test_app_leaves_the_worker_off_when_disabled(monkeypatch):
    from fastapi.testclient import TestClient

    import main

    events = []
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main.paper_worker, "start", lambda: events.append("start"))
    monkeypatch.setattr(main.paper_worker, "stop", lambda: events.append("stop"))
    with TestClient(main.app):
        pass
    assert events == ["stop"]
