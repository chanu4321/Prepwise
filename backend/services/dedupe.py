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
