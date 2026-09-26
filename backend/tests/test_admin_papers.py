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


def test_delete_removes_a_leftover_vector_from_a_failed_paper(client, admin, papers, vectors, tmp_path):
    pdf = tmp_path / "z.pdf"
    pdf.write_bytes(b"%PDF")
    paper = papers.add_paper(status="failed", file_path=str(pdf))
    vectors.points[paper.id] = {"status": "failed"}  # left behind by an earlier attempt
    assert client.delete(f"{BASE}/{paper.id}", headers=admin).status_code == 204
    assert paper.id not in vectors.points


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
