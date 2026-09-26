"""PaperStore contract. Every test runs against FakePaperStore, and against PostgresPaperStore when
TEST_DATABASE_URL points at a throwaway database (never production: rows are written and deleted)."""
import os
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

import pytest

import database  # noqa: F401  (loads .env, which may define TEST_DATABASE_URL)
from services.paper_metadata import PAPER_FIELDS
from services.paper_store import FAILED_NOTE, STATUSES, DuplicateFile, PostgresPaperStore
from tests.fakes import FakePaperStore

TEST_DB = os.getenv("TEST_DATABASE_URL")
TENANT = "pytest-papers"
FIELDS = {"subject_code": "CSE432", "subject_name": "SPM", "semester": None, "year": "June, 2023",
          "time": "3 Hrs", "marks": "60"}


@dataclass
class Backend:
    store: object
    expire: Callable[[int], None]  # makes a leased or scheduled paper due now
    make_user: Callable[[], int]


@pytest.fixture(params=["fake", "postgres"])
def backend(request, monkeypatch):
    if request.param == "fake":
        store = FakePaperStore()

        def expire(paper_id):
            store.retry_at[paper_id] = store.now - timedelta(seconds=1)

        return Backend(store, expire, lambda: store.add_user("Uploader", "uploader@example.com"))

    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL not set")
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    from auth.tokens import Claims
    from auth.users import PostgresUserStore
    from database import db_cursor, init_db

    init_db()
    with db_cursor() as cur:
        cur.execute("DELETE FROM papers WHERE filename LIKE %s", ("pytest-%",))
        cur.execute("DELETE FROM users WHERE ms_tid = %s", (TENANT,))

    def expire(paper_id):
        with db_cursor() as cur:
            cur.execute("UPDATE papers SET retry_at = now() - interval '1 second' WHERE id = %s", (paper_id,))

    def make_user():
        claims = Claims(tid=TENANT, oid=str(uuid.uuid4()), name="Uploader", email="uploader@example.com")
        return PostgresUserStore().get_or_create(claims).id

    return Backend(PostgresPaperStore(), expire, make_user)


def upload(store, name, sha=None, user=None, path=None):
    return store.create_upload(f"pytest-{name}.pdf", path or f"backend/papers/pytest-{name}.pdf",
                               sha or uuid.uuid4().hex, uuid.uuid4().hex, user)


def test_create_upload_starts_processing_and_is_found_by_key(backend):
    user = backend.make_user()
    paper = upload(backend.store, "key", user=user)
    assert (paper.status, paper.attempts, paper.uploaded_by) == ("processing", 0, user)
    assert (paper.uploader_name, paper.uploader_email) == ("Uploader", "uploader@example.com")
    assert paper.fields == {k: None for k in PAPER_FIELDS} and paper.review_reasons == []
    assert paper.uploaded_at is not None
    assert backend.store.get_by_upload_key(paper.upload_key).id == paper.id
    assert backend.store.get_by_upload_key(uuid.uuid4().hex) is None


def test_same_file_cannot_be_stored_twice_unless_the_first_failed(backend):
    store = backend.store
    first = upload(store, "dup1", sha="a" * 64)
    assert store.find_active_by_hash("a" * 64).id == first.id
    with pytest.raises(DuplicateFile):
        upload(store, "dup2", sha="a" * 64)
    store.mark_failed(first.id)
    assert store.find_active_by_hash("a" * 64) is None
    assert upload(store, "dup3", sha="a" * 64).status == "processing"


def test_claim_takes_the_oldest_due_paper_and_leases_it(backend):
    store = backend.store
    first, second = upload(store, "c1"), upload(store, "c2")
    claimed = store.claim_next()
    assert (claimed.id, claimed.attempts) == (first.id, 1)
    assert store.claim_next().id == second.id
    assert store.claim_next() is None  # both are leased
    backend.expire(first.id)  # e.g. the process died mid-job
    again = store.claim_next()
    assert (again.id, again.attempts) == (first.id, 2)


def test_scheduled_retry_waits_until_due(backend):
    store = backend.store
    paper = upload(store, "r1")
    store.claim_next()
    store.schedule_retry(paper.id)
    assert store.claim_next() is None
    backend.expire(paper.id)
    assert store.claim_next().id == paper.id


