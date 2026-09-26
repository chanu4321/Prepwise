# Upload Moderation (Part B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Uploads return in seconds and are processed by a background worker; clean papers go live, flagged ones wait for an admin; exact copies are refused, likely copies are flagged; stored filenames are unique; files are limited to 10 MB / 20 pages; the existing duplicates can be removed with a command.

**Architecture:** The `papers` table gains a `status` and becomes the job queue. One worker thread (started in the FastAPI lifespan) claims `processing` rows with a lease, runs the existing OCR/metadata/embedding pipeline plus new checks, and marks the paper `live` or `review`. Qdrant stores a `status` in each point's payload; public search and RAG only see live points. Admins act through `/admin/papers` endpoints and a new Admin → Papers page.

**Tech Stack:** FastAPI 0.141 / Starlette 1.7, psycopg2 (Neon Postgres), qdrant-client, pdf2image/poppler, pytest; Next.js 16 + React 19 + Tailwind, MSAL.

**Spec:** `docs/superpowers/specs/2026-09-26-upload-moderation-design.md`

## Global Constraints

- Run every Python command from the project root (`C:\Chaitanya\Project`) with the backend venv: `backend/.venv/Scripts/python -m pytest ...`. Never run Python from inside `backend/` (it creates a stray `backend/backend/papers`).
- The local `.env` points at the PRODUCTION Postgres and Qdrant. Tests are kept offline by `backend/tests/conftest.py`. Never run `init_db()`, the worker, `admin_cli.py dedupe` or any write against the `.env` databases.
- `TEST_DATABASE_URL` may only point at a throwaway database. Use the local container from Task 1: `postgresql://postgres:test@127.0.0.1:55432/postgres`. Pass it as a shell variable for the test run; never write it into `.env`.
- Never print or commit secrets (API keys, `DATABASE_URL`, `QDRANT_API_KEY`, `IP_HASH_SALT`).
- `docker-compose.yml` has the user's uncommitted edit: never `git add` it, never `git stash`, never revert it. Always `git add` explicit paths.
- Do not push. Branch: `feature/upload-moderation`.
- Limits and thresholds (exact values): `MAX_UPLOAD_BYTES = 10 * 1024 * 1024`, `MAX_PAGES = 20`, `MIN_READABLE_LETTERS = 300`, `DUPLICATE_OVERLAP = 0.5`, `DUPLICATE_CANDIDATES = 5`, `MAX_ATTEMPTS = 3`, `LEASE_MINUTES = 15`, `RETRY_DELAY_SECONDS = 120`, worker idle wait 10 s, upload page polls every 5 s and gives up after 15 min, admin preview 1,500 characters.
- User-facing messages (verbatim):
  - review note: "An admin will check this paper before it's published."
  - failed note: "We couldn't process this file. Try uploading it again later."
  - duplicate: live → "This paper is already on PrepWise."; processing/review → "This paper was already uploaded and is being checked."; rejected → "This file was already reviewed and not accepted."
- Statuses: `processing`, `live`, `review`, `rejected`, `failed`. Allowed admin transitions: review→live, review→rejected, live→rejected, rejected→live, failed→processing.
- Match the surrounding code: type hints, short docstrings, `ApiError(status, code, detail)` for client-visible errors, plain-English messages.
- Frontend: lint only the files you touch (the repo has old ESLint debt elsewhere) and `npm run build` must pass.

## Review Focus

1. **A blank or unreadable scan (OCR returns no text).** The worker must still embed something (the filename), flag the paper `unreadable`, and not crash or loop. The test is in Task 4.
2. **Qdrant is unreachable while the worker processes a paper.** `nearest_papers` raises; the paper must be retried later, not published unchecked. The test is in Task 4.
3. **A malformed or guessed upload key** (e.g. `../x`, 200 characters, uppercase) on the public status endpoint must be a plain 404 with no database lookup. The test is in Task 6.
4. **An admin renames a paper to a name with `/`, `\` or control characters.** It must get a 400, with nothing changed. The test is in Task 7.
5. **An admin deletes a paper while the worker is processing it.** This must be refused (409), so the worker can't write a live vector for a deleted row. The test is in Task 7.

---

## Task 1: Paper store and schema

**Files:**
- Modify: `backend/database.py` (end of `init_db`)
- Modify: `backend/services/paper_store.py`
- Modify: `backend/tests/fakes.py` (add `FakePaperStore`)
- Create: `backend/tests/test_paper_store.py`

**Interfaces:**
- Produces, in `services/paper_store.py`:
  - Constants: `STATUSES`, `MAX_ATTEMPTS`, `LEASE_MINUTES`, `RETRY_DELAY_SECONDS`, `REVIEW_NOTE`, `FAILED_NOTE`.
  - Functions and types: `DuplicateFile` (Exception), `Paper` (dataclass), `papers_dir() -> Path`, `new_upload_key() -> str`, `upload_status(paper) -> dict`, `get_paper_store() -> PostgresPaperStore`.
  - `PostgresPaperStore` methods:
    - `create_upload(filename, file_path, file_sha256, upload_key, uploaded_by) -> Paper` (raises `DuplicateFile`)
    - `get(id) -> Paper|None`
    - `get_many(ids) -> list[Paper]` (ascending id)
    - `get_by_upload_key(key) -> Paper|None`
    - `find_active_by_hash(sha) -> Paper|None`
    - `list_by_uploader(user_id, limit=50) -> list[Paper]` (newest first)
    - `list_by_status(status) -> list[Paper]` (newest first)
    - `list_all() -> list[Paper]` (ascending id)
    - `counts() -> dict[str,int]` (all five statuses)
    - `claim_next() -> Paper|None`
    - `finish(id, fields, status, reasons, note) -> None`
    - `schedule_retry(id, delay_seconds=RETRY_DELAY_SECONDS) -> None`
    - `mark_failed(id, note=FAILED_NOTE) -> None`
    - `update_details(id, filename, fields, reviewer_id) -> Paper|None`
    - `set_status(id, status, note, reviewer_id) -> Paper|None` (raises `DuplicateFile`)
    - `set_file_hash(id, sha) -> None` (raises `DuplicateFile`)
    - `delete(id) -> bool`
    - `count_file_users(file_path) -> int`
  - `PaperRow` gains `status: str = "live"`, which `get_papers` fills in.
- Produces, in `tests/fakes.py`: `FakePaperStore` with the same methods, plus:
  - test helpers `add_user(name, email) -> int` and `add_paper(...) -> Paper`
  - fields `now: datetime` (settable clock), `retry_at: dict[int, datetime|None]` and `papers: dict[int, Paper]`

- [ ] **Step 1: Start a throwaway Postgres for the SQL tests**

The user must have Docker Desktop running. Then:

```bash
docker run -d --name prepwise-test-pg -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16-alpine
```

If the container already exists, run `docker start prepwise-test-pg` instead. If Docker isn't available, the Postgres half of the tests skips. Say so in your report; don't point `TEST_DATABASE_URL` anywhere else.

- [ ] **Step 2: Write the failing contract tests**

Create `backend/tests/test_paper_store.py`:

```python
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
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_paper_store.py -q`
Expected: collection error with `ImportError: cannot import name 'FAILED_NOTE'` (or `FakePaperStore`).

- [ ] **Step 4: Add the schema changes**

At the end of `init_db()` in `backend/database.py`, after the two existing `ALTER TABLE ... uploaded_by` lines, add:

```python
        # Upload moderation (Part B): every existing paper becomes 'live'
        cur.execute("""
            ALTER TABLE papers
                ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'live'
                    CHECK (status IN ('processing', 'live', 'review', 'rejected', 'failed')),
                ADD COLUMN IF NOT EXISTS review_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
                ADD COLUMN IF NOT EXISTS status_note TEXT,
                ADD COLUMN IF NOT EXISTS file_sha256 TEXT,
                ADD COLUMN IF NOT EXISTS upload_key TEXT,
                ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0,
                ADD COLUMN IF NOT EXISTS retry_at TIMESTAMP,
                ADD COLUMN IF NOT EXISTS reviewed_by INTEGER REFERENCES users(id),
                ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP;
        """)
        # One stored copy per file; a failed upload doesn't block uploading the same file again
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS papers_active_sha256 ON papers (file_sha256) "
                    "WHERE status <> 'failed';")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS papers_upload_key ON papers (upload_key);")
        cur.execute("CREATE INDEX IF NOT EXISTS papers_status ON papers (status);")
```

- [ ] **Step 5: Implement the store**

Replace `backend/services/paper_store.py` with the following. `insert_paper` stays until Task 5 removes it.

```python
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import psycopg2.errors

from database import db_cursor
from services.paper_metadata import PAPER_FIELDS

# The folder that holds `backend/` (/app in the container). `papers.file_path` values are relative to it.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAPERS_DIR = "backend/papers"

STATUSES = ("processing", "live", "review", "rejected", "failed")
MAX_ATTEMPTS = 3
LEASE_MINUTES = 15
RETRY_DELAY_SECONDS = 120
REVIEW_NOTE = "An admin will check this paper before it's published."
FAILED_NOTE = "We couldn't process this file. Try uploading it again later."


def resolve_paper_path(stored_path: str) -> str:
    """Absolute path for a `papers.file_path` value, whatever directory the process was started from."""
    path = Path(stored_path)
    return str(path if path.is_absolute() else PROJECT_ROOT / path)


def papers_dir() -> Path:
    """The absolute `backend/papers` folder, created if missing."""
    path = PROJECT_ROOT / PAPERS_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_upload_key() -> str:
    """Names the stored file and lets an anonymous uploader look up their upload's status."""
    return secrets.token_hex(16)


class DuplicateFile(Exception):
    """Another paper that isn't 'failed' already has this file's SHA-256."""


@dataclass
class Paper:
    id: int
    filename: str
    file_path: str
    fields: dict  # keys: PAPER_FIELDS
    status: str
    review_reasons: list = field(default_factory=list)
    status_note: str | None = None
    file_sha256: str | None = None
    upload_key: str | None = None
    attempts: int = 0
    uploaded_by: int | None = None
    uploaded_at: datetime | None = None
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    uploader_name: str | None = None
    uploader_email: str | None = None


def upload_status(paper: Paper) -> dict:
    """What an uploader may see about their own upload."""
    return {"id": paper.id, "filename": paper.filename, "status": paper.status, "note": paper.status_note,
            "uploadedAt": paper.uploaded_at.isoformat() if paper.uploaded_at else None}


@dataclass
class PaperRow:
    id: int
    filename: str
    file_path: str
    fields: dict  # keys: PAPER_FIELDS
    status: str = "live"


def insert_paper(filename: str, file_path: str, fields: dict, uploaded_by: int | None) -> int:
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO papers (filename, file_path, subject_code, subject_name, semester, year, time, marks, uploaded_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (filename, file_path, *(fields[k] for k in PAPER_FIELDS), uploaded_by),
        )
        return cur.fetchone()[0]


def get_papers(ids: list[int] | None) -> list[PaperRow]:
    """All papers (ids=None) or the given ids, ordered by id."""
    query = "SELECT id, filename, file_path, subject_code, subject_name, semester, year, time, marks, status FROM papers"
    params: tuple = ()
    if ids is not None:
        query += " WHERE id = ANY(%s)"
        params = (list(ids),)
    query += " ORDER BY id"
    with db_cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return [PaperRow(id=r[0], filename=r[1], file_path=r[2], fields=dict(zip(PAPER_FIELDS, r[3:9])), status=r[9])
            for r in rows]


def update_paper_metadata(paper_id: int, fields: dict) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE papers SET subject_code = %s, subject_name = %s, semester = %s, year = %s, time = %s, marks = %s "
            "WHERE id = %s",
            (*(fields[k] for k in PAPER_FIELDS), paper_id),
        )


_SELECT = """
    SELECT p.id, p.filename, p.file_path, p.subject_code, p.subject_name, p.semester, p.year, p.time, p.marks,
           p.status, p.review_reasons, p.status_note, p.file_sha256, p.upload_key, p.attempts,
           p.uploaded_by, p.uploaded_at, p.reviewed_by, p.reviewed_at, u.name, u.email
    FROM papers p LEFT JOIN users u ON u.id = p.uploaded_by
"""


def _paper(row) -> Paper:
    return Paper(id=row[0], filename=row[1], file_path=row[2], fields=dict(zip(PAPER_FIELDS, row[3:9])),
                 status=row[9], review_reasons=row[10] or [], status_note=row[11], file_sha256=row[12],
                 upload_key=row[13], attempts=row[14], uploaded_by=row[15], uploaded_at=row[16],
                 reviewed_by=row[17], reviewed_at=row[18], uploader_name=row[19], uploader_email=row[20])


class PostgresPaperStore:
    """Papers and their moderation state. The `papers` table doubles as the upload worker's queue."""

    def _one(self, where: str, params: tuple) -> Paper | None:
        with db_cursor() as cur:
            cur.execute(_SELECT + where, params)
            row = cur.fetchone()
        return _paper(row) if row else None

    def _all(self, where: str, params: tuple) -> list[Paper]:
        with db_cursor() as cur:
            cur.execute(_SELECT + where, params)
            rows = cur.fetchall()
        return [_paper(row) for row in rows]

    def create_upload(self, filename: str, file_path: str, file_sha256: str, upload_key: str,
                      uploaded_by: int | None) -> Paper:
        try:
            with db_cursor() as cur:
                cur.execute(
                    "INSERT INTO papers (filename, file_path, file_sha256, upload_key, uploaded_by, status) "
                    "VALUES (%s, %s, %s, %s, %s, 'processing') RETURNING id",
                    (filename, file_path, file_sha256, upload_key, uploaded_by),
                )
                paper_id = cur.fetchone()[0]
        except psycopg2.errors.UniqueViolation as error:
            raise DuplicateFile() from error
        return self.get(paper_id)

    def get(self, paper_id: int) -> Paper | None:
        return self._one(" WHERE p.id = %s", (paper_id,))

    def get_many(self, ids) -> list[Paper]:
        ids = list(ids)
        return self._all(" WHERE p.id = ANY(%s) ORDER BY p.id", (ids,)) if ids else []

    def get_by_upload_key(self, upload_key: str) -> Paper | None:
        return self._one(" WHERE p.upload_key = %s", (upload_key,))

    def find_active_by_hash(self, file_sha256: str) -> Paper | None:
        return self._one(" WHERE p.file_sha256 = %s AND p.status <> 'failed'", (file_sha256,))

    def list_by_uploader(self, user_id: int, limit: int = 50) -> list[Paper]:
        return self._all(" WHERE p.uploaded_by = %s ORDER BY p.id DESC LIMIT %s", (user_id, limit))

    def list_by_status(self, status: str) -> list[Paper]:
        return self._all(" WHERE p.status = %s ORDER BY p.id DESC", (status,))

    def list_all(self) -> list[Paper]:
        return self._all(" ORDER BY p.id", ())

    def counts(self) -> dict[str, int]:
        with db_cursor() as cur:
            cur.execute("SELECT status, count(*) FROM papers GROUP BY status")
            found = dict(cur.fetchall())
        return {status: found.get(status, 0) for status in STATUSES}

    def claim_next(self) -> Paper | None:
        """Leases the oldest due 'processing' paper. A lease that runs out (process died) makes it due again."""
        with db_cursor() as cur:
            cur.execute(
                """
                UPDATE papers SET attempts = attempts + 1, retry_at = now() + make_interval(mins => %s)
                WHERE id = (SELECT id FROM papers
                            WHERE status = 'processing' AND (retry_at IS NULL OR retry_at <= now())
                            ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED)
                RETURNING id
                """,
                (LEASE_MINUTES,),
            )
            row = cur.fetchone()
        return self.get(row[0]) if row else None

    def finish(self, paper_id: int, fields: dict, status: str, reasons: list, note: str | None) -> None:
        with db_cursor() as cur:
            cur.execute(
                "UPDATE papers SET subject_code = %s, subject_name = %s, semester = %s, year = %s, time = %s, "
                "marks = %s, status = %s, review_reasons = %s::jsonb, status_note = %s, retry_at = NULL "
                "WHERE id = %s",
                (*(fields[k] for k in PAPER_FIELDS), status, json.dumps(reasons), note, paper_id),
            )

    def schedule_retry(self, paper_id: int, delay_seconds: int = RETRY_DELAY_SECONDS) -> None:
        with db_cursor() as cur:
            cur.execute("UPDATE papers SET retry_at = now() + make_interval(secs => %s) WHERE id = %s",
                        (delay_seconds, paper_id))

    def mark_failed(self, paper_id: int, note: str = FAILED_NOTE) -> None:
        with db_cursor() as cur:
            cur.execute("UPDATE papers SET status = 'failed', status_note = %s, retry_at = NULL WHERE id = %s",
                        (note, paper_id))

    def update_details(self, paper_id: int, filename: str, fields: dict, reviewer_id: int | None) -> Paper | None:
        with db_cursor() as cur:
            cur.execute(
                "UPDATE papers SET filename = %s, subject_code = %s, subject_name = %s, semester = %s, year = %s, "
                "time = %s, marks = %s, reviewed_by = %s, reviewed_at = now() WHERE id = %s RETURNING id",
                (filename, *(fields[k] for k in PAPER_FIELDS), reviewer_id, paper_id),
            )
            row = cur.fetchone()
        return self.get(paper_id) if row else None

    def set_status(self, paper_id: int, status: str, note: str | None, reviewer_id: int | None) -> Paper | None:
        """Admin status change. Moving to 'processing' (retry) also resets the attempt count."""
        try:
            with db_cursor() as cur:
                cur.execute(
                    "UPDATE papers SET status = %s, status_note = %s, reviewed_by = %s, reviewed_at = now(), "
                    "attempts = CASE WHEN %s THEN 0 ELSE attempts END, retry_at = NULL WHERE id = %s RETURNING id",
                    (status, note, reviewer_id, status == "processing", paper_id),
                )
                row = cur.fetchone()
        except psycopg2.errors.UniqueViolation as error:
            raise DuplicateFile() from error
        return self.get(paper_id) if row else None

    def set_file_hash(self, paper_id: int, file_sha256: str) -> None:
        try:
            with db_cursor() as cur:
                cur.execute("UPDATE papers SET file_sha256 = %s WHERE id = %s", (file_sha256, paper_id))
        except psycopg2.errors.UniqueViolation as error:
            raise DuplicateFile() from error

    def delete(self, paper_id: int) -> bool:
        with db_cursor() as cur:
            cur.execute("DELETE FROM papers WHERE id = %s RETURNING id", (paper_id,))
            return cur.fetchone() is not None

    def count_file_users(self, file_path: str) -> int:
        with db_cursor() as cur:
            cur.execute("SELECT count(*) FROM papers WHERE file_path = %s", (file_path,))
            return cur.fetchone()[0]


