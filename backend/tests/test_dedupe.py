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


def test_apply_leaves_papers_in_place_when_the_search_index_fails(production_like):
    store, vectors, (rescan, ai, ai_copy, cloud_a, cloud_b), tmp_path = production_like
    vectors.fail_payload = True
    lines = []
    assert run_dedupe({ai}, apply=True, store=store, vectors=vectors, out=lines.append) == 1
    assert len(store.list_all()) == 5
    assert (tmp_path / "ai1.pdf").exists()
    text = "\n".join(lines)
    assert "left in place" in text
    assert "another paper has the same file" in text
    assert "couldn't tag its search entry" in text
