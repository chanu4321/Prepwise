from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Form
from fastapi.responses import FileResponse, StreamingResponse
from services.ocr_service import DocumentProcessor
import shutil
import os
import logging
import json
import asyncio

from typing import List

from auth.deps import enforce_quota, get_user_store, refund_quota_if_counted, require_role, user_subject
from auth.limits import generate_limit
from auth.users import PostgresUserStore, User
from database import db_cursor
from errors import ApiError
from services.rag_service import RAGService
from services.syllabus_service import get_syllabus_by_code
from services.vector_service import VectorService

logger = logging.getLogger(__name__)
router = APIRouter()
# Store papers in 'backend/papers' directory
processor = DocumentProcessor(upload_dir="backend/papers")

@router.post("/documents/ingest")
async def ingest_document(file: UploadFile = File(...)):
    """
    Uploads a PDF, runs OCR/Metadata extraction, and returns the result.
    """
    try:
        # 1. Save the file locally
        file_ext = file.filename.split(".")[-1]
        if file_ext.lower() != "pdf":
            raise HTTPException(status_code=400, detail="Only PDF files are supported")

        # Use original filename
        filename = os.path.basename(file.filename)
        file_path = os.path.join(processor.upload_dir, filename)
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # 2. Process the file with retry logic
        max_retries = 2
        result = None
        metadata = {}
        
        for attempt in range(max_retries):
            result = processor.process_pdf(file_path)
            metadata = result.get("metadata", {}) or {}
            print(f"DEBUG METADATA (Attempt {attempt + 1}): {metadata}")
            
            # Validate critical fields (semester is optional)
            required_fields = [
                metadata.get("subjectCode"),
                metadata.get("subjectName"),
                metadata.get("monthYear"),
                metadata.get("time"),
                metadata.get("marks")
            ]
            
            # Check if all required fields have values
            if all(field for field in required_fields):
                print(f"OCR successful on attempt {attempt + 1}")
                break
            else:
                print(f"OCR incomplete on attempt {attempt + 1}, retrying...")
                if attempt == max_retries - 1:
                    print("Max retries reached, proceeding with partial data")
 # Debugging

        # 3. Save to Database (NeonDB)
        paper_id = None
        try:
            from database import get_db_connection
            conn = get_db_connection()
            cur = conn.cursor()
            
            # Normalize path for DB (use forward slashes)
            db_file_path = file_path.replace("\\", "/")

            # Handle marks field - sometimes OCR returns it as a dict or integer
            marks_value = metadata.get("marks")
            if isinstance(marks_value, dict):
                marks_value = marks_value.get("Max Marks") or marks_value.get("max_marks") or str(marks_value)
            elif isinstance(marks_value, int):
                marks_value = str(marks_value)
            
            cur.execute(
                """
                INSERT INTO papers (filename, file_path, subject_code, subject_name, semester, year, time, marks)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    filename,
                    db_file_path,
                    metadata.get("subjectCode"),
                    metadata.get("subjectName"),
                    metadata.get("semester"),
                    metadata.get("monthYear"),
                    metadata.get("time"),
                    marks_value
                )
            )
            paper_id = cur.fetchone()[0]
            conn.commit()
            cur.close()
            conn.close()

            # 4. Extract full text from ALL pages for RAG
            print(f"Extracting full text from all pages...")
            try:
                full_text = processor.extract_full_text(file_path)
                print(f"Extracted {len(full_text)} characters from PDF")
            except Exception as e:
                print(f"Full text extraction failed: {e}")
                full_text = result.get("text", "")  # Fallback to header text

            # 5. Generate & Store Vector Embedding (Qdrant)
            try:
                from services.vector_service import VectorService
                vector_service = VectorService()
                success = vector_service.upsert_paper(
                    paper_id=paper_id,
                    text=full_text,  # Use full text instead of just header
                    metadata={
                        "subject_code": metadata.get("subjectCode") or metadata.get("Subject Code"),
                        "subject_name": metadata.get("subjectName") or metadata.get("Subject Name"),
                        "year": metadata.get("monthYear") or metadata.get("Month/Year"),
                        "filename": filename
                    }
                )
                if success:
                    print(f"Vector embedding stored for paper {paper_id}")
                else:
                    print(f"Failed to store vector embedding for paper {paper_id}")
            except Exception as e:
                print(f"Vector Service Error: {e}")

        except Exception as e:
            print(f"Database Insert Error: {e}")
        
        return {
            "status": "success",
            "filename": filename,
            "db_id": paper_id,
            "data": result
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/syllabus/upload")
async def upload_syllabus(
    file: UploadFile = File(...),
    subject_code: str = Form(...),
    subject_name: str = Form(...)
):
    """Uploads a syllabus PDF, extracts modules and weightage, and saves to database."""
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
        
    try:
        file_bytes = await file.read()
        
        # Import dynamically or at top level to avoid circular imports if any
        from services.syllabus_service import process_and_save_syllabus
        
        result = process_and_save_syllabus(
            file_bytes=file_bytes,
            filename=file.filename,
            subject_code=subject_code,
            subject_name=subject_name
        )
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error", "Unknown error processing syllabus"))
            
        return result
        
    except Exception as e:
        logger.error(f"Error in syllabus upload endpoint: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing syllabus: {str(e)}")

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
    file_path, filename = row
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
    return result

@router.post("/generate/mock-paper-stream")
async def generate_mock_paper_stream(
    request: dict,
    user: User = Depends(require_role("faculty", "admin")),
    store: PostgresUserStore = Depends(get_user_store),
):
    """
    SSE streaming endpoint: generates one question at a time and streams each
    back as a Server-Sent Event so Cloudflare/nginx timeouts are never hit.
    """
    if "subject" not in request or "sections" not in request:
        raise ApiError(400, "invalid_request", "Missing required fields: subject, sections")

    subject_key = user_subject(user)
    limit = generate_limit(user)
    enforce_quota(store, subject_key, "generate", limit)

    async def event_stream():
        real_questions = 0
        try:
            import copy, math

            rag_service = RAGService()
            subject = request["subject"]
            sections = request["sections"]

            # Retrieve context once upfront
            similar_papers = rag_service._retrieve_similar_papers(subject, limit=3)
            if not similar_papers:
                yield _sse({"type": "error", "message": "No past papers found for this subject yet."})
                return
            context = rag_service._extract_paper_context(similar_papers)

            # Send metadata first so frontend knows source papers
            yield _sse({"type": "meta", "sourcePapers": [p["filename"] for p in similar_papers], "subject": subject})
            await asyncio.sleep(0)  # flush to client

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
                    await asyncio.sleep(0)

                for q_config in pool_configs:
                    q = rag_service._generate_question(q_config, context, subject, bloom_dist)
                    if not q.get("error"):
                        real_questions += 1
                    pool_questions.append(q)
                    yield _sse({"type": "question", "section": section_name, "is_pool": True, "question": q})
                    await asyncio.sleep(0)

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

