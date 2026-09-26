"""Slow LLM/OCR work must not freeze the server for everyone else."""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from main import app
from tests.fakes import FakeRag
from tests.test_generate import BODY, STREAM

SLOW_SECONDS = 2.0
MAX_HEALTH_SECONDS = 0.5


@pytest.fixture(autouse=True)
def no_database_at_startup(monkeypatch):
    # The shared client runs the app's startup, which would otherwise try the (dummy) database
    monkeypatch.setattr("main.init_db", lambda: None)


def health_time_while(start_slow_request, started):
    """Starts a slow request on one shared event loop and times /health while it runs."""
    with TestClient(app) as shared:  # one event loop serves both requests
        worker = threading.Thread(target=start_slow_request, args=(shared,))
        worker.start()
        assert started.wait(5), "the slow request never started"
        begin = time.monotonic()
        assert shared.get("/health").status_code == 200
        elapsed = time.monotonic() - begin
        worker.join()
    return elapsed


def test_health_answers_while_a_paper_is_generating(auth_headers, monkeypatch):
    started = threading.Event()

    class SlowRag(FakeRag):
        papers = list(FakeRag.papers)
        question_error = False
        raise_on_generate = False

        def _generate_question(self, *args, **kwargs):
            started.set()
            time.sleep(SLOW_SECONDS)  # stands in for a 2-3 minute GLM call
            return super()._generate_question(*args, **kwargs)

    monkeypatch.setattr("api.routes.RAGService", SlowRag)
    headers = auth_headers(role="faculty")

    elapsed = health_time_while(lambda c: c.post(STREAM, json=BODY, headers=headers), started)

    assert elapsed < MAX_HEALTH_SECONDS


def test_health_answers_while_a_syllabus_is_processed(auth_headers, monkeypatch):
    started = threading.Event()

    def slow_save(file_bytes, filename, subject_code, subject_name, uploaded_by=None):
        started.set()
        time.sleep(SLOW_SECONDS)  # stands in for OCR + GLM module parsing
        return {"success": True, "data": {"modules": []}}

    monkeypatch.setattr("api.routes.get_syllabus_owner", lambda code: (False, None))
    monkeypatch.setattr("api.routes.process_and_save_syllabus", slow_save)
    headers = auth_headers(role="faculty", verified=True)

    def upload(c):
        c.post("/api/v1/syllabus/upload", files={"file": ("syl.pdf", b"%PDF-1.4", "application/pdf")},
               data={"subject_code": "CSE432", "subject_name": "SPM"}, headers=headers)

    elapsed = health_time_while(upload, started)

    assert elapsed < MAX_HEALTH_SECONDS