def get_paper_store() -> PostgresPaperStore:
    return PostgresPaperStore()
```

- [ ] **Step 6: Implement `FakePaperStore`**

Append to `backend/tests/fakes.py`:

```python
import secrets
from datetime import timedelta

from services.paper_metadata import PAPER_FIELDS
from services.paper_store import (FAILED_NOTE, LEASE_MINUTES, RETRY_DELAY_SECONDS, STATUSES, DuplicateFile,
                                  Paper)


class FakePaperStore:
    """In-memory stand-in for PostgresPaperStore with the same method contract. `now` is a settable clock."""

    def __init__(self):
        self.papers: dict[int, Paper] = {}
        self.retry_at: dict[int, datetime | None] = {}
        self.users: dict[int, tuple[str | None, str | None]] = {}
        self.now = datetime(2026, 9, 26, 12, 0, 0)
        self._next_id = 1
        self._next_user_id = 1000

    # --- test helpers -------------------------------------------------------------------------
    def add_user(self, name: str | None, email: str | None) -> int:
        user_id = self._next_user_id
        self._next_user_id += 1
        self.users[user_id] = (name, email)
        return user_id

    def add_paper(self, filename="paper.pdf", file_path=None, status="live", fields=None, reasons=None,
                  file_sha256=None, note=None, uploaded_by=None) -> Paper:
        """Stores a paper in any status without going through the upload flow."""
        paper = Paper(id=self._next_id, filename=filename, file_path=file_path or f"backend/papers/{filename}",
                      fields=dict(fields or {k: None for k in PAPER_FIELDS}), status=status,
                      review_reasons=list(reasons or []), status_note=note, file_sha256=file_sha256,
                      upload_key=secrets.token_hex(16), uploaded_by=uploaded_by, uploaded_at=self.now)
        self.papers[paper.id] = paper
        self.retry_at[paper.id] = None
        self._next_id += 1
        return self._out(paper)

    # --- PostgresPaperStore contract ------------------------------------------------------------
    def _out(self, paper: Paper) -> Paper:
        name, email = self.users.get(paper.uploaded_by, (None, None))
        return replace(paper, fields=dict(paper.fields), review_reasons=list(paper.review_reasons),
                       uploader_name=name, uploader_email=email)

    def _active_owner(self, file_sha256, exclude_id=None):
        return next((p for p in self.papers.values() if file_sha256 and p.file_sha256 == file_sha256
                     and p.status != "failed" and p.id != exclude_id), None)

    def create_upload(self, filename, file_path, file_sha256, upload_key, uploaded_by) -> Paper:
        if self._active_owner(file_sha256):
            raise DuplicateFile()
        paper = Paper(id=self._next_id, filename=filename, file_path=file_path,
                      fields={k: None for k in PAPER_FIELDS}, status="processing", file_sha256=file_sha256,
                      upload_key=upload_key, uploaded_by=uploaded_by, uploaded_at=self.now)
        self.papers[paper.id] = paper
        self.retry_at[paper.id] = None
        self._next_id += 1
        return self._out(paper)

    def get(self, paper_id):
        paper = self.papers.get(paper_id)
        return self._out(paper) if paper else None

    def get_many(self, ids):
        return [self._out(self.papers[i]) for i in sorted(set(ids)) if i in self.papers]

    def get_by_upload_key(self, upload_key):
        return next((self._out(p) for p in self.papers.values() if p.upload_key == upload_key), None)

    def find_active_by_hash(self, file_sha256):
        paper = self._active_owner(file_sha256)
        return self._out(paper) if paper else None

    def list_by_uploader(self, user_id, limit=50):
        mine = sorted((p for p in self.papers.values() if p.uploaded_by == user_id), key=lambda p: -p.id)
        return [self._out(p) for p in mine[:limit]]

    def list_by_status(self, status):
        return [self._out(p) for p in sorted(self.papers.values(), key=lambda p: -p.id) if p.status == status]

    def list_all(self):
        return [self._out(p) for p in sorted(self.papers.values(), key=lambda p: p.id)]

    def counts(self):
        return {status: sum(1 for p in self.papers.values() if p.status == status) for status in STATUSES}

    def claim_next(self):
        due = [p for p in sorted(self.papers.values(), key=lambda p: p.id)
               if p.status == "processing" and (self.retry_at[p.id] is None or self.retry_at[p.id] <= self.now)]
        if not due:
            return None
        paper = replace(due[0], attempts=due[0].attempts + 1)
        self.papers[paper.id] = paper
        self.retry_at[paper.id] = self.now + timedelta(minutes=LEASE_MINUTES)
        return self._out(paper)

    def finish(self, paper_id, fields, status, reasons, note):
        if paper_id in self.papers:
            self.papers[paper_id] = replace(self.papers[paper_id], fields=dict(fields), status=status,
                                            review_reasons=list(reasons), status_note=note)
            self.retry_at[paper_id] = None

    def schedule_retry(self, paper_id, delay_seconds=RETRY_DELAY_SECONDS):
        self.retry_at[paper_id] = self.now + timedelta(seconds=delay_seconds)

    def mark_failed(self, paper_id, note=FAILED_NOTE):
        if paper_id in self.papers:
            self.papers[paper_id] = replace(self.papers[paper_id], status="failed", status_note=note)
            self.retry_at[paper_id] = None

    def update_details(self, paper_id, filename, fields, reviewer_id):
        if paper_id not in self.papers:
            return None
        self.papers[paper_id] = replace(self.papers[paper_id], filename=filename, fields=dict(fields),
                                        reviewed_by=reviewer_id, reviewed_at=self.now)
        return self.get(paper_id)

    def set_status(self, paper_id, status, note, reviewer_id):
        paper = self.papers.get(paper_id)
        if paper is None:
            return None
        if status != "failed" and self._active_owner(paper.file_sha256, exclude_id=paper_id):
            raise DuplicateFile()
        self.papers[paper_id] = replace(paper, status=status, status_note=note, reviewed_by=reviewer_id,
                                        reviewed_at=self.now, attempts=0 if status == "processing" else paper.attempts)
        self.retry_at[paper_id] = None
        return self.get(paper_id)

    def set_file_hash(self, paper_id, file_sha256):
        paper = self.papers[paper_id]
        if paper.status != "failed" and self._active_owner(file_sha256, exclude_id=paper_id):
            raise DuplicateFile()
        self.papers[paper_id] = replace(paper, file_sha256=file_sha256)

    def delete(self, paper_id):
        self.retry_at.pop(paper_id, None)
        return self.papers.pop(paper_id, None) is not None

    def count_file_users(self, file_path):
        return sum(1 for p in self.papers.values() if p.file_path == file_path)
```

- [ ] **Step 7: Run the contract tests (fake and Postgres)**

Run: `TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres backend/.venv/Scripts/python -m pytest backend/tests/test_paper_store.py -q`
Expected: 26 passed (13 fake + 13 postgres), or 13 passed and 13 skipped if Docker isn't running.

- [ ] **Step 8: Run the whole suite**

Run: `backend/.venv/Scripts/python -m pytest -q`
Expected: all pass. `test_reprocess.py` still passes because `PaperRow.status` defaults to `"live"`.

- [ ] **Step 9: Commit**

```bash
git add backend/database.py backend/services/paper_store.py backend/tests/fakes.py backend/tests/test_paper_store.py
git commit -m "feat: paper statuses and a paper store that doubles as the upload queue"
```

---

## Task 2: Status-aware search index

**Files:**
- Modify: `backend/services/vector_service.py`
- Modify: `backend/services/reprocess.py`
- Modify: `backend/tests/test_reprocess.py`
- Modify: `backend/tests/fakes.py` (add `FakeVectorIndex`)
- Create: `backend/tests/test_vector_service.py`

**Interfaces:**
- Consumes: `PaperRow.status` (Task 1).
- Produces, in `VectorService`:
  - `upsert_paper_vector(paper_id, vector, text, metadata, status) -> bool` (`status` is required)
  - `search_similar(query, limit=5, with_payload=True)`: live points only
  - `nearest_papers(vector, limit, exclude_id) -> list[ScoredPoint]` (raises on Qdrant errors)
  - `set_paper_payload(paper_id, payload) -> bool`
  - `delete_paper(paper_id) -> bool`
  - `get_paper_texts(ids) -> dict[int, str]`
  - module constant `LIVE_ONLY`
- Produces, in `tests/fakes.py`: `FakeVectorIndex` with the same six paper methods plus `get_embedding`, and `points: dict[int, dict]` and `neighbours: list[tuple[int, str]]`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_vector_service.py`:

```python
from types import SimpleNamespace

import pytest

from services import vector_service
from services.vector_service import LIVE_ONLY, VectorService


class FakeQdrant:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def _record(self, *call):
        if self.fail:
            raise RuntimeError("qdrant down")
        self.calls.append(call)

    def upsert(self, collection_name, points):
        self._record("upsert", points)

    def query_points(self, collection_name, query, limit, with_payload, query_filter=None):
        self._record("query", query, limit, query_filter)
        return SimpleNamespace(points=[SimpleNamespace(id=7, score=0.9, payload={"full_text": "t"})])

    def set_payload(self, collection_name, payload, points):
        self._record("set_payload", payload, points)

    def delete(self, collection_name, points_selector):
        self._record("delete", points_selector.points)

    def retrieve(self, collection_name, ids, with_payload):
        self._record("retrieve", ids)
        return [SimpleNamespace(id=i, payload={"full_text": f"text {i}"}) for i in ids]


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(VectorService, "_ensure_collection", lambda self: None)
    monkeypatch.setattr(vector_service, "embed_text", lambda text, input_type="passage": [0.1, 0.2])
    svc = VectorService()
    svc.client = FakeQdrant()
    return svc


def test_every_stored_vector_carries_its_status(service):
    assert service.upsert_paper_vector(3, [0.1], "text", {"filename": "a.pdf"}, "review") is True
    (_, points), = service.client.calls
    assert points[0].payload == {"filename": "a.pdf", "full_text": "text", "status": "review"}


def test_public_search_only_returns_live_papers(service):
    service.search_similar("Software Project Management", 4)
    (_, _, limit, query_filter), = service.client.calls
    assert limit == 4 and query_filter == LIVE_ONLY
    condition = query_filter.must_not[0]
    assert condition.key == "status" and set(condition.match.any) == {"review", "rejected"}


def test_nearest_papers_searches_every_status_but_skips_the_paper_itself(service):
    points = service.nearest_papers([0.3], 5, exclude_id=12)
    (_, vector, limit, query_filter), = service.client.calls
    assert (vector, limit) == ([0.3], 5)
    assert query_filter.must_not[0].has_id == [12]
    assert [p.id for p in points] == [7]


def test_nearest_papers_raises_when_qdrant_is_down(service):
    service.client = FakeQdrant(fail=True)
    with pytest.raises(RuntimeError):
        service.nearest_papers([0.3], 5, exclude_id=12)


def test_payload_delete_and_texts(service):
    assert service.set_paper_payload(3, {"status": "live"}) is True
    assert service.delete_paper(3) is True
    assert service.get_paper_texts([1, 2]) == {1: "text 1", 2: "text 2"}
    assert service.get_paper_texts([]) == {}
    assert [c[0] for c in service.client.calls] == ["set_payload", "delete", "retrieve"]


def test_payload_delete_and_texts_report_failure(service):
    service.client = FakeQdrant(fail=True)
    assert service.set_paper_payload(3, {"status": "live"}) is False
    assert service.delete_paper(3) is False
    assert service.get_paper_texts([1]) == {}
```

In `backend/tests/test_reprocess.py`, change the `Vectors` fake so it records statuses separately. This leaves every existing `vectors.upserts` unpacking untouched:

```python
class Vectors:
    def __init__(self, vector=(0.1, 0.2)):
        self.vector = list(vector) if vector else None
        self.upserts = []
        self.statuses = []

    def get_embedding(self, text, input_type="passage"):
        assert input_type == "passage"
        return self.vector

    def upsert_paper_vector(self, paper_id, vector, text, metadata, status):
        self.upserts.append((paper_id, text, metadata))
        self.statuses.append(status)
        return True
```

Then append these tests to `backend/tests/test_reprocess.py`:

```python
def test_apply_keeps_the_papers_status_in_the_index(row):
    vectors = Vectors()
    in_review = PaperRow(id=row.id, filename=row.filename, file_path=row.file_path, fields=dict(OLD), status="review")
    reprocess_paper(in_review, Processor({"subjectCode": "it 402"}), vectors, lambda *a: None, apply=True)
    assert vectors.statuses == ["review"]


@pytest.mark.parametrize("status", ["processing", "failed"])
def test_papers_the_upload_worker_owns_are_skipped(row, status):
    owned = PaperRow(id=row.id, filename=row.filename, file_path=row.file_path, fields=dict(OLD), status=status)
    processor, vectors = Processor({"subjectCode": "it 402"}), Vectors()
    result = reprocess_paper(owned, processor, vectors, lambda *a: None, apply=True)
    assert result.status == "skipped" and status in result.message
    assert vectors.upserts == [] and processor.full_text_calls == 0
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_vector_service.py backend/tests/test_reprocess.py -q`
Expected: failures. `ImportError: cannot import name 'LIVE_ONLY'`, and `TypeError: upsert_paper_vector() missing 1 required positional argument: 'status'` from reprocess.

- [ ] **Step 3: Implement the VectorService changes**

In `backend/services/vector_service.py`, add after the `VECTOR_SIZE` line:

```python
# Public search and RAG only see live papers. Points written before statuses existed have no
# `status` and count as live until `admin_cli.py dedupe --apply` tags them.
LIVE_ONLY = models.Filter(must_not=[models.FieldCondition(key="status", match=models.MatchAny(any=["review", "rejected"]))])
```

Replace `upsert_paper`, `upsert_paper_vector` and `search_similar` with the following, and add the new methods:

```python
    def upsert_paper(self, paper_id: int, text: str, metadata: dict):
        """Embeds and stores a live paper. (Removed once uploads go through the background worker.)"""
        vector = self.get_embedding(text)
        if not vector:
            return False
        return self.upsert_paper_vector(paper_id, vector, text, metadata, "live")

    def upsert_paper_vector(self, paper_id: int, vector: list, text: str, metadata: dict, status: str) -> bool:
        """Stores a precomputed vector with the paper's metadata, full text and moderation status."""
        try:
            payload = metadata.copy()
            payload["full_text"] = text
            payload["status"] = status
            self.client.upsert(
                collection_name=COLLECTION_NAME,
                points=[models.PointStruct(id=paper_id, vector=vector, payload=payload)]
            )
            return True
        except Exception as e:
            logger.error(f"Qdrant Upsert Error: {e}")
            return False

    def search_similar(self, query: str, limit: int = 5, with_payload: bool = True):
        """Searches live papers by vector similarity."""
        vector = self.get_embedding(query, input_type="query")
        if not vector:
            return []

        try:
            results = self.client.query_points(
                collection_name=COLLECTION_NAME,
                query=vector,
                limit=limit,
                with_payload=with_payload,
                query_filter=LIVE_ONLY,
            )
            return results.points
        except Exception as e:
            logger.error(f"Qdrant Search Error: {e}")
            return []

    def nearest_papers(self, vector: list, limit: int, exclude_id: int):
        """The closest stored papers of any status (only live, review and rejected papers have vectors).
        Raises when Qdrant fails, so the upload worker retries instead of skipping the duplicate check."""
        results = self.client.query_points(
            collection_name=COLLECTION_NAME,
            query=vector,
            limit=limit,
            with_payload=True,
            query_filter=models.Filter(must_not=[models.HasIdCondition(has_id=[exclude_id])]),
        )
        return results.points

    def set_paper_payload(self, paper_id: int, payload: dict) -> bool:
        """Updates some payload keys (e.g. status, details) without re-embedding."""
        try:
            self.client.set_payload(collection_name=COLLECTION_NAME, payload=payload, points=[paper_id])
            return True
        except Exception:
            logger.exception("Qdrant payload update failed for paper %s", paper_id)
            return False

    def delete_paper(self, paper_id: int) -> bool:
        try:
            self.client.delete(collection_name=COLLECTION_NAME,
                               points_selector=models.PointIdsList(points=[paper_id]))
            return True
        except Exception:
            logger.exception("Qdrant delete failed for paper %s", paper_id)
            return False

    def get_paper_texts(self, ids) -> dict[int, str]:
        """Stored full text per paper id; papers without a vector are missing from the result."""
        ids = list(ids)
        if not ids:
            return {}
        try:
            points = self.client.retrieve(collection_name=COLLECTION_NAME, ids=ids, with_payload=True)
        except Exception:
            logger.exception("Qdrant retrieve failed for papers %s", ids)
            return {}
        return {point.id: (point.payload or {}).get("full_text", "") for point in points}
```

