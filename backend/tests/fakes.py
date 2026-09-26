from dataclasses import replace
from datetime import datetime, timezone

from auth.tokens import Claims
from auth.users import ACTIONS, User, user_summary


class FakeUserStore:
    """In-memory stand-in for PostgresUserStore with the same method contract."""

    def __init__(self):
        self.users: dict[int, User] = {}
        self._ids: dict[tuple[str, str], int] = {}
        self.usage: dict[tuple[str, str], int] = {}
        self.touched: list[int] = []
        self._next_id = 1

    def get_or_create(self, claims: Claims) -> User:
        key = (claims.tid, claims.oid)
        if key not in self._ids:
            user = User(
                id=self._next_id, ms_tid=claims.tid, ms_oid=claims.oid, email=claims.email, name=claims.name,
                role=None, verified=False, created_at=datetime.now(timezone.utc),
            )
            self.users[user.id] = user
            self._ids[key] = user.id
            self._next_id += 1
        return self.users[self._ids[key]]

    def get(self, user_id):
        return self.users.get(user_id)

    def touch(self, user_id):
        self.touched.append(user_id)

    def set_role_once(self, user_id, role):
        user = self.users[user_id]
        if user.role is not None:
            return False
        self.users[user_id] = replace(user, role=role)
        return True

    def update(self, user_id, role=None, verified=None):
        user = self.users.get(user_id)
        if user is None:
            return None
        if role is not None:
            user = replace(user, role=role)
        if verified is not None:
            user = replace(user, verified=verified)
        self.users[user_id] = user
        return user

    def find_by_email(self, email):
        return [u for u in self.users.values() if (u.email or "").lower() == email.lower()]

    def list_with_usage(self):
        return [user_summary(u, self.usage_today(f"user:{u.id}")) for u in self.users.values()]

    def consume_quota(self, subject, action, limit):
        used = self.usage.get((subject, action), 0)
        if used >= limit:
            return False
        self.usage[(subject, action)] = used + 1
        return True

    def refund_quota(self, subject, action):
        used = self.usage.get((subject, action), 0)
        if used > 0:
            self.usage[(subject, action)] = used - 1

    def usage_today(self, subject):
        return {action: self.usage.get((subject, action), 0) for action in ACTIONS}


from contextlib import contextmanager


class FakeRag:
    """Stands in for RAGService. Set class attributes in a test to change behaviour."""

    papers = [{"filename": "SPM 2023.pdf", "ocrText": "Q1. Define risk."}]
    question_error = False
    raise_on_generate = False

    def _retrieve_similar_papers(self, subject, limit=3):
        return list(self.papers)

    def _extract_paper_context(self, papers):
        return "context"

    def _get_fuzzy_bloom_distribution(self, difficulty):
        return {}

    def _generate_question(self, q_config, context, subject, bloom_dist):
        if self.raise_on_generate:
            raise RuntimeError("LLM connection string postgres://secret")
        question = {"number": q_config.get("number", 1), "bloomLevel": "remember", "totalMarks": 6,
                    "parts": [], "rawGeneration": "Define risk.", "validation": {}}
        if self.question_error:
            question["error"] = True
        return question

    def generate_mock_paper(self, config):
        if not self.papers:
            return {"error": "No similar papers found in database"}
        sections = []
        for section in config.get("sections", []):
            questions = [
                self._generate_question(qc, "context", config["subject"], {})
                for qc in section.get("questions", [])
            ]
            sections.append({
                "name": section.get("name", "Section"),
                "instruction": section.get("instruction", ""),
                "questions": questions,
                "pool": [],
            })
        return {"subject": config["subject"], "sections": sections, "sourcePapers": ["SPM 2023.pdf"],
                "totalSections": len(sections)}


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


class FakeProcessor:
    """Stands in for DocumentProcessor; writes nothing but the uploaded file."""

    def __init__(self, upload_dir, metadata=None, full_text="full text", fail=False):
        self.upload_dir = str(upload_dir)
        self.metadata = metadata if metadata is not None else {
            "subjectCode": "aiml 201", "subjectName": "Intro to AIML", "monthYear": "May, 2022", "time": "3 Hrs", "marks": 60}
        self.full_text = full_text
        self.fail = fail

    def process_pdf(self, file_path):
        if self.fail:
            raise RuntimeError("tesseract crashed reading /secret/path")
        return {"text": "header text", "metadata": dict(self.metadata)}

    def extract_full_text(self, file_path):
        return self.full_text


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


from pathlib import Path

_FIXTURES = Path(__file__).parent / "fixtures" / "moderation"


def moderation_fixture(name: str) -> str:
    """Real OCR text of a production paper (see fixtures/moderation)."""
    return (_FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
