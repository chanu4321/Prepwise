"""Health probe for the generator model, used while step 4 is paused for an API outage.

Sends one tiny chat request (same endpoint and model the app uses) and exits 0 only if it answers 200 within
120 s. Every probe is logged to eval/runs/<ts>_generator_probes/calls.jsonl (one folder, reused)."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.common import RUNS, bootstrap_env, install_call_logger, new_run_dir  # noqa: E402

HEALTHY_WITHIN_S = 120


def main():
    bootstrap_env()
    existing = sorted(p for p in RUNS.iterdir() if p.is_dir() and p.name.endswith("_generator_probes"))
    run_dir = existing[-1] if existing else new_run_dir("generator_probes")
    install_call_logger(run_dir, "generator_probe", classify=lambda body: "probe")
    import requests  # the logged requests.post

    model = os.getenv("LLM_MODEL") or "nvidia/nemotron-3-ultra-550b-a55b"  # the app's default (rag_service.py)
    started = time.monotonic()
    try:
        response = requests.post(os.getenv("LLM_API_URL") or "https://integrate.api.nvidia.com/v1/chat/completions",
                                 timeout=HEALTHY_WITHIN_S + 30, headers={
                                     "Authorization": f"Bearer {os.getenv('NVIDIA_API_KEY', '')}",
                                     "Content-Type": "application/json"},
                                 json={"model": model, "messages": [{"role": "user", "content": "Reply with OK."}],
                                       "max_tokens": 64, "temperature": 0})
        status = response.status_code
    except Exception as exc:
        status = f"{type(exc).__name__}"
    elapsed = time.monotonic() - started
    healthy = status == 200 and elapsed <= HEALTHY_WITHIN_S
    print(f"{model}: {status} in {elapsed:.1f}s -> {'healthy' if healthy else 'not healthy'}")
    sys.exit(0 if healthy else 1)


if __name__ == "__main__":
    main()