def test_finish_saves_the_result_and_ends_processing(backend):
    store = backend.store
    paper = upload(store, "f1")
    store.claim_next()
    reasons = [{"code": "missing_details", "fields": ["year"]}]
    store.finish(paper.id, FIELDS, "review", reasons, "An admin will check this paper before it's published.")
    done = store.get(paper.id)
    assert (done.status, done.fields, done.review_reasons) == ("review", FIELDS, reasons)
    assert done.status_note == "An admin will check this paper before it's published."
    backend.expire(paper.id)
    assert store.claim_next() is None


def test_mark_failed_uses_the_failure_note(backend):
    store = backend.store
    paper = upload(store, "m1")
    store.mark_failed(paper.id)
    failed = store.get(paper.id)
    assert (failed.status, failed.status_note) == ("failed", FAILED_NOTE)


def test_lists_and_counts(backend):
    store = backend.store
    before = store.counts()
    assert set(before) == set(STATUSES)
    user = backend.make_user()
    a, b, c = upload(store, "l1", user=user), upload(store, "l2", user=user), upload(store, "l3")
    store.finish(b.id, FIELDS, "live", [], None)
    after = store.counts()
    assert after["processing"] == before["processing"] + 2 and after["live"] == before["live"] + 1
    assert [p.id for p in store.list_by_uploader(user)] == [b.id, a.id]
    assert [p.id for p in store.list_by_uploader(user, limit=1)] == [b.id]
    assert [p.id for p in store.list_by_status("processing") if p.id in (a.id, b.id, c.id)] == [c.id, a.id]
    assert [p.id for p in store.get_many([c.id, a.id])] == [a.id, c.id]
    assert store.get_many([]) == []
    assert {a.id, b.id, c.id} <= {p.id for p in store.list_all()}


def test_update_details_records_the_reviewer(backend):
    store = backend.store
    admin = backend.make_user()
    paper = upload(store, "u1")
    updated = store.update_details(paper.id, "pytest-renamed.pdf", FIELDS, admin)
    assert (updated.filename, updated.fields, updated.reviewed_by) == ("pytest-renamed.pdf", FIELDS, admin)
    assert updated.reviewed_at is not None
    assert store.update_details(999999999, "pytest-x.pdf", FIELDS, admin) is None


def test_set_status_changes_status_and_retry_resets_attempts(backend):
    store = backend.store
    admin = backend.make_user()
    live = upload(store, "s1")
    store.claim_next()
    store.finish(live.id, FIELDS, "live", [], None)
    taken_down = store.set_status(live.id, "rejected", "Not an exam paper.", admin)
    assert (taken_down.status, taken_down.status_note, taken_down.reviewed_by) == ("rejected", "Not an exam paper.", admin)

    failed = upload(store, "s2")
    store.claim_next()
    store.mark_failed(failed.id)
    retried = store.set_status(failed.id, "processing", None, admin)
    assert (retried.status, retried.attempts, retried.status_note) == ("processing", 0, None)
    assert store.claim_next().id == failed.id
    assert store.set_status(999999999, "live", None, admin) is None


def test_retrying_a_failed_copy_of_an_active_file_is_refused(backend):
    store = backend.store
    failed = upload(store, "h1", sha="b" * 64)
    store.mark_failed(failed.id)
    upload(store, "h2", sha="b" * 64)
    with pytest.raises(DuplicateFile):
        store.set_status(failed.id, "processing", None, None)
    assert store.get(failed.id).status == "failed"


def test_set_file_hash_refuses_a_copy(backend):
    store = backend.store
    upload(store, "sh1", sha="c" * 64)
    other = upload(store, "sh2", sha="d" * 64)
    with pytest.raises(DuplicateFile):
        store.set_file_hash(other.id, "c" * 64)
    store.set_file_hash(other.id, "e" * 64)
    assert store.get(other.id).file_sha256 == "e" * 64


def test_delete_and_count_file_users(backend):
    store = backend.store
    shared = "backend/papers/pytest-shared.pdf"
    a, _ = upload(store, "d1", path=shared), upload(store, "d2", path=shared)
    assert store.count_file_users(shared) == 2
    assert store.delete(a.id) is True and store.get(a.id) is None
    assert store.count_file_users(shared) == 1
    assert store.delete(a.id) is False
