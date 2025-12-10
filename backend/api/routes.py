from fastapi import APIRouter, UploadFile, File, HTTPException
from backend.services.ocr_service import DocumentProcessor
import shutil
import os
import uuid

from typing import List, Optional
from backend.models import PaperMetadata

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
                metadata.get("subjectCode") or metadata.get("Subject Code"),
                metadata.get("subjectName") or metadata.get("Subject Name"),
                metadata.get("monthYear") or metadata.get("Month/Year"),
                metadata.get("time") or metadata.get("Time"),
                metadata.get("marks") or metadata.get("Marks")
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

            # Handle marks field - sometimes OCR returns it as a dict
            marks_value = metadata.get("marks") or metadata.get("Marks")
            if isinstance(marks_value, dict):
                marks_value = marks_value.get("Max Marks") or marks_value.get("max_marks") or str(marks_value)
            
            cur.execute(
                """
                INSERT INTO papers (filename, file_path, subject_code, subject_name, semester, year, time, marks)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    filename,
                    db_file_path,
                    metadata.get("subjectCode") or metadata.get("Subject Code"),
                    metadata.get("subjectName") or metadata.get("Subject Name"),
                    metadata.get("semester") or metadata.get("Semester"),
                    metadata.get("monthYear") or metadata.get("Month/Year"),
                    metadata.get("time") or metadata.get("Time"),
                    marks_value
                )
            )
            paper_id = cur.fetchone()[0]
            conn.commit()
            cur.close()
            conn.close()
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
