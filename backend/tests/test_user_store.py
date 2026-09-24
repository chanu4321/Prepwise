"""Tests for user store implementations that don't need a real database."""

from auth.users import PostgresUserStore


def test_zero_limit_is_refused_without_touching_the_database():
    """Verify that consume_quota refuses limit=0 without attempting a database connection.

    This test uses the test env's unreachable DATABASE_URL, so if the guard is missing,
    the call would try to connect and raise an error. With the guard in place, it returns
    False without any database access.
    """
    assert PostgresUserStore().consume_quota("user:1", "generate", 0) is False
