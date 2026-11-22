"""Steps 4 + 5: compute generation-quality and performance metrics from a step-4 run directory.

Reads only what the run wrote (results/, bodies.jsonl, calls.jsonl, judge.jsonl, manual_sample_key.json) plus the
hand-labelled eval/data/bloom_manual_sample.csv. Writes <run_dir>/metrics.json."""
import argparse
import csv
import json
import math
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.bloom import LEVELS, iter_questions  # noqa: E402
from eval.common import DATA, is_local_network_error, write_json  # noqa: E402
from eval.step4_judge import text_sha  # noqa: E402

PATHS = ("stream", "batch")
ISSUE_CATEGORIES = [("too short", "too_short"), ("doesn't use expected", "missing_bloom_verb"),
                    ("placeholder", "placeholder_text"), ("punctuation", "bad_ending_punctuation"),
                    ("too brief", "too_brief_for_marks"), ("contextual phrase", "contextual_phrase")]


def read_jsonl(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def percentile(values, q):
    """Linear interpolation between closest ranks (numpy's default)."""
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def same_moment(a: str, b: str) -> bool:
    """Call records are stamped in UTC and reply records in local time; compare them as instants."""
    return abs((datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds()) <= 2


def call_outcome(call) -> str:
    error = call.get("error") or ""
    if call.get("status") == 200:
        return "ok_200"
    if is_local_network_error(error):
        return "local_network_error"
    if call.get("status"):
        return f"http_{call['status']}"
    if "ReadTimeout" in error:
        return "read_timeout"
    if "RemoteDisconnected" in error or "Connection aborted" in error:
        return "server_closed_connection"
    return "other_error"


def in_window(record, results) -> bool:
    """True if a generation call/reply record falls inside the run that wrote the result file for its paper/path."""
    result = results.get((record.get("paper"), record.get("path")))
    if not result:
        return False
    moment, slack = datetime.fromisoformat(record["ts"]), timedelta(seconds=2)
    return (datetime.fromisoformat(result["started_at"]) - slack <= moment
            <= datetime.fromisoformat(result["finished_at"]) + slack)


def rate(numerator, denominator):
    return {"n": denominator, "count": numerator, "rate": round(numerator / denominator, 4) if denominator else None}


def cohen_kappa(a, b):
    n = len(a)
    if not n:
        return None
    observed = sum(x == y for x, y in zip(a, b)) / n
    count_a, count_b = Counter(a), Counter(b)
    expected = sum(count_a[level] * count_b[level] for level in LEVELS) / (n * n)
    return round((observed - expected) / (1 - expected), 4) if expected < 1 else None


def app_style_parse(content):
    """The batch path's own extraction (services/rag_service.py, _generate_section): slice from the first '['
    to the last ']' and json.loads it; the app then needs a list of objects."""
    if not content:
        return None
    raw = content
    start = raw.find("[")
    if start >= 0:
        raw = raw[start:]
    end = raw.rfind("]")
    if end != -1:
        raw = raw[:end + 1]
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, list) and all(isinstance(item, dict) for item in data) else None


def requested_per_section(configs, paper):
    return [len(s["questions"]) + math.ceil(len(s["questions"]) * 0.5) for s in configs[paper]["sections"]]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir)

    configs = json.loads((DATA / "generation_configs.json").read_text(encoding="utf-8"))
    results = {(r["paper"], r["path"]): r for r in
               (json.loads(p.read_text(encoding="utf-8")) for p in sorted((run_dir / "results").glob("*.json")))}
    rows = list(iter_questions(run_dir, configs))
    calls = read_jsonl(run_dir / "calls.jsonl")
    bodies = read_jsonl(run_dir / "bodies.jsonl")
    judge = {j["qid"]: j for j in read_jsonl(run_dir / "judge.jsonl")}  # the later line for a qid wins
    current_text = {r["qid"]: r["text"] for r in rows if r["text"]}
    judge = {qid: j for qid, j in judge.items() if qid in current_text
             and j.get("text_sha") in (None, text_sha(current_text[qid]))}  # verdicts on regenerated text don't count
    metrics = {"run_dir": run_dir.name, "papers": sorted({p for p, _ in results})}

    # Every generation API call in the log, including attempts later redone: how often the API answered at all.
    # Calls that never left this machine (it was offline) are counted but kept out of the availability rate.
    outcomes = Counter(call_outcome(c) for c in calls if c.get("stage") == "step4_generation")
    reached = sum(n for name, n in outcomes.items() if name != "local_network_error")
    metrics["api_call_outcomes_all_generation_calls"] = {
        "outcomes": dict(outcomes), "ok_of_calls_that_reached_api": rate(outcomes.get("ok_200", 0), reached)}

    # Generation calls outside every result file's time window belong to an attempt that was killed before it
    # wrote its result (and was redone). They are kept out of the per-paper metrics but reported for the cost audit.
    outside = [c for c in calls if c.get("stage") == "step4_generation" and not in_window(c, results)]
    calls = [c for c in calls if c.get("stage") != "step4_generation" or in_window(c, results)]
    bodies = [b for b in bodies if in_window(b, results)]
    metrics["generation_calls_outside_completed_results"] = {
        "calls": len(outside),
        "by_paper_path": dict(Counter(f"{c.get('paper')}/{c.get('path')}" for c in outside)),
        "prompt_tokens": sum((c.get("usage") or {}).get("prompt_tokens", 0) for c in outside),
        "completion_tokens": sum((c.get("usage") or {}).get("completion_tokens", 0) for c in outside),
    }

    # (a) rule-based validation, per path. Denominator = every question the request asked for.
    validation = {}
    for path in PATHS:
        papers = [p for (p, pth) in results if pth == path]
        requested = sum(sum(requested_per_section(configs, p)) for p in papers)
        mine = [r for r in rows if r["path"] == path]
        generated = [r for r in mine if not r["failed"] and r["text"] and r["validation"]]
        issues = Counter()
        for r in generated:
            for issue in r["validation"]["issues"]:
                issues[next((name for key, name in ISSUE_CATEGORIES if key in issue), "other")] += 1
        validation[path] = {
            "requested": requested, "returned": len(mine),
            "missing_from_response": requested - len(mine),
            "generation_failed_flagged": sum(1 for r in mine if r["failed"]),
            "generated": len(generated),
            "app_valid_of_requested": rate(sum(1 for r in generated if r["validation"]["valid"]), requested),
            "app_valid_of_generated": rate(sum(1 for r in generated if r["validation"]["valid"]), len(generated)),
            "zero_issues_of_generated": rate(sum(1 for r in generated if not r["validation"]["issues"]),
                                             len(generated)),
            "issue_counts": dict(issues),
        }
    metrics["validation"] = validation
    # replies to one-question calls that hit the app's max_tokens (reasoning tokens count towards it)
    metrics["single_question_replies_cut_at_max_tokens"] = {
        path: rate(sum(1 for b in bodies if b.get("kind") == "gen_single" and b.get("path") == path
                       and b.get("finish_reason") == "length"),
                   sum(1 for b in bodies if b.get("kind") == "gen_single" and b.get("path") == path))
        for path in PATHS}

    # (b) JSON parse success of the batch path's JSON-array calls. The route makes one call per section, in order,
    # with no retry, so the i-th call of a paper is section i; replies are matched to calls by timestamp because a
    # timed-out call has no reply.
    batch_calls = [c for c in calls if c.get("kind") == "gen_batch_json" and c.get("stage") == "step4_generation"]
    batch_bodies = [b for b in bodies if b.get("kind") == "gen_batch_json"]
    parsed, complete, truncated = 0, 0, 0
    per_section = []
    for paper in sorted({c["paper"] for c in batch_calls}):
        wanted = requested_per_section(configs, paper)
        for index, call in enumerate(c for c in batch_calls if c["paper"] == paper):
            body = next((b for b in batch_bodies if b["paper"] == paper and same_moment(b["ts"], call["ts"])), None)
            items = app_style_parse(body.get("content")) if body else None
            expected = wanted[index] if index < len(wanted) else None
            parsed += items is not None
            complete += items is not None and len(items) == expected
            truncated += bool(body) and body.get("finish_reason") == "length"
            per_section.append({"paper": paper, "section": index, "status": call.get("status"),
                                "error": call.get("error"), "parsed": items is not None,
                                "items": len(items) if items is not None else None, "expected": expected,
                                "finish_reason": body.get("finish_reason") if body else None})
    answered = sum(1 for c in batch_calls if c.get("status") == 200)
    metrics["json_parse_batch"] = {
        "calls": len(batch_calls), "calls_non_200_or_timeout": len(batch_calls) - answered,
        "parse_success_of_answered_calls": rate(parsed, answered),
        "parse_success_of_all_calls": rate(parsed, len(batch_calls)),
        "parse_success_and_all_questions_present": rate(complete, len(batch_calls)),
        "finish_reason_length": truncated,
        "app_logged_fallbacks": sum(r.get("app_logged_batch_fallbacks", 0) for (p, pth), r in results.items()
                                    if pth == "batch"),
        "per_section": per_section,
    }

    # (c) Bloom adherence by the LLM judge (blind to the target)
    adherence = {}
    for path in PATHS:
        judged = [(r, judge[r["qid"]]) for r in rows if r["path"] == path and r["qid"] in judge]
        ok = [(r, j) for r, j in judged if j.get("judge_level") and r["target"] in LEVELS]
        exact = sum(1 for r, j in ok if j["judge_level"] == r["target"])
        near = sum(1 for r, j in ok if abs(LEVELS.index(j["judge_level"]) - LEVELS.index(r["target"])) <= 1)
        by_level = {level: rate(sum(1 for r, j in ok if r["target"] == level and j["judge_level"] == level),
                                sum(1 for r, j in ok if r["target"] == level)) for level in LEVELS}
        confusion = Counter(f"{r['target']}->{j['judge_level']}" for r, j in ok)
        adherence[path] = {"judge_attempted": len(judged), "judge_failures": len(judged) - len(ok),
                           "exact": rate(exact, len(ok)), "within_one_level": rate(near, len(ok)),
                           "by_target_level": by_level, "confusion": dict(sorted(confusion.items()))}
    judge_models = {j.get("judge_model") for j in judge.values()}
    metrics["bloom_judge"] = {"judge_models": sorted(m for m in judge_models if m), **adherence}

    # Manual check: human vs judge, and human vs target
    key_file, labels_file = run_dir / "manual_sample_key.json", DATA / "bloom_manual_sample.csv"
    if key_file.exists() and labels_file.exists():
        key = json.loads(key_file.read_text(encoding="utf-8"))["key"]
        by_qid = {r["qid"]: r for r in rows}
        with labels_file.open(encoding="utf-8-sig", newline="") as handle:
            labelled = [r for r in csv.DictReader(handle) if r["human_level"].strip()]
        pairs = []
        for r in labelled:
            human = r["human_level"].strip().lower()
            if human not in LEVELS:
                raise SystemExit(f"{r['sample_id']}: human_level {human!r} is not one of {LEVELS}")
            qid = key[r["sample_id"]]
            pairs.append({"sample_id": r["sample_id"], "human": human, "target": by_qid[qid]["target"],
                          "judge": (judge.get(qid) or {}).get("judge_level")})
        both = [p for p in pairs if p["judge"]]
        metrics["bloom_manual"] = {
            "labelled": len(pairs),
            "human_vs_target_exact": rate(sum(p["human"] == p["target"] for p in pairs), len(pairs)),
            "judge_vs_human_exact_agreement": rate(sum(p["human"] == p["judge"] for p in both), len(both)),
            "judge_vs_human_within_one": rate(sum(abs(LEVELS.index(p["human"]) - LEVELS.index(p["judge"])) <= 1
                                                  for p in both), len(both)),
            "judge_vs_human_cohen_kappa": cohen_kappa([p["human"] for p in both], [p["judge"] for p in both]),
            "pairs": pairs,
        }

    # Step 5: end-to-end latency per generated paper, and tokens per paper
    performance = {}
    for path in PATHS:
        mine = {p: r for (p, pth), r in results.items() if pth == path and r.get("sections")}
        latencies = [r["latency_s"] for r in mine.values()]
        per_paper_tokens = {}
        for paper in mine:
            paper_calls = [c for c in calls if c.get("paper") == paper and c.get("path") == path
                           and c.get("stage") == "step4_generation"]
            usage = [c.get("usage") or {} for c in paper_calls]
            per_paper_tokens[paper] = {
                "llm_calls": sum(1 for c in paper_calls if c.get("kind", "").startswith("gen_")),
                "failed_calls": sum(1 for c in paper_calls if c.get("status") != 200),
                "prompt_tokens": sum(u.get("prompt_tokens", 0) for u, c in zip(usage, paper_calls)
                                     if c.get("kind", "").startswith("gen_")),
                "completion_tokens": sum(u.get("completion_tokens", 0) for u, c in zip(usage, paper_calls)
                                         if c.get("kind", "").startswith("gen_")),
                "embedding_tokens": sum(u.get("prompt_tokens", 0) for u, c in zip(usage, paper_calls)
                                        if c.get("kind") == "embed"),
                "calls_missing_usage": sum(1 for u, c in zip(usage, paper_calls) if c.get("status") == 200 and not u),
            }
        totals = [t["prompt_tokens"] + t["completion_tokens"] for t in per_paper_tokens.values()]
        entry = {
            "papers_completed": len(mine),
            "papers_failed": sum(1 for (p, pth), r in results.items() if pth == path and not r.get("sections")),
            "latency_s": {"p50": round(percentile(latencies, 0.5), 1) if latencies else None,
                          "p95": round(percentile(latencies, 0.95), 1) if latencies else None,
                          "min": min(latencies, default=None), "max": max(latencies, default=None),
                          "per_paper": {p: r["latency_s"] for p, r in mine.items()}},
            "llm_tokens_per_paper": {"mean": round(sum(totals) / len(totals)) if totals else None,
                                     "p50": percentile(totals, 0.5), "min": min(totals, default=None),
                                     "max": max(totals, default=None)},
            "per_paper_tokens": per_paper_tokens,
        }
        if path == "stream":
            ttfq = [r["time_to_first_question_s"] for r in mine.values() if r.get("time_to_first_question_s")]
            entry["time_to_first_question_s"] = {"p50": round(percentile(ttfq, 0.5), 1) if ttfq else None,
                                                 "p95": round(percentile(ttfq, 0.95), 1) if ttfq else None}
            single = [c["latency_s"] for c in calls if c.get("kind") == "gen_single" and c.get("path") == "stream"
                      and c.get("status") == 200]
            entry["per_question_llm_call_s"] = {"n": len(single),
                                                "p50": round(percentile(single, 0.5), 1) if single else None,
                                                "p95": round(percentile(single, 0.95), 1) if single else None}
        performance[path] = entry

    pricing_file = DATA / "pricing.json"
    if pricing_file.exists():
        # {"<model>": {"input_per_million": <usd>, "output_per_million": <usd>}}: an assumed rate, not a bill
        pricing = json.loads(pricing_file.read_text(encoding="utf-8"))
        for path in PATHS:
            costs = []
            for paper in performance[path]["per_paper_tokens"]:
                cost = 0.0
                for c in calls:
                    if c.get("paper") == paper and c.get("path") == path and c.get("model") in pricing:
                        price, usage = pricing[c["model"]], c.get("usage") or {}
                        cost += usage.get("prompt_tokens", 0) / 1e6 * price["input_per_million"]
                        cost += usage.get("completion_tokens", 0) / 1e6 * price["output_per_million"]
                costs.append(cost)
            performance[path]["assumed_cost_usd_per_paper"] = {
                "mean": round(sum(costs) / len(costs), 5) if costs else None, "pricing": pricing}
    metrics["performance"] = performance
    metrics["judge_tokens"] = {
        "calls": sum(1 for c in calls if c.get("kind") == "judge"),
        "prompt_tokens": sum((c.get("usage") or {}).get("prompt_tokens", 0) for c in calls if c.get("kind") == "judge"),
        "completion_tokens": sum((c.get("usage") or {}).get("completion_tokens", 0)
                                 for c in calls if c.get("kind") == "judge"),
    }

    write_json(run_dir / "metrics.json", metrics)
    print(json.dumps({k: v for k, v in metrics.items() if k not in ("json_parse_batch",)}, indent=1)[:6000])
    print("json_parse_batch:", {k: v for k, v in metrics["json_parse_batch"].items() if k != "per_section"})


if __name__ == "__main__":
    main()
