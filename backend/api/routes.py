from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
import ntpath
import os
import logging
import json

from typing import List

from auth.deps import (enforce_quota, get_user_store, optional_user, quota_subject, refund_quota_if_counted,
                       require_role, user_subject)
from auth.limits import generate_limit, syllabus_limit, upload_limit
from auth.users import PostgresUserStore, User
from database import db_cursor
from errors import ApiError
from services.paper_store import (PAPERS_DIR, DuplicateFile, PostgresPaperStore, get_paper_store, new_upload_key,
                                  papers_dir, resolve_paper_path)
from services.paper_worker import paper_worker
from services.rag_service import RAGService
from services.syllabus_service import get_syllabus_by_code, get_syllabus_owner, process_and_save_syllabus
from services.upload_checks import (MAX_PAGES, MAX_UPLOAD_BYTES, FileTooLarge, count_pdf_pages,
                                    duplicate_paper_error, filename_problem, remove_quietly, save_upload)
from services.vector_service import VectorService

logger = logging.getLogger(__name__)
router = APIRouter()


def _validated_pdf_upload(file: UploadFile) -> str:
    """Validates the uploaded file's name and content before any quota is consumed.

    Uses ntpath.basename (splits on both '/' and '\\' on every OS) so a traversal filename
    like '../../evil.pdf' or '..\\..\\evil.pdf' collapses to a bare name. Returns that safe
    filename, or raises ApiError(400, "invalid_file", ...).
    """
    name = ntpath.basename(file.filename or "")
    problem = filename_problem(name)
    if problem:
        raise ApiError(400, "invalid_file", problem)

    header = file.file.read(1024)
    file.file.seek(0)
    if b"%PDF" not in header:
        raise ApiError(400, "invalid_file", "This file isn't a valid PDF.")
    return name

def _too_large() -> ApiError:
    return ApiError(413, "file_too_large",
                    f"This file is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. Compress it or split it, then try again.")


@router.post("/documents/ingest", status_code=202)
def ingest_document(
    http_request: Request,
    file: UploadFile = File(...),
    user: User | None = Depends(optional_user),
    store: PostgresUserStore = Depends(get_user_store),
    papers: PostgresPaperStore = Depends(get_paper_store),
):
    """Checks and stores an uploaded PDF. OCR, metadata, the automatic checks and indexing run in the
    background worker; the uploader polls /documents/uploads/{uploadKey} for the result."""
    if user is not None and user.role is None:
        raise ApiError(403, "role_required", "Choose Student or Faculty to continue.")
    limit = upload_limit(user)
    if limit == 0:
        raise ApiError(403, "forbidden", "Paper uploads need a student or verified faculty account.")

    filename = _validated_pdf_upload(file)
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise _too_large()

    upload_key = new_upload_key()
    stored_name = f"{upload_key}.pdf"
    destination = papers_dir() / stored_name
    try:
        file_sha256 = save_upload(file.file, destination, MAX_UPLOAD_BYTES)
    except FileTooLarge:
        raise _too_large()

    try:
        pages = count_pdf_pages(destination)
        if pages is None:
            raise ApiError(400, "invalid_file", "This file isn't a valid PDF.")
        if pages > MAX_PAGES:
            raise ApiError(400, "too_many_pages",
                           f"This PDF has {pages} pages. Papers can have at most {MAX_PAGES}.")
        existing = papers.find_active_by_hash(file_sha256)
        if existing is not None:
            raise duplicate_paper_error(existing)

        subject = quota_subject(user, http_request)
        enforce_quota(store, subject, "upload", limit)
        try:
            paper = papers.create_upload(filename, f"{PAPERS_DIR}/{stored_name}", file_sha256, upload_key,
                                         user.id if user is not None else None)
        except DuplicateFile:
            # Another upload of the same file got in first
            refund_quota_if_counted(store, subject, "upload", limit)
            raise duplicate_paper_error(papers.find_active_by_hash(file_sha256))
        except Exception:
            refund_quota_if_counted(store, subject, "upload", limit)
            raise
    except BaseException:
        remove_quietly(destination)
        raise

    paper_worker.wake()
    return {"id": paper.id, "uploadKey": upload_key, "filename": filename, "status": paper.status}