- [ ] **Step 4: Update reprocess**

In `backend/services/reprocess.py`, add below `_MARKERS`:

```python
# The upload worker owns these; reprocessing them would race it or index a paper that never finished
WORKER_STATUSES = ("processing", "failed")
```

At the top of `reprocess_paper`, before the `pdf_path` line, add:

```python
    if row.status in WORKER_STATUSES:
        return ReprocessResult(row.id, row.filename, "skipped",
                               message=f"paper is {row.status}; the upload worker handles it")
```

Then change the upsert call to pass the status:

```python
        if not vector_service.upsert_paper_vector(row.id, vector, full_text, payload, row.status):
```

- [ ] **Step 5: Add `FakeVectorIndex` to the fakes**

Append to `backend/tests/fakes.py`:

```python
from types import SimpleNamespace


class FakeVectorIndex:
    """In-memory stand-in for VectorService's paper methods. `neighbours` is what nearest_papers returns."""

    def __init__(self, vector=(0.1, 0.2), fail_upsert=False, fail_payload=False, fail_nearest=False):
        self.vector = list(vector) if vector else None
        self.points: dict[int, dict] = {}
        self.neighbours: list[tuple[int, str]] = []
        self.embedded: list[str] = []
        self.fail_upsert = fail_upsert
        self.fail_payload = fail_payload
        self.fail_nearest = fail_nearest

    def get_embedding(self, text, input_type="passage"):
        self.embedded.append(text)
        return self.vector

    def upsert_paper_vector(self, paper_id, vector, text, metadata, status):
        if self.fail_upsert:
            return False
        self.points[paper_id] = {**metadata, "full_text": text, "status": status}
        return True

    def nearest_papers(self, vector, limit, exclude_id):
        if self.fail_nearest:
            raise RuntimeError("qdrant down")
        found = [SimpleNamespace(id=i, score=0.9, payload={"full_text": text})
                 for i, text in self.neighbours if i != exclude_id]
        return found[:limit]

    def set_paper_payload(self, paper_id, payload):
        if self.fail_payload:
            return False
        self.points.setdefault(paper_id, {}).update(payload)
        return True

    def delete_paper(self, paper_id):
        if self.fail_payload:
            return False
        self.points.pop(paper_id, None)
        return True

    def get_paper_texts(self, ids):
        return {i: self.points[i].get("full_text", "") for i in ids if i in self.points}
```

- [ ] **Step 6: Run the tests**

Run: `backend/.venv/Scripts/python -m pytest -q`
Expected: all pass. That includes `test_rag_context.py`, whose `FakeVectors.search_similar(query, limit, with_payload=True)` signature is unchanged.

- [ ] **Step 7: Commit**

```bash
git add backend/services/vector_service.py backend/services/reprocess.py backend/tests/test_reprocess.py backend/tests/fakes.py backend/tests/test_vector_service.py
git commit -m "feat: store each paper's moderation status in the search index; public search sees live papers only"
```

---

## Task 3: Automatic checks

**Files:**
- Create: `backend/services/moderation.py`
- Create: `backend/tests/test_moderation.py`
- Already committed with this plan: `backend/tests/fixtures/moderation/{ai_major_2023,ai_major_2023_rescan,cloud_sec_major_2023_set_a,cloud_sec_major_2023_set_b}.txt`. These are real OCR texts from production, with enrollment numbers blanked. The AI pair is one paper scanned twice (overlap 0.825); the Cloud Sec pair is two different papers from the same session (0.025).
- Modify: `backend/tests/fakes.py` (add `moderation_fixture`)

**Interfaces:**
- Produces, in `services/moderation.py`:
  - constants `MIN_READABLE_LETTERS`, `DUPLICATE_OVERLAP`, `DUPLICATE_CANDIDATES`, `REQUIRED_DETAILS`
  - `readable_letters(text) -> int`
  - `looks_like_exam_paper(text) -> bool`
  - `missing_details(fields) -> list[str]`
  - `shingles(text) -> set[str]`
  - `jaccard(a, b) -> float`
  - `text_overlap(a, b) -> float`
  - `review_reasons(text, fields, candidates: list[tuple[int, str]]) -> list[dict]`
- Produces, in `tests/fakes.py`: `moderation_fixture(name) -> str`.

- [ ] **Step 1: Add the fixture loader**

Append to `backend/tests/fakes.py`:

```python
from pathlib import Path

_FIXTURES = Path(__file__).parent / "fixtures" / "moderation"


def moderation_fixture(name: str) -> str:
    """Real OCR text of a production paper (see fixtures/moderation)."""
    return (_FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_moderation.py`:

```python
import pytest

from services.moderation import (DUPLICATE_OVERLAP, MIN_READABLE_LETTERS, looks_like_exam_paper, missing_details,
                                 readable_letters, review_reasons, text_overlap)
from tests.fakes import moderation_fixture

AI = moderation_fixture("ai_major_2023")
AI_RESCAN = moderation_fixture("ai_major_2023_rescan")
CLOUD_A = moderation_fixture("cloud_sec_major_2023_set_a")
CLOUD_B = moderation_fixture("cloud_sec_major_2023_set_b")
FIELDS = {"subject_code": "CSE401", "subject_name": "Artificial Intelligence", "semester": None,
          "year": "April-May, 2023", "time": "3 Hrs", "marks": "60"}


def test_a_rescan_of_the_same_paper_overlaps_heavily():
    assert text_overlap(AI, AI_RESCAN) == pytest.approx(0.825, abs=0.01)
    assert text_overlap(AI, AI) == 1.0


def test_different_papers_from_the_same_session_barely_overlap():
    assert text_overlap(CLOUD_A, CLOUD_B) < 0.05
    assert text_overlap(AI, CLOUD_B) < 0.05


def test_overlap_of_empty_text_is_zero():
    assert text_overlap("", AI) == 0.0


def test_real_papers_look_like_exam_papers():
    for text in (AI, AI_RESCAN, CLOUD_A, CLOUD_B):
        assert looks_like_exam_paper(text)
        assert readable_letters(text) > MIN_READABLE_LETTERS


@pytest.mark.parametrize("text", [
    "Lecture 4 notes. Section 1 covers heuristics. Answer the reading questions. Time to revise.",  # no marks
    "Maximum Marks 60. Section A. Attempt all questions.",  # no time
    "Time: 3 Hrs. Maximum Marks: 60. Section A, Section B, Section C.",  # only one kind of exam word
])
def test_text_missing_an_exam_marker_is_not_an_exam_paper(text):
    assert not looks_like_exam_paper(text)


def test_time_can_be_written_as_hours():
    assert looks_like_exam_paper("01 Hr. Max marks 30. Section A. Attempt any two.")


def test_page_markers_and_ocr_errors_are_not_readable_text():
    assert readable_letters("--- Page 1 ---\n[OCR Error]\n--- Page 2 ---\nabc 12") == 3


def test_missing_details_lists_empty_required_fields():
    assert missing_details(FIELDS) == []
    assert missing_details({**FIELDS, "subject_code": None, "year": "  "}) == ["subject_code", "year"]


def test_clean_paper_has_no_reasons():
    assert review_reasons(AI, FIELDS, [(9, CLOUD_B)]) == []


def test_likely_copy_is_flagged_with_the_matching_paper():
    assert review_reasons(AI_RESCAN, FIELDS, [(9, CLOUD_B), (3, AI)]) == [
        {"code": "possible_duplicate", "paperId": 3, "overlap": 0.83}]


def test_copies_are_listed_closest_first():
    reasons = review_reasons(AI, FIELDS, [(2, AI_RESCAN), (4, AI)])
    assert [r["paperId"] for r in reasons] == [4, 2]


def test_overlap_threshold_is_inclusive(monkeypatch):
    monkeypatch.setattr("services.moderation.text_overlap", lambda a, b: DUPLICATE_OVERLAP)
    assert review_reasons(AI, FIELDS, [(5, "x")]) == [{"code": "possible_duplicate", "paperId": 5, "overlap": 0.5}]
    monkeypatch.setattr("services.moderation.text_overlap", lambda a, b: DUPLICATE_OVERLAP - 0.01)
    assert review_reasons(AI, FIELDS, [(5, "x")]) == []


def test_unreadable_text_skips_the_content_checks_but_still_reports_missing_details():
    reasons = review_reasons("--- Page 1 ---\nblurry", {**FIELDS, "year": None}, [(3, "--- Page 1 ---\nblurry")])
    assert reasons == [{"code": "unreadable", "letters": 6}, {"code": "missing_details", "fields": ["year"]}]


def test_non_exam_text_is_flagged():
    notes = "These are my lecture notes about search algorithms. " * 10
    assert review_reasons(notes, FIELDS, []) == [{"code": "not_exam_paper"}]
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_moderation.py -q`
Expected: `ModuleNotFoundError: No module named 'services.moderation'`.

- [ ] **Step 4: Implement the checks**

Create `backend/services/moderation.py`:

```python
"""Automatic checks run on every upload. Any flag holds the paper for an admin; none means it goes live.

Thresholds were measured on the 14 production papers (2026-09-26): the smallest real paper has 806
readable letters; every paper mentions marks and time and uses at least 4 distinct exam words; re-scans
of one paper overlap 0.83-1.00, different papers at most 0.07.
"""
import re

from services.rag_service import RAGService

MIN_READABLE_LETTERS = 300
DUPLICATE_OVERLAP = 0.5
DUPLICATE_CANDIDATES = 5
REQUIRED_DETAILS = ("subject_code", "subject_name", "year")

_NOISE_LINE = re.compile(r"^(--- Page \d+ ---|\[OCR Error\])$")
_MARKS = re.compile(r"\bmarks?\b", re.IGNORECASE)
_TIME = re.compile(r"\btime\b|\b\d+(\.\d+)?\s*(hrs?|hours?)\b", re.IGNORECASE)
_EXAM_WORDS = re.compile(r"\b(section|attempt|answer|compulsory|examination|semester)\b", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")


def readable_letters(text: str) -> int:
    """Letters in the OCR text, ignoring the page markers and OCR error lines the pipeline adds."""
    lines = [line for line in (text or "").splitlines() if not _NOISE_LINE.match(line.strip())]
    return sum(ch.isalpha() for ch in "\n".join(lines))


def looks_like_exam_paper(text: str) -> bool:
    """Mentions marks and a time limit, and uses at least two different exam words."""
    text = text or ""
    exam_words = {word.lower() for word in _EXAM_WORDS.findall(text)}
    return bool(_MARKS.search(text)) and bool(_TIME.search(text)) and len(exam_words) >= 2


def missing_details(fields: dict) -> list[str]:
    return [key for key in REQUIRED_DETAILS if not (fields.get(key) or "").strip()]


def shingles(text: str) -> set[str]:
    """3-word shingles of the question text (the exam header is dropped: it's the same on every paper)."""
    words = _WORD.findall(RAGService._questions_only(text or "").lower())
    return {" ".join(words[i:i + 3]) for i in range(len(words) - 2)}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def text_overlap(a: str, b: str) -> float:
    return jaccard(shingles(a), shingles(b))


def review_reasons(text: str, fields: dict, candidates: list[tuple[int, str]]) -> list[dict]:
    """Flags for the review queue; an empty list means the paper can go live.

    `candidates` are (paper_id, stored_text) pairs for the nearest stored papers.
    """
    reasons: list[dict] = []
    letters = readable_letters(text)
    if letters < MIN_READABLE_LETTERS:
        reasons.append({"code": "unreadable", "letters": letters})
    else:
        if not looks_like_exam_paper(text):
            reasons.append({"code": "not_exam_paper"})
        copies = []
        for paper_id, other_text in candidates:
            overlap = text_overlap(text, other_text)
            if overlap >= DUPLICATE_OVERLAP:
                copies.append({"code": "possible_duplicate", "paperId": paper_id, "overlap": round(overlap, 2)})
        reasons.extend(sorted(copies, key=lambda reason: -reason["overlap"]))
    missing = missing_details(fields)
    if missing:
        reasons.append({"code": "missing_details", "fields": missing})
    return reasons
```

- [ ] **Step 5: Run the tests**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_moderation.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/services/moderation.py backend/tests/test_moderation.py backend/tests/fakes.py
git commit -m "feat: automatic upload checks (unreadable, not an exam paper, missing details, likely copy)"
```

---

## Task 4: Background worker

**Files:**
- Create: `backend/services/paper_worker.py`
- Modify: `backend/main.py` (lifespan)
- Modify: `backend/tests/conftest.py` (worker off in tests)
- Create: `backend/tests/test_paper_worker.py`

**Interfaces:**
- Consumes:
  - From Task 1: `PostgresPaperStore` methods `claim_next`, `finish`, `schedule_retry` and `mark_failed`; `REVIEW_NOTE`, `MAX_ATTEMPTS`, `resolve_paper_path`.
  - From Task 2: `VectorService` methods `get_embedding`, `nearest_papers` and `upsert_paper_vector`.
  - From Task 3: `review_reasons` and `DUPLICATE_CANDIDATES`.
- Produces, in `services/paper_worker.py`:
  - `extract_metadata_with_retry(processor, file_path) -> dict`
  - `process_paper(paper, store, processor, vectors) -> None` (never raises)
  - `class PaperWorker(store_factory, processor_factory, vectors_factory, idle_seconds)` with `start()`, `stop(timeout=5.0)`, `wake()` and `run_once(store, processor, vectors) -> bool`
  - `paper_worker` (module singleton)
  - `worker_enabled() -> bool`

- [ ] **Step 1: Turn the worker off in tests**

In `backend/tests/conftest.py`, add with the other `os.environ[...]` lines:

```python
os.environ["PAPER_WORKER_ENABLED"] = "false"
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_paper_worker.py`:

```python
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
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_paper_worker.py -q`
Expected: `ModuleNotFoundError: No module named 'services.paper_worker'`.

- [ ] **Step 4: Implement the worker**

Create `backend/services/paper_worker.py`:

```python
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
```

- [ ] **Step 5: Start the worker with the app**

In `backend/main.py`, import it with the other imports:

```python
from services.paper_worker import paper_worker, worker_enabled
```

Then change `lifespan` to:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
    except Exception:
        logger.exception("Database initialisation failed; continuing so the API can still start")
    if worker_enabled():
        paper_worker.start()
    yield
    paper_worker.stop()
```

- [ ] **Step 6: Run the tests**

Run: `backend/.venv/Scripts/python -m pytest -q`
Expected: all pass. `test_nonblocking.py` enters the lifespan too; the worker stays off because conftest sets `PAPER_WORKER_ENABLED=false`.

- [ ] **Step 7: Commit**

```bash
git add backend/services/paper_worker.py backend/main.py backend/tests/conftest.py backend/tests/test_paper_worker.py
git commit -m "feat: background worker that processes uploads and survives restarts"
```

---

## Task 5: Upload endpoint

**Files:**
- Create: `backend/services/upload_checks.py`
- Modify: `backend/errors.py` (`ApiError.extra`)
- Modify: `backend/api/routes.py` (ingest rewrite; remove the old synchronous pipeline)
- Modify: `backend/services/paper_store.py` (remove `insert_paper`)
- Modify: `backend/services/vector_service.py` (remove `upsert_paper`)
- Modify: `backend/tests/fakes.py` (remove `FakeVectorService`)
- Modify: `backend/tests/test_uploads.py` (rewrite)
- Modify: `backend/tests/test_errors.py` (add the `extra` test)

**Interfaces:**
- Consumes:
  - From Task 1: `get_paper_store`, `new_upload_key`, `papers_dir`, `DuplicateFile`, `PAPERS_DIR`, and the `find_active_by_hash` and `create_upload` store methods.
  - From Task 4: `paper_worker.wake()`.
