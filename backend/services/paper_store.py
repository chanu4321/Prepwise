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

    def finish(self, paper_id: int, fields: dict, status: str, reasons: list, note: str | None) -> bool:
        """Completes a claimed paper. Returns False (and changes nothing) if it's no longer 'processing',
        e.g. an admin deleted or changed it while the worker was still running."""
        with db_cursor() as cur:
            cur.execute(
                "UPDATE papers SET subject_code = %s, subject_name = %s, semester = %s, year = %s, time = %s, "
                "marks = %s, status = %s, review_reasons = %s::jsonb, status_note = %s, retry_at = NULL "
                "WHERE id = %s AND status = 'processing' RETURNING id",
                (*(fields[k] for k in PAPER_FIELDS), status, json.dumps(reasons), note, paper_id),
            )
            return cur.fetchone() is not None

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

    def seconds_until_next_due(self) -> float | None:
        """Seconds until the earliest 'processing' paper is due (0 or below means due now), or None
        when nothing is processing."""
        with db_cursor() as cur:
            cur.execute(
                "SELECT EXTRACT(EPOCH FROM (min(COALESCE(retry_at, now())) - now())) FROM papers "
                "WHERE status = 'processing'"
            )
            value = cur.fetchone()[0]
        return float(value) if value is not None else None


def get_paper_store() -> PostgresPaperStore:
    return PostgresPaperStore()
