"""Step 4a + 5: generate mock papers through the app's real route functions and time them.

Both generation routes in backend/api/routes.py are called directly as an admin user (admin has no quota, so
nothing is counted or written to Postgres; the store argument is the test suite's in-memory FakeUserStore):
  * stream: generate_mock_paper_stream, the endpoint the web UI calls (one LLM call per question, SSE events,
    keep-alive pings). Consumed exactly as Starlette would iterate it.
  * batch:  generate_mock_paper, the non-streaming endpoint (one JSON-array LLM call per section, falling back to
    per-question calls if that fails). The web UI does not call it; it is the only generation path that parses JSON.
Retrieval runs against the rebuilt local eval_papers index.

Resumable: each (paper, path) result is its own file in <run_dir>/results/; pass --run-dir to continue a run."""
import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datetime import datetime  # noqa: E402

from eval.common import (BACKEND, DATA, eval_vector_service, install_call_logger,  # noqa: E402
                         is_local_network_error, new_run_dir, now_iso, wait_for_network, write_json)

# Mirrors the web UI (frontend/src/app/generate/page.tsx): addQuestion() picks the Bloom level and marks from the
# section difficulty, single-part questions are [{label: "", marks}], and every section is sent with generate_pool.
UI_BLOOM_BY_DIFFICULTY = {
    "easy": ["remember", "remember", "understand", "understand", "apply"],
    "medium": ["remember", "understand", "apply", "apply", "analyze"],
    "hard": ["understand", "apply", "analyze", "analyze", "evaluate", "create"],
}
UI_MARKS_BY_DIFFICULTY = {"easy": 4, "medium": 6, "hard": 10}