- Produces, in `services/upload_checks.py`:
  - constants `MAX_UPLOAD_BYTES` and `MAX_PAGES`
  - `class FileTooLarge(Exception)`
  - `filename_problem(name) -> str | None`
  - `save_upload(source, destination, max_bytes) -> str` (sha256 hex)
  - `count_pdf_pages(path) -> int | None`
  - `remove_quietly(path) -> None`
  - `duplicate_paper_error(existing: Paper | None) -> ApiError`
- Produces: `ApiError(..., extra: dict | None = None)`, whose keys are merged into the JSON body.
- Produces: `POST /api/v1/documents/ingest` → 202 `{"id", "uploadKey", "filename", "status"}`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_errors.py`:

```python
def test_api_error_extra_fields_are_added_to_the_body_but_cannot_replace_detail_or_code():
    import asyncio

    from errors import ApiError, api_error_handler

    error = ApiError(409, "duplicate_paper", "Already here.", extra={"paperId": 3, "code": "sneaky"})
    response = asyncio.run(api_error_handler(None, error))
    assert response.status_code == 409
    assert response.body == b'{"paperId":3,"code":"duplicate_paper","detail":"Already here."}'
```

Replace `backend/tests/test_uploads.py` with:

```python
import hashlib
import io
import itertools
import shutil

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.datastructures import UploadFile as StarletteUploadFile

from errors import ApiError
from services.paper_store import DuplicateFile, get_paper_store
from services.upload_checks import FileTooLarge, count_pdf_pages, filename_problem, save_upload
from tests.fakes import FakePaperStore

URL = "/api/v1/documents/ingest"
_counter = itertools.count()


def pdf_file(name="paper.pdf", content=None):
    """A distinct file each call, so repeated uploads aren't treated as copies of each other."""
    return (name, content or b"%PDF-1.4 fake " + str(next(_counter)).encode(), "application/pdf")


@pytest.fixture(autouse=True)
def papers(client, monkeypatch, tmp_path):
    fake = FakePaperStore()
    client.app.dependency_overrides[get_paper_store] = lambda: fake
    monkeypatch.setattr("services.paper_store.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("api.routes.count_pdf_pages", lambda path: 1)
    return fake


@pytest.fixture
def woken(monkeypatch):
    calls = []
    monkeypatch.setattr("api.routes.paper_worker.wake", lambda: calls.append(1))
    return calls


def upload(client, headers=None, file=None):
    return client.post(URL, files={"file": file or pdf_file()}, headers=headers or {})


def stored_files(tmp_path):
    folder = tmp_path / "backend" / "papers"
    return sorted(folder.iterdir()) if folder.exists() else []


def test_upload_is_accepted_for_background_processing(client, store, papers, woken, tmp_path):
    content = b"%PDF-1.4 the paper"
    response = upload(client, file=pdf_file("SPM 23.pdf", content))
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "processing" and body["filename"] == "SPM 23.pdf"
    assert len(body["uploadKey"]) == 32
    paper = papers.get(body["id"])
    assert paper.status == "processing" and paper.uploaded_by is None
    assert paper.file_path == f"backend/papers/{body['uploadKey']}.pdf"
    assert paper.file_sha256 == hashlib.sha256(content).hexdigest()
    assert (tmp_path / "backend" / "papers" / f"{body['uploadKey']}.pdf").read_bytes() == content
    assert woken == [1]


def test_same_name_uploads_are_stored_separately(client, store, papers, tmp_path):
    first = upload(client, file=pdf_file("AI Major 2023.pdf")).json()
    second = upload(client, file=pdf_file("AI Major 2023.pdf")).json()
    assert first["uploadKey"] != second["uploadKey"]
    assert len(stored_files(tmp_path)) == 2


def test_exact_copy_is_refused_without_using_quota(client, store, papers, tmp_path):
    content = b"%PDF-1.4 same bytes"
    first = upload(client, file=pdf_file("a.pdf", content)).json()
    papers.finish(first["id"], papers.get(first["id"]).fields, "live", [], None)
    used = sum(store.usage.values())

    response = upload(client, file=pdf_file("renamed.pdf", content))
    assert response.status_code == 409
    assert response.json() == {"code": "duplicate_paper", "detail": "This paper is already on PrepWise.",
                               "paperId": first["id"], "paperStatus": "live"}
    assert sum(store.usage.values()) == used
    assert len(stored_files(tmp_path)) == 1


@pytest.mark.parametrize("status,message", [
    ("processing", "This paper was already uploaded and is being checked."),
    ("review", "This paper was already uploaded and is being checked."),
    ("rejected", "This file was already reviewed and not accepted."),
])
def test_copy_message_depends_on_the_existing_papers_status(client, store, papers, status, message):
    content = b"%PDF-1.4 dup " + status.encode()
    papers.add_paper(status=status, file_sha256=hashlib.sha256(content).hexdigest())
    response = upload(client, file=pdf_file("x.pdf", content))
    assert response.status_code == 409 and response.json()["detail"] == message


def test_a_failed_copy_does_not_block_uploading_again(client, store, papers):
    content = b"%PDF-1.4 failed before"
    papers.add_paper(status="failed", file_sha256=hashlib.sha256(content).hexdigest())
    assert upload(client, file=pdf_file("x.pdf", content)).status_code == 202


def test_simultaneous_copy_is_refused_and_refunded(client, store, papers, monkeypatch, tmp_path):
    def racing_insert(*args, **kwargs):
        raise DuplicateFile()

    monkeypatch.setattr(papers, "create_upload", racing_insert)
    response = upload(client)
    assert response.status_code == 409 and response.json()["code"] == "duplicate_paper"
    assert sum(store.usage.values()) == 0
    assert stored_files(tmp_path) == []


def test_database_failure_is_generic_refunded_and_cleaned_up(client, store, papers, monkeypatch, tmp_path):
    def broken_insert(*args, **kwargs):
        raise RuntimeError("connection to 10.0.0.5 failed")

    monkeypatch.setattr(papers, "create_upload", broken_insert)
    response = upload(client)
    assert response.status_code == 500 and "10.0.0.5" not in response.text
    assert sum(store.usage.values()) == 0
    assert stored_files(tmp_path) == []


def test_file_over_the_size_limit_is_refused(client, store, papers, monkeypatch, tmp_path):
    monkeypatch.setattr("api.routes.MAX_UPLOAD_BYTES", 10)
    response = upload(client, file=pdf_file("big.pdf", b"%PDF-1.4 more than ten bytes"))
    assert response.status_code == 413 and response.json()["code"] == "file_too_large"
    assert store.usage == {} and stored_files(tmp_path) == []


def test_too_many_pages_is_refused(client, store, papers, monkeypatch, tmp_path):
    monkeypatch.setattr("api.routes.count_pdf_pages", lambda path: 21)
    response = upload(client)
    assert response.status_code == 400 and response.json()["code"] == "too_many_pages"
    assert "21 pages" in response.json()["detail"]
    assert store.usage == {} and stored_files(tmp_path) == []


def test_unreadable_pdf_is_refused(client, store, papers, monkeypatch, tmp_path):
    monkeypatch.setattr("api.routes.count_pdf_pages", lambda path: None)
    response = upload(client)
    assert response.status_code == 400 and response.json()["code"] == "invalid_file"
    assert store.usage == {} and stored_files(tmp_path) == []


def test_anonymous_limit_is_per_ip(client, store):
    assert [upload(client).status_code for _ in range(6)] == [202] * 5 + [429]
    other_ip = TestClient(client.app, client=("198.51.100.7", 50000))
    assert upload(other_ip).status_code == 202


def test_anonymous_usage_is_stored_as_hash(client, store):
    upload(client)
    (subject, action), = store.usage.keys()
    # IP_HASH_SALT is "test-salt" (conftest); TestClient's default client host is "testclient",
    # which isn't a parseable IP, so it's hashed as-is rather than normalised by ip_subject.
    expected = "ip:" + hashlib.sha256(("test-salt" + "testclient").encode()).hexdigest()
    assert subject == expected and action == "upload"


def test_student_limit(client, auth_headers, monkeypatch):
    monkeypatch.setenv("UPLOAD_LIMIT_STUDENT", "2")
    headers = auth_headers(role="student")
    assert [upload(client, headers).status_code for _ in range(3)] == [202, 202, 429]


def test_trial_faculty_cannot_upload_papers(client, auth_headers, store):
    response = upload(client, auth_headers(role="faculty"))
    assert response.status_code == 403 and response.json()["code"] == "forbidden"
    assert store.usage == {}


def test_verified_faculty_and_admin_can_upload(client, auth_headers, store, papers):
    assert upload(client, auth_headers(role="faculty", verified=True)).status_code == 202
    assert upload(client, auth_headers(role="admin")).status_code == 202
    assert all(p.uploaded_by is not None for p in papers.list_all())


def test_user_without_role_must_choose_first(client, auth_headers):
    assert upload(client, auth_headers()).json()["code"] == "role_required"


def test_non_pdf_is_rejected_without_using_quota(client, store):
    response = upload(client, file=("notes.txt", b"hello", "text/plain"))
    assert response.status_code == 400 and response.json()["code"] == "invalid_file"
    assert store.usage == {}


def test_pdf_named_file_without_pdf_content_is_rejected_without_using_quota(client, store, papers):
    response = upload(client, file=("fake.pdf", b"hello world", "application/pdf"))
    assert response.status_code == 400 and response.json()["code"] == "invalid_file"
    assert store.usage == {} and papers.list_all() == []


# These three odd names are transmitted unchanged by the TestClient's multipart encoder, so they
# exercise the validation through the real endpoint (role/limit checks -> name checks -> quota).
@pytest.mark.parametrize("filename", [".pdf", "a" * 250 + ".pdf", "a?b.pdf"])
def test_odd_filenames_are_rejected_without_using_quota(client, store, papers, filename):
    response = upload(client, file=(filename, b"%PDF-1.4 fake", "application/pdf"))
    assert response.status_code == 400 and response.json()["code"] == "invalid_file"
    assert store.usage == {} and papers.list_all() == []


# A literal embedded null byte and a literal double-quote don't survive the TestClient's multipart
# encoder unchanged (httpx percent-encodes them: '\x00' -> '%00', '"' -> '%22'), so there is no way
# to exercise these two through the HTTP endpoint. Tested directly against the helper instead.
@pytest.mark.parametrize("filename", ["a\x00b.pdf", 'a"b.pdf'])
def test_validated_pdf_upload_rejects_bad_characters_in_name(filename):
    from api.routes import _validated_pdf_upload

    upload_file = StarletteUploadFile(io.BytesIO(b"%PDF-1.4 x"), filename=filename)
    with pytest.raises(ApiError) as exc_info:
        _validated_pdf_upload(upload_file)
    assert exc_info.value.code == "invalid_file"


@pytest.mark.parametrize("filename", ["..\\..\\evil.pdf", "../../evil.pdf"])
def test_validated_pdf_upload_collapses_traversal_filenames(filename):
    from api.routes import _validated_pdf_upload

    upload_file = StarletteUploadFile(io.BytesIO(b"%PDF-1.4 x"), filename=filename)
    assert _validated_pdf_upload(upload_file) == "evil.pdf"


def test_traversal_filename_is_collapsed_end_to_end(client, store, papers):
    # httpx's multipart encoder transmits a backslash-containing filename unchanged, so this
    # exercises the same traversal payload through the real endpoint.
    response = upload(client, file=pdf_file("..\\..\\evil.pdf"))
    assert response.status_code == 202
    paper = papers.get(response.json()["id"])
    assert paper.filename == "evil.pdf" and ".." not in paper.file_path


def test_save_upload_hashes_what_it_writes(tmp_path):
    destination = tmp_path / "out.pdf"
    assert save_upload(io.BytesIO(b"abc"), destination, 10) == hashlib.sha256(b"abc").hexdigest()
    assert destination.read_bytes() == b"abc"


def test_save_upload_stops_and_cleans_up_past_the_limit(tmp_path):
    destination = tmp_path / "out.pdf"
    with pytest.raises(FileTooLarge):
        save_upload(io.BytesIO(b"x" * 11), destination, 10)
    assert not destination.exists()


# CI's test job doesn't install poppler (the Docker image does), so this one only runs where pdfinfo exists
@pytest.mark.skipif(shutil.which("pdfinfo") is None, reason="poppler (pdfinfo) not installed")
def test_count_pdf_pages_reads_real_pdfs(tmp_path):
    path = tmp_path / "three.pdf"
    pages = [Image.new("RGB", (20, 20), "white") for _ in range(3)]
    pages[0].save(path, "PDF", save_all=True, append_images=pages[1:])
    assert count_pdf_pages(path) == 3
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"%PDF-1.4 not really")
    assert count_pdf_pages(junk) is None


@pytest.mark.parametrize("name", ["a/b.pdf", "a\\b.pdf", "tab\t.pdf", "a<b.pdf", ".pdf", "x" * 200 + ".pdf"])
def test_filename_problem_rejects_unsafe_display_names(name):
    assert filename_problem(name) is not None


def test_filename_problem_accepts_normal_names():
    assert filename_problem("AI Major 2023 (Set B).pdf") is None
    assert filename_problem("notes.txt") == "Only PDF files are supported."
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_uploads.py backend/tests/test_errors.py -q`
Expected: `ModuleNotFoundError: No module named 'services.upload_checks'`, and the `extra` test failing with `TypeError`.

- [ ] **Step 3: Add `extra` to `ApiError`**

In `backend/errors.py`:

```python
class ApiError(Exception):
    """An error the client is allowed to see: returned as {"detail": ..., "code": ..., **extra}."""

    def __init__(self, status_code: int, code: str, detail: str, headers: dict[str, str] | None = None,
                 extra: dict | None = None):
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.headers = headers
        self.extra = extra or {}


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return JSONResponse({**exc.extra, "code": exc.code, "detail": exc.detail}, status_code=exc.status_code,
                        headers=exc.headers)
```

Check the byte order the test expects. It is `{"paperId":3,"code":"duplicate_paper","detail":"Already here."}`: `extra` keys first, then `code`, then `detail`. The existing tests compare parsed JSON, so the key order doesn't affect them.

- [ ] **Step 4: Create the upload checks**

Create `backend/services/upload_checks.py`:

```python
"""Checks on an uploaded file before it's accepted: name, size, page count, and exact copies."""
import hashlib
import os

from pdf2image import pdfinfo_from_path

from errors import ApiError
from services.paper_store import Paper

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
        return int(pdfinfo_from_path(str(path))["Pages"])
    except Exception:
        return None


def duplicate_paper_error(existing: Paper | None) -> ApiError:
    status = existing.status if existing is not None else "processing"
    return ApiError(409, "duplicate_paper", DUPLICATE_MESSAGES.get(status, DUPLICATE_MESSAGES["processing"]),
                    extra={"paperId": existing.id if existing is not None else None, "paperStatus": status})
```

- [ ] **Step 5: Rewrite the ingest endpoint**

In `backend/api/routes.py`:

1. Remove these, which moved to the worker:
   - the `from services.ocr_service import DocumentProcessor` import, `import shutil`, and the `from services.paper_metadata import to_paper_fields` import
   - the `processor = DocumentProcessor(...)` line and its comment
   - `REQUIRED_METADATA`, `METADATA_ATTEMPTS` and `_INVALID_FILENAME_CHARS`
   - `_extract_metadata_with_retry` and `_index_paper`
2. Replace the `services.paper_store` import with:
   ```python
   from services.paper_store import (PAPERS_DIR, DuplicateFile, PostgresPaperStore, get_paper_store, new_upload_key,
                                     papers_dir, resolve_paper_path)
   from services.paper_worker import paper_worker
   from services.upload_checks import (MAX_PAGES, MAX_UPLOAD_BYTES, FileTooLarge, count_pdf_pages,
                                       duplicate_paper_error, filename_problem, remove_quietly, save_upload)
   ```
3. Change the name part of `_validated_pdf_upload` to use `filename_problem`. Keep the docstring, the `ntpath.basename` call and the `%PDF` header check:
   ```python
       name = ntpath.basename(file.filename or "")
       problem = filename_problem(name)
       if problem:
           raise ApiError(400, "invalid_file", problem)
   ```
4. Replace `ingest_document` with:

```python
def _too_large() -> ApiError:
    return ApiError(413, "file_too_large",
                    f"This file is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. Compress it or split it, then try again.")


