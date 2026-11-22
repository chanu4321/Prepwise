"""Step 4c: LLM judge for Bloom's level on every generated question of a step-4 run.

The judge is a different vendor's model from the generator (nvidia/nemotron-3-ultra-550b-a55b since 2026-11-10;
z-ai/glm-5.3-flash before) to avoid self-grading. It sees the
subject and question text only, never the requested level. Results go to <run_dir>/judge.jsonl (resumable);
its API calls are logged to <run_dir>/calls.jsonl with kind "judge"."""
import argparse
import hashlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.bloom import JUDGE_PROMPT, LEVELS, RUBRIC, iter_questions  # noqa: E402
from eval.common import DATA, bootstrap_env, install_call_logger, now_iso, wait_for_network  # noqa: E402
from eval.step4_generate import classify  # noqa: E402

JUDGE_MODEL = "mistralai/mistral-large-4-1t-a52b"
JUDGE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
# It is a reasoning model: 200 tokens were all spent on reasoning in the probe, and 4096 still ran out on 16 of
# 286 calls in the first pass of 20261110T111039Z (8 questions got no verdict), so the retry pass used 8192.
MAX_TOKENS = 8192
ATTEMPTS = 2
WORKERS = 3
TIMEOUT_S = 420  # 180 s timed out on 18 of 48 questions in the first pass


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def parse_level(content: str | None):
    if not content:
        return None, None, "empty reply"
    start, end = content.find("{"), content.rfind("}")
    try:
        data = json.loads(content[start:end + 1])
    except Exception:
        return None, None, "unparseable reply"
    level = str(data.get("level", "")).strip().lower()
    if level not in LEVELS:
        return None, None, f"invalid level {level!r}"
    return level, data, None


def judge_one(row):
    import requests  # the logged requests.post
    prompt = JUDGE_PROMPT.format(rubric=RUBRIC, subject=row["subject"], question=row["text"])
    last_error = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = requests.post(JUDGE_URL, timeout=TIMEOUT_S, headers={
                "Authorization": f"Bearer {os.getenv('NVIDIA_API_KEY', '')}", "Content-Type": "application/json"},
                json={"model": JUDGE_MODEL, "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0, "max_tokens": MAX_TOKENS})
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}"
                continue
            content = response.json()["choices"][0]["message"].get("content")
            level, data, last_error = parse_level(content)
            if level:
                return {"qid": row["qid"], "text_sha": text_sha(row["text"]), "judge_level": level,
                        "confidence": data.get("confidence"),
                        "reason": data.get("reason"), "attempts": attempt, "judged_at": now_iso()}
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"[:200]
    return {"qid": row["qid"], "text_sha": text_sha(row["text"]), "judge_level": None, "judge_error": last_error,
            "attempts": ATTEMPTS, "judged_at": now_iso()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir)

    bootstrap_env()
    install_call_logger(run_dir, "step4_judge", classify=classify)
    configs = json.loads((DATA / "generation_configs.json").read_text(encoding="utf-8"))
    out = run_dir / "judge.jsonl"
    # only successful verdicts for the question's current text count as done; failed ones are retried, and the
    # later line wins when read back
    done = {}
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            record = json.loads(line) if line.strip() else {}
            if record.get("judge_level"):
                done[record["qid"]] = record.get("text_sha")
    if not wait_for_network():
        raise SystemExit("no network for 30 min")
    todo = [row for row in iter_questions(run_dir, configs) if row["text"]
            and not (row["qid"] in done and done[row["qid"]] in (None, text_sha(row["text"])))]
    print(f"judging {len(todo)} questions with {JUDGE_MODEL} ({len(done)} already judged)", flush=True)

    lock = threading.Lock()

    def work(row):
        result = judge_one(row)
        with lock, out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({**result, "judge_model": JUDGE_MODEL}) + "\n")
        return result

    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(work, todo))
    print("judged:", sum(1 for r in results if r["judge_level"]), "| judge failures:",
          sum(1 for r in results if not r["judge_level"]))


if __name__ == "__main__":
    main()
