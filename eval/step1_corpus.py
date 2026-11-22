"""Step 1: corpus stats. Read-only against production Postgres and Qdrant."""
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.common import (DATA, EVAL_COLLECTION, eval_vector_service, new_run_dir, now_iso,  # noqa: E402
                         readonly_pg, write_json)

PAGE_MARKER = re.compile(r"^--- Page (\d+) ---$", re.MULTILINE)


def main():
    # Production Qdrant was deleted, so this reads the rebuilt local `eval_papers` index (step 0)
    qdrant_client = eval_vector_service().client
    COLLECTION_NAME = EVAL_COLLECTION

    run_dir = new_run_dir("step1_corpus")

    # --- Qdrant (reads only: get_collection, scroll) ---
    info = qdrant_client.get_collection(COLLECTION_NAME)
    points, offset = [], None
    while True:
        batch, offset = qdrant_client.scroll(COLLECTION_NAME, limit=64, offset=offset,
                                             with_payload=True, with_vectors=False)
        points.extend(batch)
        if offset is None:
            break

    status_counts = Counter((p.payload or {}).get("status", "<none: counts as live>") for p in points)
    live = [p for p in points if (p.payload or {}).get("status") in (None, "live")]

    def pages_of(point):
        text = (point.payload or {}).get("full_text", "")
        numbers = [int(n) for n in PAGE_MARKER.findall(text)]
        return len(numbers), text.count("[OCR Error]")

    page_rows = [pages_of(p) for p in live]
    pages_total = sum(p for p, _ in page_rows)
    ocr_error_pages = sum(e for _, e in page_rows)
    chars = [len((p.payload or {}).get("full_text", "")) for p in live]

    # --- Postgres (read-only session) ---
    conn = readonly_pg()
    with conn.cursor() as cur:
        cur.execute("SELECT status, count(*) FROM papers GROUP BY status ORDER BY status")
        pg_status = dict(cur.fetchall())
        cur.execute("SELECT id, filename, subject_code, subject_name, semester, year, status FROM papers ORDER BY id")
        papers = cur.fetchall()
        cur.execute("SELECT subject_code, subject_name, jsonb_array_length(modules) FROM syllabi ORDER BY subject_code")
        syllabi = cur.fetchall()
    conn.close()

    live_pg = [r for r in papers if r[6] == "live"]
    subjects_with_papers = {r[2] for r in live_pg if r[2]}
    syllabus_codes = {r[0] for r in syllabi}

    result = {
        "measured_at": now_iso(),
        "qdrant_collection": COLLECTION_NAME,
        "qdrant_points_total": info.points_count,
        "qdrant_points_scrolled": len(points),
        "qdrant_vector_size": getattr(info.config.params.vectors, "size", None),
        "qdrant_points_by_status": dict(status_counts),
        "qdrant_live_points": len(live),
        "vectors_per_paper": "1 (one vector per paper, built from the whole paper text; there is no chunking)",
        "live_pages_total_from_page_markers": pages_total,
        "live_pages_with_[OCR Error]": ocr_error_pages,
        "live_papers_with_no_page_marker": sum(1 for p, _ in page_rows if p == 0),
        "live_full_text_chars_median": sorted(chars)[len(chars) // 2] if chars else None,
        "postgres_papers_by_status": pg_status,
        "postgres_live_papers": len(live_pg),
        "distinct_subject_codes_live": len(subjects_with_papers),
        "syllabi_count": len(syllabi),
        "syllabus_modules_total": sum(r[2] for r in syllabi),
        "syllabus_codes_with_live_papers": len(syllabus_codes & subjects_with_papers),
        "subjects_with_papers_but_no_syllabus": sorted(subjects_with_papers - syllabus_codes),
        "syllabi_without_live_papers": sorted(syllabus_codes - subjects_with_papers),
    }
    write_json(run_dir / "step1_corpus.json", result)

    # The corpus listing you need to label retrieval queries (step 3)
    by_id = {p.id: p for p in points}
    lines = ["id,subject_code,subject_name,semester,year,status,pages,filename"]
    for pid, filename, code, name, sem, year, status in papers:
        pages = pages_of(by_id[pid])[0] if pid in by_id else ""
        cells = [pid, code, name, sem, year, status, pages, filename]
        lines.append(",".join('"' + str(c if c is not None else "").replace('"', '""') + '"' for c in cells))
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "corpus_index.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for key, value in result.items():
        print(f"{key}: {value}")
    print("run dir:", run_dir)


if __name__ == "__main__":
    main()
