"""Step 2b: character / word error rate of each OCR engine against the hand transcriptions.

CER = character-level Levenshtein distance / number of reference characters.
WER = word-level Levenshtein distance / number of reference words.
Text is NFKC-normalised and whitespace is collapsed (line breaks are not scored); case and punctuation count.
Reported as a micro average (total edits / total reference length) and as a mean of per-page rates."""
import csv
import json
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.common import DATA, RUNS, write_json  # noqa: E402


def normalise(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def edit_distance(a, b) -> int:
    previous = list(range(len(b) + 1))
    for i, item_a in enumerate(a, start=1):
        current = [i]
        for j, item_b in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (item_a != item_b)))
        previous = current
    return previous[-1]


def score(reference: str, hypothesis: str) -> dict:
    ref, hyp = normalise(reference), normalise(hypothesis)
    ref_words, hyp_words = ref.split(), hyp.split()
    return {"ref_chars": len(ref), "char_edits": edit_distance(ref, hyp),
            "ref_words": len(ref_words), "word_edits": edit_distance(ref_words, hyp_words)}


def main():
    run_dir = sorted(p for p in RUNS.iterdir() if p.name.endswith("_step2_ocr"))[-1]
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    rows, skipped = [], []
    for page in manifest["pages"]:
        reference = (DATA / "ocr_sample" / "transcriptions" / f"{page['page_id']}.txt").read_text(encoding="utf-8")
        if not normalise(reference):
            skipped.append(page["page_id"])
            continue
        for engine in ("nemotron", "tesseract"):
            hypothesis = (run_dir / "engine_outputs" / f"{page['page_id']}.{engine}.txt").read_text(encoding="utf-8")
            rows.append({"page": page["page_id"], "engine": engine, **score(reference, hypothesis)})

    notes_file = DATA / "ocr_sample" / "page_notes.csv"
    figure_pages = set()
    if notes_file.exists():
        with notes_file.open(encoding="utf-8-sig", newline="") as handle:
            figure_pages = {r["page_id"] for r in csv.DictReader(handle) if r["has_figure"].strip().lower() == "yes"}

    summary = {"pages_scored": len({r["page"] for r in rows}), "pages_skipped_no_transcription": skipped,
               "pages_with_figures": sorted(figure_pages), "run_dir": run_dir.name, "engines": {}}
    subsets = {"all_pages": lambda r: True, "pages_without_figures": lambda r: r["page"] not in figure_pages}
    for subset, keep in subsets.items():
        summary["engines"][subset] = {}
        for engine in ("nemotron", "tesseract"):
            mine = [r for r in rows if r["engine"] == engine and keep(r)]
            if mine:
                summary["engines"][subset][engine] = rates(mine)
    write_json(run_dir / "ocr_scores.json", {"summary": summary, "per_page": rows})
    print(json.dumps(summary, indent=2))


def rates(mine):
    return {
        "n_pages": len(mine),
        "ref_chars_total": sum(r["ref_chars"] for r in mine),
        "ref_words_total": sum(r["ref_words"] for r in mine),
        "CER_micro": round(sum(r["char_edits"] for r in mine) / sum(r["ref_chars"] for r in mine), 4),
        "WER_micro": round(sum(r["word_edits"] for r in mine) / sum(r["ref_words"] for r in mine), 4),
        "CER_mean_of_pages": round(sum(r["char_edits"] / r["ref_chars"] for r in mine) / len(mine), 4),
        "WER_mean_of_pages": round(sum(r["word_edits"] / r["ref_words"] for r in mine) / len(mine), 4),
    }


if __name__ == "__main__":
    main()
