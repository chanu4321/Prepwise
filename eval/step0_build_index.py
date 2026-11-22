"""Step 0: rebuild the 11 papers into a local `eval_papers` index, because the production Qdrant cluster is gone.

Uses the app's own code as-is: DocumentProcessor.extract_full_text (OCR) and VectorService (embed + upsert).
Per-paper metadata (subject code/name, year, filename) comes from production Postgres, read-only, because the
metadata-extraction LLM step is not what is being rebuilt here. Everything else mirrors services/paper_worker.py."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.common import (DATA, bootstrap_env, eval_vector_service, install_call_logger, new_run_dir,  # noqa: E402
                         now_iso, readonly_pg, write_json)

PAPERS = DATA / "papers"
OCR_CACHE = DATA / "ocr_cache"


def main():
    bootstrap_env()
    run_dir = new_run_dir("step0_build_index")
    set_context = install_call_logger(run_dir, "step0_build_index")

    conn = readonly_pg()
    with conn.cursor() as cur:
        cur.execute("SELECT id, filename, subject_code, subject_name, year FROM papers WHERE status = 'live' ORDER BY id")
        rows = cur.fetchall()
    conn.close()

    from services.ocr_service import DocumentProcessor
    vectors = eval_vector_service()
    processor = DocumentProcessor(upload_dir=str(DATA / "tmp_uploads"))
    OCR_CACHE.mkdir(parents=True, exist_ok=True)

    report = []
    for paper_id, filename, code, name, year in rows:
        pdf = PAPERS / filename
        entry = {"id": paper_id, "filename": filename, "subject_code": code}
        if not pdf.exists():
            entry["error"] = "pdf missing"
            report.append(entry)
            continue
        set_context(paper_id=paper_id, phase="ocr")
        started = time.perf_counter()
        text = processor.extract_full_text(str(pdf))
        entry["ocr_seconds"] = round(time.perf_counter() - started, 1)
        entry["chars"] = len(text)
        entry["ocr_error_pages"] = text.count("[OCR Error]")
        (OCR_CACHE / f"{paper_id}.txt").write_text(text, encoding="utf-8")

        set_context(paper_id=paper_id, phase="embed")
        started = time.perf_counter()
        vector = vectors.get_embedding(text.strip() or filename, input_type="passage")
        entry["embed_seconds"] = round(time.perf_counter() - started, 1)
        entry["embedded"] = bool(vector)
        if vector:
            entry["vector_dim"] = len(vector)
            metadata = {"subject_code": code, "subject_name": name, "year": year, "filename": filename}
            entry["stored"] = vectors.upsert_paper_vector(paper_id, vector, text, metadata, "live")
        report.append(entry)
        print(entry, flush=True)

    info = vectors.client.get_collection("eval_papers")
    summary = {"built_at": now_iso(), "collection": "eval_papers (embedded local Qdrant)",
               "points_in_collection": info.points_count, "papers": report}
    write_json(run_dir / "step0_build_index.json", summary)
    print("points in eval_papers:", info.points_count, "| run dir:", run_dir)


if __name__ == "__main__":
    main()
