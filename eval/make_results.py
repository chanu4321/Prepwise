"""Composes eval/RESULTS.md from the run directories under eval/runs/.

Only numbers that a run measured are printed, each with its sample size. A section whose hand labels are not in yet
says what it is waiting for and prints no numbers. Re-run after any new run or label:

  python eval/step2_score_ocr.py              # once the transcriptions are done
  python eval/step3_retrieval.py              # once queries.csv is labelled
  python eval/step45_metrics.py --run-dir eval/runs/<SCORED_STEP4_RUN>   # after the manual Bloom labels
  python eval/make_results.py"""
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.bloom import iter_questions  # noqa: E402
from eval.common import DATA, EVAL, RUNS  # noqa: E402
from eval.step45_metrics import call_outcome, read_jsonl  # noqa: E402

SCORED_STEP4_RUN = "20261110T111039Z_step4_generation"     # generator nvidia/nemotron-3-ultra-550b-a55b, commit a92542c
SUPERSEDED_STEP4_RUN = "20261107T180337Z_step4_generation"  # generator z-ai/glm-5.3-flash, stopped answering
OCR_RUN = "20261105T060643Z_step2_ocr"
GENERATOR, JUDGE = "nvidia/nemotron-3-ultra-550b-a55b", "mistralai/mistral-large-4-1t-a52b"
LEVELS = ["remember", "understand", "apply", "analyze", "evaluate", "create"]


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def latest(stage: str, filename: str):
    dirs = sorted(p for p in RUNS.iterdir() if p.is_dir() and p.name.endswith(f"_{stage}") and (p / filename).exists())
    return (dirs[-1].name, load(dirs[-1] / filename)) if dirs else (None, None)


def frac(r) -> str:
    """'79.5% (89/112)' from a {'n','count','rate'} dict."""
    if not r or not r.get("n"):
        return "n/a (n=0)"
    return f"{r['rate'] * 100:.1f}% ({r['count']}/{r['n']})"


def secs(value) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0f} s ({value / 60:.1f} min)" if value >= 90 else f"{value:.1f} s"


def table(headers, rows) -> list:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out + [""]


def pending(what: str) -> list:
    return [f"**Not measured yet.** Waiting on {what}. No numbers are reported for this section until then.", ""]


def corpus_section() -> list:
    run1, c = latest("step1_corpus", "step1_corpus.json")
    run0, b = latest("step0_build_index", "step0_build_index.json")
    lines = ["## 1. Corpus", ""]
    if not c:
        return lines + pending("`eval/step1_corpus.py`")
    papers = (b or {}).get("papers", [])
    ocr_total = sum(p["ocr_seconds"] for p in papers)
    rows = [
        ["Live papers", f"{c['postgres_live_papers']} in Postgres, {c['qdrant_live_points']} in the search index"],
        ["Subjects (distinct subject codes)", f"{c['distinct_subject_codes_live']} "
         f"({sum(1 for p in papers if not p['subject_code'])} paper is missing its subject code)"],
        ["Pages", f"{c['live_pages_total_from_page_markers']} "
         f"({c['live_pages_with_[OCR Error]']} with an OCR error)"],
        ["Vectors", f"{c['qdrant_points_total']} x {c['qdrant_vector_size']}-dim, one per paper (no chunking)"],
        ["Text per paper (median)", f"{c['live_full_text_chars_median']:,} characters (n={c['postgres_live_papers']})"],
        ["Syllabi / modules", f"{c['syllabi_count']} / {c['syllabus_modules_total']} "
         f"(covers {c['syllabus_codes_with_live_papers']} of {c['distinct_subject_codes_live']} subjects)"],
    ]
    if papers:
        rows.append(["OCR time while indexing", f"{ocr_total:.1f} s for {c['live_pages_total_from_page_markers']} pages "
                     f"({ocr_total / c['live_pages_total_from_page_markers']:.1f} s/page, nemotron-ocr-v2)"])
        rows.append(["Embedding time per paper (median)",
                     f"{sorted(p['embed_seconds'] for p in papers)[len(papers) // 2]:.1f} s (n={len(papers)})"])
    return lines + table(["Metric", "Value"], rows) + [f"Runs: `{run1}`, `{run0}`.", ""]