@router.post("/documents/ingest", status_code=202)
def ingest_document(
    http_request: Request,
    file: UploadFile = File(...),
    user: User | None = Depends(optional_user),
    store: PostgresUserStore = Depends(get_user_store),
    papers: PostgresPaperStore = Depends(get_paper_store),
):
    """Checks and stores an uploaded PDF. OCR, metadata, the automatic checks and indexing run in the
    background worker; the uploader polls /documents/uploads/{uploadKey} for the result."""
    if user is not None and user.role is None:
        raise ApiError(403, "role_required", "Choose Student or Faculty to continue.")
    limit = upload_limit(user)
    if limit == 0:
        raise ApiError(403, "forbidden", "Paper uploads need a student or verified faculty account.")

    filename = _validated_pdf_upload(file)
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise _too_large()

    upload_key = new_upload_key()
    stored_name = f"{upload_key}.pdf"
    destination = papers_dir() / stored_name
    try:
        file_sha256 = save_upload(file.file, destination, MAX_UPLOAD_BYTES)
    except FileTooLarge:
        raise _too_large()

    try:
        pages = count_pdf_pages(destination)
        if pages is None:
            raise ApiError(400, "invalid_file", "This file isn't a valid PDF.")
        if pages > MAX_PAGES:
            raise ApiError(400, "too_many_pages",
                           f"This PDF has {pages} pages. Papers can have at most {MAX_PAGES}.")
        existing = papers.find_active_by_hash(file_sha256)
        if existing is not None:
            raise duplicate_paper_error(existing)

        subject = quota_subject(user, http_request)
        enforce_quota(store, subject, "upload", limit)
        try:
            paper = papers.create_upload(filename, f"{PAPERS_DIR}/{stored_name}", file_sha256, upload_key,
                                         user.id if user is not None else None)
        except DuplicateFile:
            # Another upload of the same file got in first
            refund_quota_if_counted(store, subject, "upload", limit)
            raise duplicate_paper_error(papers.find_active_by_hash(file_sha256))
        except Exception:
            refund_quota_if_counted(store, subject, "upload", limit)
            raise
    except BaseException:
        remove_quietly(destination)
        raise

    paper_worker.wake()
    return {"id": paper.id, "uploadKey": upload_key, "filename": filename, "status": paper.status}
```

- [ ] **Step 6: Remove the old synchronous pipeline's leftovers**

- `backend/services/paper_store.py`: delete `insert_paper`.
- `backend/services/vector_service.py`: delete `upsert_paper`.
- `backend/tests/fakes.py`: delete `class FakeVectorService`.

Check nothing still uses them:

Run: `grep -rn "insert_paper\|upsert_paper(\|FakeVectorService\|api.routes.processor\|_index_paper\|_extract_metadata_with_retry" backend --include=*.py | grep -v .venv`
Expected: no output.

- [ ] **Step 7: Run the tests**

Run: `backend/.venv/Scripts/python -m pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add backend/services/upload_checks.py backend/errors.py backend/api/routes.py backend/services/paper_store.py backend/services/vector_service.py backend/tests/fakes.py backend/tests/test_uploads.py backend/tests/test_errors.py
git commit -m "feat: uploads are checked, stored under unique names and queued; exact copies refused"
```

---

## Task 6: Public visibility and uploader status

**Files:**
- Modify: `backend/api/routes.py` (public list, download and semantic search are live only; new upload status route)
- Modify: `backend/api/me.py` (`GET /me/uploads`)
- Modify: `backend/tests/fakes.py` (`fake_db_cursor` records SQL)
- Modify: `backend/tests/test_public_routes.py`, `backend/tests/test_me.py`, `backend/tests/test_startup.py`

**Interfaces:**
- Consumes: from Task 1, `get_paper_store`, `upload_status`, and the `get_by_upload_key` and `list_by_uploader` store methods.
- Produces:
  - `GET /api/v1/documents/uploads/{upload_key}` → `{"id", "filename", "status", "note", "uploadedAt"}`
  - `GET /api/v1/me/uploads` → `{"uploads": [same shape]}`

- [ ] **Step 1: Make `fake_db_cursor` record the SQL it receives**

In `backend/tests/fakes.py`, replace `fake_db_cursor` with:

```python
@contextmanager
def fake_db_cursor(rows, executed=None):
    """A db_cursor replacement whose fetchone/fetchall return the given rows; SQL is appended to `executed`."""

    class _Cursor:
        def execute(self, sql, params=None):
            if executed is not None:
                executed.append((sql, params))

        def fetchone(self):
            return rows[0] if rows else None

        def fetchall(self):
            return rows

    yield _Cursor()
```

- [ ] **Step 2: Write the failing tests**

Append to `backend/tests/test_public_routes.py`:

```python
from types import SimpleNamespace

import pytest

from services.paper_store import get_paper_store
from tests.fakes import FakePaperStore


def test_public_list_and_download_only_show_live_papers(client, store, monkeypatch):
    executed = []
    monkeypatch.setattr("api.routes.db_cursor", lambda: fake_db_cursor([], executed))
    client.get("/api/v1/documents")
    client.get("/api/v1/documents/5/download")
    assert len(executed) == 2
    assert all("status = 'live'" in sql for sql, _ in executed)


def test_semantic_search_only_joins_live_papers(client, store, monkeypatch):
    executed = []

    class Vectors:
        def search_similar(self, query, limit):
            return [SimpleNamespace(id=4, score=0.8)]

    monkeypatch.setattr("api.routes.VectorService", Vectors)
    monkeypatch.setattr("api.routes.db_cursor", lambda: fake_db_cursor([], executed))
    client.post("/api/v1/search/semantic", json={"query": "risk"})
    (sql, params), = executed
    assert "status = 'live'" in sql and params == (4,)


@pytest.fixture
def papers(client):
    fake = FakePaperStore()
    client.app.dependency_overrides[get_paper_store] = lambda: fake
    return fake


def test_uploader_can_check_status_with_the_upload_key(client, store, papers):
    paper = papers.add_paper(filename="SPM 23.pdf", status="rejected", note="Not an exam paper.")
    response = client.get(f"/api/v1/documents/uploads/{paper.upload_key}")
    assert response.status_code == 200
    assert response.json() == {"id": paper.id, "filename": "SPM 23.pdf", "status": "rejected",
                               "note": "Not an exam paper.", "uploadedAt": "2026-09-26T12:00:00"}


# (A key with "/" or ".." never reaches this route: the URL doesn't match it, so it's a plain 404 either way.)
@pytest.mark.parametrize("key", ["0" * 32, "abc", "A" * 32, "a" * 200, "g" * 32])
def test_unknown_or_malformed_upload_key_is_404(client, store, papers, key, monkeypatch):
    looked_up = []
    monkeypatch.setattr(papers, "get_by_upload_key", lambda k: looked_up.append(k))
    response = client.get(f"/api/v1/documents/uploads/{key}")
    assert response.status_code == 404 and response.json()["code"] == "not_found"
    assert looked_up == ([key] if key == "0" * 32 else [])
```

Append to `backend/tests/test_me.py`:

```python
def test_me_uploads_lists_only_my_uploads_newest_first(client, auth_headers, store):
    from services.paper_store import get_paper_store
    from tests.fakes import FakePaperStore

    papers = FakePaperStore()
    client.app.dependency_overrides[get_paper_store] = lambda: papers
    headers = auth_headers(role="student")
    me_id = max(store.users)
    papers.add_paper(filename="old.pdf", uploaded_by=me_id)
    papers.add_paper(filename="theirs.pdf", uploaded_by=me_id + 1)
    papers.add_paper(filename="new.pdf", status="review", uploaded_by=me_id)

    response = client.get("/api/v1/me/uploads", headers=headers)
    assert response.status_code == 200
    assert [u["filename"] for u in response.json()["uploads"]] == ["new.pdf", "old.pdf"]
    assert client.get("/api/v1/me/uploads").status_code == 401
```

In `backend/tests/test_startup.py`, add to `EXPECTED_ROUTES`:

```python
    ("GET", "/api/v1/documents/uploads/{upload_key}"),
    ("GET", "/api/v1/me/uploads"),
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_public_routes.py backend/tests/test_me.py backend/tests/test_startup.py -q`
Expected: failures. The status filter is missing from the SQL, and both new routes return 404 or 405.

- [ ] **Step 4: Filter the public routes and add the status route**

In `backend/api/routes.py`:

- `get_documents`: change the SQL to
  `"SELECT id, filename, subject_code, subject_name, semester, year, time, marks FROM papers WHERE status = 'live' ORDER BY id DESC"`.
- `download_paper`: change the SQL to
  `"SELECT file_path, filename FROM papers WHERE id = %s AND status = 'live'"`.
- `semantic_search`: change the SQL to
  `f"SELECT id, filename, subject_code, subject_name, semester, year, time, marks FROM papers WHERE id IN ({placeholders}) AND status = 'live'"`.
- Add `import re` to the imports, `upload_status` to the `services.paper_store` import, and this route after `download_paper`:

```python
_UPLOAD_KEY = re.compile(r"^[0-9a-f]{32}$")


@router.get("/documents/uploads/{upload_key}")
def get_upload_status(upload_key: str, papers: PostgresPaperStore = Depends(get_paper_store)):
    """An upload's progress. The key is unguessable, so this works for anonymous uploads too."""
    paper = papers.get_by_upload_key(upload_key) if _UPLOAD_KEY.match(upload_key) else None
    if paper is None:
        raise ApiError(404, "not_found", "Upload not found.")
    return upload_status(paper)
```

In `backend/api/me.py`, add the imports and route:

```python
from services.paper_store import PostgresPaperStore, get_paper_store, upload_status


@router.get("/me/uploads")
def my_uploads(user: User = Depends(current_user), papers: PostgresPaperStore = Depends(get_paper_store)):
    return {"uploads": [upload_status(paper) for paper in papers.list_by_uploader(user.id)]}
```

- [ ] **Step 5: Run the tests**

Run: `backend/.venv/Scripts/python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/api/routes.py backend/api/me.py backend/tests/fakes.py backend/tests/test_public_routes.py backend/tests/test_me.py backend/tests/test_startup.py
git commit -m "feat: only live papers are public; uploaders can check their upload's status"
```

---

## Task 7: Admin papers API

**Files:**
- Create: `backend/api/admin_papers.py`
- Modify: `backend/main.py` (include the router)
- Create: `backend/tests/test_admin_papers.py`
- Modify: `backend/tests/test_startup.py`

**Interfaces:**
- Consumes:
  - From Task 1: the whole paper store.
  - From Task 2: the `VectorService` methods `set_paper_payload`, `delete_paper` and `get_paper_texts`.
  - From Task 4: `paper_worker.wake()`.
  - From Task 5: `filename_problem`, `remove_quietly` and `duplicate_paper_error`.
- Produces (all under `/api/v1`, admin only):

  | Method and path | Returns |
  |---|---|
  | `GET /admin/papers?status=review` | `{"papers", "counts", "matches"}` |
  | `PATCH /admin/papers/{id}` | the paper |
  | `POST /admin/papers/{id}/status` | the paper |
  | `DELETE /admin/papers/{id}` | 204 |
  | `GET /admin/papers/{id}/file` | the PDF |

  - The dependency `get_vectors()` returns a `VectorService`, so tests can override it.
  - The paper JSON shape is `{"id", "filename", "status", "note", "reasons", "subjectCode", "subjectName", "semester", "year", "time", "marks", "uploadedAt", "uploader": {"name", "email"} | null, "reviewedBy", "reviewedAt", "preview"}`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_admin_papers.py`:

```python
import pytest

from api.admin_papers import get_vectors
from services.paper_store import get_paper_store
from tests.fakes import FakePaperStore, FakeVectorIndex

BASE = "/api/v1/admin/papers"
FIELDS = {"subject_code": "CSE432", "subject_name": "SPM", "semester": None, "year": "June, 2023",
          "time": "3 Hrs", "marks": "60"}


@pytest.fixture
def papers(client):
    fake = FakePaperStore()
    client.app.dependency_overrides[get_paper_store] = lambda: fake
    return fake


@pytest.fixture
def vectors(client):
    fake = FakeVectorIndex()
    client.app.dependency_overrides[get_vectors] = lambda: fake
    return fake


@pytest.fixture
def admin(auth_headers, papers, vectors):
    return auth_headers(role="admin")


def indexed(papers, vectors, status, text="SECTION A\n1. Define risk.", **kwargs):
    paper = papers.add_paper(status=status, fields=FIELDS, **kwargs)
    vectors.points[paper.id] = {"full_text": "Time: 3 Hrs\n" + text, "status": status}
    return paper


@pytest.mark.parametrize("method,path", [("get", BASE), ("patch", f"{BASE}/1"), ("post", f"{BASE}/1/status"),
                                         ("delete", f"{BASE}/1"), ("get", f"{BASE}/1/file")])
def test_admin_only(client, auth_headers, papers, vectors, method, path):
    assert client.request(method, path, json={}).status_code == 401
    student = auth_headers(role="student")
    assert client.request(method, path, json={}, headers=student).status_code == 403


def test_list_shows_one_status_with_counts_previews_and_matches(client, admin, papers, vectors):
    original = indexed(papers, vectors, "live", text="SECTION A\n1. Explain Bayes nets.")
    copy = indexed(papers, vectors, "review", filename="copy.pdf",
                   reasons=[{"code": "possible_duplicate", "paperId": original.id, "overlap": 0.83}])
    papers.add_paper(status="failed")

    body = client.get(BASE, headers=admin).json()
    assert [p["id"] for p in body["papers"]] == [copy.id]
    listed = body["papers"][0]
    assert listed["preview"] == "SECTION A\n1. Define risk."  # questions only, header dropped
    assert listed["reasons"][0]["paperId"] == original.id and listed["subjectCode"] == "CSE432"
    assert body["counts"] == {"processing": 0, "live": 1, "review": 1, "rejected": 0, "failed": 1}
    assert body["matches"][str(original.id)]["preview"] == "SECTION A\n1. Explain Bayes nets."

    live = client.get(f"{BASE}?status=live", headers=admin).json()["papers"]
    assert [p["id"] for p in live] == [original.id]
    assert client.get(f"{BASE}?status=unknown", headers=admin).status_code == 422


def test_edit_updates_details_and_search_index(client, admin, papers, vectors):
    paper = indexed(papers, vectors, "review")
    response = client.patch(f"{BASE}/{paper.id}", headers=admin,
                            json={"filename": "SPM June 2023.pdf", "subjectCode": "cse 432", "year": " "})
    assert response.status_code == 200
    body = response.json()
    assert (body["filename"], body["subjectCode"], body["year"]) == ("SPM June 2023.pdf", "CSE432", None)
    assert vectors.points[paper.id]["filename"] == "SPM June 2023.pdf"
    assert vectors.points[paper.id]["year"] is None
    assert papers.get(paper.id).reviewed_by is not None


def test_edit_of_a_paper_without_a_vector_leaves_the_index_alone(client, admin, papers, vectors):
    paper = papers.add_paper(status="failed")
    assert client.patch(f"{BASE}/{paper.id}", headers=admin, json={"subjectName": "SPM"}).status_code == 200
    assert vectors.points == {}


@pytest.mark.parametrize("filename", ["a/b.pdf", "a\\b.pdf", "bad\x01.pdf", "notes.txt", ""])
def test_edit_rejects_unsafe_names_and_changes_nothing(client, admin, papers, vectors, filename):
    paper = indexed(papers, vectors, "live")
    response = client.patch(f"{BASE}/{paper.id}", headers=admin, json={"filename": filename})
    assert response.status_code == 400
    assert papers.get(paper.id).filename == paper.filename


def test_edit_edge_cases(client, admin, papers, vectors):
    paper = indexed(papers, vectors, "live")
    assert client.patch(f"{BASE}/{paper.id}", headers=admin, json={}).status_code == 400
    assert client.patch(f"{BASE}/999", headers=admin, json={"year": "2023"}).status_code == 404
    vectors.fail_payload = True
    response = client.patch(f"{BASE}/{paper.id}", headers=admin, json={"year": "2024"})
    assert response.status_code == 503 and response.json()["code"] == "search_index_unavailable"
    assert papers.get(paper.id).fields["year"] == "June, 2023"


@pytest.mark.parametrize("start,target,note", [("review", "live", None), ("review", "rejected", "Blurry photo."),
                                               ("live", "rejected", "Wrong subject."), ("rejected", "live", None)])
def test_allowed_status_changes_update_database_and_index(client, admin, papers, vectors, start, target, note):
    paper = indexed(papers, vectors, start)
    response = client.post(f"{BASE}/{paper.id}/status", headers=admin, json={"status": target, "note": note})
    assert response.status_code == 200
    assert (response.json()["status"], response.json()["note"]) == (target, note)
    assert vectors.points[paper.id]["status"] == target
    assert papers.get(paper.id).reviewed_by is not None


@pytest.mark.parametrize("start,target", [("review", "rejected"), ("live", "rejected")])
def test_reject_and_take_down_need_a_reason(client, admin, papers, vectors, start, target):
    paper = indexed(papers, vectors, start)
    response = client.post(f"{BASE}/{paper.id}/status", headers=admin, json={"status": target, "note": "  "})
    assert response.status_code == 400 and papers.get(paper.id).status == start


@pytest.mark.parametrize("start,target", [("processing", "live"), ("live", "live"), ("failed", "live"),
                                          ("review", "processing")])
def test_other_status_changes_are_refused(client, admin, papers, vectors, start, target):
    paper = papers.add_paper(status=start)
    response = client.post(f"{BASE}/{paper.id}/status", headers=admin, json={"status": target, "note": "x"})
    assert response.status_code == 409 and response.json()["code"] == "invalid_transition"


def test_status_change_when_the_index_is_down_changes_nothing(client, admin, papers, vectors):
    paper = indexed(papers, vectors, "review")
    vectors.fail_payload = True
    response = client.post(f"{BASE}/{paper.id}/status", headers=admin, json={"status": "live"})
    assert response.status_code == 503 and papers.get(paper.id).status == "review"


def test_retry_requeues_a_failed_paper_and_wakes_the_worker(client, admin, papers, vectors, monkeypatch):
    woken = []
    monkeypatch.setattr("api.admin_papers.paper_worker.wake", lambda: woken.append(1))
    paper = papers.add_paper(status="failed", note="We couldn't process this file. Try uploading it again later.")
    response = client.post(f"{BASE}/{paper.id}/status", headers=admin, json={"status": "processing"})
    assert response.status_code == 200 and response.json()["status"] == "processing"
    assert papers.get(paper.id).attempts == 0 and woken == [1]


def test_retry_of_a_file_that_is_live_elsewhere_is_a_duplicate(client, admin, papers, vectors):
    papers.add_paper(status="live", file_sha256="a" * 64)
    failed = papers.add_paper(status="failed", file_sha256="a" * 64)
    response = client.post(f"{BASE}/{failed.id}/status", headers=admin, json={"status": "processing"})
    assert response.status_code == 409 and response.json()["code"] == "duplicate_paper"


def test_delete_removes_row_vector_and_file(client, admin, papers, vectors, tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    paper = indexed(papers, vectors, "rejected", file_path=str(pdf))
    assert client.delete(f"{BASE}/{paper.id}", headers=admin).status_code == 204
    assert papers.get(paper.id) is None and paper.id not in vectors.points and not pdf.exists()


def test_delete_keeps_a_file_another_paper_still_uses(client, admin, papers, vectors, tmp_path):
    pdf = tmp_path / "shared.pdf"
    pdf.write_bytes(b"%PDF")
    first = indexed(papers, vectors, "live", file_path=str(pdf))
    indexed(papers, vectors, "live", file_path=str(pdf))
    assert client.delete(f"{BASE}/{first.id}", headers=admin).status_code == 204
    assert pdf.exists()


def test_a_paper_being_processed_cannot_be_deleted(client, admin, papers, vectors):
    paper = papers.add_paper(status="processing")
    response = client.delete(f"{BASE}/{paper.id}", headers=admin)
    assert response.status_code == 409 and papers.get(paper.id) is not None


def test_admin_can_open_any_papers_pdf(client, admin, papers, vectors, tmp_path):
    pdf = tmp_path / "held.pdf"
    pdf.write_bytes(b"%PDF-1.4 held")
    paper = papers.add_paper(filename="Held.pdf", status="review", file_path=str(pdf))
    response = client.get(f"{BASE}/{paper.id}/file", headers=admin)
    assert response.status_code == 200 and response.content == b"%PDF-1.4 held"
    missing = papers.add_paper(status="review", file_path=str(tmp_path / "gone.pdf"))
    assert client.get(f"{BASE}/{missing.id}/file", headers=admin).status_code == 404
    assert client.get(f"{BASE}/999/file", headers=admin).status_code == 404
```

