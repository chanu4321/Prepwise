import os
import sys
import logging

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.services.ocr_service import DocumentProcessor

logging.basicConfig(level=logging.INFO)

def test_ocr():
    print("Initializing DocumentProcessor...")
    try:
        processor = DocumentProcessor(upload_dir="backend/papers")
    except Exception as e:
        print(f"Failed to init processor: {e}")
        return

    # Find a PDF to test
    papers_dir = "backend/papers"
    if not os.path.exists(papers_dir):
        print(f"Directory {papers_dir} does not exist.")
        return

    files = [f for f in os.listdir(papers_dir) if f.lower().endswith(".pdf")]
    if not files:
        print("No PDF files found in backend/papers to test.")
        return

    test_file = os.path.join(papers_dir, files[0])
    print(f"Testing OCR on: {test_file}")

    try:
        result = processor.process_pdf(test_file)
        print("\n--- OCR Success ---")
        print("Extracted Metadata:")
        print(result.get("metadata"))
        print("\nText Preview (first 200 chars):")
        print(result.get("text", "")[:200])
    except Exception as e:
        print(f"\n--- OCR Failed ---")
        print(e)

if __name__ == "__main__":
    test_ocr()
