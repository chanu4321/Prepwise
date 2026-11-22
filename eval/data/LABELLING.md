# Labelling rules (fixed before labelling; do not change after seeing any score)

## Step 2 — OCR transcriptions (`ocr_sample/transcriptions/page_XX.txt`)

- Type the printed text exactly as it appears, in normal reading order (top to bottom, left to right).
  Keep the original spelling, capitals, punctuation and typos. Line breaks and spacing don't matter.
- Include headers, page numbers, subject codes, "P.T.O.", marks like "(10 Marks)".
- Ignore text showing through from the back of the sheet, handwriting/stamps, and the spiral binding.
- **Tables:** transcribe row by row, left to right, one row per line.
- **Diagrams, figures, images:** skip them, including any labels inside them. Only type the
  question text around the figure. Then put `yes` for that page in `ocr_sample/page_notes.csv`.
  (Anything an OCR engine reads out of a figure will count as an error. That is a conservative choice, so the
  results also show the pages with no figures on their own.)
- **Maths / symbols:** type them as plain text as best you can (e.g. `x^2`, `<=`, `sum`). If a symbol has no
  plain-text form, skip it.
- Unreadable for you too? Leave that word out and add a note in `page_notes.csv`.

## Step 3 — retrieval queries (`queries.csv`)

- **Where queries come from (decided 2026-11-14, before any query was written):** topic lines from each course's
  official syllabus. The author did not take these courses, and neither the author nor Claude writes topics from
  the exam papers, because wording copied from the papers would inflate recall. Sources: the AI syllabus Prepwise
  stores (`syllabus_topics_from_prepwise.md`) and the official syllabi for the other four subjects. That file and
  `papers_text_for_labelling.md` are written by `eval/prepare_retrieval_labelling.py` (not kept in git).
- **2026-11-20: step 3 was skipped by the author** (see RESULTS.md section 3); no queries were written.
- Take 4-6 topics per subject (20-30 in all), **one topic per query**, keeping the syllabus wording (shortening a
  long line to its topic phrase is fine, e.g. "hill climbing", "fuzzy membership functions"). Pick topics without
  checking first whether a paper covers them. Put the source in `notes`, e.g. `CSE401 syllabus, Module 1`.
- One row per query: `query_id,query,relevant_paper_ids,notes`. Ids come from `corpus_index.csv`; separate
  several with `;`. If no paper is relevant, write `none` (the row is kept, reported, and left out of recall/MRR).
- A paper is relevant if it actually contains a question on that topic, in any subject. Judge by its content, not
  its filename or subject. `papers_text_for_labelling.md` has every paper's text for Ctrl+F; open it only after all
  queries are written. When a search finds nothing, try a synonym before writing `none`.
- Write all queries and labels before looking at any retrieval output.

## Step 4 — manual Bloom check (`bloom_manual_sample.csv`)

- Fill `human_level` with one of: remember, understand, apply, analyze, evaluate, create.
- Use the rubric in `bloom_rubric.txt`, the same one the LLM judge gets. Rate what the question actually
  asks the student to do, not just its first verb. For multi-part questions, take the highest level any part needs.
- Don't look at the run outputs (requested levels or the judge's answers) until all 30 are done.