In `backend/tests/test_startup.py`, add to `EXPECTED_ROUTES`:

```python
    ("GET", "/api/v1/admin/papers"),
    ("PATCH", "/api/v1/admin/papers/{paper_id}"),
    ("POST", "/api/v1/admin/papers/{paper_id}/status"),
    ("DELETE", "/api/v1/admin/papers/{paper_id}"),
    ("GET", "/api/v1/admin/papers/{paper_id}/file"),
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_admin_papers.py -q`
Expected: `ModuleNotFoundError: No module named 'api.admin_papers'`.

- [ ] **Step 3: Implement the router**

Create `backend/api/admin_papers.py`:

```python
"""Admin moderation of papers: the review queue, edits, status changes and deletion."""
import os
from typing import Literal

from fastapi import APIRouter, Depends, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from auth.deps import require_role
from auth.users import User
from errors import ApiError
from services.paper_metadata import normalize_subject_code
from services.paper_store import (STATUSES, DuplicateFile, Paper, PostgresPaperStore, get_paper_store,
                                  resolve_paper_path)
from services.paper_worker import paper_worker
from services.rag_service import RAGService
from services.upload_checks import duplicate_paper_error, filename_problem, remove_quietly
from services.vector_service import VectorService

router = APIRouter(prefix="/admin/papers")

PREVIEW_CHARS = 1500
HAS_VECTOR = ("live", "review", "rejected")
TRANSITIONS = {("review", "live"), ("review", "rejected"), ("live", "rejected"), ("rejected", "live"),
               ("failed", "processing")}
EDITABLE = {"subjectCode": "subject_code", "subjectName": "subject_name", "semester": "semester",
            "year": "year", "time": "time", "marks": "marks"}


def get_vectors() -> VectorService:
    return VectorService()


class PaperEdit(BaseModel):
    filename: str | None = None
    subjectCode: str | None = None
    subjectName: str | None = None
    semester: str | None = None
    year: str | None = None
    time: str | None = None
    marks: str | None = None


class StatusChange(BaseModel):
    status: Literal["live", "rejected", "processing"]
    note: str | None = Field(default=None, max_length=500)


def _iso(value):
    return value.isoformat() if value else None


def _preview(text: str | None) -> str:
    return RAGService._questions_only(text or "")[:PREVIEW_CHARS]


def admin_paper(paper: Paper, preview: str = "") -> dict:
    fields = paper.fields
    return {
        "id": paper.id, "filename": paper.filename, "status": paper.status, "note": paper.status_note,
        "reasons": paper.review_reasons,
        "subjectCode": fields["subject_code"], "subjectName": fields["subject_name"], "semester": fields["semester"],
        "year": fields["year"], "time": fields["time"], "marks": fields["marks"],
        "uploadedAt": _iso(paper.uploaded_at),
        "uploader": ({"name": paper.uploader_name, "email": paper.uploader_email}
                     if paper.uploaded_by is not None else None),
        "reviewedBy": paper.reviewed_by,
        "reviewedAt": _iso(paper.reviewed_at),
        "preview": preview,
    }


def _get_or_404(papers: PostgresPaperStore, paper_id: int) -> Paper:
    paper = papers.get(paper_id)
    if paper is None:
        raise ApiError(404, "not_found", "Paper not found.")
    return paper


def _index_unavailable() -> ApiError:
    return ApiError(503, "search_index_unavailable", "Couldn't update the search index, so nothing was changed. Try again.")


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


@router.get("")
def list_papers(status: Literal[STATUSES] = "review", admin: User = Depends(require_role("admin")),
                papers: PostgresPaperStore = Depends(get_paper_store), vectors: VectorService = Depends(get_vectors)):
    listed = papers.list_by_status(status)
    match_ids = {reason["paperId"] for paper in listed for reason in paper.review_reasons
                 if reason.get("code") == "possible_duplicate"}
    matched = papers.get_many(match_ids)
    texts = vectors.get_paper_texts(sorted({p.id for p in listed + matched if p.status in HAS_VECTOR}))
    return {
        "papers": [admin_paper(p, _preview(texts.get(p.id))) for p in listed],
        "counts": papers.counts(),
        "matches": {str(p.id): admin_paper(p, _preview(texts.get(p.id))) for p in matched},
    }


@router.patch("/{paper_id}")
def edit_paper(paper_id: int, body: PaperEdit, admin: User = Depends(require_role("admin")),
               papers: PostgresPaperStore = Depends(get_paper_store), vectors: VectorService = Depends(get_vectors)):
    paper = _get_or_404(papers, paper_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(400, "invalid_request", "Nothing to update.")
    filename = paper.filename
    if "filename" in changes:
        filename = (changes["filename"] or "").strip()
        problem = filename_problem(filename)
        if problem:
            raise ApiError(400, "invalid_request", problem)
    fields = dict(paper.fields)
    for api_key, column in EDITABLE.items():
        if api_key in changes:
            fields[column] = _clean(changes[api_key])
    fields["subject_code"] = normalize_subject_code(fields["subject_code"])
    if paper.status in HAS_VECTOR:
        payload = {"subject_code": fields["subject_code"], "subject_name": fields["subject_name"],
                   "year": fields["year"], "filename": filename}
        if not vectors.set_paper_payload(paper.id, payload):
            raise _index_unavailable()
    return admin_paper(papers.update_details(paper.id, filename, fields, admin.id))


@router.post("/{paper_id}/status")
def change_status(paper_id: int, body: StatusChange, admin: User = Depends(require_role("admin")),
                  papers: PostgresPaperStore = Depends(get_paper_store), vectors: VectorService = Depends(get_vectors)):
    paper = _get_or_404(papers, paper_id)
    if (paper.status, body.status) not in TRANSITIONS:
        raise ApiError(409, "invalid_transition", f"A {paper.status} paper can't be moved to {body.status}.")
    note = _clean(body.note)
    if body.status == "rejected" and not note:
        raise ApiError(400, "invalid_request", "Give a reason. The uploader will see it.")
    if body.status in HAS_VECTOR and not vectors.set_paper_payload(paper.id, {"status": body.status}):
        raise _index_unavailable()
    try:
        updated = papers.set_status(paper.id, body.status, note if body.status == "rejected" else None, admin.id)
    except DuplicateFile:
        raise duplicate_paper_error(papers.find_active_by_hash(paper.file_sha256))
    if body.status == "processing":
        paper_worker.wake()
    return admin_paper(updated)


@router.delete("/{paper_id}", status_code=204)
def delete_paper(paper_id: int, admin: User = Depends(require_role("admin")),
                 papers: PostgresPaperStore = Depends(get_paper_store), vectors: VectorService = Depends(get_vectors)):
    paper = _get_or_404(papers, paper_id)
    if paper.status == "processing":
        raise ApiError(409, "invalid_transition", "This paper is still being processed. Try again in a few minutes.")
    if paper.status in HAS_VECTOR and not vectors.delete_paper(paper.id):
        raise _index_unavailable()
    papers.delete(paper.id)
    if papers.count_file_users(paper.file_path) == 0:
        remove_quietly(resolve_paper_path(paper.file_path))
    return Response(status_code=204)


@router.get("/{paper_id}/file")
def paper_file(paper_id: int, admin: User = Depends(require_role("admin")),
               papers: PostgresPaperStore = Depends(get_paper_store)):
    paper = _get_or_404(papers, paper_id)
    path = resolve_paper_path(paper.file_path)
    if not os.path.exists(path):
        raise ApiError(404, "not_found", "This paper's file is missing.")
    return FileResponse(path=path, filename=paper.filename, media_type="application/pdf")
```

If `Literal[STATUSES]` isn't accepted by the installed Python/pydantic, write the five statuses out literally: `Literal["processing", "live", "review", "rejected", "failed"]`.

In `backend/main.py`:

```python
from api.admin_papers import router as admin_papers_router
...
app.include_router(admin_papers_router, prefix="/api/v1")
```

- [ ] **Step 4: Run the tests**

Run: `backend/.venv/Scripts/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/api/admin_papers.py backend/main.py backend/tests/test_admin_papers.py backend/tests/test_startup.py
git commit -m "feat: admin API to review, edit, take down, restore, retry and delete papers"
```

---

## Task 8: Dedupe command

**Files:**
- Create: `backend/services/dedupe.py`
- Modify: `backend/admin_cli.py`
- Create: `backend/tests/test_dedupe.py`

**Interfaces:**
- Consumes:
  - From Task 1: the store methods `list_all`, `delete`, `count_file_users` and `set_file_hash`; `resolve_paper_path`.
  - From Task 2: `get_paper_texts`, `delete_paper` and `set_paper_payload`.
  - From Task 3: `shingles`, `jaccard` and `DUPLICATE_OVERLAP`.
  - From Task 5: `remove_quietly`.
- Produces:
  - `file_sha256(path) -> str | None`
  - `Group(keep: Paper, remove: list[Paper], links: list[tuple[int, int, str, float]])`
  - `find_groups(papers, hashes, texts, keep_ids) -> list[Group]` (raises `ValueError` when two `--keep` ids share a group)
  - `run_dedupe(keep_ids: set[int], apply: bool, store=None, vectors=None, out=print) -> int`
  - CLI: `admin_cli.py dedupe [--keep ID ...] [--apply]`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_dedupe.py`:

```python
import hashlib

import pytest

from admin_cli import main as cli_main
from services.dedupe import find_groups, run_dedupe
from tests.fakes import FakePaperStore, FakeVectorIndex, moderation_fixture

AI = moderation_fixture("ai_major_2023")
AI_RESCAN = moderation_fixture("ai_major_2023_rescan")
CLOUD_A = moderation_fixture("cloud_sec_major_2023_set_a")
CLOUD_B = moderation_fixture("cloud_sec_major_2023_set_b")


@pytest.fixture
def production_like(tmp_path):
    """Mirrors production: 2 is a re-scan of 3; 3 and 4 share one file (same-name overwrite); 5 and 7 differ."""
    store, vectors = FakePaperStore(), FakeVectorIndex()
    (tmp_path / "ai1.pdf").write_bytes(b"%PDF rescan")
    (tmp_path / "ai.pdf").write_bytes(b"%PDF ai")
    (tmp_path / "c2.pdf").write_bytes(b"%PDF c2")
    (tmp_path / "c1.pdf").write_bytes(b"%PDF c1")
    rows = [("AI Major 2023(1).PDF", "ai1.pdf", AI_RESCAN), ("AI Major 2023.pdf", "ai.pdf", AI),
            ("AI Major 2023.pdf", "ai.pdf", AI), ("Cloud Sec Major 2023 (2).pdf", "c2.pdf", CLOUD_A),
            ("Cloud Sec Major 2023 (1).pdf", "c1.pdf", CLOUD_B)]
    ids = []
    for filename, file_name, text in rows:
        paper = store.add_paper(filename=filename, file_path=str(tmp_path / file_name))
        vectors.points[paper.id] = {"full_text": text}
        ids.append(paper.id)
    return store, vectors, ids, tmp_path


def test_groups_chain_exact_and_near_copies_and_keep_the_oldest(production_like):
    store, vectors, (rescan, ai, ai_copy, cloud_a, cloud_b), _ = production_like
    lines = []
    assert run_dedupe(set(), apply=False, store=store, vectors=vectors, out=lines.append) == 0
    text = "\n".join(lines)
    assert f"keep {rescan}" in text and f"remove {ai}" in text and f"remove {ai_copy}" in text
    assert "exact" in text and "near" in text
    for other in (cloud_a, cloud_b):  # same subject and session, different questions
        assert f"keep {other}" not in text and f"remove {other}" not in text
    assert "Preview only" in text
    assert len(store.list_all()) == 5 and len(vectors.points) == 5  # preview writes nothing


def test_find_groups_respects_keep_and_rejects_two_keeps_in_one_group(production_like):
    store, vectors, (rescan, ai, ai_copy, cloud_a, cloud_b), _ = production_like
    papers = store.list_all()
    texts = vectors.get_paper_texts([p.id for p in papers])
    hashes = {ai: "h", ai_copy: "h", rescan: "r", cloud_a: "a", cloud_b: "b"}
    (group,) = find_groups(papers, hashes, texts, keep_ids={ai})
    assert group.keep.id == ai and sorted(p.id for p in group.remove) == [rescan, ai_copy]
    assert {(a, b, kind) for a, b, kind, _ in group.links} >= {(ai, ai_copy, "exact")}
    with pytest.raises(ValueError):
        find_groups(papers, hashes, texts, keep_ids={ai, rescan})


def test_apply_removes_copies_but_keeps_a_shared_file_and_backfills(production_like):
    store, vectors, (rescan, ai, ai_copy, cloud_a, cloud_b), tmp_path = production_like
    assert run_dedupe({ai}, apply=True, store=store, vectors=vectors, out=lambda _: None) == 0
    assert sorted(p.id for p in store.list_all()) == [ai, cloud_a, cloud_b]
    assert set(vectors.points) == {ai, cloud_a, cloud_b}
    assert (tmp_path / "ai.pdf").exists()  # still used by the kept paper
    assert not (tmp_path / "ai1.pdf").exists()
    assert store.get(ai).file_sha256 == hashlib.sha256(b"%PDF ai").hexdigest()
    assert store.get(cloud_a).file_sha256 == hashlib.sha256(b"%PDF c2").hexdigest()
    assert all(vectors.points[i]["status"] == "live" for i in (ai, cloud_a, cloud_b))


def test_missing_file_is_reported_and_still_compared_by_text(production_like):
    store, vectors, (rescan, ai, ai_copy, cloud_a, cloud_b), tmp_path = production_like
    (tmp_path / "ai1.pdf").unlink()
    lines = []
    run_dedupe(set(), apply=False, store=store, vectors=vectors, out=lines.append)
    assert any("file missing" in line and str(rescan) in line for line in lines)
    assert any(f"remove {ai}" in line for line in lines)


def test_nothing_to_do(tmp_path):
    lines = []
    assert run_dedupe(set(), apply=True, store=FakePaperStore(), vectors=FakeVectorIndex(), out=lines.append) == 0
    assert "No duplicates found." in lines


