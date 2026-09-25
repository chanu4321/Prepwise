"""Runs the real SQL. Point TEST_DATABASE_URL at a throwaway database (e.g. a Neon branch).

Never point it at production: the tests create tables and write rows (they only delete their own).
"""
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

import database  # noqa: F401  (loads .env, which may define TEST_DATABASE_URL)
from auth.tokens import Claims
from auth.users import PostgresUserStore

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")
TENANT = "pytest-tenant"


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    from database import db_cursor, init_db

    init_db()
    with db_cursor() as cur:
        cur.execute("DELETE FROM usage_daily WHERE subject LIKE 'pytest:%%'")
        cur.execute("DELETE FROM users WHERE ms_tid = %s", (TENANT,))
    return PostgresUserStore()


def test_get_or_create_is_idempotent(store):
    first = store.get_or_create(Claims(tid=TENANT, oid="o1", name="A", email="a@example.com"))
    second = store.get_or_create(Claims(tid=TENANT, oid="o1", name="A", email="a@example.com"))
    assert first.id == second.id
    assert first.role is None and first.verified is False


def test_role_can_only_be_chosen_once(store):
    user = store.get_or_create(Claims(tid=TENANT, oid="o2", name=None, email=None))
    assert store.set_role_once(user.id, "student") is True
    assert store.set_role_once(user.id, "faculty") is False
    assert store.get(user.id).role == "student"


def test_quota_stops_at_limit_and_refund_gives_one_back(store):
    assert [store.consume_quota("pytest:q", "generate", 2) for _ in range(3)] == [True, True, False]
    store.refund_quota("pytest:q", "generate")
    assert store.consume_quota("pytest:q", "generate", 2) is True
    assert store.usage_today("pytest:q")["generate"] == 2


def test_concurrent_consume_never_exceeds_limit(store):
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: store.consume_quota("pytest:race", "generate", 3), range(10)))
    assert results.count(True) == 3
