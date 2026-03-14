import os
import json
import logging
import requests
import pytesseract
import numpy as np
from pdf2image import convert_from_path
from PIL import Image, ImageEnhance

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
STOP_KEYWORDS = ["SECTION-A", "Section A", "SECTION - A", "Section-A", "attempt", "Attempt", "ATTEMPT"]

class DocumentProcessor:
    def __init__(self, upload_dir: str = "uploads"):
        self.upload_dir = upload_dir
        os.makedirs(self.upload_dir, exist_ok=True)

    def preprocess_text(self, text: str) -> str:
        """Cleans extracted text."""
        return text.lower().strip()

    def clean_json(self, text: str):
        """Attempts to extract valid JSON from LLM response."""
        text = text.strip()
        # Remove markdown code blocks
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return text.strip()

    def generate_metadata(self, extracted_text: str):
        """Calls Ollama to extract metadata from text."""
        prompt = (
            f"Analyze the following text from a question paper headers:\n"
            f"{extracted_text[:2000]}\n\n"
            "Extract the following metadata in strictly valid JSON format:\n"
            "- Subject Code (Look for patterns like 'KCS401', 'RAS301', 'BCS301' etc.)\n"
            "- Subject Name\n"
            "- Semester (e.g., '3rd Sem')\n"
            "- Month/Year (Look for 'Examination : Month, Year' or similar. e.g., 'June, 2023')\n"
            "- Time (Duration)\n"
            "- Marks (Max Marks)\n\n"
            "Response must be ONLY the JSON object, no explanation."
        )
        
        data = {
            "model": "qwen2.5:3b", 
            "prompt": prompt,
            "format": "json",
            "stream": False
        }

        try:
            response = requests.post(OLLAMA_URL, json=data)
            if response.status_code == 200:
                raw_response = json.loads(response.content).get("response", "")
                clean_response = self.clean_json(raw_response)
                return json.loads(clean_response)
            else:
                logger.error(f"Ollama API Error: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Failed to connect to Ollama or parse JSON: {e}")
            return None

    def process_pdf(self, file_path: str):
        """
        Main pipeline: PDF -> Image -> OCR -> Metadata Extraction
        """
        try:
            # 1. Convert first page to image (assuming header info is on page 1)
            images = convert_from_path(file_path)
            if not images:
                raise ValueError("No images converted from PDF")
            
            first_page = images[0].convert('L') # Grayscale
            
            # 2. OCR using Tesseract
            text = pytesseract.image_to_string(first_page)
            
            # 3. Filter text until stop keyword (Header extraction)
            extracted_text = ""
            for line in text.split('\n'):
                processed_line = self.preprocess_text(line)
                if not processed_line:
                    continue
                
                extracted_text += processed_line + "\n"
                
                if any(k.lower() in processed_line for k in STOP_KEYWORDS):
                    # Stop reading further
                    extracted_text = extracted_text[:extracted_text.rfind(processed_line)]
                    break
            
            # 4. Generate Metadata via LLM
            metadata = self.generate_metadata(extracted_text.strip())
            
            return {
                "text": extracted_text.strip(),
                "metadata": metadata
            }

        except Exception as e:
            logger.error(f"Error processing PDF {file_path}: {e}")
            raise e

    def extract_full_text(self, pdf_path: str) -> str:
        """Extracts full text from all pages of a PDF."""
        images = convert_from_path(pdf_path, dpi=300)
        full_text = ""
        
        for idx, image in enumerate(images):
            try:
                # Preprocess image for better OCR
                # Convert to grayscale
                image = image.convert('L')
                
                # Enhance contrast
                enhancer = ImageEnhance.Contrast(image)
                image = enhancer.enhance(2.0)
                
                # Apply threshold to make text clearer
                img_array = np.array(image)
                threshold = 150
                img_array = np.where(img_array > threshold, 255, 0).astype(np.uint8)
                image = Image.fromarray(img_array)
                
                # Perform OCR with better config
                page_text = pytesseract.image_to_string(
                    image, 
                    config='--psm 6 --oem 3'  # PSM 6: Assume uniform block of text, OEM 3: Default
                )
                full_text += f"\n--- Page {idx + 1} ---\n{page_text}"
            except Exception as e:
                logger.error(f"OCR failed for page {idx + 1}: {e}")
                full_text += f"\n--- Page {idx + 1} ---\n[OCR Error]\n"
        
        return full_text.strip()

    def extract_full_text_from_bytes(self, file_bytes: bytes) -> str:
        """Extracts full text from a PDF provided as bytes by saving to a temp file."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            return self.extract_full_text(tmp_path)
        finally:
            import os as _os
            _os.unlink(tmp_path)
