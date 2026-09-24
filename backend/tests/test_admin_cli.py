from admin_cli import make_admin
from auth.tokens import Claims
from tests.fakes import FakeUserStore


def store_with(*emails):
    store = FakeUserStore()
    for i, email in enumerate(emails):
        store.get_or_create(Claims(tid=f"tenant-{i}", oid=f"oid-{i}", name=f"Person {i}", email=email))
    return store


def test_unknown_email_explains_what_to_do():
    lines = []
    assert make_admin("nobody@example.com", store_with(), out=lines.append) is False
    assert "Sign in to PrepWise once" in lines[0]


def test_single_match_confirmed():
    store = store_with("me@outlook.com")
    assert make_admin("ME@outlook.com", store, input_fn=lambda _: "y", out=lambda _: None) is True
    assert store.get(1).role == "admin" and store.get(1).verified is True


def test_single_match_declined_changes_nothing():
    store = store_with("me@outlook.com")
    assert make_admin("me@outlook.com", store, input_fn=lambda _: "n", out=lambda _: None) is False
    assert store.get(1).role is None


def test_duplicate_emails_require_choosing_an_id():
    store = store_with("me@outlook.com", "me@outlook.com")
    assert make_admin("me@outlook.com", store, input_fn=lambda _: "2", out=lambda _: None) is True
    assert store.get(1).role is None and store.get(2).role == "admin"
    assert make_admin("me@outlook.com", store, input_fn=lambda _: "", out=lambda _: None) is False
