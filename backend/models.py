from pydantic import BaseModel
from typing import Optional, Dict, Any

class PaperMetadata(BaseModel):
    subject_code: Optional[str] = None
    subject_name: Optional[str] = None
    semester: Optional[str] = None
    month_year: Optional[str] = None
    time: Optional[str] = None
    marks: Optional[str] = None

class OCRResponse(BaseModel):
    filename: str
    text_preview: str
    metadata: PaperMetadata
