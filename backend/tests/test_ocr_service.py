import base64
import io
import logging

import pytest
import requests
from PIL import Image

from services import ocr_service
from services.ocr_service import DocumentProcessor, lines_from_detections, page_to_jpeg_base64


def box(text, x0, y0, x1, y1, conf=0.9):
    points = [{"x": x0, "y": y0}, {"x": x1, "y": y0}, {"x": x1, "y": y1}, {"x": x0, "y": y1}]
    return {"text_prediction": {"text": text, "confidence": conf}, "bounding_box": {"points": points}}


class FakeResponse:
    def __init__(self, detections, status_code=200):
        self.status_code = status_code
        self._detections = detections

    def raise_for_status(self):
        if self.status_code != 200:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return {"data": [{"index": 0, "text_detections": self._detections}], "usage": {}}


def test_detections_are_rebuilt_into_lines_in_reading_order():
    detections = [
        box("carries", 0.40, 0.205, 0.55, 0.225),
        box("1242", 0.80, 0.071, 0.86, 0.092),
        box("[No. of Printed Pages-4]", 0.19, 0.068, 0.51, 0.092),
        box("Each question", 0.10, 0.200, 0.38, 0.222),
    ]
    assert lines_from_detections(detections) == "[No. of Printed Pages-4] 1242\nEach question carries"


def test_no_detections_give_empty_text():
    assert lines_from_detections([]) == ""


def test_large_pages_are_shrunk_to_a_1000px_jpeg():
    encoded = page_to_jpeg_base64(Image.new("L", (3000, 4000), 255))
    image = Image.open(io.BytesIO(base64.b64decode(encoded)))
    assert image.format == "JPEG" and image.size == (1000, 1333)


def test_small_pages_are_not_enlarged():
    encoded = page_to_jpeg_base64(Image.new("RGB", (800, 1000), "white"))
    assert Image.open(io.BytesIO(base64.b64decode(encoded))).size == (800, 1000)


@pytest.fixture
def two_pages(monkeypatch):
    pages = [Image.new("RGB", (960, 1280), "white"), Image.new("RGB", (960, 1280), "white")]
    monkeypatch.setattr(ocr_service, "convert_from_path", lambda *args, **kwargs: pages)
    return pages


def test_full_text_is_read_with_nemotron(monkeypatch, two_pages, tmp_path):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append((url, headers["Authorization"], json["input"][0]["url"][:23]))
        return FakeResponse([box(f"Q{len(calls)}. Define risk.", 0.1, 0.1, 0.6, 0.12)])

    def no_tesseract(*args, **kwargs):
        raise AssertionError("Tesseract must not run when nemotron succeeds")

    monkeypatch.setattr(ocr_service.requests, "post", fake_post)
    monkeypatch.setattr(ocr_service.pytesseract, "image_to_string", no_tesseract)

    text = DocumentProcessor(upload_dir=str(tmp_path)).extract_full_text("paper.pdf")

    assert text == "--- Page 1 ---\nQ1. Define risk.\n--- Page 2 ---\nQ2. Define risk."
    assert calls == [(ocr_service.OCR_URL, "Bearer test-key", "data:image/jpeg;base64,")] * 2


@pytest.mark.parametrize("failure", ["network error", "server error", "no text"])
def test_a_failed_nemotron_call_falls_back_to_tesseract(monkeypatch, two_pages, tmp_path, caplog, failure):
    def fake_post(url, headers, json, timeout):
        if failure == "network error":
            raise requests.ConnectionError("connection reset")
        if failure == "server error":
            return FakeResponse([], status_code=500)
        return FakeResponse([])

    monkeypatch.setattr(ocr_service.requests, "post", fake_post)
    monkeypatch.setattr(ocr_service.pytesseract, "image_to_string", lambda *args, **kwargs: "tesseract text")

    with caplog.at_level(logging.WARNING, logger=ocr_service.logger.name):
        text = DocumentProcessor(upload_dir=str(tmp_path)).extract_full_text("paper.pdf")

    assert text == "--- Page 1 ---\ntesseract text\n--- Page 2 ---\ntesseract text"
    assert "falling back to Tesseract" in caplog.text


def test_header_for_metadata_is_read_with_nemotron(monkeypatch, two_pages, tmp_path):
    detections = [
        box("SOFTWARE PROJECT MANAGEMENT", 0.2, 0.30, 0.8, 0.32),
        box("SECTION - A", 0.4, 0.46, 0.6, 0.48),
        box("1. Discuss stakeholders.", 0.1, 0.60, 0.9, 0.62),
    ]
    monkeypatch.setattr(ocr_service.requests, "post", lambda *args, **kwargs: FakeResponse(detections))
    processor = DocumentProcessor(upload_dir=str(tmp_path))
    monkeypatch.setattr(processor, "generate_metadata", lambda header: {"header": header})

    result = processor.process_pdf("paper.pdf")

    assert result["text"] == "software project management"
    assert result["metadata"] == {"header": "software project management"}
