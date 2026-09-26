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