@router.post("/syllabus/upload")
def upload_syllabus(
    file: UploadFile = File(...),
    subject_code: str = Form(...),
    subject_name: str = Form(...),
    user: User = Depends(require_role("faculty", "admin")),
    store: PostgresUserStore = Depends(get_user_store),
):
    """Uploads a syllabus PDF, extracts modules and weightage, and saves to database."""
    limit = syllabus_limit(user)
    if limit == 0:
        raise ApiError(403, "forbidden", "Syllabus uploads need a verified faculty account.")
    filename = _validated_pdf_upload(file)

    exists, owner = get_syllabus_owner(subject_code)
    if exists and user.role != "admin" and owner != user.id:
        raise ApiError(403, "forbidden", "Only the original uploader or an admin can replace this syllabus.")

    subject = user_subject(user)
    enforce_quota(store, subject, "syllabus", limit)
    result = process_and_save_syllabus(
        file_bytes=file.file.read(),
        filename=filename,
        subject_code=subject_code,
        subject_name=subject_name,
        uploaded_by=user.id,
    )
    if not result.get("success"):
        logger.error("Syllabus processing failed for %s: %s", subject_code, result.get("error"))
        refund_quota_if_counted(store, subject, "syllabus", limit)
        raise ApiError(422, "syllabus_unreadable",
                       "We couldn't read the modules from this syllabus. Check the PDF and try again.")
    return result

@router.get("/syllabus/{subject_code}")
def get_syllabus(subject_code: str, user: User = Depends(require_role("faculty", "admin"))):
    """Retrieves a syllabus by subject code. Faculty only: syllabi are not public."""
    syllabus = get_syllabus_by_code(subject_code)
    if not syllabus:
        raise ApiError(404, "not_found", f"No syllabus found for subject code {subject_code}.")
    return {"success": True, "data": syllabus}

@router.get("/documents", response_model=List[dict])
def get_documents():
    """Fetch all documents from NeonDB for the frontend."""
    with db_cursor() as cur:
        cur.execute("SELECT id, filename, subject_code, subject_name, semester, year, time, marks FROM papers ORDER BY id DESC")
        rows = cur.fetchall()
    return [
        {"id": row[0], "filename": row[1], "subjectCode": row[2], "subjectName": row[3],
         "semester": row[4], "year": row[5], "time": row[6], "marks": row[7]}
        for row in rows
    ]

@router.get("/documents/{paper_id}/download")
def download_paper(paper_id: int):
    """Download a paper PDF by ID."""
    with db_cursor() as cur:
        cur.execute("SELECT file_path, filename FROM papers WHERE id = %s", (paper_id,))
        row = cur.fetchone()
    if not row:
        raise ApiError(404, "not_found", "Paper not found.")
    stored_path, filename = row
    file_path = resolve_paper_path(stored_path)
    if not os.path.exists(file_path):
        logger.error("Paper %s is in the database but missing on disk at %s", paper_id, file_path)
        raise ApiError(404, "not_found", "This paper's file is missing.")
    return FileResponse(path=file_path, filename=filename, media_type="application/pdf")

@router.post("/search/semantic")
def semantic_search(request: dict):
    """Search for papers semantically using Qdrant and NeonDB."""
    query = request.get("query", "")
    limit = request.get("limit", 5)
    results = VectorService().search_similar(query, limit)
    if not results:
        return []

    paper_ids = [point.id for point in results]
    scores = {point.id: point.score for point in results}
    placeholders = ",".join(["%s"] * len(paper_ids))
    with db_cursor() as cur:
        cur.execute(
            f"SELECT id, filename, subject_code, subject_name, semester, year, time, marks FROM papers WHERE id IN ({placeholders})",
            tuple(paper_ids),
        )
        rows = cur.fetchall()

    papers = {
        row[0]: {"id": row[0], "filename": row[1], "subjectCode": row[2], "subjectName": row[3], "semester": row[4],
                 "year": row[5], "time": row[6], "marks": row[7], "relevance": scores.get(row[0], 0)}
        for row in rows
    }
    # Return in order of Qdrant relevance
    return [papers[pid] for pid in paper_ids if pid in papers]

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"

def _has_usable_question(sections: list) -> bool:
    """True if any question (in a section's `questions` or `pool` list) isn't a flagged fallback."""
    for section in sections:
        for question in section.get("questions", []) + section.get("pool", []):
            if not question.get("error"):
                return True
    return False

