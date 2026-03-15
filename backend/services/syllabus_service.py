import os
import json
import logging
import requests
from typing import Dict, Any, Optional
from backend.services.ocr_service import DocumentProcessor
from backend.database import get_db_connection

logger = logging.getLogger(__name__)

# OLLAMA_URL = "http://localhost:11434/api/chat"
# OLLAMA_MODEL = "qwen2.5:3b"

def _parse_syllabus_with_llm(text: str) -> Optional[list]:
    """Uses LLM to extract modules, topics, and weightage percentage as a structured JSON array."""
    
    system_prompt = '''You are an expert academic assistant. Your task is to extract syllabus modules, their topics, and their weightage (or marks) from the provided syllabus text.

    IMPORTANT: The text was extracted via OCR and may contain digit misreads (e.g., "2" read as "9", "1" as "l", etc.). 
    - Number the modules sequentially (Module 1, Module 2, Module 3...) in the order they appear in the text. DO NOT trust the digit from the OCR text if it looks out of sequence.
    - Clean up any garbled characters in the module name.

    You MUST output ONLY a valid JSON array of objects. Do not include any markdown formatting, explanations, or greeting.
    Each object in the array must have the following exact keys:
    - "name": (string) The module name numbered sequentially, e.g. "Module 1", "Module 2". Use sequential order, not whatever digit OCR extracted.
    - "topics": (string) A concise summary of the topics covered in this module.
    - "weightage_percent": (number) The relative weightage or marks of this module as an integer percentage (e.g., 20). If only raw marks are provided, convert them to an estimated percentage so the total across all modules is roughly 100. If no weightage or marks are mentioned at all for a module, estimate it to be equal to others so the total is 100.

    Example output format:
    [
        {"name": "Module 1", "topics": "Introduction, concepts, definitions", "weightage_percent": 20},
        {"name": "Module 2", "topics": "Advanced topics, applications", "weightage_percent": 30}
    ]
    '''
    
    api_url = os.getenv("LLM_API_URL", "https://integrate.api.nvidia.com/v1/chat/completions")
    headers = {
        "Authorization": f"Bearer {os.getenv('NVIDIA_API_KEY', '')}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": os.getenv("LLM_MODEL", "qwen/qwen3-next-80b-a3b-thinking"),
        "messages": [
            {"role": "user", "content": f"{system_prompt}\n\nTask: Extract the module breakdown from this syllabus text:\n\n{text}"}
        ],
        "temperature": 0.6,
        "top_p": 0.7,
        "max_tokens": 4096
    }

    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        
        content = result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
        
        # Fallback
        if not content:
            content = result.get('message', {}).get('content', '').strip()
        
        # Clean up potential markdown code blocks
        if content.startswith('```json'):
            content = content[7:]
        elif content.startswith('```'):
            content = content[3:]
        if content.endswith('```'):
            content = content[:-3]
            
        content = content.strip()
        
        modules = json.loads(content)
        
        if not isinstance(modules, list):
            logger.error("LLM did not return a JSON array.")
            return None
            
        # Validate/Normalize percentages
        total_percent = sum(m.get('weightage_percent', 0) for m in modules)
        if total_percent == 0:
             # Fallback equal distribution
             avg = 100 // len(modules) if modules else 0
             for m in modules:
                 m['weightage_percent'] = avg
        elif 95 <= total_percent <= 105:
            pass # Close enough
        else:
            # Normalize to 100%
            for m in modules:
                m['weightage_percent'] = round((m.get('weightage_percent', 0) / total_percent) * 100)
                
        return modules
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM JSON output: {e}. Output was: {content}")
        return None
    except Exception as e:
        logger.error(f"Error calling Ollama for syllabus parsing: {e}")
        return None

def process_and_save_syllabus(file_bytes: bytes, filename: str, subject_code: str, subject_name: str) -> Dict[str, Any]:
    """Processes a syllabus PDF, extracts modules, and saves to database."""
    
    # 1. Extract raw text via OCR
    try:
        doc_processor = DocumentProcessor(upload_dir="/tmp/syllabus_ocr")
        raw_text = doc_processor.extract_full_text_from_bytes(file_bytes)
        if not raw_text.strip():
            return {"success": False, "error": "Could not extract text from document."}
    except Exception as e:
        logger.error(f"OCR failed for syllabus {filename}: {e}")
        return {"success": False, "error": f"OCR extraction failed: {str(e)}"}
        
    # 2. Parse text with LLM
    modules = _parse_syllabus_with_llm(raw_text)
    if not modules:
         return {"success": False, "error": "Failed to extract structured modules from syllabus text."}
         
    # 3. Save to database
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Upsert logic (Insert or Update if subject_code exists)
        cur.execute("""
            INSERT INTO syllabi (subject_code, subject_name, modules)
            VALUES (%s, %s, %s)
            ON CONFLICT (subject_code) 
            DO UPDATE SET 
                subject_name = EXCLUDED.subject_name,
                modules = EXCLUDED.modules,
                created_at = CURRENT_TIMESTAMP
            RETURNING id;
        """, (subject_code, subject_name, json.dumps(modules)))
        
        syllabus_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        
        return {
            "success": True, 
            "message": "Syllabus processed successfully.",
            "data": {
                "id": syllabus_id,
                "subject_code": subject_code,
                "subject_name": subject_name,
                "modules": modules
            }
        }
    except Exception as e:
         logger.error(f"Database error saving syllabus: {e}")
         return {"success": False, "error": f"Database error: {str(e)}"}

def get_syllabus_by_code(subject_code: str) -> Optional[Dict[str, Any]]:
    """Retrieves a syllabus from the database by subject code."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT id, subject_code, subject_name, modules, created_at
            FROM syllabi
            WHERE subject_code = %s
        """, (subject_code,))
        
        row = cur.fetchone()
        cur.close()
        conn.close()
        
        if row:
            return {
                "id": row[0],
                "subject_code": row[1],
                "subject_name": row[2],
                "modules": row[3], # psycopg2 parses JSONB to dict automatically
                "created_at": row[4].isoformat() if row[4] else None
            }
        return None
    except Exception as e:
        logger.error(f"Error retrieving syllabus for {subject_code}: {e}")
        return None
