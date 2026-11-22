"""Lists the chat models on the NVIDIA API and probes candidates with one tiny request each, to choose a
replacement generator while z-ai/glm-5.3-flash is down. Every call (including the model listing) is logged to
eval/runs/<ts>_model_probes/calls.jsonl.

  python eval/probe_models.py --list              # print the catalogue
  python eval/probe_models.py MODEL [MODEL ...]   # probe these, in parallel"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.common import bootstrap_env, install_call_logger, new_run_dir, now_iso  # noqa: E402

BASE = "https://integrate.api.nvidia.com/v1"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("models", nargs="*")
    args = parser.parse_args()

    bootstrap_env()
    run_dir = new_run_dir("model_probes")
    install_call_logger(run_dir, "model_probe", classify=lambda body: "probe")
    import requests  # the logged requests.post
    headers = {"Authorization": f"Bearer {os.getenv('NVIDIA_API_KEY', '')}", "Content-Type": "application/json"}

    if args.list:
        started = time.monotonic()
        response = requests.get(f"{BASE}/models", headers=headers, timeout=60)
        with (run_dir / "calls.jsonl").open("a", encoding="utf-8") as handle:  # GET is not patched; log by hand
            handle.write(json.dumps({"ts": now_iso(), "stage": "model_probe", "kind": "list_models",
                                     "url": f"{BASE}/models", "status": response.status_code,
                                     "latency_s": round(time.monotonic() - started, 3)}) + "\n")
        for model in sorted(m["id"] for m in response.json().get("data", [])):
            print(model)

    def probe(model):
        started = time.monotonic()
        try:
            response = requests.post(f"{BASE}/chat/completions", headers=headers, timeout=150, json={
                "model": model, "messages": [{"role": "user", "content": "Reply with the word OK."}],
                "max_tokens": 256, "temperature": 0})
            body = response.json() if response.status_code == 200 else {}
            message = (body.get("choices") or [{}])[0].get("message", {})
            reasoning = (body.get("usage", {}).get("completion_tokens_details") or {}).get("reasoning_tokens")
            return (f"{model}: {response.status_code} in {time.monotonic() - started:.1f}s | "
                    f"reply={str(message.get('content'))[:20]!r} reasoning_tokens={reasoning} "
                    f"has_reasoning_content={bool(message.get('reasoning_content'))}")
        except Exception as exc:
            return f"{model}: {type(exc).__name__} after {time.monotonic() - started:.1f}s"

    if args.models:
        with ThreadPoolExecutor(len(args.models)) as pool:
            for line in pool.map(probe, args.models):
                print(line, flush=True)
    print("run dir:", run_dir)


if __name__ == "__main__":
    main()
