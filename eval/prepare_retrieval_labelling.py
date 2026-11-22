"""Labelling aids for step 3 (retrieval). Writes two files under eval/data/ and makes no API calls:

  syllabus_topics_from_prepwise.md   the syllabi Prepwise already stores (read-only Postgres), as a source of
                                     query topics
  papers_text_for_labelling.md       every indexed paper's text with its corpus id, so relevance can be checked
                                     with Ctrl+F instead of opening each PDF (open it only AFTER writing queries)

The paper text is the app's own OCR output from the index build (eval/data/ocr_cache); it may contain OCR slips."""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.common import DATA, bootstrap_env, readonly_pg  # noqa: E402


def main():
    bootstrap_env()
    conn = readonly_pg()
    with conn.cursor() as cur:
        cur.execute("SELECT subject_code, subject_name, modules FROM syllabi ORDER BY subject_code")
        syllabi = cur.fetchall()
    conn.close()

    out = ["# Syllabus topics stored in Prepwise", "",
           "Source: the `syllabi` table (read-only). Only subjects listed here have a syllabus in Prepwise; for the "
           "others, use the official course syllabus.", ""]
    for code, name, modules in syllabi:
        modules = modules if isinstance(modules, list) else json.loads(modules)
        out += [f"## {code}: {name}", ""]
        for module in modules:
            if isinstance(module, dict) and "topics" in module:
                weight = f" ({module['weightage_percent']}%)" if module.get("weightage_percent") else ""
                out.append(f"- **{module.get('name', 'Module')}**{weight}: {module['topics']}")
            else:
                out.append(f"- {module if isinstance(module, str) else json.dumps(module, ensure_ascii=False)}")
        out.append("")
    (DATA / "syllabus_topics_from_prepwise.md").write_text("\n".join(out), encoding="utf-8")

    with (DATA / "corpus_index.csv").open(encoding="utf-8-sig", newline="") as handle:
        corpus = list(csv.DictReader(handle))
    out = ["# Paper text for relevance labelling", "",
           "Use this only after the queries are written. For each query, search for its topic and list every "
           "paper id that has a question on it. OCR may have small slips, so check the PDF when in doubt.", ""]
    for paper in corpus:
        pid = paper.get("id") or paper.get("paper_id")
        text = (DATA / "ocr_cache" / f"{pid}.txt")
        out += [f"## Paper id {pid}: {paper.get('filename', '')} ({paper.get('subject_name', '')}, "
                f"{paper.get('subject_code', '') or 'no code'}, {paper.get('year', '')})", "",
                text.read_text(encoding="utf-8").strip() if text.exists() else "(no cached text)", ""]
    (DATA / "papers_text_for_labelling.md").write_text("\n".join(out), encoding="utf-8")
    print(f"syllabi: {len(syllabi)}; papers: {len(corpus)}")


if __name__ == "__main__":
    main()
