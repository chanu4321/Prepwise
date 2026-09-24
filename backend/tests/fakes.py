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
        return {"subject": config["subject"], "sections": [], "sourcePapers": ["SPM 2023.pdf"], "totalSections": 0}


@contextmanager
def fake_db_cursor(rows):
    """A db_cursor replacement whose fetchone/fetchall return the given rows."""

    class _Cursor:
        def execute(self, *args, **kwargs):
            pass

        def fetchone(self):
            return rows[0] if rows else None

        def fetchall(self):
            return rows

    yield _Cursor()
