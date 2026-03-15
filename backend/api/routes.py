from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from backend.services.ocr_service import DocumentProcessor
import shutil
import os
import uuid
import logging

from typing import List, Optional
from backend.models import PaperMetadata

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
            from backend.database import get_db_connection
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
                from backend.services.vector_service import VectorService
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
        from backend.services.syllabus_service import process_and_save_syllabus
        
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
async def get_syllabus(subject_code: str):
    """Retrieves a syllabus by subject code."""
    from backend.services.syllabus_service import get_syllabus_by_code
    
    syllabus = get_syllabus_by_code(subject_code)
    if not syllabus:
        raise HTTPException(status_code=404, detail=f"Syllabus not found for subject code: {subject_code}")
        
    return {"success": True, "data": syllabus}

@router.get("/documents", response_model=List[dict])
async def get_documents():
    """Fetch all documents from NeonDB for the frontend."""
    try:
        from backend.database import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, filename, subject_code, subject_name, semester, year, time, marks FROM papers ORDER BY id DESC")
        rows = cur.fetchall()
        
        papers = []
        for row in rows:
            papers.append({
                "id": row[0],
                "filename": row[1],
                "subjectCode": row[2],
                "subjectName": row[3],
                "semester": row[4],
                "year": row[5],
                "time": row[6],
                "marks": row[7]
            })
        
        cur.close()
        conn.close()
        return papers
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/documents/{paper_id}/download")
async def download_paper(paper_id: int):
    """Download a paper PDF by ID."""
    try:
        from backend.database import get_db_connection
        from fastapi.responses import FileResponse
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT file_path, filename FROM papers WHERE id = %s", (paper_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        
        if not result:
            raise HTTPException(status_code=404, detail="Paper not found")
        
        file_path, filename = result
        
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="File not found on disk")
        
        return FileResponse(
            path=file_path,
            filename=filename,
            media_type="application/pdf"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/search/semantic")
async def semantic_search(request: dict):
    """
    Search for papers semantically using Qdrant and NeonDB.
    """
    query = request.get("query", "")
    limit = request.get("limit", 5)
    try:
        from backend.services.vector_service import VectorService
        from backend.database import get_db_connection
        
        # 1. Get similar paper IDs from Qdrant
        vector_service = VectorService()
        results = vector_service.search_similar(query, limit)
        
        if not results:
            return []
            
        paper_ids = [point.id for point in results]
        
        # 2. Fetch full details from NeonDB
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Safe SQL query for multiple IDs
        if not paper_ids:
            return []
            
        query_placeholder = ','.join(['%s'] * len(paper_ids))
        cur.execute(
            f"SELECT id, filename, subject_code, subject_name, semester, year, time, marks FROM papers WHERE id IN ({query_placeholder})",
            tuple(paper_ids)
        )
        rows = cur.fetchall()
        
        # 3. Map results back to order of relevance (Qdrant order)
        db_papers = {row[0]: {
            "id": row[0],
            "filename": row[1],
            "subjectCode": row[2],
            "subjectName": row[3],
            "semester": row[4],
            "year": row[5],
            "time": row[6],
            "marks": row[7],
            "relevance": next((r.score for r in results if r.id == row[0]), 0)
        } for row in rows}
        
        # Return in order of Qdrant results
        ordered_response = [db_papers[pid] for pid in paper_ids if pid in db_papers]
        
        cur.close()
        conn.close()
        
        return ordered_response
        
    except Exception as e:
        print(f"Semantic Search Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/generate/mock-paper")
async def generate_mock_paper(request: dict):
    """
    Generate a mock examination paper using RAG.
    
    Request body:
    {
      "subject": "Software Project Management",
      "sections": [
        {
          "name": "Section A",
          "instruction": "Attempt any 4 out of 5",
          "bloomMode": "simple",
          "difficulty": "easy",
          "questions": [
            {
              "number": 1,
              "bloomLevel": "remember",
              "totalMarks": 6,
              "parts": [{"label": "a", "marks": 3}, {"label": "b", "marks": 3}]
            }
          ]
        }
      ]
    }
    """
    try:
        from backend.services.rag_service import RAGService
        
        # Validate required fields
        if "subject" not in request or "sections" not in request:
            raise HTTPException(status_code=400, detail="Missing required fields: subject, sections")
        
        # Initialize RAG service
        rag_service = RAGService()
        
        # Generate mock paper
        result = rag_service.generate_mock_paper(request)
        
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Mock Paper Generation Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