@router.post("/generate/mock-paper")
def generate_mock_paper(
    request: dict,
    user: User = Depends(require_role("faculty", "admin")),
    store: PostgresUserStore = Depends(get_user_store),
):
    """Generate a mock examination paper using RAG (non-streaming)."""
    if "subject" not in request or "sections" not in request:
        raise ApiError(400, "invalid_request", "Missing required fields: subject, sections")

    subject_key = user_subject(user)
    limit = generate_limit(user)
    enforce_quota(store, subject_key, "generate", limit)
    try:
        result = RAGService().generate_mock_paper(request)
    except Exception:
        refund_quota_if_counted(store, subject_key, "generate", limit)
        raise
    if "error" in result:
        refund_quota_if_counted(store, subject_key, "generate", limit)
        raise ApiError(404, "not_found", "No past papers found for this subject yet.")
    if not _has_usable_question(result.get("sections", [])):
        refund_quota_if_counted(store, subject_key, "generate", limit)
    return result

@router.post("/generate/mock-paper-stream")
def generate_mock_paper_stream(
    request: dict,
    user: User = Depends(require_role("faculty", "admin")),
    store: PostgresUserStore = Depends(get_user_store),
):
    """
    SSE streaming endpoint: generates one question at a time and streams each
    back as a Server-Sent Event so Cloudflare/nginx timeouts are never hit.

    Plain `def` on purpose: the quota check, the vector search and the minutes-long LLM calls are
    blocking, so they run in worker threads (Starlette iterates a sync generator in a threadpool)
    instead of freezing the event loop for every other request.
    """
    if "subject" not in request or "sections" not in request:
        raise ApiError(400, "invalid_request", "Missing required fields: subject, sections")

    subject_key = user_subject(user)
    limit = generate_limit(user)
    enforce_quota(store, subject_key, "generate", limit)

    def event_stream():
        real_questions = 0
        try:
            import copy, math

            rag_service = RAGService()
            subject = request["subject"]
            sections = request["sections"]

            # Retrieve context once upfront
            similar_papers = rag_service._retrieve_similar_papers(subject)
            if not similar_papers:
                yield _sse({"type": "error", "message": "No past papers found for this subject yet."})
                return
            context = rag_service._extract_paper_context(similar_papers)

            # Send metadata first so frontend knows source papers
            yield _sse({"type": "meta", "sourcePapers": [p["filename"] for p in similar_papers], "subject": subject})

            result_sections = []

            for section in sections:
                section_name = section.get("name", "Section")
                questions_config = section.get("questions", [])
                bloom_mode = section.get("bloomMode", "simple")
                generate_pool = section.get("generate_pool", False)

                if bloom_mode == "simple":
                    bloom_dist = rag_service._get_fuzzy_bloom_distribution(section.get("difficulty", "medium"))
                else:
                    bloom_dist = section.get("bloomDistribution", {})

                pool_configs = []
                if generate_pool and questions_config:
                    pool_count = math.ceil(len(questions_config) * 0.5)
                    for i in range(pool_count):
                        base = copy.deepcopy(questions_config[i % len(questions_config)])
                        base["number"] = len(questions_config) + i + 1
                        pool_configs.append(base)

                generated_questions = []
                pool_questions = []

                # Stream each question as it's generated
                for q_config in questions_config:
                    q = rag_service._generate_question(q_config, context, subject, bloom_dist)
                    if not q.get("error"):
                        real_questions += 1
                    generated_questions.append(q)
                    yield _sse({"type": "question", "section": section_name, "is_pool": False, "question": q})

                for q_config in pool_configs:
                    q = rag_service._generate_question(q_config, context, subject, bloom_dist)
                    if not q.get("error"):
                        real_questions += 1
                    pool_questions.append(q)
                    yield _sse({"type": "question", "section": section_name, "is_pool": True, "question": q})

                result_sections.append({
                    "name": section_name,
                    "instruction": section.get("instruction", ""),
                    "questions": generated_questions,
                    "pool": pool_questions
                })

            # Final event with complete assembled paper
            yield _sse({"type": "done", "sections": result_sections, "subject": subject,
                        "sourcePapers": [p["filename"] for p in similar_papers]})

        except Exception:
            logger.exception("Streaming generation failed for subject %r", request.get("subject"))
            yield _sse({"type": "error", "message": "Paper generation failed. Please try again."})
        finally:
            # A run that produced no usable question doesn't count against the daily limit
            if real_questions == 0:
                refund_quota_if_counted(store, subject_key, "generate", limit)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no"  # disables nginx response buffering
        }
    )