def test_cli_parses_keep_and_apply(monkeypatch):
    seen = []
    monkeypatch.setattr("services.dedupe.run_dedupe", lambda keep_ids, apply: seen.append((keep_ids, apply)) or 0)
    assert cli_main(["dedupe", "--keep", "3", "--keep", "10", "--apply"]) == 0
    assert seen == [({3, 10}, True)]
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_dedupe.py -q`
Expected: `ModuleNotFoundError: No module named 'services.dedupe'`.

- [ ] **Step 3: Implement dedupe**

Create `backend/services/dedupe.py`:

```python
"""Finds duplicate papers already in the database (exact file copies and re-scans) and removes them."""
import hashlib
import os
from dataclasses import dataclass
from itertools import combinations

from services.moderation import DUPLICATE_OVERLAP, jaccard, shingles
from services.paper_store import DuplicateFile, Paper, resolve_paper_path
from services.upload_checks import remove_quietly

DEDUPE_STATUSES = ("live", "review", "rejected")


@dataclass
class Group:
    keep: Paper
    remove: list[Paper]
    links: list[tuple[int, int, str, float]]  # (paper_a, paper_b, "exact" | "near", overlap)


def file_sha256(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_groups(papers: list[Paper], hashes: dict[int, str], texts: dict[int, str], keep_ids: set[int]) -> list[Group]:
    """Groups papers that share a file hash or whose question text overlaps by DUPLICATE_OVERLAP or more."""
    parent = {paper.id: paper.id for paper in papers}

    def root(paper_id):
        while parent[paper_id] != paper_id:
            parent[paper_id] = parent[parent[paper_id]]
            paper_id = parent[paper_id]
        return paper_id

    shingled = {paper.id: shingles(texts.get(paper.id, "")) for paper in papers}
    links = []
    for a, b in combinations(papers, 2):
        if hashes.get(a.id) and hashes.get(a.id) == hashes.get(b.id):
            links.append((a.id, b.id, "exact", 1.0))
        else:
            overlap = jaccard(shingled[a.id], shingled[b.id])
            if overlap < DUPLICATE_OVERLAP:
                continue
            links.append((a.id, b.id, "near", overlap))
        parent[root(a.id)] = root(b.id)

    members: dict[int, list[Paper]] = {}
    for paper in papers:
        members.setdefault(root(paper.id), []).append(paper)
    groups = []
    for group in members.values():
        if len(group) < 2:
            continue
        chosen = [paper for paper in group if paper.id in keep_ids]
        if len(chosen) > 1:
            raise ValueError(f"--keep names more than one paper in the same group: {sorted(p.id for p in chosen)}")
        keep = chosen[0] if chosen else min(group, key=lambda paper: paper.id)
        ids = {paper.id for paper in group}
        groups.append(Group(keep=keep, remove=sorted((p for p in group if p.id != keep.id), key=lambda p: p.id),
                            links=[link for link in links if link[0] in ids]))
    return sorted(groups, key=lambda group: group.keep.id)


def run_dedupe(keep_ids: set[int], apply: bool, store=None, vectors=None, out=print) -> int:
    if store is None:
        from services.paper_store import PostgresPaperStore
        store = PostgresPaperStore()
    if vectors is None:
        from services.vector_service import VectorService
        vectors = VectorService()

    papers = [paper for paper in store.list_all() if paper.status in DEDUPE_STATUSES]
    hashes = {}
    for paper in papers:
        digest = file_sha256(resolve_paper_path(paper.file_path))
        if digest is None:
            out(f"! {paper.id}: {paper.filename}: file missing at {paper.file_path}; compared by text only")
        else:
            hashes[paper.id] = digest
    texts = vectors.get_paper_texts([paper.id for paper in papers])
    for unknown in sorted(keep_ids - {paper.id for paper in papers}):
        out(f"! --keep {unknown}: no such paper")

    try:
        groups = find_groups(papers, hashes, texts, keep_ids)
    except ValueError as error:
        out(str(error))
        return 1

    if not groups:
        out("No duplicates found.")
    for group in groups:
        out(f"Group: keep {group.keep.id} ({group.keep.filename})")
        for paper in group.remove:
            out(f"  remove {paper.id} ({paper.filename})")
        for a, b, kind, overlap in group.links:
            out(f"    {a} ~ {b}: exact copy" if kind == "exact" else f"    {a} ~ {b}: near copy, {overlap:.0%} same questions")

    if not apply:
        if groups:
            out("Preview only: nothing was changed. Re-run with --apply to remove the papers marked 'remove'.")
        return 0

    problems = 0
    removed = set()
    for group in groups:
        for paper in group.remove:
            if not vectors.delete_paper(paper.id):
                out(f"x {paper.id}: couldn't remove it from the search index; left in place")
                problems += 1
                continue
            store.delete(paper.id)
            removed.add(paper.id)
            if store.count_file_users(paper.file_path) == 0:
                remove_quietly(resolve_paper_path(paper.file_path))
            out(f"- removed {paper.id} ({paper.filename})")

    for paper in papers:
        if paper.id in removed:
            continue
        if paper.id in hashes and paper.file_sha256 != hashes[paper.id]:
            try:
                store.set_file_hash(paper.id, hashes[paper.id])
            except DuplicateFile:
                out(f"x {paper.id}: another paper has the same file; its hash was not saved")
                problems += 1
        if paper.id in texts and not vectors.set_paper_payload(paper.id, {"status": paper.status}):
            out(f"x {paper.id}: couldn't tag its search entry as {paper.status}")
            problems += 1
    out(f"Done: removed {len(removed)} paper(s)" + (f", {problems} problem(s) above." if problems else "."))
    return 1 if problems else 0
```

- [ ] **Step 4: Add the CLI command**

In `backend/admin_cli.py`, extend the module docstring with:

```
    python backend/admin_cli.py dedupe [--keep 3] [--apply]
```

In `main()`, after the reprocess parser:

```python
    dedupe_parser = commands.add_parser("dedupe", help="Find duplicate papers; remove them with --apply")
    dedupe_parser.add_argument("--keep", type=int, action="append", default=[], dest="keep",
                               help="paper id to keep in its group (repeatable; default: the oldest)")
    dedupe_parser.add_argument("--apply", action="store_true", help="remove the duplicates (default: preview only)")
```

And before `return 1`:

```python
    if args.command == "dedupe":
        from services import dedupe
        return dedupe.run_dedupe(set(args.keep), apply=args.apply)
```

The test patches `services.dedupe.run_dedupe`, so reach it through the module attribute (`dedupe.run_dedupe`) as shown, not with `from services.dedupe import run_dedupe`.

- [ ] **Step 5: Run the tests**

Run: `backend/.venv/Scripts/python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/services/dedupe.py backend/admin_cli.py backend/tests/test_dedupe.py
git commit -m "feat: admin dedupe command to find and remove existing duplicate papers"
```

---

## Task 9: Upload page

**Files:**
- Modify: `frontend/src/lib/api.ts` (error details)
- Modify: `frontend/src/app/upload/page.tsx`

**Interfaces:**
- Consumes:
  - From Task 5: `POST /api/v1/documents/ingest` returns 202 `{id, uploadKey, filename, status}`, or 409 `duplicate_paper` with `paperId` and `paperStatus`.
  - From Task 6: `GET /api/v1/documents/uploads/{key}` and `GET /api/v1/me/uploads`.
- Produces: `ApiRequestError.details: Record<string, unknown>`, which holds the extra fields of an error body.

- [ ] **Step 1: Carry extra error fields in `ApiRequestError`**

In `frontend/src/lib/api.ts`:

```ts
export class ApiRequestError extends Error {
    constructor(
        public status: number,
        public code: string,
        message: string,
        public retryAfterSeconds: number | null = null,
        public details: Record<string, unknown> = {},
    ) {
        super(message);
    }
}
```

In `apiFetch`, type the body as `Record<string, unknown>` and pass the rest:

```ts
        let body: Record<string, unknown> = {};
        try {
            body = await response.json();
        } catch {
            // non-JSON error body
        }
        const { detail, code, ...details } = body;
        const retryAfter = Number(response.headers.get("Retry-After"));
        throw new ApiRequestError(
            response.status,
            typeof code === "string" ? code : "error",
            typeof detail === "string" ? detail : "Something went wrong. Please try again.",
            Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null,
            details,
        );
```

- [ ] **Step 2: Rewrite the upload page's logic and list**

In `frontend/src/app/upload/page.tsx`, keep the page layout (heading, allowance line, dropzone, container styles) and change the following.

Types and constants, replacing `UploadTask`:

```tsx
type PaperStatus = "processing" | "live" | "review" | "rejected" | "failed";

type UploadTask = {
    id: string;
    file: File;
    progress: number;
    status: "uploading" | "error" | PaperStatus;
    uploadKey?: string;
    paperId?: number | null;
    message?: string;
    startedAt?: number;
    gaveUp?: boolean;
};

type UploadStatus = { id: number; filename: string; status: PaperStatus; note: string | null; uploadedAt: string | null };

const MAX_BYTES = 10 * 1024 * 1024;
const POLL_MS = 5000;
const GIVE_UP_MS = 15 * 60 * 1000;

const STATUS_LABEL: Record<PaperStatus, string> = {
    processing: "Processing…",
    live: "Live",
    review: "Under review",
    rejected: "Rejected",
    failed: "Failed",
};

const STATUS_CLASS: Record<PaperStatus, string> = {
    processing: "text-amber-400",
    live: "text-green-500",
    review: "text-sky-400",
    rejected: "text-red-400",
    failed: "text-red-400",
};

const isPdf = (f: File) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf");
const paperUrl = (id: number) => `${API_BASE_URL}/api/v1/documents/${id}/download`;
```

Import `API_BASE_URL` from `@/lib/utils` and `ApiRequestError` from `@/lib/api`. Import `useEffect` from `react`, and `Clock` and `ExternalLink` from `lucide-react`.

State, inside the component:

```tsx
    const [myUploads, setMyUploads] = useState<UploadStatus[]>([]);
    const tasksRef = useRef<UploadTask[]>([]);
    useEffect(() => { tasksRef.current = tasks; }, [tasks]);

    const updateTask = useCallback((id: string, change: Partial<UploadTask>) => {
        setTasks(prev => prev.map(t => (t.id === id ? { ...t, ...change } : t)));
    }, []);

    const loadMyUploads = useCallback(async () => {
        if (!me) return;
        try {
            const response = await apiFetch("/api/v1/me/uploads");
            setMyUploads(((await response.json()) as { uploads: UploadStatus[] }).uploads);
        } catch {
            // the list is a convenience; the per-file status above still works
        }
    }, [me]);

    useEffect(() => {
        void (async () => {
            await loadMyUploads();
        })();
    }, [loadMyUploads]);
```

In `processFiles`, drop files over the limit before sending, and handle the 202 and 409 responses:

```tsx
    const processFiles = useCallback((selectedFiles: File[]) => {
        const newTasks: UploadTask[] = selectedFiles.map(file => ({
            id: Math.random().toString(36).substring(7),
            file,
            progress: 0,
            status: file.size > MAX_BYTES ? "error" : "uploading",
            message: file.size > MAX_BYTES ? "This file is over 10 MB. Compress it or split it, then try again." : undefined,
        }));
        setTasks(prev => [...prev, ...newTasks]);

        newTasks.filter(task => task.status === "uploading").forEach(task => {
            let currentProgress = 0;
            const progressInterval = setInterval(() => {
                currentProgress = Math.min(90, currentProgress + Math.floor(Math.random() * 15) + 5);
                setTasks(prev => prev.map(t =>
                    t.id === task.id && t.status === "uploading" ? { ...t, progress: currentProgress } : t
                ));
            }, 300);

            const formData = new FormData();
            formData.append("file", task.file);

            apiFetch("/api/v1/documents/ingest", { method: "POST", body: formData })
                .then(async response => {
                    const body = (await response.json()) as { id: number; uploadKey: string; status: PaperStatus };
                    clearInterval(progressInterval);
                    updateTask(task.id, { progress: 100, status: body.status, uploadKey: body.uploadKey,
                                          paperId: body.id, startedAt: Date.now() });
                    void refresh();
                    void loadMyUploads();
                })
                .catch(err => {
                    clearInterval(progressInterval);
                    const duplicate = err instanceof ApiRequestError && err.code === "duplicate_paper";
                    updateTask(task.id, {
                        status: "error",
                        message: describeApiError(err),
                        paperId: duplicate && err.details.paperStatus === "live" ? Number(err.details.paperId) : null,
                    });
                });
        });
    }, [refresh, loadMyUploads, updateTask]);
```

Polling, added after `processFiles`:

```tsx
    const polling = tasks.some(t => t.status === "processing" && !t.gaveUp);

    useEffect(() => {
        if (!polling) return;
        const timer = setInterval(() => {
            for (const task of tasksRef.current) {
                if (task.status !== "processing" || task.gaveUp || !task.uploadKey) continue;
                if (Date.now() - (task.startedAt ?? Date.now()) > GIVE_UP_MS) {
                    updateTask(task.id, { gaveUp: true, message: "Still processing. Check back later." });
                    continue;
                }
                apiFetch(`/api/v1/documents/uploads/${task.uploadKey}`)
                    .then(async response => {
                        const status = (await response.json()) as UploadStatus;
                        if (status.status !== "processing") {
                            updateTask(task.id, { status: status.status, message: status.note ?? undefined });
                            void loadMyUploads();
                        }
                    })
                    .catch(() => {
                        // try again on the next tick
                    });
            }
        }, POLL_MS);
        return () => clearInterval(timer);
    }, [polling, updateTask, loadMyUploads]);
```

In `handleDrop` and `handleFileSelect`, change the filters to `.filter(isPdf)`. Change the input's `accept` to `".pdf,application/pdf"`. Change the dropzone hint to `PDF only · up to 10 MB and 20 pages per file`.

In each task row, replace the status text and progress bar blocks with:

```tsx
<span className="text-xs font-medium tabular-nums">
    {task.status === "uploading" && <span className="text-muted-foreground">{task.progress}%</span>}
    {task.status === "error" && <span className="text-red-500 flex items-center gap-1"><AlertCircle className="h-3 w-3"/> Not uploaded</span>}
    {task.status !== "uploading" && task.status !== "error" && (
        <span className={cn("flex items-center gap-1", STATUS_CLASS[task.status])}>
            {task.status === "processing" ? <Clock className="h-3 w-3"/> : task.status === "live" ? <CheckCircle className="h-3 w-3"/> : <AlertCircle className="h-3 w-3"/>}
            {STATUS_LABEL[task.status]}
        </span>
    )}
</span>
```

Below it, replace the three progress-bar branches with a single bar:

```tsx
<div className="h-1.5 w-full bg-white/5 rounded-full overflow-hidden">
    <div
        className={cn("h-full rounded-full transition-all duration-300 ease-out",
            task.status === "uploading" && "bg-gradient-to-r from-cyan-400 to-emerald-400",
            task.status === "processing" && "bg-amber-400/70 animate-pulse w-full",
            (task.status === "live") && "bg-green-500 w-full",
            task.status === "review" && "bg-sky-400 w-full",
            (task.status === "rejected" || task.status === "failed" || task.status === "error") && "bg-red-500 w-full")}
        style={task.status === "uploading" ? { width: `${task.progress}%` } : undefined}
    />
</div>
{task.message && <p className={cn("mt-2 text-xs", task.status === "error" || task.status === "rejected" || task.status === "failed" ? "text-red-400" : "text-muted-foreground")}>{task.message}</p>}
{task.paperId != null && (task.status === "live" || task.status === "error") && (
    <a href={paperUrl(task.paperId)} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-xs text-cyan-400 hover:underline">
        Open the paper <ExternalLink className="h-3 w-3"/>
    </a>
)}
```

Add a "Your uploads" panel after the Recent Uploads panel, shown only for signed-in users with at least one upload:

```tsx
{me && myUploads.length > 0 && (
    <div className="mt-8 rounded-2xl bg-[#131620] border border-white/5 p-6 sm:p-8 shadow-xl">
        <h4 className="text-xs font-semibold text-muted-foreground tracking-widest uppercase mb-6">Your uploads</h4>
        <ul className="divide-y divide-white/5">
            {myUploads.map(u => (
                <li key={u.id} className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-4 py-3">
                    <span className="flex-1 min-w-0 truncate text-sm">{u.filename}</span>
                    <span className={cn("text-xs font-medium", STATUS_CLASS[u.status])}>{STATUS_LABEL[u.status]}</span>
                    {u.note && <span className="text-xs text-muted-foreground sm:max-w-xs">{u.note}</span>}
                    {u.status === "live" && (
                        <a href={paperUrl(u.id)} target="_blank" rel="noreferrer" className="text-xs text-cyan-400 hover:underline">Open</a>
                    )}
                </li>
            ))}
        </ul>
    </div>
)}
```

- [ ] **Step 3: Lint and build**

Run: `cd frontend && npx eslint src/app/upload/page.tsx src/lib/api.ts && npm run build`
Expected: no lint errors in these files, and the build succeeds. If the React hooks lint rule flags a `setState` call inside an effect, wrap it the way `loadMyUploads` does (`void (async () => { ... })()`). This is the same pattern as `admin/users/page.tsx`.

The browser check happens in "Final check" at the end, not in this task.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/app/upload/page.tsx
git commit -m "feat: upload page shows processing, live, under review, rejected and failed per file"
```

---

## Task 10: Admin papers page

**Files:**
- Create: `frontend/src/components/AdminNav.tsx`
- Create: `frontend/src/app/admin/papers/page.tsx`
- Modify: `frontend/src/app/admin/users/page.tsx` (add `<AdminNav />`)
- Modify: `frontend/src/components/Navbar.tsx` (the Admin link goes to `/admin/papers`)

**Interfaces:**
- Consumes: from Task 7, the `/api/v1/admin/papers` endpoints and their paper JSON shape.

- [ ] **Step 1: Shared admin nav**

Create `frontend/src/components/AdminNav.tsx`:

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const LINKS = [
    { href: "/admin/papers", label: "Papers" },
    { href: "/admin/users", label: "Users" },
];

export function AdminNav() {
    const pathname = usePathname();
    return (
        <nav className="mb-6 flex gap-2">
            {LINKS.map(link => (
                <Link
                    key={link.href}
                    href={link.href}
                    className={cn(
                        "rounded-md px-3 py-1.5 text-sm transition-colors",
                        pathname === link.href ? "bg-primary/15 text-primary" : "text-muted-foreground hover:text-foreground",
                    )}
                >
                    {link.label}
                </Link>
            ))}
        </nav>
    );
}
```

In `frontend/src/app/admin/users/page.tsx`, import `AdminNav` and render `<AdminNav />` directly above the `<h1>`. In `frontend/src/components/Navbar.tsx`, change the admin link's `href` from `/admin/users` to `/admin/papers`.

- [ ] **Step 2: The papers page**

Create `frontend/src/app/admin/papers/page.tsx`:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { AccessNotice } from "@/components/AccessNotice";
import { AdminNav } from "@/components/AdminNav";
import { apiFetch, describeApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

type Status = "review" | "live" | "rejected" | "failed" | "processing";

type Reason =
    | { code: "unreadable"; letters: number }
    | { code: "not_exam_paper" }
    | { code: "missing_details"; fields: string[] }
    | { code: "possible_duplicate"; paperId: number; overlap: number };

type AdminPaper = {
    id: number;
    filename: string;
    status: Status;
    note: string | null;
    reasons: Reason[];
    subjectCode: string | null;
    subjectName: string | null;
    semester: string | null;
    year: string | null;
    time: string | null;
    marks: string | null;
    uploadedAt: string | null;
    uploader: { name: string | null; email: string | null } | null;
    reviewedAt: string | null;
    preview: string;
};

type Listing = { papers: AdminPaper[]; counts: Record<Status, number>; matches: Record<string, AdminPaper> };

const TABS: { status: Status; label: string }[] = [
    { status: "review", label: "Review" },
    { status: "live", label: "Live" },
    { status: "rejected", label: "Rejected" },
    { status: "failed", label: "Failed" },
    { status: "processing", label: "Processing" },
];

const DETAIL_FIELDS = [
    ["subjectCode", "Subject code"],
    ["subjectName", "Subject name"],
    ["semester", "Semester"],
    ["year", "Session"],
    ["time", "Time"],
    ["marks", "Marks"],
] as const;

const FIELD_LABEL: Record<string, string> = { subject_code: "subject code", subject_name: "subject name", year: "session" };

function describeReason(reason: Reason): string {
    switch (reason.code) {
        case "unreadable":
            return `Very little readable text (${reason.letters} letters).`;
        case "not_exam_paper":
            return "Doesn't look like an exam paper (no marks, time or exam wording).";
        case "missing_details":
            return `Couldn't read: ${reason.fields.map(f => FIELD_LABEL[f] ?? f).join(", ")}.`;
        case "possible_duplicate":
            return `Looks like a copy of paper #${reason.paperId} (${Math.round(reason.overlap * 100)}% same questions).`;
    }
}

