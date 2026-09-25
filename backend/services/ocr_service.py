import base64
import io
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
# OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
STOP_KEYWORDS = ["SECTION-A", "Section A", "SECTION - A", "Section-A", "attempt", "Attempt", "ATTEMPT"]

# Hosted NVIDIA NIM OCR model; authenticated with NVIDIA_API_KEY
OCR_URL = "https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-ocr-v2"
OCR_MAX_WIDTH = 1000  # larger uploads get dropped by the API (1500px pages failed)
OCR_JPEG_QUALITY = 85
OCR_TIMEOUT_SECONDS = 60


def page_to_jpeg_base64(page: Image.Image) -> str:
    """Encodes a page as a base64 JPEG no wider than OCR_MAX_WIDTH."""
    if page.width > OCR_MAX_WIDTH:
        page = page.resize((OCR_MAX_WIDTH, round(page.height * OCR_MAX_WIDTH / page.width)))
    buffer = io.BytesIO()
    page.convert("RGB").save(buffer, format="JPEG", quality=OCR_JPEG_QUALITY)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def lines_from_detections(detections: list[dict]) -> str:
    """Orders nemotron's text boxes top to bottom, joining boxes on the same line left to right."""
    boxes = []
    for detection in detections:
        points = detection["bounding_box"]["points"]
        ys = [point["y"] for point in points]
        boxes.append({
            "text": detection["text_prediction"]["text"],
            "y": sum(ys) / len(ys),
            "height": max(ys) - min(ys),
            "x": min(point["x"] for point in points),
        })
    boxes.sort(key=lambda b: b["y"])

    lines: list[list[dict]] = []
    for b in boxes:
        previous = lines[-1][-1] if lines else None
        if previous is not None and abs(b["y"] - previous["y"]) <= max(b["height"], previous["height"]) * 0.6:
            lines[-1].append(b)
        else:
            lines.append([b])
    return "\n".join(" ".join(b["text"] for b in sorted(line, key=lambda b: b["x"])) for line in lines)


def ocr_with_nemotron(page: Image.Image) -> str:
    """Reads one page with nemotron-ocr-v2. Raises on any failure so the caller can fall back."""
    response = requests.post(
        OCR_URL,
        headers={"Authorization": f"Bearer {os.getenv('NVIDIA_API_KEY', '')}", "Accept": "application/json"},
        json={"input": [{"type": "image_url", "url": f"data:image/jpeg;base64,{page_to_jpeg_base64(page)}"}]},
        timeout=OCR_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    text = lines_from_detections(response.json()["data"][0]["text_detections"])
    if not text.strip():
        raise ValueError("no text detected")
    return text

class DocumentProcessor:
    def __init__(self, upload_dir: str = "uploads"):
        self.upload_dir = upload_dir
        os.makedirs(self.upload_dir, exist_ok=True)

    def _ocr_page(self, page: Image.Image, tesseract_fallback) -> str:
        """nemotron-ocr-v2 first. Tesseract only when that call fails: its partial misses look like successes."""
        try:
            return ocr_with_nemotron(page)
        except Exception as error:
            logger.warning("nemotron-ocr failed (%s); falling back to Tesseract for this page", error)
            return tesseract_fallback(page)

    @staticmethod
    def _tesseract_full_page(image: Image.Image) -> str:
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
        return pytesseract.image_to_string(
            image,
            config='--psm 6 --oem 3'  # PSM 6: Assume uniform block of text, OEM 3: Default
        )

    def preprocess_text(self, text: str) -> str:
        """Cleans extracted text."""
        return text.lower().strip()

    def clean_json(self, text: str):
        """Attempts to extract valid JSON from LLM response (handles thinking model output)."""
        text = text.strip()
        # Remove markdown code blocks
        if '```json' in text:
            text = text.split('```json', 1)[1]
        if '```' in text:
            text = text.split('```')[0]
        text = text.strip()
        # Thinking models output reasoning before JSON — find the actual JSON object
        brace_idx = text.find('{')
        if brace_idx > 0:
            text = text[brace_idx:]
        last_brace = text.rfind('}')
        if last_brace != -1:
            text = text[:last_brace + 1]
        return text.strip()

    def generate_metadata(self, extracted_text: str):
        """Calls Ollama to extract metadata from text."""
        prompt = (
            f"Analyze the following text from a question paper headers:\n"
            f"{extracted_text[:2000]}\n\n"
            "Extract the following metadata in strictly valid JSON format exactly matching these keys:\n"
            '- "subjectCode" (Look for patterns like "KCS401", "RAS301", "BCS301" etc.)\n'
            '- "subjectName" (The name of the subject)\n'
            '- "semester" (e.g., "3rd Sem", optional)\n'
            '- "monthYear" (Look for "Examination : Month, Year" or similar. e.g., "June, 2023")\n'
            '- "time" (Duration, e.g., "3 Hours")\n'
            '- "marks" (Max Marks, look for "Max. Marks", "Maximum Marks", e.g., "60" or "100")\n\n'
            "Response must be ONLY the JSON object, no explanation."
        )
        
        data = {
            "model": os.getenv("LLM_MODEL") or "z-ai/glm-5.3-flash",
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.6,
            "top_p": 0.7,
            "max_tokens": 16384
        }
        
        headers = {
            "Authorization": f"Bearer {os.getenv('NVIDIA_API_KEY', '')}",
            "Content-Type": "application/json"
        }
        api_url = os.getenv("LLM_API_URL") or "https://integrate.api.nvidia.com/v1/chat/completions"

        try:
            response = requests.post(api_url, headers=headers, json=data, timeout=90)
            if response.status_code == 200:
                resp_json = response.json()
                raw_response = resp_json.get("choices", [{}])[0].get("message", {}).get("content", "")
                
                # fallback parsing
                if not raw_response:
                    raw_response = resp_json.get("response", "")
                    
                clean_response = self.clean_json(raw_response)
                return json.loads(clean_response)
            else:
                logger.error(f"LLM API Error: {response.status_code} - {response.text}")
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
            
            # 2. OCR the first page (nemotron-ocr-v2, Tesseract if the API call fails)
            text = self._ocr_page(images[0], lambda page: pytesseract.image_to_string(page.convert('L')))
            
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
                page_text = self._ocr_page(image, self._tesseract_full_page)
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
