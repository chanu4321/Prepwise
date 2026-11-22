"""Shared plumbing for the eval scripts. Imports the app as a black box; modifies nothing in it.

Safety rules enforced here:
  * Postgres is opened read-only (the server rejects any write).
  * Every outgoing `requests.post` is logged (url, model, status, latency, token usage, no headers/keys).
  * `VectorService` is built without its constructor, because the constructor can create a payload
    index on the production collection; eval only ever reads from it.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
EVAL = ROOT / "eval"
RUNS = EVAL / "runs"
DATA = EVAL / "data"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_dir(stage: str) -> Path:
    run_dir = RUNS / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{stage}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def readonly_pg():
    """A Postgres connection that the server will refuse to write through."""
    from database import get_db_connection
    conn = get_db_connection()
    conn.set_session(readonly=True, autocommit=True)
    return conn


EVAL_COLLECTION = "eval_papers"
LOCAL_QDRANT_DIR = DATA / "qdrant_local"

API_HOST = "integrate.api.nvidia.com"
# Errors raised before a request reaches the API: the eval machine itself is offline. They say nothing about
# Prepwise or the API, so attempts that hit them are redone rather than measured.
LOCAL_NETWORK_SIGNS = ("NameResolutionError", "getaddrinfo failed", "WinError 10051", "WinError 10065")


def is_local_network_error(error) -> bool:
    return bool(error) and any(sign in error for sign in LOCAL_NETWORK_SIGNS)


def wait_for_network(max_wait_s: int = 1800, poll_s: int = 60) -> bool:
    """True once the API host resolves; False if it still doesn't after max_wait_s."""
    import socket
    deadline = time.monotonic() + max_wait_s
    while True:
        try:
            socket.getaddrinfo(API_HOST, 443)
            return True
        except OSError:
            if time.monotonic() > deadline:
                return False
            time.sleep(poll_s)


def bootstrap_env():
    """Makes poppler and tesseract findable, and blanks the production Qdrant URL/key so nothing in the
    app can reach the (now deleted) production cluster. The app's `database` module then builds an unused
    localhost client; eval swaps in the embedded local one below."""
    import shutil
    for tool, folder in (("pdftoppm", r"C:\Chaitanya\poppler-23.11.0\Library\bin"),
                         ("tesseract", r"C:\Program Files\Tesseract-OCR")):
        if shutil.which(tool) is None:
            os.environ["PATH"] = folder + os.pathsep + os.environ["PATH"]
    os.environ["QDRANT_URL"] = ""
    os.environ["QDRANT_API_KEY"] = ""
    os.environ["QDRANT_COLLECTION"] = EVAL_COLLECTION
    # Not every service module imports `database` (which is what loads .env in the app); load it explicitly.
    # Existing variables, including the blanks above, are not overridden.
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")


def eval_vector_service():
    """The app's VectorService, running against an embedded local Qdrant and the `eval_papers` collection."""
    bootstrap_env()
    from qdrant_client import QdrantClient
    import services.vector_service as vector_module
    LOCAL_QDRANT_DIR.mkdir(parents=True, exist_ok=True)
    local_client = QdrantClient(path=str(LOCAL_QDRANT_DIR))
    vector_module.qdrant_client = local_client
    assert vector_module.COLLECTION_NAME == EVAL_COLLECTION, vector_module.COLLECTION_NAME
    return vector_module.VectorService()


def install_call_logger(run_dir: Path, stage: str, classify=None, save_bodies: bool = False):
    """Logs every `requests.post` made anywhere in this process to run_dir/calls.jsonl.

    `classify(request_json) -> str` tags each call with a `kind` (e.g. which prompt it was).
    `save_bodies` also writes each chat reply's content and finish_reason to run_dir/bodies.jsonl.
    Returns a `set_context` function: call it to tag subsequent calls (e.g. with a paper id)."""
    import requests

    log_path = run_dir / "calls.jsonl"
    bodies_path = run_dir / "bodies.jsonl"
    original_post = requests.post
    context: dict = {}

    def logged_post(url, *args, **kwargs):
        body = kwargs.get("json") or {}
        kind = classify(body) if classify and isinstance(body, dict) else None
        started = time.perf_counter()
        status, usage, error = None, None, None
        try:
            response = original_post(url, *args, **kwargs)
            status = response.status_code
            try:
                reply = response.json()
                usage = reply.get("usage")
                if save_bodies and status == 200 and reply.get("choices"):
                    choice = reply["choices"][0]
                    with bodies_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps({**context, "kind": kind, "ts": now_iso(),
                                                 "finish_reason": choice.get("finish_reason"),
                                                 "content": (choice.get("message") or {}).get("content")}) + "\n")
            except Exception:
                usage = None
            return response
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:300]
            raise
        finally:
            record = {
                "ts": now_iso(), "stage": stage, **context, "kind": kind, "url": url,
                "model": body.get("model") if isinstance(body, dict) else None,
                "status": status, "latency_s": round(time.perf_counter() - started, 3),
                "usage": usage, "error": error,
            }
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")

    requests.post = logged_post

    def set_context(**tags):
        context.clear()
        context.update(tags)

    return set_context


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