def section(name, instruction, difficulty, count, two_part=False):
    marks = UI_MARKS_BY_DIFFICULTY[difficulty]
    questions = []
    for n in range(1, count + 1):
        parts = ([{"label": "a", "marks": marks // 2}, {"label": "b", "marks": marks - marks // 2}]
                 if two_part else [{"label": "", "marks": marks}])
        levels = UI_BLOOM_BY_DIFFICULTY[difficulty]
        questions.append({"id": f"q-{n}", "number": n, "bloomLevel": levels[(n - 1) % len(levels)],
                          "totalMarks": marks, "parts": parts})
    return {"id": name, "name": name, "instruction": instruction, "bloomMode": "simple", "difficulty": difficulty,
            "questions": questions, "pool": [], "generate_pool": True}


def standard_paper():
    return [section("Section A", "Attempt any 4 out of 5 questions", "easy", 5),
            section("Section B", "Attempt any 4 out of 6 questions", "hard", 6)]


def two_part_paper():
    return [section("Section A", "Attempt all questions", "medium", 5, two_part=True),
            section("Section B", "Attempt any 2 out of 3 questions", "hard", 3)]


# Subject strings are the subject names stored in Postgres (the UI sends free text; its default is a subject name)
PAPERS = [
    ("P1", "Software Project Management", standard_paper),
    ("P2", "Artificial Intelligence", standard_paper),
    ("P3", "Cloud Security", standard_paper),
    ("P4", "Introduction to AI and Machine Learning", standard_paper),
    ("P5", "Data Mining and Business Intelligence", standard_paper),
    ("P6", "Software Project Management", two_part_paper),
    ("P7", "Cloud Security", two_part_paper),
    ("P8", "Data Mining and Business Intelligence", two_part_paper),
]


def classify(body: dict) -> str:
    if "messages" not in body:
        return "embed" if "input" in body else "other"
    prompt = body["messages"][0].get("content", "")
    if "Output ONLY a valid JSON array" in prompt:
        return "gen_batch_json"
    if "Generate the question now:" in prompt:
        return "gen_single"
    if "BLOOM_JUDGE" in prompt:
        return "judge"
    return "other_chat"


class FallbackCounter(logging.Handler):
    """Counts the app's own 'batch failed, falling back to per-question' log lines."""
    def __init__(self):
        super().__init__(logging.ERROR)
        self.count = 0

    def emit(self, record):
        if "Batch question generation failed" in record.getMessage():
            self.count += 1


async def consume_stream(response, started):
    events, pings = [], 0
    async for chunk in response.body_iterator:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        if text.startswith(":"):
            pings += 1
            continue
        for line in text.splitlines():
            if line.startswith("data: "):
                events.append({"t": round(time.perf_counter() - started, 3), **json.loads(line[6:])})
    return events, pings


def run_stream(routes, admin, store, request):
    started = time.perf_counter()
    response = routes.generate_mock_paper_stream(request, user=admin, store=store)
    events, pings = asyncio.run(consume_stream(response, started))
    total = time.perf_counter() - started
    questions = [e for e in events if e["type"] == "question"]
    done = next((e for e in events if e["type"] == "done"), None)
    return {
        "latency_s": round(total, 3),
        "time_to_first_question_s": questions[0]["t"] if questions else None,
        "keepalive_pings": pings,
        "errors": [e for e in events if e["type"] == "error"],
        "source_papers": next((e["sourcePapers"] for e in events if e["type"] == "meta"), []),
        "sections": done["sections"] if done else None,
    }


def run_batch(routes, admin, store, request, fallbacks):
    before = fallbacks.count
    started = time.perf_counter()
    try:
        result, error = routes.generate_mock_paper(request, user=admin, store=store), None
    except Exception as exc:
        result, error = {}, f"{type(exc).__name__}: {exc}"[:300]
    return {
        "latency_s": round(time.perf_counter() - started, 3),
        "route_error": error,
        "app_logged_batch_fallbacks": fallbacks.count - before,
        "source_papers": result.get("sourcePapers", []),
        "sections": result.get("sections"),
    }


def local_network_errors(run_dir: Path, pid: str, path: str, started_at: str) -> list:
    """Calls of this attempt (same paper/path, logged since it started) that failed before reaching the API."""
    since, hits = datetime.fromisoformat(started_at), []
    for line in (run_dir / "calls.jsonl").read_text(encoding="utf-8").splitlines():
        call = json.loads(line) if line.strip() else {}
        if (call.get("paper") == pid and call.get("path") == path and is_local_network_error(call.get("error"))
                and datetime.fromisoformat(call["ts"]) >= since):
            hits.append(call)
    return hits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", help="continue an existing run directory")
    parser.add_argument("--only", nargs="*", help="paper ids to run, e.g. P1")
    args = parser.parse_args()

    run_dir = Path(args.run_dir) if args.run_dir else new_run_dir("step4_generation")
    (run_dir / "results").mkdir(parents=True, exist_ok=True)
    set_context = install_call_logger(run_dir, "step4_generation", classify=classify, save_bodies=True)

    eval_vector_service()  # local index + env must be in place before the app's routes build their services
    sys.path.insert(0, str(BACKEND / "tests"))
    from fakes import FakeUserStore
    import api.routes as routes
    from auth.users import User

    fallbacks = FallbackCounter()
    logging.getLogger("services.rag_service").addHandler(fallbacks)
    admin = User(id=0, ms_tid="eval", ms_oid="eval", email=None, name="eval", role="admin", verified=True)
    store = FakeUserStore()

    configs = {pid: {"subject": subject, "sections": build()} for pid, subject, build in PAPERS}
    write_json(DATA / "generation_configs.json", configs)

    for pid, subject, _ in PAPERS:
        if args.only and pid not in args.only:
            continue
        for path in ("stream", "batch"):
            out = run_dir / "results" / f"{pid}__{path}.json"
            if out.exists():
                print(pid, path, "already done", flush=True)
                continue
            if not wait_for_network():
                print("no network for 30 min; stopping before", pid, path, flush=True)
                sys.exit(2)
            request = json.loads(json.dumps(configs[pid]))  # a fresh copy; the app may mutate it
            set_context(paper=pid, path=path)
            started_at = now_iso()
            result = (run_stream(routes, admin, store, request) if path == "stream"
                      else run_batch(routes, admin, store, request, fallbacks))
            finished_at = now_iso()
            local = local_network_errors(run_dir, pid, path, started_at)
            if local:
                # the eval machine went offline mid-attempt: not a measurement of the app, so no result file
                with (run_dir / "local_network_failures.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"paper": pid, "path": path, "started_at": started_at,
                                             "finished_at": finished_at, "local_network_errors": len(local),
                                             "first_error": local[0]["error"][:300]}) + "\n")
                print(pid, path, f"hit {len(local)} local network errors; not saved, stopping", flush=True)
                sys.exit(2)
            write_json(out, {"paper": pid, "path": path, "subject": subject, "started_at": started_at,
                             "finished_at": finished_at, **result})
            print(pid, path, f"{result['latency_s']:.1f}s", "sections:", bool(result["sections"]), flush=True)
    print("run dir:", run_dir)


if __name__ == "__main__":
    main()
