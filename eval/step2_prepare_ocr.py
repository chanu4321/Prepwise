"""Step 2a: pick 10 random pages, render them for hand-transcription, and run both OCR engines on them.

Engine outputs are saved but deliberately not printed, so they can't bias the human transcription."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.common import DATA, bootstrap_env, install_call_logger, new_run_dir, now_iso, write_json  # noqa: E402

SEED = 20261105
SAMPLE_SIZE = 10
DPI = 300  # same as DocumentProcessor.extract_full_text
SAMPLE_DIR = DATA / "ocr_sample"


def main():
    bootstrap_env()
    from pdf2image import convert_from_path, pdfinfo_from_path
    from services.ocr_service import DocumentProcessor, ocr_with_nemotron

    run_dir = new_run_dir("step2_ocr")
    set_context = install_call_logger(run_dir, "step2_ocr")

    pdfs = sorted(p for p in (DATA / "papers").iterdir() if p.suffix.lower() == ".pdf")
    universe = [(pdf.name, page) for pdf in pdfs for page in range(1, pdfinfo_from_path(str(pdf))["Pages"] + 1)]
    sample = sorted(random.Random(SEED).sample(universe, SAMPLE_SIZE))

    (SAMPLE_DIR / "transcriptions").mkdir(parents=True, exist_ok=True)
    (run_dir / "engine_outputs").mkdir(exist_ok=True)
    manifest = []
    for index, (name, page_number) in enumerate(sample, start=1):
        page_id = f"page_{index:02d}"
        image = convert_from_path(str(DATA / "papers" / name), dpi=DPI, first_page=page_number, last_page=page_number)[0]
        image.save(SAMPLE_DIR / f"{page_id}.png")
        set_context(page=page_id)
        try:
            nemotron_text = ocr_with_nemotron(image)  # the production engine; raises on failure
            nemotron_error = None
        except Exception as error:
            nemotron_text, nemotron_error = "", f"{type(error).__name__}: {error}"[:200]
        tesseract_text = DocumentProcessor._tesseract_full_page(image)  # the app's fallback for the full-text path
        (run_dir / "engine_outputs" / f"{page_id}.nemotron.txt").write_text(nemotron_text, encoding="utf-8")
        (run_dir / "engine_outputs" / f"{page_id}.tesseract.txt").write_text(tesseract_text, encoding="utf-8")
        template = SAMPLE_DIR / "transcriptions" / f"{page_id}.txt"
        if not template.exists():
            template.write_text("", encoding="utf-8")
        manifest.append({"page_id": page_id, "pdf": name, "pdf_page": page_number, "nemotron_error": nemotron_error})

    write_json(run_dir / "manifest.json", {"seed": SEED, "universe_pages": len(universe), "dpi": DPI,
                                           "prepared_at": now_iso(), "pages": manifest})
    print(f"Sampled {SAMPLE_SIZE} of {len(universe)} pages (seed {SEED}).")
    for row in manifest:
        print(row["page_id"], "|", row["pdf"], "| page", row["pdf_page"], "|", row["nemotron_error"] or "nemotron ok")
    print("images:", SAMPLE_DIR, "| run dir:", run_dir)


if __name__ == "__main__":
    main()
