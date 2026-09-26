"""Admin moderation of papers: the review queue, edits, status changes and deletion."""
import os
from typing import Literal

from fastapi import APIRouter, Depends, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from auth.deps import require_role
from auth.users import User
from errors import ApiError
from services.paper_metadata import normalize_subject_code
from services.paper_store import (STATUSES, DuplicateFile, Paper, PostgresPaperStore, get_paper_store,
                                  resolve_paper_path)
from services.paper_worker import paper_worker
from services.rag_service import RAGService
from services.upload_checks import duplicate_paper_error, filename_problem, remove_quietly
from services.vector_service import VectorService

router = APIRouter(prefix="/admin/papers")

PREVIEW_CHARS = 1500
HAS_VECTOR = ("live", "review", "rejected")
TRANSITIONS = {("review", "live"), ("review", "rejected"), ("live", "rejected"), ("rejected", "live"),
               ("failed", "processing")}
EDITABLE = {"subjectCode": "subject_code", "subjectName": "subject_name", "semester": "semester",
            "year": "year", "time": "time", "marks": "marks"}


def get_vectors() -> VectorService:
    return VectorService()


class PaperEdit(BaseModel):
    filename: str | None = None
    subjectCode: str | None = None
    subjectName: str | None = None
    semester: str | None = None
    year: str | None = None
    time: str | None = None
    marks: str | None = None


class StatusChange(BaseModel):
    status: Literal["live", "rejected", "processing"]
    note: str | None = Field(default=None, max_length=500)


def _iso(value):
    return value.isoformat() if value else None


def _preview(text: str | None) -> str:
    return RAGService._questions_only(text or "")[:PREVIEW_CHARS]


def admin_paper(paper: Paper, preview: str = "") -> dict:
    fields = paper.fields
    return {
        "id": paper.id, "filename": paper.filename, "status": paper.status, "note": paper.status_note,
        "reasons": paper.review_reasons,
        "subjectCode": fields["subject_code"], "subjectName": fields["subject_name"], "semester": fields["semester"],
        "year": fields["year"], "time": fields["time"], "marks": fields["marks"],
        "uploadedAt": _iso(paper.uploaded_at),
        "uploader": ({"name": paper.uploader_name, "email": paper.uploader_email}
                     if paper.uploaded_by is not None else None),
        "reviewedBy": paper.reviewed_by,
        "reviewedAt": _iso(paper.reviewed_at),
        "preview": preview,
    }


def _get_or_404(papers: PostgresPaperStore, paper_id: int) -> Paper:
    paper = papers.get(paper_id)
    if paper is None:
        raise ApiError(404, "not_found", "Paper not found.")
    return paper


def _index_unavailable() -> ApiError:
    return ApiError(503, "search_index_unavailable", "Couldn't update the search index, so nothing was changed. Try again.")


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


@router.get("")
def list_papers(status: Literal[STATUSES] = "review", admin: User = Depends(require_role("admin")),
                papers: PostgresPaperStore = Depends(get_paper_store), vectors: VectorService = Depends(get_vectors)):
    listed = papers.list_by_status(status)
    match_ids = {reason["paperId"] for paper in listed for reason in paper.review_reasons
                 if reason.get("code") == "possible_duplicate"}
    matched = papers.get_many(match_ids)
    texts = vectors.get_paper_texts(sorted({p.id for p in listed + matched if p.status in HAS_VECTOR}))
    return {
        "papers": [admin_paper(p, _preview(texts.get(p.id))) for p in listed],
        "counts": papers.counts(),
        "matches": {str(p.id): admin_paper(p, _preview(texts.get(p.id))) for p in matched},
    }


@router.patch("/{paper_id}")
def edit_paper(paper_id: int, body: PaperEdit, admin: User = Depends(require_role("admin")),
               papers: PostgresPaperStore = Depends(get_paper_store), vectors: VectorService = Depends(get_vectors)):
    paper = _get_or_404(papers, paper_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(400, "invalid_request", "Nothing to update.")
    filename = paper.filename
    if "filename" in changes:
        filename = (changes["filename"] or "").strip()
        problem = filename_problem(filename)
        if problem:
            raise ApiError(400, "invalid_request", problem)
    fields = dict(paper.fields)
    for api_key, column in EDITABLE.items():
        if api_key in changes:
            fields[column] = _clean(changes[api_key])
    fields["subject_code"] = normalize_subject_code(fields["subject_code"])
    if paper.status in HAS_VECTOR:
        payload = {"subject_code": fields["subject_code"], "subject_name": fields["subject_name"],
                   "year": fields["year"], "filename": filename}
        if not vectors.set_paper_payload(paper.id, payload):
            raise _index_unavailable()
    return admin_paper(papers.update_details(paper.id, filename, fields, admin.id))


@router.post("/{paper_id}/status")
def change_status(paper_id: int, body: StatusChange, admin: User = Depends(require_role("admin")),
                  papers: PostgresPaperStore = Depends(get_paper_store), vectors: VectorService = Depends(get_vectors)):
    paper = _get_or_404(papers, paper_id)
    if (paper.status, body.status) not in TRANSITIONS:
        raise ApiError(409, "invalid_transition", f"A {paper.status} paper can't be moved to {body.status}.")
    note = _clean(body.note)
    if body.status == "rejected" and not note:
        raise ApiError(400, "invalid_request", "Give a reason. The uploader will see it.")
    if body.status in HAS_VECTOR and not vectors.set_paper_payload(paper.id, {"status": body.status}):
        raise _index_unavailable()
    try:
        updated = papers.set_status(paper.id, body.status, note if body.status == "rejected" else None, admin.id)
    except DuplicateFile:
        raise duplicate_paper_error(papers.find_active_by_hash(paper.file_sha256))
    if body.status == "processing":
        paper_worker.wake()
    return admin_paper(updated)


@router.delete("/{paper_id}", status_code=204)
def delete_paper(paper_id: int, admin: User = Depends(require_role("admin")),
                 papers: PostgresPaperStore = Depends(get_paper_store), vectors: VectorService = Depends(get_vectors)):
    paper = _get_or_404(papers, paper_id)
    if paper.status == "processing":
        raise ApiError(409, "invalid_transition", "This paper is still being processed. Try again in a few minutes.")
    if paper.status in HAS_VECTOR and not vectors.delete_paper(paper.id):
        raise _index_unavailable()
    papers.delete(paper.id)
    if papers.count_file_users(paper.file_path) == 0:
        remove_quietly(resolve_paper_path(paper.file_path))
    return Response(status_code=204)


@router.get("/{paper_id}/file")
def paper_file(paper_id: int, admin: User = Depends(require_role("admin")),
               papers: PostgresPaperStore = Depends(get_paper_store)):
    paper = _get_or_404(papers, paper_id)
    path = resolve_paper_path(paper.file_path)
    if not os.path.exists(path):
        raise ApiError(404, "not_found", "This paper's file is missing.")
    return FileResponse(path=path, filename=paper.filename, media_type="application/pdf")