async function openPdf(id: number) {
    const response = await apiFetch(`/api/v1/admin/papers/${id}/file`);
    const url = URL.createObjectURL(await response.blob());
    window.open(url, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

function Details({ paper }: { paper: AdminPaper }) {
    return (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-3">
            {DETAIL_FIELDS.map(([key, label]) => (
                <div key={key}>
                    <dt className="text-xs text-muted-foreground">{label}</dt>
                    <dd>{paper[key] ?? "–"}</dd>
                </div>
            ))}
        </dl>
    );
}

function PaperCard({ paper, matches, onChanged, onError }: {
    paper: AdminPaper;
    matches: Record<string, AdminPaper>;
    onChanged: () => Promise<void>;
    onError: (message: string) => void;
}) {
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState<Record<string, string>>({});
    const [busy, setBusy] = useState(false);

    const run = async (action: () => Promise<unknown>) => {
        setBusy(true);
        try {
            await action();
            await onChanged();
        } catch (e) {
            onError(describeApiError(e));
        } finally {
            setBusy(false);
        }
    };

    const post = (status: "live" | "rejected" | "processing", note?: string) =>
        run(() => apiFetch(`/api/v1/admin/papers/${paper.id}/status`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status, note }),
        }));

    const askReasonThen = (status: "rejected") => {
        const note = window.prompt("Reason (the uploader will see this):")?.trim();
        if (note) void post(status, note);
    };

    const startEdit = () => {
        setDraft({
            filename: paper.filename,
            ...Object.fromEntries(DETAIL_FIELDS.map(([key]) => [key, paper[key] ?? ""])),
        });
        setEditing(true);
    };

    const save = () => {
        const current: Record<string, string> = {
            filename: paper.filename,
            ...Object.fromEntries(DETAIL_FIELDS.map(([key]) => [key, paper[key] ?? ""])),
        };
        const changes = Object.fromEntries(Object.entries(draft).filter(([key, value]) => value !== current[key]));
        if (Object.keys(changes).length === 0) {
            setEditing(false);
            return;
        }
        void run(async () => {
            await apiFetch(`/api/v1/admin/papers/${paper.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(changes),
            });
            setEditing(false);
        });
    };

    const remove = () => {
        if (window.confirm(`Delete "${paper.filename}" permanently? Its file and search entry are removed too.`)) {
            void run(() => apiFetch(`/api/v1/admin/papers/${paper.id}`, { method: "DELETE" }));
        }
    };

    const copies = paper.reasons.filter((r): r is Extract<Reason, { code: "possible_duplicate" }> => r.code === "possible_duplicate");
    const button = "rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted/40 disabled:opacity-50";

    return (
        <article className="rounded-xl border border-border p-4 sm:p-5">
            <div className="flex flex-col gap-4 lg:flex-row">
                <div className="flex-1 min-w-0 space-y-3">
                    <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                        <h2 className="font-semibold break-all">#{paper.id} {paper.filename}</h2>
                        <span className="text-xs text-muted-foreground">
                            {paper.uploader ? paper.uploader.name ?? paper.uploader.email : "Anonymous"}
                            {paper.uploadedAt && ` · ${new Date(paper.uploadedAt).toLocaleString()}`}
                        </span>
                    </header>

                    {paper.reasons.length > 0 && (
                        <ul className="list-disc pl-5 text-sm text-amber-400">
                            {paper.reasons.map((reason, i) => <li key={i}>{describeReason(reason)}</li>)}
                        </ul>
                    )}
                    {paper.note && <p className="text-sm text-muted-foreground">Note: {paper.note}</p>}

                    {editing ? (
                        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                            {[["filename", "File name"] as const, ...DETAIL_FIELDS].map(([key, label]) => (
                                <label key={key} className="text-xs text-muted-foreground">
                                    {label}
                                    <input
                                        value={draft[key] ?? ""}
                                        onChange={e => setDraft(d => ({ ...d, [key]: e.target.value }))}
                                        className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground"
                                    />
                                </label>
                            ))}
                        </div>
                    ) : (
                        <Details paper={paper} />
                    )}

                    {paper.preview && (
                        <details className="text-sm">
                            <summary className="cursor-pointer text-muted-foreground">Questions (OCR)</summary>
                            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-muted/30 p-3 text-xs">{paper.preview}</pre>
                        </details>
                    )}

                    <div className="flex flex-wrap gap-2">
                        <button className={button} disabled={busy} onClick={() => void openPdf(paper.id).catch(e => onError(describeApiError(e)))}>Open PDF</button>
                        {editing ? (
                            <>
                                <button className={button} disabled={busy} onClick={save}>Save</button>
                                <button className={button} disabled={busy} onClick={() => setEditing(false)}>Cancel</button>
                            </>
                        ) : (
                            paper.status !== "processing" && <button className={button} disabled={busy} onClick={startEdit}>Edit</button>
                        )}
                        {paper.status === "review" && (
                            <>
                                <button className={cn(button, "border-green-600 text-green-500")} disabled={busy} onClick={() => void post("live")}>Approve</button>
                                <button className={cn(button, "border-red-600 text-red-400")} disabled={busy} onClick={() => askReasonThen("rejected")}>Reject</button>
                            </>
                        )}
                        {paper.status === "live" && (
                            <button className={cn(button, "border-red-600 text-red-400")} disabled={busy} onClick={() => askReasonThen("rejected")}>Take down</button>
                        )}
                        {paper.status === "rejected" && <button className={button} disabled={busy} onClick={() => void post("live")}>Restore</button>}
                        {paper.status === "failed" && <button className={button} disabled={busy} onClick={() => void post("processing")}>Retry</button>}
                        {paper.status !== "processing" && (
                            <button className={cn(button, "text-red-400")} disabled={busy} onClick={remove}>Delete</button>
                        )}
                    </div>
                </div>

                {copies.map(copy => {
                    const match = matches[String(copy.paperId)];
                    if (!match) return null;
                    return (
                        <aside key={copy.paperId} className="lg:w-96 shrink-0 space-y-3 rounded-lg border border-amber-500/40 bg-amber-500/5 p-4">
                            <p className="text-xs uppercase tracking-wider text-amber-400">
                                Possible copy of · {Math.round(copy.overlap * 100)}% same questions
                            </p>
                            <h3 className="font-medium break-all">#{match.id} {match.filename} <span className="text-xs text-muted-foreground">({match.status})</span></h3>
                            <Details paper={match} />
                            {match.preview && (
                                <details className="text-sm">
                                    <summary className="cursor-pointer text-muted-foreground">Questions (OCR)</summary>
                                    <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-muted/30 p-3 text-xs">{match.preview}</pre>
                                </details>
                            )}
                            <button className={button} onClick={() => void openPdf(match.id).catch(e => onError(describeApiError(e)))}>Open PDF</button>
                        </aside>
                    );
                })}
            </div>
        </article>
    );
}

export default function AdminPapersPage() {
    const { ready, me } = useAuth();
    const [tab, setTab] = useState<Status>("review");
    const [listing, setListing] = useState<Listing | null>(null);
    const [error, setError] = useState("");
    const isAdmin = me?.role === "admin";

    const load = useCallback(async () => {
        try {
            const response = await apiFetch(`/api/v1/admin/papers?status=${tab}`);
            setListing((await response.json()) as Listing);
            setError("");
        } catch (e) {
            setError(describeApiError(e));
        }
    }, [tab]);

    useEffect(() => {
        if (!isAdmin) return;

        void (async () => {
            await load();
        })();
    }, [isAdmin, load]);

    if (!ready) return null;
    if (!isAdmin) return <AccessNotice title="Admins only" message="This page is for PrepWise admins." showSignIn={!me} />;

    return (
        <div className="container mx-auto max-w-6xl px-4 py-12">
            <AdminNav />
            <h1 className="mb-6 text-3xl font-bold">Papers</h1>
            <div className="mb-6 flex flex-wrap gap-2">
                {TABS.map(t => (
                    <button
                        key={t.status}
                        onClick={() => setTab(t.status)}
                        className={cn(
                            "rounded-full border px-3 py-1 text-sm",
                            tab === t.status ? "border-primary bg-primary/15 text-primary" : "border-border text-muted-foreground hover:text-foreground",
                        )}
                    >
                        {t.label} {listing ? `(${listing.counts[t.status] ?? 0})` : ""}
                    </button>
                ))}
            </div>
            {error && <p className="mb-4 text-sm text-red-400">{error}</p>}
            {listing && listing.papers.length === 0 && <p className="text-muted-foreground">Nothing here.</p>}
            <div className="space-y-4">
                {listing?.papers.map(paper => (
                    <PaperCard key={paper.id} paper={paper} matches={listing.matches} onChanged={load} onError={setError} />
                ))}
            </div>
        </div>
    );
}
```

- [ ] **Step 3: Lint and build**

Run: `cd frontend && npx eslint src/app/admin/papers/page.tsx src/app/admin/users/page.tsx src/components/AdminNav.tsx src/components/Navbar.tsx && npm run build`
Expected: no lint errors in these files, and the build succeeds.

The browser check happens in "Final check" at the end, not in this task.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/AdminNav.tsx frontend/src/app/admin/papers/page.tsx frontend/src/app/admin/users/page.tsx frontend/src/components/Navbar.tsx
git commit -m "feat: Admin → Papers page with review queue, side-by-side copies and moderation actions"
```

---

## Task 11: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the README**

1. **Database Schema → `papers` Table:** after the `CREATE TABLE`, add:
   > Upload moderation adds `status` (`processing` / `live` / `review` / `rejected` / `failed`), `review_reasons` (JSONB flags), `status_note`, `file_sha256` (unique among non-failed papers), `upload_key` (names the stored file `backend/papers/<upload_key>.pdf`), `attempts`, `retry_at`, `reviewed_by` and `reviewed_at`. `init_db()` adds them at startup; existing papers become `live`.
2. **Core Features:** add a bullet:
   > **Upload moderation:** uploads are processed in the background; clean papers go live, and papers flagged as unreadable, not an exam paper, missing details, or a likely copy wait in an admin review queue. Exact copies are refused.
3. **Environment Variables:** under the optional limits, add:
   ```
   # Background upload worker (default true; tests set false)
   PAPER_WORKER_ENABLED=true
   ```
4. **Admin commands:** add:
   ```bash
   python backend/admin_cli.py dedupe                  # preview duplicate groups
   python backend/admin_cli.py dedupe --keep 3 --apply # remove copies, keep paper 3 in its group
   ```
   Then add a line: "Admins review, edit, take down, restore, retry and delete papers on **Admin → Papers** (`/admin/papers`)."
5. **Deployment note:** add:
   > Uploads are limited to 10 MB, but nginx allows only 1 MB by default: add `client_max_body_size 11m;` to the prepwise `server` block.
6. **Tests:** replace the `TEST_DATABASE_URL` sentence with:
   > An optional `TEST_DATABASE_URL` runs the SQL tests (quota, paper store) against a real Postgres database — never production. A throwaway local one: `docker run -d --name prepwise-test-pg -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16-alpine`, then `TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres python -m pytest`.
7. **Before merging to `default`:** add these steps:
   > 7. Add `client_max_body_size 11m;` to nginx.
   > 8. After the new container starts: `docker compose exec backend python backend/admin_cli.py dedupe` to preview, then `... dedupe --apply` (add `--keep <id>` to choose which copy stays).

- [ ] **Step 2: Run the whole test suite one last time**

Run: `TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres backend/.venv/Scripts/python -m pytest -q`
Expected: all pass, with the Postgres tests included if the container is running.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: upload moderation, dedupe command and the nginx upload size"
```

---

## Final check (controller, with the user — not an implementer task)

This needs a browser and a real Microsoft sign-in, so the controller runs it with the user after Task 11 and the whole-branch review.

1. Start the backend against the throwaway DB, with the worker on and production Qdrant unreachable, so nothing in production is touched:
   ```bash
   DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres QDRANT_URL=http://127.0.0.1:1 PAPER_WORKER_ENABLED=true backend/.venv/Scripts/python -m uvicorn main:app --app-dir backend --port 8000
   ```
   It still calls NVIDIA OCR, the LLM and embeddings with the `.env` keys: a few calls per upload. With Qdrant unreachable, a paper ends as **Failed** after 3 attempts (about 4 minutes). That still exercises every screen.
2. Run `cd frontend && npm run dev`. Upload `backend/papers/SPM Major 2025.pdf` anonymously and check:
   - The row shows **Processing…** and polls every 5 s.
   - Uploading the same file again shows "Not uploaded" with the duplicate message.
   - A file over 10 MB is refused before it's sent.
3. Sign in, then make that account admin **in the throwaway DB only**: `DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres backend/.venv/Scripts/python backend/admin_cli.py make-admin <email>`.
4. On **Admin → Papers**, check:
   - The Failed tab shows the upload.
   - **Retry** moves it back to Processing.
   - **Edit** saves and shows the new details.
   - **Open PDF** opens it.
   - **Delete** asks for confirmation and removes it.
   - The Users tab still works through the new admin nav.
5. Report exactly what was seen, including anything that didn't work.