def ocr_section() -> list:
    lines = ["## 2. OCR quality (CER / WER against hand transcriptions)", ""]
    scores = load(RUNS / OCR_RUN / "ocr_scores.json")
    transcribed = sum(1 for f in (DATA / "ocr_sample" / "transcriptions").glob("*.txt")
                      if f.read_text(encoding="utf-8").strip())
    if not scores:
        return lines + pending(f"the hand transcriptions ({transcribed} of 10 pages done)")
    s = scores["summary"]
    rows = []
    for subset, engines in s["engines"].items():
        for engine, r in engines.items():
            rows.append([subset.replace("_", " "), engine, r["n_pages"], f"{r['ref_chars_total']:,}",
                         f"{r['CER_micro'] * 100:.1f}%", f"{r['WER_micro'] * 100:.1f}%",
                         f"{r['CER_mean_of_pages'] * 100:.1f}%", f"{r['WER_mean_of_pages'] * 100:.1f}%"])
    lines += table(["Pages", "Engine", "n pages", "Ref. chars", "CER (micro)", "WER (micro)", "CER (page mean)",
                    "WER (page mean)"], rows)
    lines += [
        "Lower is better. nemotron-ocr-v2 is what production runs; Tesseract only runs when that API call fails. "
        "As in production, nemotron is sent each page shrunk to 1000 px wide (the app's setting; larger images "
        "failed at the API), while Tesseract reads the full 300-dpi render. Text inside figures was not "
        "transcribed, so anything an engine reads from a figure counts as an error (pages with figures: "
        f"{', '.join(s['pages_with_figures']) or 'none'}).", "",
        "Per page (added after scoring, to show where the errors are; the table above is the pre-registered "
        "measure):", ""]
    manifest = load(RUNS / OCR_RUN / "manifest.json") or {}
    source = {p["page_id"]: f"{p['pdf']} p{p['pdf_page']}" for p in manifest.get("pages", [])}
    by_page = {}
    for r in scores["per_page"]:
        hyp = (RUNS / OCR_RUN / "engine_outputs" / f"{r['page']}.{r['engine']}.txt").read_text(encoding="utf-8")
        by_page.setdefault(r["page"], {})[r["engine"]] = (r, len(" ".join(hyp.split())))
    rows = []
    for page in sorted(by_page):
        (nr, nlen), (tr, tlen) = by_page[page]["nemotron"], by_page[page]["tesseract"]
        rows.append([page + (" (figure)" if page in s["pages_with_figures"] else ""), source.get(page, ""),
                     f"{nr['ref_chars']:,}", f"{nlen:,}", f"{nr['char_edits'] / nr['ref_chars']:.1%}",
                     f"{tlen:,}", f"{tr['char_edits'] / tr['ref_chars']:.1%}"])
    lines += table(["Page", "Source", "Ref. chars", "nemotron output chars", "nemotron CER", "Tesseract output chars",
                    "Tesseract CER"], rows)
    cers = {e: sorted(by_page[p][e][0]["char_edits"] / by_page[p][e][0]["ref_chars"] for p in by_page)
            for e in ("nemotron", "tesseract")}
    median = {e: (v[len(v) // 2] + v[(len(v) - 1) // 2]) / 2 for e, v in cers.items()}
    short = [p for p in sorted(by_page) if p not in s["pages_with_figures"]
             and by_page[p]["nemotron"][1] < 0.8 * by_page[p]["nemotron"][0]["ref_chars"]]
    lines += [f"Median page CER: nemotron {median['nemotron']:.1%}, Tesseract {median['tesseract']:.1%} "
              f"(n={len(by_page)} pages). nemotron's errors are concentrated on {', '.join(short)}, photographed "
              "pages whose lines are slanted or curled; there its output is at least 20% shorter than the page. "
              "Three failures show up on those pages: (1) whole lines are never detected, and since the app keeps "
              "every box the API returns, that loss is in the model, at the app's 1000 px input; (2) words in small, "
              "dense text come back garbled or with repeated fragments; (3) the app's own line-joining step "
              "(`lines_from_detections`) merges neighbouring slanted lines into one, so correctly read text comes "
              "out reordered (e.g. \"Q2. Q3. Explain ... Justify ...\"), which CER counts as errors. Text that is "
              "missed never reaches the search index or the question generator.", "", f"Run: `{OCR_RUN}`.", ""]
    return lines


RETRIEVAL_SKIPPED = True  # decided by the author on 2026-11-20; step3_retrieval.py still works if this is revisited


def retrieval_section() -> list:
    lines = ["## 3. Retrieval (labelled queries)", ""]
    run, r = latest("step3_retrieval", "retrieval_results.json")
    if not r and RETRIEVAL_SKIPPED:
        n = (load(RUNS / "20261105T060235Z_step1_corpus" / "step1_corpus.json") or {}).get("qdrant_points_total", 11)
        harmonic = sum(1 / rank for rank in range(1, n + 1))
        return lines + [
            "**Not measured: skipped by the author.** With only "
            f"{n} papers, a retrieval benchmark says little. A random ordering would already put {10 / n:.0%} of the "
            f"relevant papers in the top 10 (top 5: {5 / n:.0%}; first result relevant: {1 / n:.0%}; "
            f"MRR {harmonic / n:.2f} for a query with one relevant paper). recall@5 and MRR could still show whether "
            "search beats chance, but over about 20 queries on 11 documents even a good score would not support a "
            "general claim, so no retrieval number is reported. The corpus is indexed with one vector per paper "
            "(section 1).", ""]
    if not r:
        return lines + pending("the labelled queries in `eval/data/queries.csv`")
    s = r["summary"]
    n = s["n_queries"]
    lines += table(["Metric", f"Prepwise (n={n} queries)", f"Random ranking of {s['corpus_size_papers']} papers"], [
        ["recall@5", f"{s['recall@5'] * 100:.1f}%", f"{s['random_baseline_recall@5'] * 100:.1f}%"],
        ["recall@10", f"{s['recall@10'] * 100:.1f}%", f"{s['random_baseline_recall@10'] * 100:.1f}%"],
        ["MRR", f"{s['MRR']:.3f}", "n/a"],
    ])
    excluded = s.get("queries_with_no_relevant_paper_excluded", [])
    return lines + [f"Queries are topic lines taken from the courses' official syllabi, chosen and relevance-labelled "
                    f"by the author before seeing any retrieval output. Relevant papers per query: "
                    f"{s['mean_relevant_per_query']} on average. {len(excluded)} more syllabus topics had no relevant "
                    "paper in the corpus; recall and MRR are undefined for them, so they are not in the table.", "",
                    f"Run: `{run}`.", ""]


def validation_notes() -> dict:
    """Counts behind the prose in 4a, measured from the scored run's questions."""
    configs = load(DATA / "generation_configs.json")
    rows = [r for r in iter_questions(RUNS / SCORED_STEP4_RUN, configs) if r["text"] and r["validation"]]
    punct = [r for r in rows if r["path"] == "stream" and any("punctuation" in i for i in r["validation"]["issues"])]
    verb = [i for r in rows if r["path"] == "batch" for i in r["validation"]["issues"] if "expected" in i]
    return {
        "punct": len(punct),
        "marks_suffix": sum(1 for r in punct if re.search(r"\(\s*\d+\s*(marks?)?\s*\)\s*$", r["text"].strip(), re.I)),
        "verb": len(verb),
        "empty_verbs": sum(1 for i in verb if i.rstrip().endswith(":")),
        "errors": [r["qid"] for r in rows if not r["validation"]["valid"]],
    }


def generation_section(m) -> list:
    v, j = m["validation"], m["json_parse_batch"]
    lines = ["## 4. Generation quality", "",
             f"{len(m['papers'])} mock papers across all 5 subjects, built with the same request shapes as the web UI "
             "(3 papers use two-part questions; the UI's spare-question pool is on). Each paper was generated "
             "through both routes: **stream**, the one the web UI calls (one LLM call per question), and **batch**, "
             "the non-streaming route (one JSON call per section), which the UI does not use.", "",
             "### 4a. Rule-based validation (the app's own `_validate_question`)", ""]
    rows = []
    for path in ("stream", "batch"):
        p = v[path]
        rows.append([path, f"{p['generated']}/{p['requested']}", frac(p["app_valid_of_requested"]),
                     frac(p["zero_issues_of_generated"]),
                     ", ".join(f"{k} {c}" for k, c in sorted(p["issue_counts"].items(), key=lambda x: -x[1])) or "none"])
    lines += table(["Path", "Generated", "Valid (app rule: warnings allowed)", "No warnings at all",
                    "Warnings raised"], rows)
    notes = validation_notes()
    lines += [f"The app counts a question as valid unless an issue is an error. Several warnings and both errors come "
              f"from the validator rather than the question: {notes['marks_suffix']} of the {notes['punct']} stream "
              "punctuation warnings are questions that end with their marks, e.g. \"... (4)\"; "
              f"{notes['empty_verbs']} of the {notes['verb']} batch `missing_bloom_verb` warnings list no expected "
              f"verbs at all; and the {len(notes['errors'])} questions rejected as errors "
              f"({', '.join(notes['errors'])}) were flagged as \"placeholder text\" because they contain \"...\" in a "
              "sequence such as \"L1, L2, L3, ...\".", "",
              "### 4b. JSON parse success (batch path only)", ""]
    lines += table(["Metric", "Value"], [
        ["Section calls answered", f"{j['calls'] - j['calls_non_200_or_timeout']}/{j['calls']}"],
        ["Parsed as a JSON array (app's own extraction)", frac(j["parse_success_of_all_calls"])],
        ["Parsed with every requested question present", frac(j["parse_success_and_all_questions_present"])],
        ["Replies cut off at max_tokens", f"{j['finish_reason_length']} of {j['calls']}"],
        ["Fallbacks to per-question generation", f"{j['app_logged_fallbacks']}"],
    ])
    lines += ["The stream path asks for plain text, so it has no JSON to parse.", "",
              "### 4c. Bloom's-level adherence", "",
              f"Judge: `{JUDGE}` (a different vendor from the generator), shown only the subject and the question, "
              "never the requested level, with the rubric in `eval/data/bloom_rubric.txt`.", ""]
    rows = []
    for path in ("stream", "batch"):
        b = m["bloom_judge"][path]
        rows.append([path, frac(b["exact"]), frac(b["within_one_level"]),
                     f"{b['judge_failures']} of {b['judge_attempted']}"])
    lines += table(["Path", "Judge level = requested level", "Within one level", "No verdict (judge failed)"], rows)
    rows = [[level] + [frac(m["bloom_judge"][path]["by_target_level"].get(level)) for path in ("stream", "batch")]
            for level in LEVELS]
    lines += ["Exact match by requested level:", ""] + table(["Requested level", "stream", "batch"], rows)
    lines += ["Manual check (30-question blind sample from the stream path, labelled by the author):", ""]
    manual = m.get("bloom_manual") or {}
    if not manual.get("labelled"):
        lines += pending("the 30 labels in `eval/data/bloom_manual_sample.csv`")
    else:
        kappa = manual["judge_vs_human_cohen_kappa"]
        lines += table(["Metric", "Value"], [
            ["Labelled questions", manual["labelled"]],
            ["Human level = requested level", frac(manual["human_vs_target_exact"])],
            ["Judge = human (exact)", frac(manual["judge_vs_human_exact_agreement"])],
            ["Judge within one level of human", frac(manual["judge_vs_human_within_one"])],
            ["Cohen's kappa, judge vs human", f"{kappa:.3f} (n={manual['judge_vs_human_exact_agreement']['n']})"
             if kappa is not None else "n/a"],
        ])
        with (DATA / "bloom_manual_sample.csv").open(encoding="utf-8-sig", newline="") as handle:
            borderline = sum(1 for r in csv.DictReader(handle) if "could be" in r["notes"].lower())
        no_verdict = manual["labelled"] - manual["judge_vs_human_exact_agreement"]["n"]
        lines += [f"Agreement is over the {manual['judge_vs_human_exact_agreement']['n']} sampled questions that "
                  f"got a judge verdict ({no_verdict} did not). The rater noted {borderline} of {manual['labelled']} "
                  "as borderline between two adjacent levels. With n this small, kappa's uncertainty is wide.", ""]
    return lines


def performance_section(m) -> list:
    perf, api = m["performance"], m["api_call_outcomes_all_generation_calls"]
    lines = ["## 5. Performance and cost", "",
             f"End-to-end time per generated paper, timed around the route call (retrieval + every LLM call), "
             f"n={perf['stream']['papers_completed']} papers per path. With n=8, p95 is close to the slowest paper.", ""]
    rows = []
    for path in ("stream", "batch"):
        lat = perf[path]["latency_s"]
        rows.append([path, secs(lat["p50"]), secs(lat["p95"]), secs(lat["min"]), secs(lat["max"])])
    lines += table(["Path", "p50", "p95", "Fastest", "Slowest"], rows)
    s = perf["stream"]
    lines += table(["Stream path detail", "Value"], [
        ["Time to first question (p50 / p95)",
         f"{secs(s['time_to_first_question_s']['p50'])} / {secs(s['time_to_first_question_s']['p95'])}"],
        ["One question's LLM call (p50 / p95)",
         f"{secs(s['per_question_llm_call_s']['p50'])} / {secs(s['per_question_llm_call_s']['p95'])} "
         f"(n={s['per_question_llm_call_s']['n']})"],
        ["Generator calls that reached the API and succeeded", frac(api["ok_of_calls_that_reached_api"])],
    ])
    rows = []
    for path in ("stream", "batch"):
        t = perf[path]["llm_tokens_per_paper"]
        papers = perf[path]["per_paper_tokens"].values()
        rows.append([path, f"{t['mean']:,}", f"{t['min']:,} - {t['max']:,}",
                     f"{round(sum(p['prompt_tokens'] for p in papers) / len(papers)):,}",
                     f"{round(sum(p['completion_tokens'] for p in papers) / len(papers)):,}"])
    lines += ["Tokens per paper (generator; embedding calls add about 5 tokens):", ""]
    lines += table(["Path", "Mean total", "Range", "Mean prompt", "Mean completion (incl. reasoning)"], rows)
    cost = perf["stream"].get("assumed_cost_usd_per_paper")
    if cost:
        rate = cost["pricing"][GENERATOR]
        lines += [f"Cost per paper at an **assumed rate, not billed** (Prepwise ran on NVIDIA's free tier): "
                  f"${rate['input_per_million']:.2f} input / ${rate['output_per_million']:.2f} output per million "
                  f"tokens for `{GENERATOR}`. Source: {rate.get('source', 'not recorded')}.", ""]
        lines += table(["Path", "USD per paper (mean, n=8)", "USD per 1,000 papers"],
                       [[path, f"${perf[path]['assumed_cost_usd_per_paper']['mean']:.4f}",
                         f"${perf[path]['assumed_cost_usd_per_paper']['mean'] * 1000:.2f}"] for path in ("stream", "batch")])
        comparison = load(DATA / "pricing_comparison.json")
        if comparison:
            means = {}
            for path in ("stream", "batch"):
                papers = list(perf[path]["per_paper_tokens"].values())
                means[path] = (sum(p["prompt_tokens"] for p in papers) / len(papers),
                               sum(p["completion_tokens"] for p in papers) / len(papers))
            rows = [[name] + [f"${(means[path][0] * r['input_per_million'] + means[path][1] * r['output_per_million']) / 1e6:.4f}"
                              for path in ("stream", "batch")]
                    for name, r in comparison["rates"].items()]
            lines += ["**What-if, not measured.** " + comparison["note"], ""]
            lines += table(["Model (list price)", "stream USD per paper", "batch USD per paper"], rows)
    else:
        lines += ["Cost is reported in tokens only. NVIDIA's hosted API was used on its free tier, so nothing was "
                  "billed; a dollar figure needs an assumed per-token rate (`eval/data/pricing.json`).", ""]
    jt = m["judge_tokens"]
    return lines + [f"Evaluation overhead, not part of the app: the judge used {jt['calls']} calls, "
                    f"{jt['prompt_tokens']:,} prompt and {jt['completion_tokens']:,} completion tokens.", ""]


def superseded_glm_note() -> str:
    calls = [c for c in read_jsonl(RUNS / SUPERSEDED_STEP4_RUN / "calls.jsonl") if c.get("stage") == "step4_generation"]
    outcomes = [call_outcome(c) for c in calls]
    reached = [o for o in outcomes if o != "local_network_error"]
    ok = sum(1 for o in reached if o == "ok_200")
    return (f"**The generator changed mid-evaluation.** Before this run, the app's default generator was "
            f"`z-ai/glm-5.3-flash`. In the superseded run "
            f"`{SUPERSEDED_STEP4_RUN}`, {ok} of {len(reached)} generator calls that reached NVIDIA succeeded "
            f"({ok / len(reached) * 100:.0f}%), and on 2026-11-10 it stopped answering altogether (a one-word prompt "
            "timed out at 150 s while other models answered in seconds). The default was switched to "
            f"`{GENERATOR}` (PR #7) and step 4 was re-run from scratch; no glm number appears in the tables.")


def main():
    m = load(RUNS / SCORED_STEP4_RUN / "metrics.json")
    if not m:
        raise SystemExit(f"run eval/step45_metrics.py --run-dir eval/runs/{SCORED_STEP4_RUN} first")
    out = [
        "# Prepwise: measured evaluation", "",
        f"Generated by `eval/make_results.py` on {datetime.now().astimezone():%Y-%m-%d} from the run logs in "
        "`eval/runs/`, which are kept by the author and not published in this repository. Every number below was "
        "measured in those runs, and each one carries its sample size. Small samples are labelled as such. A section "
        "waiting on hand labels says so and reports nothing.", "",
        "**System measured:** commit `a92542c` (branch `default` after PR #7). Generator "
        f"`{GENERATOR}`, embeddings `nvidia/nemotron-3-embed-1b` (2048-dim), OCR `nvidia/nemotron-ocr-v2` with a "
        "Tesseract fallback, all on NVIDIA's hosted API. The app was imported and called as a black box; nothing "
        "in it was changed for the evaluation, and nothing was written to the production database or index.", "",
    ]
    out += corpus_section() + ocr_section() + retrieval_section() + generation_section(m) + performance_section(m)
    out += ["## Limitations", ""] + [f"- {item}" for item in [
        "**Small corpus.** 11 papers, 5 subjects, 37 pages, and a syllabus for only 1 subject. Retrieval and "
        "generation numbers describe this corpus, not a large deployment.",
        "**The OCR sample is small.** 10 of the corpus's 37 pages (a seeded random sample), transcribed once by "
        "the author; one is mostly a diagram. Error rates swing with page condition (flat vs slanted photo), so "
        "the per-page table says more than any single average.",
        "**Rebuilt index.** The production Qdrant cluster had been deleted, so the index was rebuilt locally from "
        "the same 11 PDFs with the app's own OCR, embedding and storage code. Paper metadata came from the production "
        "database (read-only) rather than being re-extracted.",
        "**Retrieval was not benchmarked.** At 11 papers the result would be dominated by chance (section 3), so "
        "this evaluation makes no claim about search quality.",
        "**JSON parsing is measured on the batch route only.** The web UI uses the streaming route, which asks for "
        "plain text.",
        "**Bloom adherence is mostly LLM-judged.** The judge can be wrong, and the judge-human agreement on the "
        "30-question sample is the check on it. Questions with no verdict (NVIDIA's gateway cut off calls longer "
        "than about 5 minutes) lean towards long, multi-part questions, so the judged set slightly under-represents "
        "them.",
        "**One generation per paper.** The generator samples at temperature 0.6, so a re-run gives different "
        "questions. Latency has n=8 per path, from one client in India against a shared, free hosted API at "
        "particular times of day; it moves with NVIDIA's load. The routes were called in-process, so the deployed "
        "site's nginx/Cloudflare hop is not included.",
        "**The app's validation is lenient.** Warnings still count as valid, so the strict \"no warnings\" rate is "
        "reported beside it. Some warnings are validator quirks rather than question defects (see 4a).",
        "**Repetition is not scored.** Questions within a section often revisit the same topic or scenario; no "
        "metric here captures that.",
        "**Dollar costs are assumed, not billed.** The API was used on NVIDIA's free tier. The dollar figure prices "
        "the measured tokens at a third-party list price for the same model, and providers' prices for it vary "
        "(about 2x across the listings found). The Claude/GPT comparison ignores that those models would use "
        "different token counts.",
        superseded_glm_note(),
        "**Interrupted attempts were redone, not hidden.** Attempts cut short by this machine losing its network or "
        "being powered off were not scored and were re-run; their API calls stay in the run logs, where each one is "
        "documented. Failures on NVIDIA's side (504s, dropped connections, timeouts) were kept and are counted in "
        "the reliability figure.",
    ]]
    out += ["", "## Reproducing", "",
            "Every API call (endpoint, model, status, latency, token usage; never keys) is logged in each run's "
            "`calls.jsonl` under `eval/runs/` (private; available on request). Scripts, in order: `step0_build_index.py`, `step1_corpus.py`, `step2_prepare_ocr.py` / "
            "`step2_score_ocr.py`, `step3_retrieval.py`, `run_step45.ps1` (runs `step4_generate.py`, `step4_judge.py`, "
            "`step4_manual_sample.py`, `step45_metrics.py`), then `make_results.py`. Labelling rules were fixed "
            "before any labels existed: `eval/data/LABELLING.md`.", ""]
    (EVAL / "RESULTS.md").write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {EVAL / 'RESULTS.md'}")


if __name__ == "__main__":
    main()
