# Accounts & Access (Part A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Microsoft sign-in with student / faculty / admin roles, daily usage limits, an admin CLI (`make-admin`, `reprocess`), and clean error handling to PrepWise, all enforced by the FastAPI backend.

**Architecture:** The browser signs in with MSAL (Microsoft's official library) and sends a Microsoft access token as `Authorization: Bearer …`. FastAPI verifies the token against Microsoft's public keys, looks the user up in Postgres, and FastAPI dependencies enforce roles and atomic daily quotas (per user, or per hashed IP address for anonymous uploads). Every unexpected error becomes a generic JSON 500 that still carries CORS headers.

**Tech Stack:** FastAPI 0.141 / Starlette 1.7, psycopg2, PyJWT[crypto] 2.15, pytest 9; Next.js 16 (App Router), React 19.2.1, `@azure/msal-browser` 5.23 + `@azure/msal-react` 5.7.

**Spec:** `docs/superpowers/specs/2026-09-25-accounts-and-access-design.md`

## Before you start

- The working tree holds uncommitted changes from earlier work (import fixes, footer, model switches, and more). Commit them on their own first, then create the branch: `git checkout -b feature/accounts-and-access`. Never push to `default`: that triggers the production image build.
- On this Windows machine, "python" in commands below means `backend/.venv/Scripts/python.exe`. Commands assume Git Bash in the project root `C:\Chaitanya\Project`.
- Backend imports are relative to `backend/` (`from services.x import …`, never `from backend.x`). The server runs from the project root with `backend/` on the path (`uvicorn main:app --app-dir backend`), so relative data paths like `backend/papers` keep working.

## Global Constraints

- Daily limits (all from env, defaults shown): generate trial faculty `3`, verified faculty `25`, admin unlimited; paper upload anonymous `5` per IP, student `20`, trial faculty none (403), verified faculty `10`, admin unlimited; syllabus upload verified faculty `10`, admin unlimited, trial faculty none (403).
- Limits reset at UTC midnight. A 429 carries `Retry-After` (seconds until UTC midnight). CORS must expose `Retry-After`.
- Token validation: RS256 against `https://login.microsoftonline.com/common/discovery/v2.0/keys`; `aud` == `AZURE_CLIENT_ID`; `iss` == `https://login.microsoftonline.com/{tid}/v2.0` using the token's own `tid`; 60 s leeway; `scp` contains `access_as_user`; identity is (`tid`, `oid`). `name` / `preferred_username` are display-only, never used for authorization.
- Error bodies: `{ "detail": "<human message>", "code": "<machine code>" }`. Codes: `not_authenticated`, `invalid_token` (401); `role_required`, `forbidden` (403); `role_already_set` (409); `quota_exceeded` (429); `internal_error` (500); plus `invalid_request`, `invalid_file` (400), `not_found` (404), `syllabus_unreadable` (422), `cannot_demote_self` (409).
- Raw exception text never reaches the browser; details go to the log. Tokens are never logged.
- Anonymous IPs are stored only as `sha256(IP_HASH_SALT + ip)`; subjects are `user:<id>` or `ip:<hash>`.
- Default test run needs no network and no database; tests use a fake user store. Code must run on Python 3.11 (Docker and CI) and 3.12 (local venv).
- Frontend has no test framework: every frontend task is verified with `npx tsc --noEmit`, `npx eslint src`, `npx next build`, plus the manual checks listed.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A personal Microsoft account** (tenant `9188040d-6c67-4c5b-b112-36a304b66dad`) must validate: the issuer check has to use the token's own `tid`, not a fixed tenant. Pinned by `test_personal_account_token_is_accepted` in Task 4.
2. **Two requests racing for the last quota slot** must not both pass. Only the real SQL can show this: `test_concurrent_consume_never_exceeds_limit` in Task 5 (runs when `TEST_DATABASE_URL` is set).
3. **A generation run where every question falls back** (`"error": True`) must not use up a slot, and neither must one that fails before its first question. Pinned by `test_stream_refunds_when_every_question_failed` in Task 8.
4. **Reprocess where the LLM returns empty fields** must keep the existing metadata, never blank it out. Pinned by `test_merge_keeps_old_values_when_new_are_empty` in Task 12.
5. **Uploads that aren't PDFs, or that have odd filenames,** must get a clear 400 without spending quota, never a 500. Pinned by `test_non_pdf_is_rejected_without_using_quota` in Task 9.

## File Structure

**Backend (new)**
- `pytest.ini`: test paths and `pythonpath = backend`.
- `backend/requirements-dev.txt`: pytest, httpx.
- `backend/errors.py`: `ApiError`, JSON error handler, catch-all ASGI middleware, `install_error_handling(app)`.
- `backend/auth/__init__.py`: empty package marker.
- `backend/auth/tokens.py`: Microsoft access-token verification → `Claims`.
- `backend/auth/users.py`: `User`, `user_summary()`, `PostgresUserStore` (users + daily usage).
- `backend/auth/limits.py`: limits per role and the seconds until UTC midnight.
- `backend/auth/deps.py`: FastAPI dependencies (`optional_user`, `current_user`, `require_role`) and quota helpers.
- `backend/api/me.py`: `GET /me`, `POST /me/role`.
- `backend/api/admin.py`: `GET /admin/users`, `PATCH /admin/users/{id}`.
- `backend/services/paper_metadata.py`: `normalize_subject_code()`, `to_paper_fields()`.
- `backend/services/paper_store.py`: paper rows in Postgres (insert, fetch, update metadata).
- `backend/services/reprocess.py`: re-run stored papers through the pipeline.
- `backend/admin_cli.py`: `make-admin`, `reprocess` commands.
- `backend/tests/conftest.py`, `backend/tests/fakes.py`, and `backend/tests/test_*.py`.

**Backend (modified)**
- `backend/main.py`: logging, error handling, CORS `expose_headers`, lifespan `init_db()`, new routers.
- `backend/database.py`: `db_cursor()`; `init_db()` creates `users` and `usage_daily` and adds the `uploaded_by` columns.
- `backend/api/routes.py`: auth dependencies, quotas, error cleanup, upload refactor.
- `backend/services/syllabus_service.py`: `get_syllabus_owner()`; `uploaded_by` on save.
- `backend/services/vector_service.py`: `upsert_paper_vector()`.
- `backend/requirements.txt`: `PyJWT[crypto]`.
- `backend/Dockerfile`: `--proxy-headers`.
- `docker-compose.yml`: new environment variables.
- `.github/workflows/deploy.yml`: `test` job gating the image build.
- `README.md`: auth setup, "How auth works", admin commands.

**Frontend (new)**
- `frontend/src/lib/auth/msal.ts`: MSAL config and instance, the ready signal.
- `frontend/src/lib/api.ts`: `apiFetch()`, `ApiRequestError`, `describeApiError()`.
- `frontend/src/components/AuthProvider.tsx`: MSAL provider, `/me` state, `useAuth()`.
- `frontend/src/components/AuthMenu.tsx`: sign-in button or user menu.
- `frontend/src/components/AccessNotice.tsx`: the "sign in" / "not allowed" panel.
- `frontend/src/app/auth/redirect/page.tsx`: MSAL v5 redirect bridge.
- `frontend/src/app/welcome/page.tsx`: one-time Student / Faculty picker.
- `frontend/src/app/admin/users/page.tsx`: admin users table.

**Frontend (modified)**
- `frontend/src/app/layout.tsx`: wrap in `AuthProvider`.
- `frontend/src/components/Navbar.tsx`: client component with per-role links and `AuthMenu`.
- `frontend/src/app/upload/page.tsx`, `frontend/src/app/syllabus/page.tsx`, `frontend/src/app/generate/page.tsx`: `apiFetch`, gating, error messages.

---

### Task 1: Register the Entra app and prove a personal account gets a valid token (spike)

This is the only part that depends on Microsoft's configuration rather than our code. Do it first: if it fails, stop and revisit the design.

**Files:**
- Create: `backend/dev-scripts/get_test_token.py` (gitignored folder; local tool only)

**Interfaces:**
- Produces: the app's **Application (client) ID**, stored as `AZURE_CLIENT_ID` in the root `.env` and `NEXT_PUBLIC_AZURE_CLIENT_ID` in `frontend/.env.local`. Also produces `backend/dev-scripts/.test_token`, a real access token for manual API checks.

- [ ] **Step 1: Create the app registration (manual, in the browser)**

1. Open https://entra.microsoft.com and sign in with your personal Microsoft account. Using it creates a free "Default Directory".
2. **App registrations → New registration**
   - Name: `PrepWise`
   - Supported account types: **Accounts in any organizational directory and personal Microsoft accounts**
   - Redirect URI: platform **Single-page application (SPA)**, `http://localhost:3000/auth/redirect`
   - Register.
3. **Authentication:** under Single-page application, add `https://prepwise-opal-three.vercel.app/auth/redirect`. Save.
4. **Manifest:** confirm `"requestedAccessTokenVersion": 2` inside `"api"`. In the older manifest format it's `"accessTokenAcceptedVersion": 2`. Set it if it's `null`, then save.
5. **Expose an API → Application ID URI → Add:** accept `api://<client-id>` and save. **Add a scope:** name `access_as_user`, who can consent **Admins and users**, admin and user consent display name "Access PrepWise as you", State **Enabled**.
6. **API permissions → Add a permission → My APIs → PrepWise → Delegated → `access_as_user` → Add permissions.**
7. Copy the **Application (client) ID** from Overview. Add `AZURE_CLIENT_ID=<id>` to the root `.env`, and `NEXT_PUBLIC_AZURE_CLIENT_ID=<id>` to `frontend/.env.local` (create the file; it's gitignored by Next's default `.gitignore`).
8. **For this spike only:** Authentication → Advanced settings → **Allow public client flows → Yes**. Save. Step 4 turns it off again.

- [ ] **Step 2: Write the token script**

```python
"""Gets a real PrepWise API access token via device-code sign-in and prints its claims.

Dev-only. Needs: `python -m pip install msal`, AZURE_CLIENT_ID in the root .env,
and "Allow public client flows" = Yes on the app registration while you use it.
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import jwt  # noqa: E402  (PyJWT; installed in Task 4, `python -m pip install PyJWT` for now)
import msal  # noqa: E402

import database  # noqa: E402,F401  (loads the root .env)

client_id = os.environ["AZURE_CLIENT_ID"]
app = msal.PublicClientApplication(client_id, authority="https://login.microsoftonline.com/common")
flow = app.initiate_device_flow(scopes=[f"api://{client_id}/access_as_user"])
if "user_code" not in flow:
    sys.exit(json.dumps(flow, indent=2))
print(flow["message"])
result = app.acquire_token_by_device_flow(flow)
token = result.get("access_token")
if not token:
    sys.exit(json.dumps(result, indent=2))

claims = jwt.decode(token, options={"verify_signature": False})
print(json.dumps({k: claims.get(k) for k in ("ver", "aud", "iss", "tid", "oid", "scp", "name", "preferred_username")}, indent=2))
token_path = os.path.join(os.path.dirname(__file__), ".test_token")
with open(token_path, "w", encoding="utf-8") as fh:
    fh.write(token)
print(f"Token saved to {token_path} (valid ~1 hour).")
```

- [ ] **Step 3: Run it with a personal account**

Run: `python -m pip install msal PyJWT && python backend/dev-scripts/get_test_token.py`
Open the printed URL, enter the code, and sign in with a **personal** Microsoft account (outlook.com / hotmail.com).
Expected claims: `"ver": "2.0"`, `"aud"` equal to your client ID, `"iss": "https://login.microsoftonline.com/9188040d-6c67-4c5b-b112-36a304b66dad/v2.0"`, `"tid": "9188040d-6c67-4c5b-b112-36a304b66dad"`, `"scp": "access_as_user"`, and a non-empty `"oid"`.
If `ver` is `1.0` or `aud` is `api://…`, fix step 1.4 and try again. If sign-in is refused, stop and report: the design depends on this.

- [ ] **Step 4: Turn public client flows off again**

Entra → Authentication → Allow public client flows → **No** → Save. (Re-enable it briefly whenever you need a fresh `.test_token`.)

No commit: nothing tracked changed.

---

### Task 2: Backend test harness, startup smoke test, CI gate

**Files:**
- Create: `pytest.ini`, `backend/requirements-dev.txt`, `backend/tests/__init__.py` (empty), `backend/tests/conftest.py`, `backend/tests/test_startup.py`
- Modify: `.github/workflows/deploy.yml`

**Interfaces:**
- Produces: the pytest configuration every later task uses; the `client` fixture (`fastapi.testclient.TestClient` for `main.app`), extended in Task 6.

- [ ] **Step 1: Add the test config and dev requirements**

`pytest.ini` (project root):

```ini
[pytest]
testpaths = backend/tests
pythonpath = backend
addopts = -q
```

`backend/requirements-dev.txt`:

```text
pytest>=9.1
# Starlette 1.x's TestClient prefers httpx2 (plain httpx still works but is deprecated there)
httpx2>=2.13
```

- [ ] **Step 2: Write `backend/tests/conftest.py`**

```python
import os

# Tests must never reach real services. python-dotenv does not override variables that are
# already set, so fixing them here (before any backend module loads .env) keeps tests offline.
os.environ["DATABASE_URL"] = "postgresql://tests-must-not-use-the-real-db@127.0.0.1:1/none"
os.environ["QDRANT_URL"] = "http://127.0.0.1:1"
os.environ["QDRANT_API_KEY"] = ""
os.environ["NVIDIA_API_KEY"] = "test-key"
os.environ["NVIDIA_EMBED_API_KEY"] = "test-key"
os.environ["AZURE_CLIENT_ID"] = "test-client-id"
os.environ["IP_HASH_SALT"] = "test-salt"
os.environ["GENERATE_LIMIT_TRIAL"] = "3"
os.environ["GENERATE_LIMIT_VERIFIED"] = "25"
os.environ["UPLOAD_LIMIT_ANON"] = "5"
os.environ["UPLOAD_LIMIT_STUDENT"] = "20"
os.environ["UPLOAD_LIMIT_FACULTY"] = "10"
os.environ["SYLLABUS_LIMIT"] = "10"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client():
    from main import app

    # Not used as a context manager, so the lifespan (init_db against Postgres) never runs.
    yield TestClient(app)
    app.dependency_overrides.clear()
```

- [ ] **Step 3: Write the failing startup test**

`backend/tests/test_startup.py`:

```python
EXPECTED_ROUTES = {
    ("POST", "/api/v1/documents/ingest"),
    ("POST", "/api/v1/syllabus/upload"),
    ("GET", "/api/v1/syllabus/{subject_code}"),
    ("GET", "/api/v1/documents"),
    ("GET", "/api/v1/documents/{paper_id}/download"),
    ("POST", "/api/v1/search/semantic"),
    ("POST", "/api/v1/generate/mock-paper"),
    ("POST", "/api/v1/generate/mock-paper-stream"),
    ("GET", "/health"),
}


def registered_routes(app):
    return {(method, route.path) for route in app.routes for method in getattr(route, "methods", set())}


def test_app_imports_and_health_is_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_all_expected_routes_are_registered(client):
    missing = EXPECTED_ROUTES - registered_routes(client.app)
    assert not missing, f"missing routes: {sorted(missing)}"
```

- [ ] **Step 4: Run the tests**

Run: `python -m pip install -r backend/requirements-dev.txt && python -m pytest backend/tests/test_startup.py -v`
Expected: 2 passed. This pins the current behaviour. To prove it catches import breaks, temporarily change `from api.routes import router` in `backend/main.py` to `from backend.api.routes import router`, re-run, see `ModuleNotFoundError`, and revert.

- [ ] **Step 5: Gate the image build on the tests in CI**

In `.github/workflows/deploy.yml`, add a `test` job above `build-and-push` and make the build depend on it:

```yaml
jobs:
  test:
    name: Backend tests
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
          cache-dependency-path: |
            backend/requirements.txt
            backend/requirements-dev.txt

      - name: Install dependencies
        run: python -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt

      - name: Run tests
        run: python -m pytest

  build-and-push:
    name: Build Docker Image & Push to GHCR
    needs: test
    runs-on: ubuntu-latest
```

Keep the rest of `build-and-push` (permissions and steps) exactly as it is.

- [ ] **Step 6: Commit**

```bash
git add pytest.ini backend/requirements-dev.txt backend/tests/__init__.py backend/tests/conftest.py backend/tests/test_startup.py .github/workflows/deploy.yml
git commit -m "test: add backend test harness, startup smoke test and CI test gate

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Error handling foundation

**Files:**
- Create: `backend/errors.py`, `backend/tests/test_errors.py`
- Modify: `backend/main.py`

**Interfaces:**
- Produces: `errors.ApiError(status_code: int, code: str, detail: str, headers: dict[str, str] | None = None)`, `errors.GENERIC_ERROR_MESSAGE: str`, `errors.install_error_handling(app: FastAPI) -> None` (must be called **before** `app.add_middleware(CORSMiddleware, …)`).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_errors.py`:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from errors import GENERIC_ERROR_MESSAGE, ApiError, install_error_handling

ORIGIN = "http://localhost:3000"


def make_app():
    app = FastAPI()
    install_error_handling(app)
    app.add_middleware(CORSMiddleware, allow_origins=[ORIGIN], allow_methods=["*"], allow_headers=["*"])

    @app.get("/boom")
    def boom():
        raise RuntimeError("password=hunter2 leaked from the database driver")

    @app.get("/limited")
    def limited():
        raise ApiError(429, "quota_exceeded", "Daily limit reached.", headers={"Retry-After": "120"})

    return app


def test_unexpected_error_returns_generic_body_with_cors_headers():
    response = TestClient(make_app()).get("/boom", headers={"Origin": ORIGIN})
    assert response.status_code == 500
    assert response.json() == {"detail": GENERIC_ERROR_MESSAGE, "code": "internal_error"}
    assert "hunter2" not in response.text
    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_api_error_uses_the_standard_shape_and_keeps_headers():
    response = TestClient(make_app()).get("/limited", headers={"Origin": ORIGIN})
    assert response.status_code == 429
    assert response.json() == {"detail": "Daily limit reached.", "code": "quota_exceeded"}
    assert response.headers["retry-after"] == "120"
    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_main_app_exposes_retry_after_to_the_browser(client):
    response = client.get("/health", headers={"Origin": ORIGIN})
    assert "retry-after" in response.headers.get("access-control-expose-headers", "").lower()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/tests/test_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'errors'`.

- [ ] **Step 3: Implement `backend/errors.py`**

```python
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

GENERIC_ERROR_MESSAGE = "Something went wrong. Please try again."


class ApiError(Exception):
    """An error the client is allowed to see: returned as {"detail": ..., "code": ...}."""

    def __init__(self, status_code: int, code: str, detail: str, headers: dict[str, str] | None = None):
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.headers = headers


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status_code, headers=exc.headers)


class CatchAllErrorsMiddleware:
    """Turns unhandled exceptions into a generic JSON 500.

    Installed inside CORSMiddleware so error responses still carry CORS headers
    (Starlette's own 500 handler sits outside CORS, so browsers couldn't read it).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_wrapper(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            logger.exception("Unhandled error on %s %s", scope.get("method"), scope.get("path"))
            if response_started:
                raise
            response = JSONResponse({"detail": GENERIC_ERROR_MESSAGE, "code": "internal_error"}, status_code=500)
            await response(scope, receive, send)


def install_error_handling(app: FastAPI) -> None:
    """Call before adding CORSMiddleware: the last middleware added is the outermost."""
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_middleware(CatchAllErrorsMiddleware)
```

- [ ] **Step 4: Wire it into `backend/main.py`**

Replace the whole file with:

```python
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router as api_router
from errors import install_error_handling

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="PrepWise API",
    description="Backend for the PrepWise past-paper repository and mock paper generator",
    version="0.1.0"
)

# Must come before CORS so that error responses still get CORS headers
install_error_handling(app)

# CORS (Allow Frontend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://prepwise-opal-three.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

app.include_router(api_router, prefix="/api/v1")

@app.get("/")
def read_root():
    return {"message": "PrepWise API is running"}

@app.get("/health")
def health_check():
    return {"status": "ok"}
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest -v`
Expected: all tests pass (startup + errors).

- [ ] **Step 6: Commit**

```bash
git add backend/errors.py backend/main.py backend/tests/test_errors.py
git commit -m "feat: generic JSON errors that keep CORS headers, logging setup

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Microsoft access-token verification

**Files:**
- Create: `backend/auth/__init__.py` (empty), `backend/auth/tokens.py`, `backend/tests/test_tokens.py`
- Modify: `backend/requirements.txt`, `backend/tests/conftest.py`

**Interfaces:**
- Produces: `auth.tokens.Claims(tid: str, oid: str, name: str | None, email: str | None)` (frozen dataclass); `auth.tokens.TokenError(Exception)`; `auth.tokens.verify_access_token(token: str) -> Claims`; `auth.tokens._get_jwks_client()` (patched in tests). Test fixtures: `rsa_keys` (session), `make_token(**overrides) -> str`, and `TEST_TID` in `conftest.py`.

- [ ] **Step 1: Add the dependency**

Append to `backend/requirements.txt`:

```text
# Auth
PyJWT[crypto]>=2.15
```

Run: `python -m pip install -r backend/requirements.txt`

- [ ] **Step 2: Add token fixtures to `backend/tests/conftest.py`**

Append:

```python
import time  # noqa: E402

import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

TEST_TID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(scope="session")
def rsa_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


class _FakeJwksClient:
    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return _FakeSigningKey(self.public_key)


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch, rsa_keys):
    """Every test validates tokens against the test key pair instead of Microsoft's keys."""
    from auth import tokens

    monkeypatch.setattr(tokens, "_get_jwks_client", lambda: _FakeJwksClient(rsa_keys[1]))


@pytest.fixture
def make_token(rsa_keys):
    def _make(**overrides):
        now = int(time.time())
        claims = {
            "aud": "test-client-id",
            "iss": f"https://login.microsoftonline.com/{TEST_TID}/v2.0",
            "tid": TEST_TID,
            "oid": "oid-1",
            "sub": "sub-1",
            "scp": "access_as_user",
            "name": "Test User",
            "preferred_username": "test@example.com",
            "ver": "2.0",
            "iat": now,
            "nbf": now,
            "exp": now + 3600,
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, rsa_keys[0], algorithm="RS256", headers={"kid": "test-key"})

    return _make
```

- [ ] **Step 3: Write the failing tests**

`backend/tests/test_tokens.py`:

```python
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from auth.tokens import Claims, TokenError, verify_access_token
from tests.conftest import TEST_TID

PERSONAL_TID = "9188040d-6c67-4c5b-b112-36a304b66dad"


def test_valid_token_returns_claims(make_token):
    claims = verify_access_token(make_token())
    assert claims == Claims(tid=TEST_TID, oid="oid-1", name="Test User", email="test@example.com")


def test_personal_account_token_is_accepted(make_token):
    token = make_token(tid=PERSONAL_TID, iss=f"https://login.microsoftonline.com/{PERSONAL_TID}/v2.0")
    assert verify_access_token(token).tid == PERSONAL_TID


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "someone-elses-app"},
        {"iss": "https://login.microsoftonline.com/22222222-2222-2222-2222-222222222222/v2.0"},
        {"iss": "https://sts.windows.net/11111111-1111-1111-1111-111111111111/"},
        {"exp": int(time.time()) - 120},
        {"scp": "User.Read"},
        {"scp": None},
        {"oid": None},
        {"tid": None},
    ],
    ids=["wrong-audience", "issuer-other-tenant", "v1-issuer", "expired", "wrong-scope", "no-scope", "no-oid", "no-tid"],
)
def test_invalid_claims_are_rejected(make_token, overrides):
    with pytest.raises(TokenError):
        verify_access_token(make_token(**overrides))


def test_token_signed_with_another_key_is_rejected():
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    forged = jwt.encode(
        {"aud": "test-client-id", "iss": f"https://login.microsoftonline.com/{TEST_TID}/v2.0", "tid": TEST_TID,
         "oid": "oid-1", "sub": "s", "scp": "access_as_user", "iat": now, "nbf": now, "exp": now + 3600},
        other_key, algorithm="RS256",
    )
    with pytest.raises(TokenError):
        verify_access_token(forged)


def test_malformed_token_is_rejected():
    with pytest.raises(TokenError):
        verify_access_token("not-a-jwt")


def test_missing_client_id_configuration_is_rejected(make_token, monkeypatch):
    monkeypatch.delenv("AZURE_CLIENT_ID")
    with pytest.raises(TokenError):
        verify_access_token(make_token())
```

- [ ] **Step 4: Run to verify they fail**

Run: `python -m pytest backend/tests/test_tokens.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'auth'`.

- [ ] **Step 5: Implement `backend/auth/tokens.py`**

Create an empty `backend/auth/__init__.py`, then:

```python
import os
from dataclasses import dataclass

import jwt
from jwt import PyJWKClient

JWKS_URL = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
REQUIRED_SCOPE = "access_as_user"
LEEWAY_SECONDS = 60


class TokenError(Exception):
    """The access token is missing required properties or failed validation."""


@dataclass(frozen=True)
class Claims:
    tid: str
    oid: str
    name: str | None  # display only, never used for authorization
    email: str | None  # display only, never used for authorization


_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        # Keys are cached; an unknown key id triggers a refetch.
        _jwks_client = PyJWKClient(JWKS_URL, cache_keys=True, lifespan=3600)
    return _jwks_client


def verify_access_token(token: str) -> Claims:
    """Validates a Microsoft identity platform v2 access token issued for this API."""
    client_id = os.getenv("AZURE_CLIENT_ID")
    if not client_id:
        raise TokenError("AZURE_CLIENT_ID is not configured")
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        # Multi-tenant app: the expected issuer depends on the tenant the token names.
        tid = jwt.decode(token, options={"verify_signature": False}).get("tid")
        if not tid:
            raise TokenError("token has no tenant id")
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=f"https://login.microsoftonline.com/{tid}/v2.0",
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except TokenError:
        raise
    except jwt.PyJWTError as error:
        raise TokenError(str(error)) from error

    if REQUIRED_SCOPE not in (payload.get("scp") or "").split():
        raise TokenError(f"token lacks the {REQUIRED_SCOPE} scope")
    oid = payload.get("oid")
    if not oid:
        raise TokenError("token has no object id")
    return Claims(
        tid=tid,
        oid=oid,
        name=payload.get("name"),
        email=payload.get("preferred_username") or payload.get("email"),
    )
```

- [ ] **Step 6: Run the tests**

Run: `python -m pytest -v`
Expected: all pass.

- [ ] **Step 7: Check against the real token from Task 1**

Run:

```bash
python -c "import sys; sys.path.insert(0,'backend'); import database; from auth.tokens import verify_access_token; print(verify_access_token(open('backend/dev-scripts/.test_token').read().strip()))"
```

Expected: `Claims(tid='9188040d-…', oid='…', …)`. If the token has expired (after ~1 hour), re-run Task 1 steps 1.8 and 3–4 first.

- [ ] **Step 8: Commit**

```bash
git add backend/auth/__init__.py backend/auth/tokens.py backend/tests/test_tokens.py backend/tests/conftest.py backend/requirements.txt
git commit -m "feat: verify Microsoft access tokens for the PrepWise API

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Schema and Postgres user store

**Files:**
- Create: `backend/auth/users.py`, `backend/tests/fakes.py`, `backend/tests/test_users_postgres.py`
- Modify: `backend/database.py`, `backend/main.py`

**Interfaces:**
- Consumes: `auth.tokens.Claims`.
- Produces:
  - `database.db_cursor()`: a context manager yielding a cursor that commits on success, rolls back on error and always closes.
  - `database.init_db() -> None` (idempotent).
  - `auth.users.ROLES = ("student", "faculty", "admin")`, `auth.users.ACTIONS = ("generate", "upload", "syllabus")`.
  - `auth.users.User(id: int, ms_tid: str, ms_oid: str, email: str | None, name: str | None, role: str | None, verified: bool, created_at: datetime | None = None)` (frozen).
  - `auth.users.user_summary(user: User, used_today: dict[str, int]) -> dict` with keys `id, email, name, role, verified, createdAt, usedToday`.
  - `auth.users.PostgresUserStore` methods: `get_or_create(claims) -> User`, `get(user_id) -> User | None`, `touch(user_id) -> None`, `set_role_once(user_id, role) -> bool`, `update(user_id, role=None, verified=None) -> User | None`, `find_by_email(email) -> list[User]`, `list_with_usage() -> list[dict]`, `consume_quota(subject, action, limit) -> bool`, `refund_quota(subject, action) -> None`, `usage_today(subject) -> dict[str, int]`.
  - `tests.fakes.FakeUserStore`, with the same methods backed by dicts.

- [ ] **Step 1: Add `db_cursor` and the new schema to `backend/database.py`**

Replace the whole `init_db()` function, and add `db_cursor()` above it. The imports at the top gain `from contextlib import contextmanager`:

```python
from contextlib import contextmanager
```

```python
@contextmanager
def db_cursor():
    """Yields a cursor; commits on success, rolls back on error, always closes the connection."""
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                yield cur
    finally:
        conn.close()


def init_db():
    """Creates or migrates all tables. Safe to run repeatedly (runs at app startup)."""
    with db_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS papers (
                id SERIAL PRIMARY KEY,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                subject_code TEXT,
                subject_name TEXT,
                semester TEXT,
                year TEXT,
                time TEXT,
                marks TEXT,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS syllabi (
                id SERIAL PRIMARY KEY,
                subject_code TEXT UNIQUE NOT NULL,
                subject_name TEXT,
                modules JSONB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            SERIAL PRIMARY KEY,
                ms_tid        TEXT NOT NULL,
                ms_oid        TEXT NOT NULL,
                email         TEXT,
                name          TEXT,
                role          TEXT CHECK (role IN ('student', 'faculty', 'admin')),
                verified      BOOLEAN NOT NULL DEFAULT FALSE,
                created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at  TIMESTAMP,
                UNIQUE (ms_tid, ms_oid)
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usage_daily (
                subject  TEXT NOT NULL,
                action   TEXT NOT NULL CHECK (action IN ('generate', 'upload', 'syllabus')),
                day      DATE NOT NULL,
                count    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (subject, action, day)
            );
        """)
        cur.execute("ALTER TABLE papers ADD COLUMN IF NOT EXISTS uploaded_by INTEGER REFERENCES users(id);")
        cur.execute("ALTER TABLE syllabi ADD COLUMN IF NOT EXISTS uploaded_by INTEGER REFERENCES users(id);")
```

- [ ] **Step 2: Implement `backend/auth/users.py`**

```python
from dataclasses import dataclass
from datetime import datetime

from auth.tokens import Claims
from database import db_cursor

ROLES = ("student", "faculty", "admin")
ACTIONS = ("generate", "upload", "syllabus")

_USER_COLUMNS = "id, ms_tid, ms_oid, email, name, role, verified, created_at"
_TODAY = "(now() AT TIME ZONE 'UTC')::date"


@dataclass(frozen=True)
class User:
    id: int
    ms_tid: str
    ms_oid: str
    email: str | None
    name: str | None
    role: str | None
    verified: bool
    created_at: datetime | None = None


def user_summary(user: User, used_today: dict[str, int]) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "verified": user.verified,
        "createdAt": user.created_at.isoformat() if user.created_at else None,
        "usedToday": used_today,
    }


class PostgresUserStore:
    """Users and daily usage counters in Postgres."""

    def get_or_create(self, claims: Claims) -> User:
        select = f"SELECT {_USER_COLUMNS} FROM users WHERE ms_tid = %s AND ms_oid = %s"
        with db_cursor() as cur:
            cur.execute(select, (claims.tid, claims.oid))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO users (ms_tid, ms_oid, email, name) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (ms_tid, ms_oid) DO NOTHING",
                    (claims.tid, claims.oid, claims.email, claims.name),
                )
                cur.execute(select, (claims.tid, claims.oid))
                row = cur.fetchone()
        return User(*row)

    def get(self, user_id: int) -> User | None:
        with db_cursor() as cur:
            cur.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
        return User(*row) if row else None

    def touch(self, user_id: int) -> None:
        with db_cursor() as cur:
            cur.execute("UPDATE users SET last_seen_at = CURRENT_TIMESTAMP WHERE id = %s", (user_id,))

    def set_role_once(self, user_id: int, role: str) -> bool:
        with db_cursor() as cur:
            cur.execute("UPDATE users SET role = %s WHERE id = %s AND role IS NULL RETURNING id", (role, user_id))
            return cur.fetchone() is not None

    def update(self, user_id: int, role: str | None = None, verified: bool | None = None) -> User | None:
        assignments, params = [], []
        if role is not None:
            assignments.append("role = %s")
            params.append(role)
        if verified is not None:
            assignments.append("verified = %s")
            params.append(verified)
        if not assignments:
            return self.get(user_id)
        with db_cursor() as cur:
            cur.execute(
                f"UPDATE users SET {', '.join(assignments)} WHERE id = %s RETURNING {_USER_COLUMNS}",
                (*params, user_id),
            )
            row = cur.fetchone()
        return User(*row) if row else None

    def find_by_email(self, email: str) -> list[User]:
        with db_cursor() as cur:
            cur.execute(
                f"SELECT {_USER_COLUMNS} FROM users WHERE lower(email) = lower(%s) ORDER BY created_at",
                (email,),
            )
            return [User(*row) for row in cur.fetchall()]

    def list_with_usage(self) -> list[dict]:
        with db_cursor() as cur:
            cur.execute(f"""
                SELECT u.id, u.ms_tid, u.ms_oid, u.email, u.name, u.role, u.verified, u.created_at,
                       COALESCE(SUM(d.count) FILTER (WHERE d.action = 'generate'), 0),
                       COALESCE(SUM(d.count) FILTER (WHERE d.action = 'upload'), 0),
                       COALESCE(SUM(d.count) FILTER (WHERE d.action = 'syllabus'), 0)
                FROM users u
                LEFT JOIN usage_daily d ON d.subject = 'user:' || u.id AND d.day = {_TODAY}
                GROUP BY u.id
                ORDER BY u.created_at DESC
            """)
            rows = cur.fetchall()
        return [
            user_summary(User(*row[:8]), {"generate": int(row[8]), "upload": int(row[9]), "syllabus": int(row[10])})
            for row in rows
        ]

    def consume_quota(self, subject: str, action: str, limit: int) -> bool:
        """Atomically counts one use; False (and nothing counted) once the limit is reached."""
        with db_cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO usage_daily (subject, action, day, count)
                VALUES (%(subject)s, %(action)s, {_TODAY}, 1)
                ON CONFLICT (subject, action, day)
                DO UPDATE SET count = usage_daily.count + 1
                WHERE usage_daily.count < %(limit)s
                RETURNING count
                """,
                {"subject": subject, "action": action, "limit": limit},
            )
            return cur.fetchone() is not None

    def refund_quota(self, subject: str, action: str) -> None:
        with db_cursor() as cur:
            cur.execute(
                f"UPDATE usage_daily SET count = count - 1 "
                f"WHERE subject = %s AND action = %s AND day = {_TODAY} AND count > 0",
                (subject, action),
            )

    def usage_today(self, subject: str) -> dict[str, int]:
        used = {action: 0 for action in ACTIONS}
        with db_cursor() as cur:
            cur.execute(f"SELECT action, count FROM usage_daily WHERE subject = %s AND day = {_TODAY}", (subject,))
            for action, count in cur.fetchall():
                used[action] = count
        return used
```

- [ ] **Step 3: Implement `backend/tests/fakes.py` (the in-memory store used by every endpoint test)**

```python
from dataclasses import replace
from datetime import datetime, timezone

from auth.tokens import Claims
from auth.users import ACTIONS, User, user_summary


class FakeUserStore:
    """In-memory stand-in for PostgresUserStore with the same method contract."""

    def __init__(self):
        self.users: dict[int, User] = {}
        self._ids: dict[tuple[str, str], int] = {}
        self.usage: dict[tuple[str, str], int] = {}
        self.touched: list[int] = []
        self._next_id = 1

    def get_or_create(self, claims: Claims) -> User:
        key = (claims.tid, claims.oid)
        if key not in self._ids:
            user = User(
                id=self._next_id, ms_tid=claims.tid, ms_oid=claims.oid, email=claims.email, name=claims.name,
                role=None, verified=False, created_at=datetime.now(timezone.utc),
            )
            self.users[user.id] = user
            self._ids[key] = user.id
            self._next_id += 1
        return self.users[self._ids[key]]

    def get(self, user_id):
        return self.users.get(user_id)

    def touch(self, user_id):
        self.touched.append(user_id)

    def set_role_once(self, user_id, role):
        user = self.users[user_id]
        if user.role is not None:
            return False
        self.users[user_id] = replace(user, role=role)
        return True

    def update(self, user_id, role=None, verified=None):
        user = self.users.get(user_id)
        if user is None:
            return None
        if role is not None:
            user = replace(user, role=role)
        if verified is not None:
            user = replace(user, verified=verified)
        self.users[user_id] = user
        return user

    def find_by_email(self, email):
        return [u for u in self.users.values() if (u.email or "").lower() == email.lower()]

    def list_with_usage(self):
        return [user_summary(u, self.usage_today(f"user:{u.id}")) for u in self.users.values()]

    def consume_quota(self, subject, action, limit):
        used = self.usage.get((subject, action), 0)
        if used >= limit:
            return False
        self.usage[(subject, action)] = used + 1
        return True

    def refund_quota(self, subject, action):
        used = self.usage.get((subject, action), 0)
        if used > 0:
            self.usage[(subject, action)] = used - 1

    def usage_today(self, subject):
        return {action: self.usage.get((subject, action), 0) for action in ACTIONS}
```

- [ ] **Step 4: Write the Postgres integration tests (skipped unless `TEST_DATABASE_URL` is set)**

`backend/tests/test_users_postgres.py`:

```python
"""Runs the real SQL. Point TEST_DATABASE_URL at a throwaway database (e.g. a Neon branch).

Never point it at production: the tests create tables and write rows (they only delete their own).
"""
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

import database  # noqa: F401  (loads .env, which may define TEST_DATABASE_URL)
from auth.tokens import Claims
from auth.users import PostgresUserStore

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")
TENANT = "pytest-tenant"


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    from database import db_cursor, init_db

    init_db()
    with db_cursor() as cur:
        cur.execute("DELETE FROM usage_daily WHERE subject LIKE 'pytest:%%'")
        cur.execute("DELETE FROM users WHERE ms_tid = %s", (TENANT,))
    return PostgresUserStore()


def test_get_or_create_is_idempotent(store):
    first = store.get_or_create(Claims(tid=TENANT, oid="o1", name="A", email="a@example.com"))
    second = store.get_or_create(Claims(tid=TENANT, oid="o1", name="A", email="a@example.com"))
    assert first.id == second.id
    assert first.role is None and first.verified is False


def test_role_can_only_be_chosen_once(store):
    user = store.get_or_create(Claims(tid=TENANT, oid="o2", name=None, email=None))
    assert store.set_role_once(user.id, "student") is True
    assert store.set_role_once(user.id, "faculty") is False
    assert store.get(user.id).role == "student"


def test_quota_stops_at_limit_and_refund_gives_one_back(store):
    assert [store.consume_quota("pytest:q", "generate", 2) for _ in range(3)] == [True, True, False]
    store.refund_quota("pytest:q", "generate")
    assert store.consume_quota("pytest:q", "generate", 2) is True
    assert store.usage_today("pytest:q")["generate"] == 2


def test_concurrent_consume_never_exceeds_limit(store):
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: store.consume_quota("pytest:race", "generate", 3), range(10)))
    assert results.count(True) == 3
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest -v`
Expected: all earlier tests pass; `test_users_postgres.py` shows 4 skipped. If you have a throwaway database, run with `TEST_DATABASE_URL=<url> python -m pytest backend/tests/test_users_postgres.py -v`. Expected: 4 passed.

- [ ] **Step 6: Create the tables at app startup**

In `backend/main.py`, add these imports:

```python
from contextlib import asynccontextmanager

from database import init_db
```

Below `logging.basicConfig(...)`, add:

```python
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
    except Exception:
        logger.exception("Database initialisation failed; continuing so the API can still start")
    yield
```

and pass it to the app: `app = FastAPI(title=..., description=..., version="0.1.0", lifespan=lifespan)`.

- [ ] **Step 7: Run everything, then commit**

Run: `python -m pytest -v`. Expected: pass (the lifespan doesn't run in tests).

```bash
git add backend/database.py backend/auth/users.py backend/tests/fakes.py backend/tests/test_users_postgres.py backend/main.py
git commit -m "feat: users and daily usage tables with an atomic quota store

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Limits and auth dependencies

**Files:**
- Create: `backend/auth/limits.py`, `backend/auth/deps.py`, `backend/tests/test_limits_and_deps.py`
- Modify: `backend/tests/conftest.py`

**Interfaces:**
- Consumes: `verify_access_token`, `TokenError`, `User`, `PostgresUserStore`, `ApiError`.
- Produces:
  - `auth.limits.generate_limit(user: User) -> int | None`, `upload_limit(user: User | None) -> int | None`, `syllabus_limit(user: User) -> int | None`, `limits_for(user: User) -> dict[str, int | None]`, `seconds_until_utc_midnight(now: datetime | None = None) -> int`. `None` means unlimited; `0` means not allowed.
  - `auth.deps.get_user_store() -> PostgresUserStore`, `optional_user` (dependency, `User | None`), `current_user` (dependency, `User`), `require_role(*roles: str)` (dependency factory → `User`), `user_subject(user: User) -> str`, `ip_subject(request: Request) -> str`, `quota_subject(user: User | None, request: Request) -> str`, `enforce_quota(store, subject: str, action: str, limit: int | None) -> None`, `refund_quota_if_counted(store, subject: str, action: str, limit: int | None) -> None`.
  - Test fixtures: `store` (`FakeUserStore`, installed as the app's user store) and `auth_headers(role=None, verified=False) -> dict`.

- [ ] **Step 1: Add fixtures to `backend/tests/conftest.py`**

Append:

```python
import uuid  # noqa: E402


@pytest.fixture
def store(client):
    from auth.deps import get_user_store
    from tests.fakes import FakeUserStore

    fake = FakeUserStore()
    client.app.dependency_overrides[get_user_store] = lambda: fake
    return fake


@pytest.fixture
def auth_headers(store, make_token):
    """Returns Authorization headers for a new user with the given role (None = hasn't picked yet)."""
    from auth.tokens import Claims

    def _headers(role=None, verified=False):
        oid = f"oid-{uuid.uuid4()}"
        user = store.get_or_create(Claims(tid=TEST_TID, oid=oid, name="Test User", email=f"{oid}@example.com"))
        store.update(user.id, role=role, verified=verified)
        return {"Authorization": f"Bearer {make_token(oid=oid)}"}

    return _headers
```

- [ ] **Step 2: Write the failing tests**

`backend/tests/test_limits_and_deps.py`:

```python
from datetime import datetime, timezone

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from auth import limits
from auth.deps import (current_user, enforce_quota, get_user_store, ip_subject, optional_user,
                       quota_subject, require_role)
from auth.users import User
from errors import ApiError, install_error_handling
from tests.fakes import FakeUserStore


def user(role, verified=False):
    return User(id=7, ms_tid="t", ms_oid="o", email=None, name=None, role=role, verified=verified)


@pytest.mark.parametrize("role,verified,expected", [
    ("admin", False, None), ("faculty", True, 25), ("faculty", False, 3), ("student", False, 0), (None, False, 0),
])
def test_generate_limit(role, verified, expected):
    assert limits.generate_limit(user(role, verified)) == expected


@pytest.mark.parametrize("u,expected", [
    (None, 5), (user("admin"), None), (user("student"), 20), (user("faculty", True), 10),
    (user("faculty", False), 0), (user(None), 0),
])
def test_upload_limit(u, expected):
    assert limits.upload_limit(u) == expected


@pytest.mark.parametrize("role,verified,expected", [
    ("admin", False, None), ("faculty", True, 10), ("faculty", False, 0), ("student", False, 0),
])
def test_syllabus_limit(role, verified, expected):
    assert limits.syllabus_limit(user(role, verified)) == expected


def test_limits_follow_environment(monkeypatch):
    monkeypatch.setenv("GENERATE_LIMIT_TRIAL", "7")
    assert limits.generate_limit(user("faculty")) == 7


def test_seconds_until_utc_midnight():
    assert limits.seconds_until_utc_midnight(datetime(2026, 9, 25, 23, 59, 0, tzinfo=timezone.utc)) == 60
    assert limits.seconds_until_utc_midnight(datetime(2026, 9, 25, 0, 0, 0, tzinfo=timezone.utc)) == 86400


@pytest.fixture
def probe(make_token):
    """A tiny app exposing each dependency, sharing one fake store."""
    app = FastAPI()
    install_error_handling(app)
    fake = FakeUserStore()
    app.dependency_overrides[get_user_store] = lambda: fake

    @app.get("/optional")
    def optional(u=Depends(optional_user)):
        return {"id": u.id if u else None}

    @app.get("/current")
    def current(u=Depends(current_user)):
        return {"id": u.id}

    @app.get("/faculty")
    def faculty(u=Depends(require_role("faculty", "admin"))):
        return {"role": u.role}

    @app.get("/subject")
    def subject(request: Request, u=Depends(optional_user)):
        return {"subject": quota_subject(u, request)}

    return TestClient(app), fake, make_token


def test_no_token_is_anonymous_for_optional_and_401_for_current(probe):
    client, _, _ = probe
    assert client.get("/optional").json() == {"id": None}
    response = client.get("/current")
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"
    assert response.headers["www-authenticate"] == "Bearer"


def test_bad_token_is_401_even_where_login_is_optional(probe):
    client, _, make_token = probe
    response = client.get("/optional", headers={"Authorization": f"Bearer {make_token(aud='other')}"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_first_request_creates_the_user(probe):
    client, fake, make_token = probe
    response = client.get("/current", headers={"Authorization": f"Bearer {make_token(oid='new-person')}"})
    assert response.status_code == 200
    assert len(fake.users) == 1


def test_require_role_distinguishes_no_role_from_wrong_role(probe):
    client, fake, make_token = probe
    headers = {"Authorization": f"Bearer {make_token(oid='p1')}"}
    assert client.get("/faculty", headers=headers).json()["code"] == "role_required"
    fake.update(1, role="student")
    response = client.get("/faculty", headers=headers)
    assert response.status_code == 403 and response.json()["code"] == "forbidden"
    fake.update(1, role="faculty")
    assert client.get("/faculty", headers=headers).status_code == 200


def test_anonymous_subject_is_a_salted_hash_not_the_ip(probe):
    client, _, _ = probe
    subject = client.get("/subject").json()["subject"]
    assert subject.startswith("ip:") and len(subject) == 3 + 64
    assert "testclient" not in subject


def test_ip_subject_differs_per_address():
    def request_from(host):
        return Request({"type": "http", "client": (host, 1), "headers": []})

    assert ip_subject(request_from("203.0.113.1")) != ip_subject(request_from("203.0.113.2"))


def test_enforce_quota_raises_429_with_retry_after():
    fake = FakeUserStore()
    enforce_quota(fake, "user:1", "generate", 1)
    with pytest.raises(ApiError) as caught:
        enforce_quota(fake, "user:1", "generate", 1)
    assert caught.value.status_code == 429
    assert caught.value.code == "quota_exceeded"
    assert int(caught.value.headers["Retry-After"]) > 0


def test_enforce_quota_skips_unlimited_and_refuses_zero():
    fake = FakeUserStore()
    for _ in range(50):
        enforce_quota(fake, "user:1", "generate", None)
    assert fake.usage == {}
    with pytest.raises(ApiError) as caught:
        enforce_quota(fake, "user:1", "generate", 0)
    assert caught.value.status_code == 403
```

- [ ] **Step 3: Run to verify they fail**

Run: `python -m pytest backend/tests/test_limits_and_deps.py -v`
Expected: FAIL with `ImportError: cannot import name 'limits' from 'auth'`.

- [ ] **Step 4: Implement `backend/auth/limits.py`**

```python
import os
from datetime import datetime, timedelta, timezone

from auth.users import User


def _limit(name: str, default: int) -> int:
    return int(os.getenv(name) or default)


# None = unlimited, 0 = not allowed. Values are read on every call so env changes apply.
def generate_limit(user: User) -> int | None:
    if user.role == "admin":
        return None
    if user.role == "faculty":
        return _limit("GENERATE_LIMIT_VERIFIED", 25) if user.verified else _limit("GENERATE_LIMIT_TRIAL", 3)
    return 0


def upload_limit(user: User | None) -> int | None:
    if user is None:
        return _limit("UPLOAD_LIMIT_ANON", 5)
    if user.role == "admin":
        return None
    if user.role == "student":
        return _limit("UPLOAD_LIMIT_STUDENT", 20)
    if user.role == "faculty" and user.verified:
        return _limit("UPLOAD_LIMIT_FACULTY", 10)
    return 0


def syllabus_limit(user: User) -> int | None:
    if user.role == "admin":
        return None
    if user.role == "faculty" and user.verified:
        return _limit("SYLLABUS_LIMIT", 10)
    return 0


def limits_for(user: User) -> dict[str, int | None]:
    return {"generate": generate_limit(user), "upload": upload_limit(user), "syllabus": syllabus_limit(user)}


def seconds_until_utc_midnight(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((next_midnight - now).total_seconds()))
```

- [ ] **Step 5: Implement `backend/auth/deps.py`**

```python
import hashlib
import logging
import os

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth.limits import seconds_until_utc_midnight
from auth.tokens import TokenError, verify_access_token
from auth.users import PostgresUserStore, User
from errors import ApiError

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)
_BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def get_user_store() -> PostgresUserStore:
    return PostgresUserStore()


def optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    store: PostgresUserStore = Depends(get_user_store),
) -> User | None:
    """The signed-in user, or None without a token. A token that fails validation is always a 401."""
    if credentials is None:
        return None
    try:
        claims = verify_access_token(credentials.credentials)
    except TokenError as error:
        logger.info("Rejected access token: %s", error)
        raise ApiError(401, "invalid_token", "Your sign-in has expired or is invalid. Please sign in again.",
                       headers=_BEARER_CHALLENGE)
    return store.get_or_create(claims)


def current_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise ApiError(401, "not_authenticated", "Please sign in to continue.", headers=_BEARER_CHALLENGE)
    return user


def require_role(*roles: str):
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role is None:
            raise ApiError(403, "role_required", "Choose Student or Faculty to continue.")
        if user.role not in roles:
            raise ApiError(403, "forbidden", "Your account can't do this.")
        return user

    return dependency


def user_subject(user: User) -> str:
    return f"user:{user.id}"


def ip_subject(request: Request) -> str:
    salt = os.getenv("IP_HASH_SALT")
    if not salt:
        raise RuntimeError("IP_HASH_SALT is not configured")
    ip = request.client.host if request.client else "unknown"
    return "ip:" + hashlib.sha256((salt + ip).encode()).hexdigest()


def quota_subject(user: User | None, request: Request) -> str:
    return user_subject(user) if user is not None else ip_subject(request)


def enforce_quota(store, subject: str, action: str, limit: int | None) -> None:
    """Counts one use of `action`. None = unlimited (not counted); callers reject 0 with a specific message."""
    if limit is None:
        return
    if limit <= 0:
        raise ApiError(403, "forbidden", "Your account can't do this.")
    if not store.consume_quota(subject, action, limit):
        raise ApiError(
            429, "quota_exceeded", f"You've reached today's limit of {limit}. It resets at midnight UTC.",
            headers={"Retry-After": str(seconds_until_utc_midnight())},
        )


def refund_quota_if_counted(store, subject: str, action: str, limit: int | None) -> None:
    if limit is not None:
        store.refund_quota(subject, action)
```

- [ ] **Step 5b: Make `limits.py` safe to import alongside `deps.py`**

`auth/limits.py` imports `auth.users` and `auth/deps.py` imports `auth.limits`. Neither imports the other back, so there's no cycle. Confirm with `python -c "import sys; sys.path.insert(0,'backend'); import auth.deps"`. Expected: no output.

- [ ] **Step 6: Run the tests**

Run: `python -m pytest -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/auth/limits.py backend/auth/deps.py backend/tests/test_limits_and_deps.py backend/tests/conftest.py
git commit -m "feat: role checks and atomic daily quotas as FastAPI dependencies

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `/me` endpoints

**Files:**
- Create: `backend/api/me.py`, `backend/tests/test_me.py`
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `current_user`, `get_user_store`, `user_subject`, `limits_for`, `ApiError`.
- Produces: `GET /api/v1/me` returns `{id, name, email, role, verified, limits: {generate, upload, syllabus}, usedToday: {generate, upload, syllabus}}` (limit `null` = unlimited). `POST /api/v1/me/role {"role": "student" | "faculty"}` returns the same shape.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_me.py`:

```python
def test_me_requires_sign_in(client, store):
    response = client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def test_first_call_creates_user_without_role(client, store, make_token):
    response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {make_token(oid='brand-new')}"})
    assert response.status_code == 200
    body = response.json()
    assert body["role"] is None
    assert body["verified"] is False
    assert body["limits"] == {"generate": 0, "upload": 0, "syllabus": 0}
    assert body["usedToday"] == {"generate": 0, "upload": 0, "syllabus": 0}
    assert store.touched == [body["id"]]


def test_trial_faculty_limits(client, auth_headers):
    body = client.get("/api/v1/me", headers=auth_headers(role="faculty")).json()
    assert body["limits"] == {"generate": 3, "upload": 0, "syllabus": 0}


def test_admin_limits_are_unlimited(client, auth_headers):
    body = client.get("/api/v1/me", headers=auth_headers(role="admin")).json()
    assert body["limits"] == {"generate": None, "upload": None, "syllabus": None}


def test_role_can_be_chosen_once(client, auth_headers):
    headers = auth_headers()
    first = client.post("/api/v1/me/role", json={"role": "student"}, headers=headers)
    assert first.status_code == 200
    assert first.json()["role"] == "student"
    assert first.json()["limits"]["upload"] == 20

    second = client.post("/api/v1/me/role", json={"role": "faculty"}, headers=headers)
    assert second.status_code == 409
    assert second.json()["code"] == "role_already_set"


def test_admin_cannot_be_self_assigned(client, auth_headers):
    response = client.post("/api/v1/me/role", json={"role": "admin"}, headers=auth_headers())
    assert response.status_code == 422
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/tests/test_me.py -v`
Expected: FAIL with 404 responses (route not found).

- [ ] **Step 3: Implement `backend/api/me.py`**

```python
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from auth.deps import current_user, get_user_store, user_subject
from auth.limits import limits_for
from auth.users import PostgresUserStore, User
from errors import ApiError

router = APIRouter()


class RoleChoice(BaseModel):
    role: Literal["student", "faculty"]


def me_payload(user: User, store: PostgresUserStore) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "verified": user.verified,
        "limits": limits_for(user),
        "usedToday": store.usage_today(user_subject(user)),
    }


@router.get("/me")
def get_me(user: User = Depends(current_user), store: PostgresUserStore = Depends(get_user_store)):
    store.touch(user.id)
    return me_payload(user, store)


@router.post("/me/role")
def choose_role(choice: RoleChoice, user: User = Depends(current_user),
                store: PostgresUserStore = Depends(get_user_store)):
    if not store.set_role_once(user.id, choice.role):
        raise ApiError(409, "role_already_set", "Your role is already set. Ask an admin if it needs to change.")
    return me_payload(store.get(user.id), store)
```

- [ ] **Step 4: Register the router in `backend/main.py`**

Add `from api.me import router as me_router` to the imports, and below `app.include_router(api_router, prefix="/api/v1")` add:

```python
app.include_router(me_router, prefix="/api/v1")
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest -v`
Expected: all pass.

- [ ] **Step 6: Manual check with the real token (optional but recommended)**

Run the API: `run_backend.bat` (or `uvicorn main:app --app-dir backend --port 8000`), then:

```bash
curl -s -H "Authorization: Bearer $(cat backend/dev-scripts/.test_token)" http://localhost:8000/api/v1/me
```

Expected: your user with `"role": null`. The `users` table now exists in the database, created by the lifespan.

- [ ] **Step 7: Commit**

```bash
git add backend/api/me.py backend/tests/test_me.py backend/main.py
git commit -m "feat: /me profile with limits and one-time role choice

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Protect generation and syllabus reads; clean up public-route errors

**Files:**
- Modify: `backend/api/routes.py`, `backend/tests/fakes.py`
- Create: `backend/tests/test_generate.py`, `backend/tests/test_public_routes.py`

**Interfaces:**
- Consumes: `require_role`, `get_user_store`, `user_subject`, `enforce_quota`, `refund_quota_if_counted`, `generate_limit`, `ApiError`, `db_cursor`.
- Produces: in `api.routes`, module-level names that tests monkeypatch: `RAGService`, `VectorService`, `get_syllabus_by_code`, `db_cursor`. Also `tests.fakes.FakeRag` and `tests.fakes.fake_db_cursor(rows)`.

- [ ] **Step 1: Add fakes to `backend/tests/fakes.py`**

Append:

```python
from contextlib import contextmanager


class FakeRag:
    """Stands in for RAGService. Set class attributes in a test to change behaviour."""

    papers = [{"filename": "SPM 2023.pdf", "ocrText": "Q1. Define risk."}]
    question_error = False
    raise_on_generate = False

    def _retrieve_similar_papers(self, subject, limit=3):
        return list(self.papers)

    def _extract_paper_context(self, papers):
        return "context"

    def _get_fuzzy_bloom_distribution(self, difficulty):
        return {}

    def _generate_question(self, q_config, context, subject, bloom_dist):
        if self.raise_on_generate:
            raise RuntimeError("LLM connection string postgres://secret")
        question = {"number": q_config.get("number", 1), "bloomLevel": "remember", "totalMarks": 6,
                    "parts": [], "rawGeneration": "Define risk.", "validation": {}}
        if self.question_error:
            question["error"] = True
        return question

    def generate_mock_paper(self, config):
        if not self.papers:
            return {"error": "No similar papers found in database"}
        return {"subject": config["subject"], "sections": [], "sourcePapers": ["SPM 2023.pdf"], "totalSections": 0}


@contextmanager
def fake_db_cursor(rows):
    """A db_cursor replacement whose fetchone/fetchall return the given rows."""

    class _Cursor:
        def execute(self, *args, **kwargs):
            pass

        def fetchone(self):
            return rows[0] if rows else None

        def fetchall(self):
            return rows

    yield _Cursor()
```

- [ ] **Step 2: Write the failing tests**

`backend/tests/test_generate.py`:

```python
import json

import pytest

from tests.fakes import FakeRag

BODY = {"subject": "Software Project Management",
        "sections": [{"name": "Section A", "questions": [{"number": 1, "bloomLevel": "remember", "totalMarks": 6, "parts": []}]}]}
STREAM = "/api/v1/generate/mock-paper-stream"
PLAIN = "/api/v1/generate/mock-paper"


@pytest.fixture(autouse=True)
def fake_rag(monkeypatch):
    class Rag(FakeRag):
        papers = list(FakeRag.papers)
        question_error = False
        raise_on_generate = False

    monkeypatch.setattr("api.routes.RAGService", Rag)
    return Rag


def events(response):
    return [json.loads(chunk[6:]) for chunk in response.text.split("\n\n") if chunk.startswith("data: ")]


@pytest.mark.parametrize("url", [STREAM, PLAIN])
def test_anonymous_gets_401(client, store, url):
    assert client.post(url, json=BODY).status_code == 401


@pytest.mark.parametrize("url", [STREAM, PLAIN])
def test_student_gets_403(client, auth_headers, url):
    response = client.post(url, json=BODY, headers=auth_headers(role="student"))
    assert response.status_code == 403 and response.json()["code"] == "forbidden"


def test_user_without_role_is_asked_to_choose(client, auth_headers):
    assert client.post(STREAM, json=BODY, headers=auth_headers()).json()["code"] == "role_required"


def test_trial_faculty_gets_three_then_429(client, auth_headers):
    headers = auth_headers(role="faculty")
    for _ in range(3):
        assert client.post(STREAM, json=BODY, headers=headers).status_code == 200
    response = client.post(STREAM, json=BODY, headers=headers)
    assert response.status_code == 429
    assert response.json()["code"] == "quota_exceeded"
    assert int(response.headers["retry-after"]) > 0


def test_verified_faculty_uses_the_verified_limit(client, auth_headers, monkeypatch):
    monkeypatch.setenv("GENERATE_LIMIT_VERIFIED", "4")
    headers = auth_headers(role="faculty", verified=True)
    statuses = [client.post(PLAIN, json=BODY, headers=headers).status_code for _ in range(5)]
    assert statuses == [200, 200, 200, 200, 429]


def test_admin_is_unlimited(client, auth_headers, store):
    headers = auth_headers(role="admin")
    assert all(client.post(PLAIN, json=BODY, headers=headers).status_code == 200 for _ in range(6))
    assert store.usage == {}


def test_stream_emits_meta_question_done_and_counts_one(client, auth_headers, store):
    response = client.post(STREAM, json=BODY, headers=auth_headers(role="faculty"))
    assert [e["type"] for e in events(response)] == ["meta", "question", "done"]
    assert sum(store.usage.values()) == 1


def test_stream_refunds_when_every_question_failed(client, auth_headers, store, fake_rag):
    fake_rag.question_error = True
    client.post(STREAM, json=BODY, headers=auth_headers(role="faculty"))
    assert sum(store.usage.values()) == 0


def test_stream_refunds_when_no_source_papers(client, auth_headers, store, fake_rag):
    fake_rag.papers = []
    response = client.post(STREAM, json=BODY, headers=auth_headers(role="faculty"))
    assert events(response) == [{"type": "error", "message": "No past papers found for this subject yet."}]
    assert sum(store.usage.values()) == 0


def test_stream_error_is_generic_and_refunded(client, auth_headers, store, fake_rag):
    fake_rag.raise_on_generate = True
    response = client.post(STREAM, json=BODY, headers=auth_headers(role="faculty"))
    assert events(response)[-1] == {"type": "error", "message": "Paper generation failed. Please try again."}
    assert "secret" not in response.text
    assert sum(store.usage.values()) == 0


def test_plain_generation_without_papers_is_404_and_refunded(client, auth_headers, store, fake_rag):
    fake_rag.papers = []
    response = client.post(PLAIN, json=BODY, headers=auth_headers(role="faculty"))
    assert response.status_code == 404 and response.json()["code"] == "not_found"
    assert sum(store.usage.values()) == 0


def test_missing_fields_are_400_without_using_quota(client, auth_headers, store):
    response = client.post(STREAM, json={"subject": "x"}, headers=auth_headers(role="faculty"))
    assert response.status_code == 400 and response.json()["code"] == "invalid_request"
    assert sum(store.usage.values()) == 0
```

`backend/tests/test_public_routes.py`:

```python
from tests.fakes import fake_db_cursor


def test_syllabus_read_is_faculty_only(client, auth_headers, monkeypatch):
    monkeypatch.setattr("api.routes.get_syllabus_by_code", lambda code: {"subject_code": code, "modules": []})
    assert client.get("/api/v1/syllabus/CSE432").status_code == 401
    assert client.get("/api/v1/syllabus/CSE432", headers=auth_headers(role="student")).status_code == 403
    trial = client.get("/api/v1/syllabus/CSE432", headers=auth_headers(role="faculty"))
    assert trial.status_code == 200 and trial.json()["data"]["subject_code"] == "CSE432"


def test_missing_syllabus_is_404_with_code(client, auth_headers, monkeypatch):
    monkeypatch.setattr("api.routes.get_syllabus_by_code", lambda code: None)
    response = client.get("/api/v1/syllabus/NOPE", headers=auth_headers(role="faculty"))
    assert response.status_code == 404 and response.json()["code"] == "not_found"


def test_download_of_unknown_paper_is_404_not_500(client, store, monkeypatch):
    monkeypatch.setattr("api.routes.db_cursor", lambda: fake_db_cursor([]))
    response = client.get("/api/v1/documents/999/download")
    assert response.status_code == 404 and response.json()["code"] == "not_found"


def test_documents_list_is_public(client, store, monkeypatch):
    row = (1, "SPM 23.pdf", "CSE432", "SPM", None, "June, 2023", "3 Hrs.", "60")
    monkeypatch.setattr("api.routes.db_cursor", lambda: fake_db_cursor([row]))
    response = client.get("/api/v1/documents")
    assert response.status_code == 200
    assert response.json()[0]["subjectCode"] == "CSE432"


def test_database_failure_is_a_generic_500(client, store, monkeypatch):
    def broken():
        raise RuntimeError("could not connect to server at 10.0.0.5 password=x")
    monkeypatch.setattr("api.routes.db_cursor", broken)
    response = client.get("/api/v1/documents")
    assert response.status_code == 500 and response.json()["code"] == "internal_error"
    assert "10.0.0.5" not in response.text
```

- [ ] **Step 3: Run to verify they fail**

Run: `python -m pytest backend/tests/test_generate.py backend/tests/test_public_routes.py -v`
Expected: FAIL. Anonymous requests currently get 200 or reach the real services, and `api.routes` has no `RAGService` attribute yet.

- [ ] **Step 4: Update the imports at the top of `backend/api/routes.py`**

Replace lines 1–17 (imports through `processor = …`) with the block below. `HTTPException`, `shutil`, `Form`, `File` and `UploadFile` stay for now because the upload endpoints still use them until Task 9.

```python
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Form
from fastapi.responses import FileResponse, StreamingResponse
from services.ocr_service import DocumentProcessor
import shutil
import os
import logging
import json
import asyncio

from typing import List

from auth.deps import enforce_quota, get_user_store, refund_quota_if_counted, require_role, user_subject
from auth.limits import generate_limit
from auth.users import PostgresUserStore, User
from database import db_cursor
from errors import ApiError
from services.rag_service import RAGService
from services.syllabus_service import get_syllabus_by_code
from services.vector_service import VectorService

logger = logging.getLogger(__name__)
router = APIRouter()
# Store papers in 'backend/papers' directory
processor = DocumentProcessor(upload_dir="backend/papers")
```

- [ ] **Step 5: Replace the syllabus-read, documents, download and search endpoints**

Replace the functions `get_syllabus`, `get_documents`, `download_paper` and `semantic_search` with:

```python
@router.get("/syllabus/{subject_code}")
def get_syllabus(subject_code: str, user: User = Depends(require_role("faculty", "admin"))):
    """Retrieves a syllabus by subject code. Faculty only: syllabi are not public."""
    syllabus = get_syllabus_by_code(subject_code)
    if not syllabus:
        raise ApiError(404, "not_found", f"No syllabus found for subject code {subject_code}.")
    return {"success": True, "data": syllabus}

@router.get("/documents", response_model=List[dict])
def get_documents():
    """Fetch all documents from NeonDB for the frontend."""
    with db_cursor() as cur:
        cur.execute("SELECT id, filename, subject_code, subject_name, semester, year, time, marks FROM papers ORDER BY id DESC")
        rows = cur.fetchall()
    return [
        {"id": row[0], "filename": row[1], "subjectCode": row[2], "subjectName": row[3],
         "semester": row[4], "year": row[5], "time": row[6], "marks": row[7]}
        for row in rows
    ]

@router.get("/documents/{paper_id}/download")
def download_paper(paper_id: int):
    """Download a paper PDF by ID."""
    with db_cursor() as cur:
        cur.execute("SELECT file_path, filename FROM papers WHERE id = %s", (paper_id,))
        row = cur.fetchone()
    if not row:
        raise ApiError(404, "not_found", "Paper not found.")
    file_path, filename = row
    if not os.path.exists(file_path):
        logger.error("Paper %s is in the database but missing on disk at %s", paper_id, file_path)
        raise ApiError(404, "not_found", "This paper's file is missing.")
    return FileResponse(path=file_path, filename=filename, media_type="application/pdf")

@router.post("/search/semantic")
def semantic_search(request: dict):
    """Search for papers semantically using Qdrant and NeonDB."""
    query = request.get("query", "")
    limit = request.get("limit", 5)
    results = VectorService().search_similar(query, limit)
    if not results:
        return []

    paper_ids = [point.id for point in results]
    scores = {point.id: point.score for point in results}
    placeholders = ",".join(["%s"] * len(paper_ids))
    with db_cursor() as cur:
        cur.execute(
            f"SELECT id, filename, subject_code, subject_name, semester, year, time, marks FROM papers WHERE id IN ({placeholders})",
            tuple(paper_ids),
        )
        rows = cur.fetchall()

    papers = {
        row[0]: {"id": row[0], "filename": row[1], "subjectCode": row[2], "subjectName": row[3], "semester": row[4],
                 "year": row[5], "time": row[6], "marks": row[7], "relevance": scores.get(row[0], 0)}
        for row in rows
    }
    # Return in order of Qdrant relevance
    return [papers[pid] for pid in paper_ids if pid in papers]
```

- [ ] **Step 6: Replace both generation endpoints**

Replace `generate_mock_paper` and `generate_mock_paper_stream` (from `@router.post("/generate/mock-paper")` to the end of the file) with:

```python
def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"

@router.post("/generate/mock-paper")
def generate_mock_paper(
    request: dict,
    user: User = Depends(require_role("faculty", "admin")),
    store: PostgresUserStore = Depends(get_user_store),
):
    """Generate a mock examination paper using RAG (non-streaming)."""
    if "subject" not in request or "sections" not in request:
        raise ApiError(400, "invalid_request", "Missing required fields: subject, sections")

    subject_key = user_subject(user)
    limit = generate_limit(user)
    enforce_quota(store, subject_key, "generate", limit)
    try:
        result = RAGService().generate_mock_paper(request)
    except Exception:
        refund_quota_if_counted(store, subject_key, "generate", limit)
        raise
    if "error" in result:
        refund_quota_if_counted(store, subject_key, "generate", limit)
        raise ApiError(404, "not_found", "No past papers found for this subject yet.")
    return result

@router.post("/generate/mock-paper-stream")
async def generate_mock_paper_stream(
    request: dict,
    user: User = Depends(require_role("faculty", "admin")),
    store: PostgresUserStore = Depends(get_user_store),
):
    """
    SSE streaming endpoint: generates one question at a time and streams each
    back as a Server-Sent Event so Cloudflare/nginx timeouts are never hit.
    """
    if "subject" not in request or "sections" not in request:
        raise ApiError(400, "invalid_request", "Missing required fields: subject, sections")

    subject_key = user_subject(user)
    limit = generate_limit(user)
    enforce_quota(store, subject_key, "generate", limit)

    async def event_stream():
        real_questions = 0
        try:
            import copy, math

            rag_service = RAGService()
            subject = request["subject"]
            sections = request["sections"]

            # Retrieve context once upfront
            similar_papers = rag_service._retrieve_similar_papers(subject, limit=3)
            if not similar_papers:
                yield _sse({"type": "error", "message": "No past papers found for this subject yet."})
                return
            context = rag_service._extract_paper_context(similar_papers)

            # Send metadata first so frontend knows source papers
            yield _sse({"type": "meta", "sourcePapers": [p["filename"] for p in similar_papers], "subject": subject})
            await asyncio.sleep(0)  # flush to client

            result_sections = []

            for section in sections:
                section_name = section.get("name", "Section")
                questions_config = section.get("questions", [])
                bloom_mode = section.get("bloomMode", "simple")
                generate_pool = section.get("generate_pool", False)

                if bloom_mode == "simple":
                    bloom_dist = rag_service._get_fuzzy_bloom_distribution(section.get("difficulty", "medium"))
                else:
                    bloom_dist = section.get("bloomDistribution", {})

                pool_configs = []
                if generate_pool and questions_config:
                    pool_count = math.ceil(len(questions_config) * 0.5)
                    for i in range(pool_count):
                        base = copy.deepcopy(questions_config[i % len(questions_config)])
                        base["number"] = len(questions_config) + i + 1
                        pool_configs.append(base)

                generated_questions = []
                pool_questions = []

                # Stream each question as it's generated
                for q_config in questions_config:
                    q = rag_service._generate_question(q_config, context, subject, bloom_dist)
                    if not q.get("error"):
                        real_questions += 1
                    generated_questions.append(q)
                    yield _sse({"type": "question", "section": section_name, "is_pool": False, "question": q})
                    await asyncio.sleep(0)

                for q_config in pool_configs:
                    q = rag_service._generate_question(q_config, context, subject, bloom_dist)
                    if not q.get("error"):
                        real_questions += 1
                    pool_questions.append(q)
                    yield _sse({"type": "question", "section": section_name, "is_pool": True, "question": q})
                    await asyncio.sleep(0)

                result_sections.append({
                    "name": section_name,
                    "instruction": section.get("instruction", ""),
                    "questions": generated_questions,
                    "pool": pool_questions
                })

            # Final event with complete assembled paper
            yield _sse({"type": "done", "sections": result_sections, "subject": subject,
                        "sourcePapers": [p["filename"] for p in similar_papers]})

        except Exception:
            logger.exception("Streaming generation failed for subject %r", request.get("subject"))
            yield _sse({"type": "error", "message": "Paper generation failed. Please try again."})
        finally:
            # A run that produced no usable question doesn't count against the daily limit
            if real_questions == 0:
                refund_quota_if_counted(store, subject_key, "generate", limit)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no"  # disables nginx response buffering
        }
    )
```

- [ ] **Step 7: Run the tests**

Run: `python -m pytest -v`
Expected: all pass, including `test_startup.py`.

- [ ] **Step 8: Commit**

```bash
git add backend/api/routes.py backend/tests/fakes.py backend/tests/test_generate.py backend/tests/test_public_routes.py
git commit -m "feat: faculty-only generation with daily limits and refunds; generic errors on public routes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Paper and syllabus uploads with role limits

**Files:**
- Create: `backend/services/paper_metadata.py`, `backend/services/paper_store.py`, `backend/tests/test_paper_metadata.py`, `backend/tests/test_uploads.py`, `backend/tests/test_syllabus_rules.py`
- Modify: `backend/api/routes.py`, `backend/services/syllabus_service.py`, `backend/tests/fakes.py`

**Interfaces:**
- Consumes: `optional_user`, `require_role`, `quota_subject`, `user_subject`, `enforce_quota`, `refund_quota_if_counted`, `upload_limit`, `syllabus_limit`.
- Produces:
  - `services.paper_metadata.PAPER_FIELDS = ("subject_code", "subject_name", "semester", "year", "time", "marks")`, `normalize_subject_code(code) -> str | None`, `to_paper_fields(metadata: dict | None) -> dict[str, str | None]`.
  - `services.paper_store.insert_paper(filename: str, file_path: str, fields: dict, uploaded_by: int | None) -> int`.
  - `services.syllabus_service.get_syllabus_owner(subject_code: str) -> tuple[bool, int | None]`; `process_and_save_syllabus(..., uploaded_by: int | None = None)`.
  - In `api.routes`, patchable names: `processor`, `insert_paper`, `get_syllabus_owner`, `process_and_save_syllabus`. Test fakes `FakeProcessor` and `FakeVectorService`.

- [ ] **Step 1: Write the failing metadata tests**

`backend/tests/test_paper_metadata.py`:

```python
import pytest

from services.paper_metadata import normalize_subject_code, to_paper_fields


@pytest.mark.parametrize("raw,expected", [
    ("aiml 201", "AIML201"), ("cse-432", "CSE432"), (" it402 ", "IT402"), ("CSE_439", "CSE439"),
    ("", None), ("   ", None), (None, None), (401, "401"),
])
def test_normalize_subject_code(raw, expected):
    assert normalize_subject_code(raw) == expected


def test_to_paper_fields_maps_and_cleans():
    fields = to_paper_fields({"subjectCode": "cse 432", "subjectName": " Software Project Management ",
                              "semester": "", "monthYear": "June, 2023", "time": "3 Hrs.", "marks": 60})
    assert fields == {"subject_code": "CSE432", "subject_name": "Software Project Management", "semester": None,
                      "year": "June, 2023", "time": "3 Hrs.", "marks": "60"}


def test_to_paper_fields_handles_marks_dict_and_missing_metadata():
    assert to_paper_fields({"marks": {"Max Marks": "100"}})["marks"] == "100"
    assert to_paper_fields(None) == {k: None for k in ("subject_code", "subject_name", "semester", "year", "time", "marks")}
```

- [ ] **Step 2: Implement `backend/services/paper_metadata.py`**

```python
import re

PAPER_FIELDS = ("subject_code", "subject_name", "semester", "year", "time", "marks")


def normalize_subject_code(code) -> str | None:
    """'aiml 201' -> 'AIML201': uppercase, no spaces, hyphens or underscores."""
    if code is None:
        return None
    cleaned = re.sub(r"[\s\-_]+", "", str(code)).upper()
    return cleaned or None


def _text(value) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _marks(value) -> str | None:
    # The LLM sometimes returns marks as a dict or an int
    if isinstance(value, dict):
        value = value.get("Max Marks") or value.get("max_marks") or str(value)
    return _text(value)


def to_paper_fields(metadata: dict | None) -> dict[str, str | None]:
    """Maps LLM metadata keys to `papers` columns, normalizing the subject code."""
    m = metadata or {}
    return {
        "subject_code": normalize_subject_code(m.get("subjectCode") or m.get("Subject Code")),
        "subject_name": _text(m.get("subjectName") or m.get("Subject Name")),
        "semester": _text(m.get("semester")),
        "year": _text(m.get("monthYear") or m.get("Month/Year")),
        "time": _text(m.get("time")),
        "marks": _marks(m.get("marks")),
    }
```

Run: `python -m pytest backend/tests/test_paper_metadata.py -v`. Expected: PASS.

- [ ] **Step 3: Implement `backend/services/paper_store.py`**

```python
from database import db_cursor
from services.paper_metadata import PAPER_FIELDS


def insert_paper(filename: str, file_path: str, fields: dict, uploaded_by: int | None) -> int:
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO papers (filename, file_path, subject_code, subject_name, semester, year, time, marks, uploaded_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (filename, file_path, *(fields[k] for k in PAPER_FIELDS), uploaded_by),
        )
        return cur.fetchone()[0]
```

- [ ] **Step 4: Add the syllabus owner lookup and `uploaded_by` to `backend/services/syllabus_service.py`**

Change the import `from database import get_db_connection` to `from database import db_cursor, get_db_connection`. Add `uploaded_by: int | None = None` as the last parameter of `process_and_save_syllabus`. Replace its upsert SQL call with:

```python
        cur.execute("""
            INSERT INTO syllabi (subject_code, subject_name, modules, uploaded_by)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (subject_code)
            DO UPDATE SET
                subject_name = EXCLUDED.subject_name,
                modules = EXCLUDED.modules,
                created_at = CURRENT_TIMESTAMP,
                uploaded_by = COALESCE(syllabi.uploaded_by, EXCLUDED.uploaded_by)
            RETURNING id;
        """, (subject_code, subject_name, json.dumps(modules), uploaded_by))
```

Append the owner lookup:

```python
def get_syllabus_owner(subject_code: str) -> tuple[bool, int | None]:
    """(exists, uploaded_by) for a subject code. uploaded_by is None for legacy syllabi."""
    with db_cursor() as cur:
        cur.execute("SELECT uploaded_by FROM syllabi WHERE subject_code = %s", (subject_code,))
        row = cur.fetchone()
    return (row is not None, row[0] if row else None)
```

- [ ] **Step 5: Add upload fakes to `backend/tests/fakes.py`**

Append:

```python
class FakeProcessor:
    """Stands in for DocumentProcessor; writes nothing but the uploaded file."""

    def __init__(self, upload_dir, metadata=None, full_text="full text", fail=False):
        self.upload_dir = str(upload_dir)
        self.metadata = metadata if metadata is not None else {
            "subjectCode": "aiml 201", "subjectName": "Intro to AIML", "monthYear": "May, 2022", "time": "3 Hrs", "marks": 60}
        self.full_text = full_text
        self.fail = fail

    def process_pdf(self, file_path):
        if self.fail:
            raise RuntimeError("tesseract crashed reading /secret/path")
        return {"text": "header text", "metadata": dict(self.metadata)}

    def extract_full_text(self, file_path):
        return self.full_text


class FakeVectorService:
    upserts: list = []

    def upsert_paper(self, paper_id, text, metadata):
        FakeVectorService.upserts.append((paper_id, text, metadata))
        return True
```

- [ ] **Step 6: Write the failing upload tests**

`backend/tests/test_uploads.py`:

```python
import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeProcessor, FakeVectorService

URL = "/api/v1/documents/ingest"
PDF = ("paper.pdf", b"%PDF-1.4 fake", "application/pdf")


@pytest.fixture(autouse=True)
def pipeline(monkeypatch, tmp_path):
    inserted = []

    def fake_insert(filename, file_path, fields, uploaded_by):
        inserted.append({"filename": filename, "fields": fields, "uploaded_by": uploaded_by})
        return 100 + len(inserted)

    monkeypatch.setattr("api.routes.processor", FakeProcessor(tmp_path))
    monkeypatch.setattr("api.routes.insert_paper", fake_insert)
    monkeypatch.setattr("api.routes.VectorService", FakeVectorService)
    return inserted


def upload(client, headers=None, file=PDF):
    return client.post(URL, files={"file": file}, headers=headers or {})


def test_anonymous_upload_is_saved_with_normalized_code(client, store, pipeline):
    response = upload(client)
    assert response.status_code == 200
    assert pipeline[0]["uploaded_by"] is None
    assert pipeline[0]["fields"]["subject_code"] == "AIML201"


def test_anonymous_limit_is_per_ip(client, store):
    assert [upload(client).status_code for _ in range(6)] == [200] * 5 + [429]
    other_ip = TestClient(client.app, client=("198.51.100.7", 50000))
    assert upload(other_ip).status_code == 200


def test_anonymous_usage_is_stored_as_hash(client, store):
    upload(client)
    (subject, action), = store.usage.keys()
    assert subject.startswith("ip:") and "testclient" not in subject and action == "upload"


def test_student_limit(client, auth_headers, monkeypatch):
    monkeypatch.setenv("UPLOAD_LIMIT_STUDENT", "2")
    headers = auth_headers(role="student")
    assert [upload(client, headers).status_code for _ in range(3)] == [200, 200, 429]


def test_trial_faculty_cannot_upload_papers(client, auth_headers, store):
    response = upload(client, auth_headers(role="faculty"))
    assert response.status_code == 403 and response.json()["code"] == "forbidden"
    assert store.usage == {}


def test_verified_faculty_and_admin_can_upload(client, auth_headers, store, pipeline):
    assert upload(client, auth_headers(role="faculty", verified=True)).status_code == 200
    assert upload(client, auth_headers(role="admin")).status_code == 200
    assert pipeline[0]["uploaded_by"] is not None


def test_user_without_role_must_choose_first(client, auth_headers):
    assert upload(client, auth_headers()).json()["code"] == "role_required"


def test_non_pdf_is_rejected_without_using_quota(client, store):
    response = upload(client, file=("notes.txt", b"hello", "text/plain"))
    assert response.status_code == 400 and response.json()["code"] == "invalid_file"
    assert store.usage == {}


def test_pipeline_failure_is_generic_and_refunded(client, store, monkeypatch, tmp_path):
    monkeypatch.setattr("api.routes.processor", FakeProcessor(tmp_path, fail=True))
    response = upload(client)
    assert response.status_code == 500 and "secret" not in response.text
    assert sum(store.usage.values()) == 0
```

`backend/tests/test_syllabus_rules.py`:

```python
import pytest

URL = "/api/v1/syllabus/upload"


@pytest.fixture
def syllabus(monkeypatch):
    state = {"owner": None, "exists": False, "saved": [], "success": True}

    def fake_owner(code):
        return (state["exists"], state["owner"])

    def fake_save(file_bytes, filename, subject_code, subject_name, uploaded_by=None):
        state["saved"].append({"code": subject_code, "uploaded_by": uploaded_by})
        if not state["success"]:
            return {"success": False, "error": "Database error: password=x"}
        return {"success": True, "data": {"modules": []}}

    monkeypatch.setattr("api.routes.get_syllabus_owner", fake_owner)
    monkeypatch.setattr("api.routes.process_and_save_syllabus", fake_save)
    return state


def post(client, headers):
    return client.post(URL, files={"file": ("syl.pdf", b"%PDF", "application/pdf")},
                       data={"subject_code": "CSE432", "subject_name": "SPM"}, headers=headers)


def verified_id(store):
    return max(store.users)  # id of the most recently created user


def test_trial_faculty_and_students_cannot_upload_syllabi(client, auth_headers, syllabus):
    assert post(client, auth_headers(role="faculty")).status_code == 403
    assert post(client, auth_headers(role="student")).status_code == 403
    assert syllabus["saved"] == []


def test_verified_faculty_creates_new_syllabus_as_owner(client, auth_headers, store, syllabus):
    headers = auth_headers(role="faculty", verified=True)
    assert post(client, headers).status_code == 200
    assert syllabus["saved"][0]["uploaded_by"] == verified_id(store)


def test_only_owner_or_admin_can_replace(client, auth_headers, store, syllabus):
    owner_headers = auth_headers(role="faculty", verified=True)
    syllabus.update(exists=True, owner=verified_id(store))
    other_headers = auth_headers(role="faculty", verified=True)
    assert post(client, other_headers).status_code == 403
    assert post(client, owner_headers).status_code == 200
    assert post(client, auth_headers(role="admin")).status_code == 200


def test_legacy_syllabus_is_admin_only(client, auth_headers, syllabus):
    syllabus.update(exists=True, owner=None)
    assert post(client, auth_headers(role="faculty", verified=True)).status_code == 403
    assert post(client, auth_headers(role="admin")).status_code == 200


def test_processing_failure_is_422_generic_and_refunded(client, auth_headers, store, syllabus):
    syllabus["success"] = False
    response = post(client, auth_headers(role="faculty", verified=True))
    assert response.status_code == 422 and response.json()["code"] == "syllabus_unreadable"
    assert "password" not in response.text
    assert sum(store.usage.values()) == 0
```

- [ ] **Step 7: Run to verify they fail**

Run: `python -m pytest backend/tests/test_uploads.py backend/tests/test_syllabus_rules.py -v`
Expected: FAIL. `api.routes` has no `insert_paper` or `get_syllabus_owner`, and uploads are still unrestricted.

- [ ] **Step 8: Rewrite the upload endpoints in `backend/api/routes.py`**

Replace the import block from Task 8 step 4 with the final version (removing `HTTPException`):

```python
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from services.ocr_service import DocumentProcessor
import shutil
import os
import logging
import json
import asyncio

from typing import List

from auth.deps import (enforce_quota, get_user_store, optional_user, quota_subject, refund_quota_if_counted,
                       require_role, user_subject)
from auth.limits import generate_limit, syllabus_limit, upload_limit
from auth.users import PostgresUserStore, User
from database import db_cursor
from errors import ApiError
from services.paper_metadata import to_paper_fields
from services.paper_store import insert_paper
from services.rag_service import RAGService
from services.syllabus_service import get_syllabus_by_code, get_syllabus_owner, process_and_save_syllabus
from services.vector_service import VectorService

logger = logging.getLogger(__name__)
router = APIRouter()
# Store papers in 'backend/papers' directory
processor = DocumentProcessor(upload_dir="backend/papers")

REQUIRED_METADATA = ("subjectCode", "subjectName", "monthYear", "time", "marks")
METADATA_ATTEMPTS = 2
```

Replace `ingest_document` and `upload_syllabus` with:

```python
def _extract_metadata_with_retry(file_path: str) -> dict:
    """Header OCR + LLM metadata extraction, asking the LLM again if required fields come back empty."""
    result: dict = {"text": "", "metadata": None}
    for attempt in range(1, METADATA_ATTEMPTS + 1):
        result = processor.process_pdf(file_path)
        metadata = result.get("metadata") or {}
        if all(metadata.get(field) for field in REQUIRED_METADATA):
            break
        logger.info("Metadata incomplete for %s on attempt %d", file_path, attempt)
    return result

def _index_paper(paper_id: int, file_path: str, filename: str, fields: dict, header_text: str) -> None:
    """Best effort: OCR every page and store the embedding. The paper row is saved either way."""
    try:
        full_text = processor.extract_full_text(file_path)
    except Exception:
        logger.exception("Full-text OCR failed for %s; indexing the header text only", filename)
        full_text = header_text
    try:
        stored = VectorService().upsert_paper(
            paper_id=paper_id,
            text=full_text,
            metadata={"subject_code": fields["subject_code"], "subject_name": fields["subject_name"],
                      "year": fields["year"], "filename": filename},
        )
        if not stored:
            logger.error("Failed to store the embedding for paper %s", paper_id)
    except Exception:
        logger.exception("Vector indexing failed for paper %s", paper_id)

@router.post("/documents/ingest")
def ingest_document(
    http_request: Request,
    file: UploadFile = File(...),
    user: User | None = Depends(optional_user),
    store: PostgresUserStore = Depends(get_user_store),
):
    """Uploads a PDF, runs OCR/metadata extraction, saves it, and indexes it for search."""
    if user is not None and user.role is None:
        raise ApiError(403, "role_required", "Choose Student or Faculty to continue.")
    limit = upload_limit(user)
    if limit == 0:
        raise ApiError(403, "forbidden", "Paper uploads need a student or verified faculty account.")

    filename = os.path.basename(file.filename or "")
    if not filename.lower().endswith(".pdf"):
        raise ApiError(400, "invalid_file", "Only PDF files are supported.")

    subject = quota_subject(user, http_request)
    enforce_quota(store, subject, "upload", limit)
    try:
        file_path = os.path.join(processor.upload_dir, filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        result = _extract_metadata_with_retry(file_path)
        fields = to_paper_fields(result.get("metadata"))
        paper_id = insert_paper(
            filename=filename,
            file_path=file_path.replace("\\", "/"),
            fields=fields,
            uploaded_by=user.id if user is not None else None,
        )
    except Exception:
        refund_quota_if_counted(store, subject, "upload", limit)
        raise

    _index_paper(paper_id, file_path, filename, fields, result.get("text", ""))
    return {"status": "success", "filename": filename, "db_id": paper_id, "data": result}

@router.post("/syllabus/upload")
async def upload_syllabus(
    file: UploadFile = File(...),
    subject_code: str = Form(...),
    subject_name: str = Form(...),
    user: User = Depends(require_role("faculty", "admin")),
    store: PostgresUserStore = Depends(get_user_store),
):
    """Uploads a syllabus PDF, extracts modules and weightage, and saves to database."""
    limit = syllabus_limit(user)
    if limit == 0:
        raise ApiError(403, "forbidden", "Syllabus uploads need a verified faculty account.")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise ApiError(400, "invalid_file", "Only PDF files are supported.")

    exists, owner = get_syllabus_owner(subject_code)
    if exists and user.role != "admin" and owner != user.id:
        raise ApiError(403, "forbidden", "Only the original uploader or an admin can replace this syllabus.")

    subject = user_subject(user)
    enforce_quota(store, subject, "syllabus", limit)
    result = process_and_save_syllabus(
        file_bytes=await file.read(),
        filename=file.filename,
        subject_code=subject_code,
        subject_name=subject_name,
        uploaded_by=user.id,
    )
    if not result.get("success"):
        logger.error("Syllabus processing failed for %s: %s", subject_code, result.get("error"))
        refund_quota_if_counted(store, subject, "syllabus", limit)
        raise ApiError(422, "syllabus_unreadable",
                       "We couldn't read the modules from this syllabus. Check the PDF and try again.")
    return result
```

Note: `ingest_document` is now a plain `def`, so FastAPI runs it in a worker thread and the OCR no longer blocks the event loop.

- [ ] **Step 9: Run the tests**

Run: `python -m pytest -v`
Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add backend/api/routes.py backend/services/paper_metadata.py backend/services/paper_store.py backend/services/syllabus_service.py backend/tests/fakes.py backend/tests/test_paper_metadata.py backend/tests/test_uploads.py backend/tests/test_syllabus_rules.py
git commit -m "feat: role-based upload limits, anonymous per-IP uploads, syllabus ownership

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Admin users API

**Files:**
- Create: `backend/api/admin.py`, `backend/tests/test_admin_api.py`
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `require_role`, `get_user_store`, `user_summary`, `user_subject`, `ApiError`.
- Produces: `GET /api/v1/admin/users` returns `{"users": [user_summary…]}`. `PATCH /api/v1/admin/users/{id}` with body `{"role"?: "student"|"faculty"|"admin", "verified"?: bool}` returns a `user_summary`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_admin_api.py`:

```python
def test_only_admins_can_list_users(client, auth_headers, store):
    assert client.get("/api/v1/admin/users").status_code == 401
    assert client.get("/api/v1/admin/users", headers=auth_headers(role="faculty", verified=True)).status_code == 403
    response = client.get("/api/v1/admin/users", headers=auth_headers(role="admin"))
    assert response.status_code == 200
    assert {"id", "email", "name", "role", "verified", "createdAt", "usedToday"} <= set(response.json()["users"][0])


def test_admin_can_verify_and_change_role(client, auth_headers, store):
    auth_headers(role="faculty")
    target_id = max(store.users)
    admin = auth_headers(role="admin")
    response = client.patch(f"/api/v1/admin/users/{target_id}", json={"verified": True}, headers=admin)
    assert response.status_code == 200 and response.json()["verified"] is True
    response = client.patch(f"/api/v1/admin/users/{target_id}", json={"role": "student"}, headers=admin)
    assert response.json()["role"] == "student"


def test_admin_cannot_demote_self(client, auth_headers, store):
    admin = auth_headers(role="admin")
    admin_id = max(store.users)
    response = client.patch(f"/api/v1/admin/users/{admin_id}", json={"role": "student"}, headers=admin)
    assert response.status_code == 409 and response.json()["code"] == "cannot_demote_self"


def test_unknown_user_and_empty_update(client, auth_headers):
    admin = auth_headers(role="admin")
    assert client.patch("/api/v1/admin/users/9999", json={"verified": True}, headers=admin).status_code == 404
    assert client.patch("/api/v1/admin/users/1", json={}, headers=admin).status_code == 400
    assert client.patch("/api/v1/admin/users/1", json={"role": "superuser"}, headers=admin).status_code == 422
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/tests/test_admin_api.py -v`
Expected: FAIL with 404 (route not found).

- [ ] **Step 3: Implement `backend/api/admin.py`**

```python
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from auth.deps import get_user_store, require_role, user_subject
from auth.users import PostgresUserStore, User, user_summary
from errors import ApiError

router = APIRouter(prefix="/admin")


class UserUpdate(BaseModel):
    role: Literal["student", "faculty", "admin"] | None = None
    verified: bool | None = None


@router.get("/users")
def list_users(admin: User = Depends(require_role("admin")), store: PostgresUserStore = Depends(get_user_store)):
    return {"users": store.list_with_usage()}


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, admin: User = Depends(require_role("admin")),
                store: PostgresUserStore = Depends(get_user_store)):
    if body.role is None and body.verified is None:
        raise ApiError(400, "invalid_request", "Nothing to update.")
    if user_id == admin.id and body.role is not None and body.role != "admin":
        raise ApiError(409, "cannot_demote_self", "You can't remove your own admin role.")
    updated = store.update(user_id, role=body.role, verified=body.verified)
    if updated is None:
        raise ApiError(404, "not_found", "User not found.")
    return user_summary(updated, store.usage_today(user_subject(updated)))
```

- [ ] **Step 4: Register the router in `backend/main.py`**

Add `from api.admin import router as admin_router`, and `app.include_router(admin_router, prefix="/api/v1")` next to the other routers.

- [ ] **Step 5: Run the tests, then commit**

Run: `python -m pytest -v`. Expected: all pass.

```bash
git add backend/api/admin.py backend/tests/test_admin_api.py backend/main.py
git commit -m "feat: admin API to list users, verify faculty and change roles

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: `make-admin` command

**Files:**
- Create: `backend/admin_cli.py`, `backend/tests/test_admin_cli.py`

**Interfaces:**
- Consumes: `PostgresUserStore.find_by_email`, `PostgresUserStore.update`.
- Produces: `admin_cli.make_admin(email: str, store, input_fn=input, out=print) -> bool`, `admin_cli.main(argv: list[str] | None = None) -> int`. Run it as `python backend/admin_cli.py make-admin you@outlook.com` from the project root (or `/app` in the container).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_admin_cli.py`:

```python
from admin_cli import make_admin
from auth.tokens import Claims
from tests.fakes import FakeUserStore


def store_with(*emails):
    store = FakeUserStore()
    for i, email in enumerate(emails):
        store.get_or_create(Claims(tid=f"tenant-{i}", oid=f"oid-{i}", name=f"Person {i}", email=email))
    return store


def test_unknown_email_explains_what_to_do():
    lines = []
    assert make_admin("nobody@example.com", store_with(), out=lines.append) is False
    assert "Sign in to PrepWise once" in lines[0]


def test_single_match_confirmed():
    store = store_with("me@outlook.com")
    assert make_admin("ME@outlook.com", store, input_fn=lambda _: "y", out=lambda _: None) is True
    assert store.get(1).role == "admin" and store.get(1).verified is True


def test_single_match_declined_changes_nothing():
    store = store_with("me@outlook.com")
    assert make_admin("me@outlook.com", store, input_fn=lambda _: "n", out=lambda _: None) is False
    assert store.get(1).role is None


def test_duplicate_emails_require_choosing_an_id():
    store = store_with("me@outlook.com", "me@outlook.com")
    assert make_admin("me@outlook.com", store, input_fn=lambda _: "2", out=lambda _: None) is True
    assert store.get(1).role is None and store.get(2).role == "admin"
    assert make_admin("me@outlook.com", store, input_fn=lambda _: "", out=lambda _: None) is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/tests/test_admin_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'admin_cli'`.

- [ ] **Step 3: Implement `backend/admin_cli.py`**

```python
"""PrepWise admin commands. Run from the project root (or /app in the container):

    python backend/admin_cli.py make-admin you@outlook.com
"""
import argparse
import sys


def make_admin(email: str, store, input_fn=input, out=print) -> bool:
    """Grants admin to the account that signed in with `email`, after confirmation."""
    matches = store.find_by_email(email)
    if not matches:
        out("No account with that email has signed in yet. Sign in to PrepWise once, then run this again.")
        return False

    if len(matches) == 1:
        target = matches[0]
        answer = input_fn(f"Make {target.name or target.email} (id {target.id}) an admin? [y/N] ")
        if answer.strip().lower() != "y":
            out("Cancelled.")
            return False
    else:
        out("Several accounts use this email (e.g. a work and a personal account):")
        for user in matches:
            created = user.created_at.strftime("%Y-%m-%d") if user.created_at else "?"
            out(f"  id {user.id}: {user.name or '-'} (tenant {user.ms_tid}, joined {created})")
        choice = input_fn("Enter the id to make admin (blank to cancel): ").strip()
        target = next((user for user in matches if str(user.id) == choice), None)
        if target is None:
            out("Cancelled.")
            return False

    store.update(target.id, role="admin", verified=True)
    out(f"{target.email} is now an admin.")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PrepWise admin commands")
    commands = parser.add_subparsers(dest="command", required=True)
    make_admin_parser = commands.add_parser("make-admin", help="Grant admin to an account that has signed in")
    make_admin_parser.add_argument("email")
    args = parser.parse_args(argv)

    if args.command == "make-admin":
        from auth.users import PostgresUserStore
        return 0 if make_admin(args.email, PostgresUserStore()) else 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest -v`
Expected: all pass.

- [ ] **Step 5: Make yourself admin (manual)**

After signing in once (you did via `/me` in Task 7), run `python backend/admin_cli.py make-admin <your email>` and answer `y`. Then `curl -s -H "Authorization: Bearer $(cat backend/dev-scripts/.test_token)" http://localhost:8000/api/v1/me` shows `"role": "admin"`.

- [ ] **Step 6: Commit**

```bash
git add backend/admin_cli.py backend/tests/test_admin_cli.py
git commit -m "feat: make-admin command to bootstrap the first admin safely

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: `reprocess` command

**Files:**
- Create: `backend/services/reprocess.py`, `backend/tests/test_reprocess.py`
- Modify: `backend/services/paper_store.py`, `backend/services/vector_service.py`, `backend/admin_cli.py`

**Interfaces:**
- Consumes: `to_paper_fields`, `PAPER_FIELDS`, `DocumentProcessor.process_pdf / extract_full_text`, `VectorService.get_embedding(text, input_type)`.
- Produces:
  - `services.paper_store.PaperRow(id: int, filename: str, file_path: str, fields: dict)`, `get_papers(ids: list[int] | None) -> list[PaperRow]`, `update_paper_metadata(paper_id: int, fields: dict) -> None`.
  - `VectorService.upsert_paper_vector(paper_id: int, vector: list, text: str, metadata: dict) -> bool`.
  - `services.reprocess.ReprocessResult(paper_id, filename, status, changes, message="")` with status one of `preview`, `updated`, `skipped`, `failed`; `merge_fields(old, new) -> dict`, `diff_fields(old, merged) -> dict[str, tuple]`, `reprocess_paper(row, processor, vector_service, update_metadata, apply) -> ReprocessResult`, `reprocess_many(rows, processor, vector_service, update_metadata, apply, out=print) -> list[ReprocessResult]`, `run_reprocess(ids: list[int] | None, apply: bool, out=print) -> int`.
  - CLI: `python backend/admin_cli.py reprocess (--id N [--id M …] | --all) [--apply]`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_reprocess.py`:

```python
import pytest

from admin_cli import main as cli_main
from services.paper_store import PaperRow
from services.reprocess import merge_fields, reprocess_many, reprocess_paper

OLD = {"subject_code": "1T402", "subject_name": "DMBI", "semester": None, "year": "Nov, 2025", "time": "3 Hrs", "marks": "60"}


class Processor:
    def __init__(self, metadata, fail_on=None):
        self.metadata = metadata
        self.fail_on = fail_on
        self.full_text_calls = 0

    def process_pdf(self, path):
        if self.fail_on and self.fail_on in path:
            raise RuntimeError("OCR exploded")
        return {"text": "header", "metadata": dict(self.metadata)}

    def extract_full_text(self, path):
        self.full_text_calls += 1
        return "new full text"


class Vectors:
    def __init__(self, vector=(0.1, 0.2)):
        self.vector = list(vector) if vector else None
        self.upserts = []

    def get_embedding(self, text, input_type="passage"):
        assert input_type == "passage"
        return self.vector

    def upsert_paper_vector(self, paper_id, vector, text, metadata):
        self.upserts.append((paper_id, text, metadata))
        return True


@pytest.fixture
def row(tmp_path):
    pdf = tmp_path / "DMBI.pdf"
    pdf.write_bytes(b"%PDF")
    return PaperRow(id=14, filename="DMBI.pdf", file_path=str(pdf), fields=dict(OLD))


def test_merge_keeps_old_values_when_new_are_empty():
    new = {"subject_code": "IT402", "subject_name": None, "semester": None, "year": "", "time": None, "marks": None}
    merged = merge_fields(OLD, new)
    assert merged["subject_code"] == "IT402"
    assert merged["subject_name"] == "DMBI" and merged["year"] == "Nov, 2025" and merged["marks"] == "60"


def test_preview_writes_nothing(row):
    processor, vectors, updates = Processor({"subjectCode": "it 402"}), Vectors(), []
    result = reprocess_paper(row, processor, vectors, lambda pid, f: updates.append(pid), apply=False)
    assert result.status == "preview"
    assert result.changes == {"subject_code": ("1T402", "IT402")}
    assert updates == [] and vectors.upserts == [] and processor.full_text_calls == 0


def test_apply_updates_vector_and_metadata(row):
    vectors, updates = Vectors(), []
    result = reprocess_paper(row, Processor({"subjectCode": "it 402"}), vectors,
                             lambda pid, fields: updates.append((pid, fields)), apply=True)
    assert result.status == "updated"
    assert updates == [(14, {**OLD, "subject_code": "IT402"})]
    paper_id, text, payload = vectors.upserts[0]
    assert (paper_id, text) == (14, "new full text")
    assert payload == {"subject_code": "IT402", "subject_name": "DMBI", "year": "Nov, 2025", "filename": "DMBI.pdf"}


def test_embedding_failure_changes_nothing(row):
    updates = []
    result = reprocess_paper(row, Processor({"subjectCode": "it 402"}), Vectors(vector=None),
                             lambda pid, f: updates.append(pid), apply=True)
    assert result.status == "failed" and updates == []


def test_missing_pdf_is_skipped(row):
    missing = PaperRow(id=7, filename="gone.pdf", file_path="/nope/gone.pdf", fields=dict(OLD))
    assert reprocess_paper(missing, Processor({}), Vectors(), lambda *a: None, apply=True).status == "skipped"


def test_one_failure_does_not_stop_the_rest(row, tmp_path):
    bad_pdf = tmp_path / "bad.pdf"
    bad_pdf.write_bytes(b"%PDF")
    bad = PaperRow(id=1, filename="bad.pdf", file_path=str(bad_pdf), fields=dict(OLD))
    lines = []
    results = reprocess_many([bad, row], Processor({"subjectCode": "it402"}, fail_on="bad"), Vectors(),
                             lambda *a: None, apply=True, out=lines.append)
    assert [r.status for r in results] == ["failed", "updated"]
    assert any("subject_code: '1T402' -> 'IT402'" in line for line in lines)


def test_cli_requires_ids_or_all():
    with pytest.raises(SystemExit):
        cli_main(["reprocess"])
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/tests/test_reprocess.py -v`
Expected: FAIL with `ImportError: cannot import name 'PaperRow'`.

- [ ] **Step 3: Extend `backend/services/paper_store.py`**

Add below the imports:

```python
from dataclasses import dataclass


@dataclass
class PaperRow:
    id: int
    filename: str
    file_path: str
    fields: dict  # keys: PAPER_FIELDS
```

Append:

```python
def get_papers(ids: list[int] | None) -> list[PaperRow]:
    """All papers (ids=None) or the given ids, ordered by id."""
    query = "SELECT id, filename, file_path, subject_code, subject_name, semester, year, time, marks FROM papers"
    params: tuple = ()
    if ids is not None:
        query += " WHERE id = ANY(%s)"
        params = (list(ids),)
    query += " ORDER BY id"
    with db_cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return [PaperRow(id=r[0], filename=r[1], file_path=r[2], fields=dict(zip(PAPER_FIELDS, r[3:9]))) for r in rows]


def update_paper_metadata(paper_id: int, fields: dict) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE papers SET subject_code = %s, subject_name = %s, semester = %s, year = %s, time = %s, marks = %s "
            "WHERE id = %s",
            (*(fields[k] for k in PAPER_FIELDS), paper_id),
        )
```

- [ ] **Step 4: Split storing from embedding in `backend/services/vector_service.py`**

Replace `upsert_paper` with:

```python
    def upsert_paper(self, paper_id: int, text: str, metadata: dict):
        """Uploads paper vector and metadata to Qdrant."""
        vector = self.get_embedding(text)
        if not vector:
            return False
        return self.upsert_paper_vector(paper_id, vector, text, metadata)

    def upsert_paper_vector(self, paper_id: int, vector: list, text: str, metadata: dict) -> bool:
        """Stores a precomputed vector with the paper's metadata and full text."""
        try:
            payload = metadata.copy()
            payload["full_text"] = text
            self.client.upsert(
                collection_name=COLLECTION_NAME,
                points=[models.PointStruct(id=paper_id, vector=vector, payload=payload)]
            )
            return True
        except Exception as e:
            logger.error(f"Qdrant Upsert Error: {e}")
            return False
```

- [ ] **Step 5: Implement `backend/services/reprocess.py`**

```python
"""Re-runs stored papers through the upload pipeline (OCR, metadata, embedding)."""
import logging
import os
from collections import Counter
from dataclasses import dataclass, field

from services.ocr_service import DocumentProcessor
from services.paper_metadata import PAPER_FIELDS, to_paper_fields
from services.paper_store import PaperRow, get_papers, update_paper_metadata

logger = logging.getLogger(__name__)

STATUSES = ("preview", "updated", "skipped", "failed")
_MARKERS = {"preview": "*", "updated": "+", "skipped": "-", "failed": "x"}


@dataclass
class ReprocessResult:
    paper_id: int
    filename: str
    status: str
    changes: dict = field(default_factory=dict)  # field -> (old, new)
    message: str = ""


def merge_fields(old: dict, new: dict) -> dict:
    """New non-empty values win; an empty new value never wipes out existing data."""
    return {k: new.get(k) if new.get(k) else old.get(k) for k in PAPER_FIELDS}


def diff_fields(old: dict, merged: dict) -> dict:
    return {k: (old.get(k), merged[k]) for k in PAPER_FIELDS if old.get(k) != merged[k]}


def reprocess_paper(row: PaperRow, processor, vector_service, update_metadata, apply: bool) -> ReprocessResult:
    if not os.path.exists(row.file_path):
        return ReprocessResult(row.id, row.filename, "skipped", message=f"PDF not found at {row.file_path}")
    try:
        extracted = processor.process_pdf(row.file_path)
        merged = merge_fields(row.fields, to_paper_fields(extracted.get("metadata")))
        changes = diff_fields(row.fields, merged)
        if not apply:
            return ReprocessResult(row.id, row.filename, "preview", changes)

        full_text = processor.extract_full_text(row.file_path)
        vector = vector_service.get_embedding(full_text, input_type="passage")
        if not vector:
            return ReprocessResult(row.id, row.filename, "failed", changes, "embedding failed; nothing was changed")
        payload = {"subject_code": merged["subject_code"], "subject_name": merged["subject_name"],
                   "year": merged["year"], "filename": row.filename}
        if not vector_service.upsert_paper_vector(row.id, vector, full_text, payload):
            return ReprocessResult(row.id, row.filename, "failed", changes, "Qdrant update failed; nothing was changed")
        if changes:
            update_metadata(row.id, merged)
        return ReprocessResult(row.id, row.filename, "updated", changes)
    except Exception as error:
        logger.exception("Reprocessing paper %s failed", row.id)
        return ReprocessResult(row.id, row.filename, "failed", message=str(error))


def reprocess_many(rows, processor, vector_service, update_metadata, apply: bool, out=print) -> list[ReprocessResult]:
    results = []
    for row in rows:
        result = reprocess_paper(row, processor, vector_service, update_metadata, apply)
        out(f"{_MARKERS[result.status]} {result.paper_id}: {result.filename} [{result.status}] {result.message}".rstrip())
        for name, (old, new) in result.changes.items():
            out(f"    {name}: {old!r} -> {new!r}")
        results.append(result)
    return results


def run_reprocess(ids: list[int] | None, apply: bool, out=print) -> int:
    from services.vector_service import VectorService

    rows = get_papers(ids)
    for missing in sorted(set(ids or []) - {row.id for row in rows}):
        out(f"- {missing}: no paper with this id")
    if not rows:
        out("Nothing to reprocess.")
        return 1

    results = reprocess_many(rows, DocumentProcessor(upload_dir="backend/papers"), VectorService(),
                             update_paper_metadata, apply, out)
    counts = Counter(result.status for result in results)
    out("Summary: " + ", ".join(f"{counts[s]} {s}" for s in STATUSES if counts[s]))
    if not apply:
        out("Preview only: nothing was written. Re-run with --apply to save these changes.")
    return 1 if counts["failed"] else 0
```

- [ ] **Step 6: Add the `reprocess` subcommand to `backend/admin_cli.py`**

In `main()`, after the `make-admin` parser lines, add:

```python
    reprocess_parser = commands.add_parser("reprocess", help="Re-run stored papers through OCR, metadata and embedding")
    selection = reprocess_parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--id", type=int, action="append", dest="ids", help="paper id (repeatable)")
    selection.add_argument("--all", action="store_true", help="every paper")
    reprocess_parser.add_argument("--apply", action="store_true", help="write changes (default: preview only)")
```

and before `return 1`:

```python
    if args.command == "reprocess":
        from services.reprocess import run_reprocess
        return run_reprocess(None if args.all else args.ids, apply=args.apply)
```

Update the module docstring to list `python backend/admin_cli.py reprocess --id 7 [--apply]`.

- [ ] **Step 7: Run the tests**

Run: `python -m pytest -v`
Expected: all pass.

- [ ] **Step 8: Try a preview locally (manual)**

Run: `python backend/admin_cli.py reprocess --all`
Expected locally: every paper is `[skipped] PDF not found …`, because the PDFs only exist on the server, and the command exits 0. On the server, after deploying:

```bash
docker exec -it prepwise-backend python backend/admin_cli.py reprocess --id 7 --id 8 --id 14
docker exec -it prepwise-backend python backend/admin_cli.py reprocess --id 7 --id 8 --id 14 --apply
```

- [ ] **Step 9: Commit**

```bash
git add backend/services/reprocess.py backend/services/paper_store.py backend/services/vector_service.py backend/admin_cli.py backend/tests/test_reprocess.py
git commit -m "feat: reprocess command to re-feed stored papers through the pipeline

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Frontend sign-in foundation

**Files:**
- Create: `frontend/src/lib/auth/msal.ts`, `frontend/src/lib/api.ts`, `frontend/src/components/AuthProvider.tsx`, `frontend/src/components/AuthMenu.tsx`, `frontend/src/app/auth/redirect/page.tsx`
- Modify: `frontend/package.json` (via npm), `frontend/src/app/layout.tsx`, `frontend/src/components/Navbar.tsx`

**Interfaces:**
- Consumes: `GET /api/v1/me`.
- Produces:
  - `msal.ts`: `AZURE_CLIENT_ID`, `AUTH_ENABLED: boolean`, `API_SCOPE`, `REDIRECT_PATH = "/auth/redirect"`, `loginRequest`, `getMsalInstance(): PublicClientApplication`, `msalReady: Promise<void>`, `markMsalReady(): void`.
  - `api.ts`: `class ApiRequestError extends Error { status; code; retryAfterSeconds }`, `apiFetch(path: string, init?: RequestInit, baseUrl?: string): Promise<Response>` (throws `ApiRequestError` on non-2xx), `describeApiError(error: unknown): string`.
  - `AuthProvider.tsx`: types `Role`, `Limits`, `Me`; `AuthProvider`; `useAuth(): { enabled, ready, me, signIn, signOut, refresh }`.

- [ ] **Step 1: Install MSAL**

Run: `cd frontend && npm install @azure/msal-browser@^5.23.0 @azure/msal-react@^5.7.1`
Expected: both added to `dependencies`, no peer-dependency errors (msal-react 5.7.1 supports React `^19.2.1`).

- [ ] **Step 2: Create `frontend/src/lib/auth/msal.ts`**

```ts
import { PublicClientApplication, type Configuration } from "@azure/msal-browser";

export const AZURE_CLIENT_ID = process.env.NEXT_PUBLIC_AZURE_CLIENT_ID ?? "";
/** Sign-in is switched off when no client ID is configured (e.g. a fresh local checkout). */
export const AUTH_ENABLED = AZURE_CLIENT_ID.length > 0;
export const API_SCOPE = `api://${AZURE_CLIENT_ID}/access_as_user`;
/** MSAL v5 returns every sign-in and sign-out through this "redirect bridge" page. */
export const REDIRECT_PATH = "/auth/redirect";
export const loginRequest = { scopes: [API_SCOPE] };

let instance: PublicClientApplication | null = null;

/** One shared instance. Safe during server rendering: MSAL detects there's no browser and stays inert. */
export function getMsalInstance(): PublicClientApplication {
    if (!instance) {
        const config: Configuration = {
            auth: {
                clientId: AZURE_CLIENT_ID,
                authority: "https://login.microsoftonline.com/common",
                redirectUri: REDIRECT_PATH, // resolved against the current origin
                postLogoutRedirectUri: REDIRECT_PATH,
            },
            cache: { cacheLocation: "sessionStorage" },
        };
        instance = new PublicClientApplication(config);
    }
    return instance;
}

let resolveReady: () => void = () => {};
/** Resolves once MsalProvider has initialised MSAL and processed any sign-in redirect. */
export const msalReady = new Promise<void>((resolve) => {
    resolveReady = resolve;
});

export function markMsalReady(): void {
    resolveReady();
}
```

- [ ] **Step 3: Create `frontend/src/lib/api.ts`**

```ts
import { InteractionRequiredAuthError } from "@azure/msal-browser";
import { API_BASE_URL } from "@/lib/utils";
import { AUTH_ENABLED, getMsalInstance, loginRequest, msalReady } from "@/lib/auth/msal";

export class ApiRequestError extends Error {
    constructor(
        public status: number,
        public code: string,
        message: string,
        public retryAfterSeconds: number | null = null,
    ) {
        super(message);
    }
}

/** The signed-in user's access token, or null when signed out (requests then go out anonymously). */
async function getAccessToken(): Promise<string | null> {
    if (!AUTH_ENABLED) return null;
    await msalReady;
    const msal = getMsalInstance();
    const account = msal.getActiveAccount() ?? msal.getAllAccounts()[0];
    if (!account) return null;
    try {
        const result = await msal.acquireTokenSilent({ ...loginRequest, account });
        return result.accessToken;
    } catch (error) {
        if (error instanceof InteractionRequiredAuthError) {
            await msal.acquireTokenRedirect({ ...loginRequest, account });
            return null; // the page is navigating away
        }
        throw error;
    }
}

/** fetch() against the API with the user's token attached. Throws ApiRequestError on non-2xx responses. */
export async function apiFetch(path: string, init: RequestInit = {}, baseUrl: string = API_BASE_URL): Promise<Response> {
    const token = await getAccessToken();
    const headers = new Headers(init.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const response = await fetch(`${baseUrl}${path}`, { ...init, headers });
    if (!response.ok) {
        let body: { detail?: unknown; code?: unknown } = {};
        try {
            body = await response.json();
        } catch {
            // non-JSON error body
        }
        const retryAfter = Number(response.headers.get("Retry-After"));
        throw new ApiRequestError(
            response.status,
            typeof body.code === "string" ? body.code : "error",
            typeof body.detail === "string" ? body.detail : "Something went wrong. Please try again.",
            Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null,
        );
    }
    return response;
}

export function describeApiError(error: unknown): string {
    if (error instanceof ApiRequestError) {
        if (error.status === 429 && error.retryAfterSeconds) {
            const hours = Math.max(1, Math.round(error.retryAfterSeconds / 3600));
            return `${error.message} (about ${hours} hour${hours === 1 ? "" : "s"} from now)`;
        }
        return error.message;
    }
    return "Couldn't reach the server. Check your connection and try again.";
}
```

- [ ] **Step 4: Create `frontend/src/components/AuthProvider.tsx`**

```tsx
"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { MsalProvider, useMsal } from "@azure/msal-react";
import { EventType, InteractionStatus, type AuthenticationResult, type EventMessage } from "@azure/msal-browser";
import { AUTH_ENABLED, REDIRECT_PATH, getMsalInstance, loginRequest, markMsalReady, msalReady } from "@/lib/auth/msal";
import { apiFetch } from "@/lib/api";

export type Role = "student" | "faculty" | "admin";
export type Limits = { generate: number | null; upload: number | null; syllabus: number | null };
export type Me = {
    id: number;
    name: string | null;
    email: string | null;
    role: Role | null;
    verified: boolean;
    limits: Limits; // null = unlimited
    usedToday: { generate: number; upload: number; syllabus: number };
};

type AuthState = {
    enabled: boolean; // sign-in is configured
    ready: boolean; // MSAL has started and /me has loaded (or there is no signed-in account)
    me: Me | null; // null when signed out
    signIn: () => void;
    signOut: () => void;
    refresh: () => Promise<void>;
};

const signedOut: AuthState = {
    enabled: false,
    ready: true,
    me: null,
    signIn: () => {},
    signOut: () => {},
    refresh: async () => {},
};

const AuthContext = createContext<AuthState>(signedOut);

export function useAuth(): AuthState {
    return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: ReactNode }) {
    const pathname = usePathname();
    // The redirect bridge must not run MSAL itself: it hands the response back to the main window.
    if (!AUTH_ENABLED || pathname === REDIRECT_PATH) {
        return <SignedOutProvider>{children}</SignedOutProvider>;
    }
    return (
        <MsalProvider instance={getMsalInstance()}>
            <AuthStateProvider>{children}</AuthStateProvider>
        </MsalProvider>
    );
}

function SignedOutProvider({ children }: { children: ReactNode }) {
    useEffect(() => {
        markMsalReady();
    }, []);
    return <AuthContext.Provider value={signedOut}>{children}</AuthContext.Provider>;
}

function AuthStateProvider({ children }: { children: ReactNode }) {
    const { instance, accounts, inProgress } = useMsal();
    const accountId = accounts[0]?.homeAccountId ?? null;
    const [me, setMe] = useState<Me | null>(null);
    const [loaded, setLoaded] = useState(false);

    // Remember who signed in so token requests know which account to use.
    useEffect(() => {
        const callbackId = instance.addEventCallback((event: EventMessage) => {
            if (event.eventType === EventType.LOGIN_SUCCESS && event.payload) {
                instance.setActiveAccount((event.payload as AuthenticationResult).account);
            }
        });
        return () => {
            if (callbackId) instance.removeEventCallback(callbackId);
        };
    }, [instance]);

    useEffect(() => {
        if (inProgress === InteractionStatus.None) markMsalReady();
    }, [inProgress]);

    const refresh = useCallback(async () => {
        await msalReady;
        if (!accountId) {
            setMe(null);
            setLoaded(true);
            return;
        }
        try {
            const response = await apiFetch("/api/v1/me");
            setMe((await response.json()) as Me);
        } catch {
            setMe(null);
        } finally {
            setLoaded(true);
        }
    }, [accountId]);

    useEffect(() => {
        if (inProgress === InteractionStatus.None) void refresh();
    }, [inProgress, refresh]);

    const value = useMemo<AuthState>(
        () => ({
            enabled: true,
            ready: loaded,
            me,
            signIn: () => void instance.loginRedirect(loginRequest),
            signOut: () => void instance.logoutRedirect(),
            refresh,
        }),
        [instance, loaded, me, refresh],
    );

    return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
```

- [ ] **Step 5: Create the redirect bridge page `frontend/src/app/auth/redirect/page.tsx`**

```tsx
"use client";

import { useEffect } from "react";

/** MSAL v5 redirect bridge: hands the sign-in response back to the page that started it. */
export default function AuthRedirectPage() {
    useEffect(() => {
        import("@azure/msal-browser/redirect-bridge")
            .then(({ broadcastResponseToMainFrame }) => broadcastResponseToMainFrame())
            .catch(() => window.location.replace("/"));
    }, []);

    return <p className="container mx-auto px-4 py-16 text-sm text-muted-foreground">Signing you in…</p>;
}
```

- [ ] **Step 6: Create `frontend/src/components/AuthMenu.tsx`**

```tsx
"use client";

import { LogIn, LogOut } from "lucide-react";
import { useAuth } from "@/components/AuthProvider";

const ROLE_LABEL = { student: "Student", faculty: "Faculty", admin: "Admin" } as const;

export function AuthMenu() {
    const { enabled, ready, me, signIn, signOut } = useAuth();
    if (!enabled) return null;
    if (!ready) return <span className="h-8 w-24 animate-pulse rounded-md bg-muted" aria-hidden />;

    if (!me) {
        return (
            <button
                onClick={signIn}
                className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm font-medium hover:bg-muted transition-colors"
            >
                <LogIn className="h-4 w-4" /> Sign in with Microsoft
            </button>
        );
    }

    const role = me.role ? ROLE_LABEL[me.role] : "New user";
    const badge = me.role === "faculty" && !me.verified ? `${role} (trial)` : role;
    return (
        <div className="flex items-center gap-3 text-sm">
            <span className="hidden md:inline text-foreground/80">{me.name ?? me.email}</span>
            <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">{badge}</span>
            <button onClick={signOut} className="text-foreground/60 hover:text-foreground" aria-label="Sign out">
                <LogOut className="h-4 w-4" />
            </button>
        </div>
    );
}
```

- [ ] **Step 7: Make the navbar role-aware (`frontend/src/components/Navbar.tsx`)**

Replace the whole file:

```tsx
"use client";

import Link from "next/link";
import { BookOpen, Upload, Shield } from "lucide-react";
import { AuthMenu } from "@/components/AuthMenu";
import { useAuth } from "@/components/AuthProvider";

export function Navbar() {
    const { me } = useAuth();
    const canUploadSyllabus = me?.role === "admin" || (me?.role === "faculty" && me.verified);

    return (
        <nav className="border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 sticky top-0 z-50">
            <div className="container mx-auto flex h-14 items-center px-4 md:px-6">
                <Link href="/" className="mr-6 flex items-center space-x-2">
                    <BookOpen className="h-6 w-6 text-primary" />
                    <span className="hidden font-bold sm:inline-block">PrepWise</span>
                </Link>
                <div className="flex flex-1 items-center justify-between space-x-2 md:justify-end">
                    <div className="flex items-center space-x-4 md:space-x-6 text-sm font-medium">
                        {canUploadSyllabus && (
                            <Link href="/syllabus" className="flex items-center space-x-2 text-foreground/60 transition-colors hover:text-foreground/80">
                                <BookOpen className="h-4 w-4" />
                                <span>Syllabus</span>
                            </Link>
                        )}
                        <Link href="/upload" className="flex items-center space-x-2 text-foreground/60 transition-colors hover:text-foreground/80">
                            <Upload className="h-4 w-4" />
                            <span>Upload Papers</span>
                        </Link>
                        <Link href="/papers" className="text-foreground/60 transition-colors hover:text-foreground/80">
                            Browse Papers
                        </Link>
                        <Link href="/generate" className="text-primary font-semibold transition-colors hover:text-primary/80">
                            Generate Paper
                        </Link>
                        {me?.role === "admin" && (
                            <Link href="/admin/users" className="flex items-center space-x-1 text-foreground/60 transition-colors hover:text-foreground/80">
                                <Shield className="h-4 w-4" />
                                <span>Admin</span>
                            </Link>
                        )}
                        <AuthMenu />
                    </div>
                </div>
            </div>
        </nav>
    );
}
```

- [ ] **Step 8: Wrap the app in `AuthProvider` (`frontend/src/app/layout.tsx`)**

Add `import { AuthProvider } from "@/components/AuthProvider";` and change the body contents to:

```tsx
      <body className={cn(inter.className, "flex min-h-screen flex-col bg-background font-sans antialiased")} suppressHydrationWarning>
        <AuthProvider>
          <Navbar />
          <main className="flex-1">{children}</main>
          <Footer />
        </AuthProvider>
      </body>
```

- [ ] **Step 9: Type-check, lint and build**

Run: `cd frontend && rm -rf .next/dev && npx tsc --noEmit -p . && npx eslint src && npx next build`
Expected: no errors; routes include `/auth/redirect`. If ESLint flags `react-hooks/set-state-in-effect` in `AuthProvider`, check that every `setMe`/`setLoaded` call happens after the `await msalReady` line (as written); don't disable the rule.

- [ ] **Step 10: Manual sign-in check**

1. `frontend/.env.local` has `NEXT_PUBLIC_AZURE_CLIENT_ID` (Task 1). Start the backend (`run_backend.bat`) and the frontend (`cd frontend && npm run dev`).
2. Open http://localhost:3000 → **Sign in with Microsoft** → sign in with a personal account. You land back on the page you started from, and the navbar shows your name with the badge **Admin** (from Task 11) or **New user**.
3. Reload: you're still signed in (sessionStorage).
4. Sign out: you land back on the site, signed out.
5. Temporarily remove `NEXT_PUBLIC_AZURE_CLIENT_ID` and restart `npm run dev`: the site works with no sign-in button. Restore it afterwards.

- [ ] **Step 11: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/lib/auth/msal.ts frontend/src/lib/api.ts frontend/src/components/AuthProvider.tsx frontend/src/components/AuthMenu.tsx frontend/src/app/auth/redirect/page.tsx frontend/src/components/Navbar.tsx frontend/src/app/layout.tsx
git commit -m "feat(frontend): Microsoft sign-in with MSAL, /me state and role-aware navbar

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Role picker and gated pages

**Files:**
- Create: `frontend/src/components/AccessNotice.tsx`, `frontend/src/app/welcome/page.tsx`
- Modify: `frontend/src/components/AuthProvider.tsx`, `frontend/src/app/upload/page.tsx`, `frontend/src/app/syllabus/page.tsx`, `frontend/src/app/generate/page.tsx`

**Interfaces:**
- Consumes: `useAuth()`, `apiFetch`, `describeApiError`, `ApiRequestError`, `POST /api/v1/me/role`.
- Produces: `AccessNotice({ title, message, showSignIn? })`; `/welcome` page.

- [ ] **Step 1: Create `frontend/src/components/AccessNotice.tsx`**

```tsx
"use client";

import { Lock } from "lucide-react";
import { useAuth } from "@/components/AuthProvider";

export function AccessNotice({ title, message, showSignIn = false }: { title: string; message: string; showSignIn?: boolean }) {
    const { enabled, signIn } = useAuth();
    return (
        <div className="container mx-auto max-w-xl px-4 py-24 text-center">
            <Lock className="mx-auto mb-4 h-10 w-10 text-primary" />
            <h1 className="mb-3 text-2xl font-bold">{title}</h1>
            <p className="mb-8 text-muted-foreground">{message}</p>
            {showSignIn && enabled && (
                <button onClick={signIn} className="rounded-lg bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground hover:bg-primary/90">
                    Sign in with Microsoft
                </button>
            )}
        </div>
    );
}
```

- [ ] **Step 2: Create the role picker `frontend/src/app/welcome/page.tsx`**

```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { GraduationCap, Presentation } from "lucide-react";
import { useAuth } from "@/components/AuthProvider";
import { AccessNotice } from "@/components/AccessNotice";
import { apiFetch, describeApiError } from "@/lib/api";

const CHOICES = [
    {
        role: "student" as const,
        icon: GraduationCap,
        title: "Student",
        text: "Browse and search past papers, and upload up to 20 papers a day.",
    },
    {
        role: "faculty" as const,
        icon: Presentation,
        title: "Faculty (trial)",
        text: "Generate up to 3 mock papers a day. An admin can verify you for higher limits and uploads.",
    },
];

export default function WelcomePage() {
    const { ready, me, refresh } = useAuth();
    const router = useRouter();
    const [saving, setSaving] = useState<string | null>(null);
    const [error, setError] = useState("");

    if (!ready) return null;
    if (!me) return <AccessNotice title="Sign in first" message="Sign in with your Microsoft account to choose a role." showSignIn />;
    if (me.role) return <AccessNotice title="You're all set" message="Your role is already chosen. An admin can change it if needed." />;

    const choose = async (role: "student" | "faculty") => {
        setSaving(role);
        setError("");
        try {
            await apiFetch("/api/v1/me/role", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ role }),
            });
            await refresh();
            router.replace(role === "faculty" ? "/generate" : "/papers");
        } catch (e) {
            setError(describeApiError(e));
            setSaving(null);
        }
    };

    return (
        <div className="container mx-auto max-w-3xl px-4 py-20">
            <h1 className="mb-2 text-center text-3xl font-bold">Welcome to PrepWise</h1>
            <p className="mb-10 text-center text-muted-foreground">How will you use it? You can only choose once.</p>
            {error && <p className="mb-6 text-center text-sm text-red-400">{error}</p>}
            <div className="grid gap-6 sm:grid-cols-2">
                {CHOICES.map(({ role, icon: Icon, title, text }) => (
                    <button
                        key={role}
                        disabled={saving !== null}
                        onClick={() => choose(role)}
                        className="rounded-2xl border border-border bg-card p-8 text-left transition-all hover:-translate-y-1 hover:border-primary/50 disabled:opacity-50"
                    >
                        <Icon className="mb-4 h-8 w-8 text-primary" />
                        <h2 className="mb-2 text-lg font-semibold">{title}</h2>
                        <p className="text-sm text-muted-foreground">{text}</p>
                        {saving === role && <p className="mt-4 text-xs text-primary">Saving…</p>}
                    </button>
                ))}
            </div>
        </div>
    );
}
```

- [ ] **Step 3: Send new users to `/welcome` (`frontend/src/components/AuthProvider.tsx`)**

In `AuthStateProvider`, add `import { usePathname, useRouter } from "next/navigation";` (merge it with the existing `usePathname` import), and after the `refresh` effect add:

```tsx
    const router = useRouter();
    const currentPath = usePathname();
    useEffect(() => {
        if (me && me.role === null && currentPath !== "/welcome") router.replace("/welcome");
    }, [me, currentPath, router]);
```

- [ ] **Step 4: Gate the upload page (`frontend/src/app/upload/page.tsx`)**

1. Replace `import { API_BASE_URL } from "@/lib/utils";` with:

```tsx
import { useAuth } from "@/components/AuthProvider";
import { AccessNotice } from "@/components/AccessNotice";
import { apiFetch, describeApiError } from "@/lib/api";
```

2. At the top of `UploadPage()`, add `const { ready, me, refresh } = useAuth();`.
3. Replace the `fetch(`${API_BASE_URL}/api/v1/documents/ingest`, { … })` chain (lines 59–78) with:

```tsx
            apiFetch("/api/v1/documents/ingest", { method: "POST", body: formData })
            .then(() => {
                clearInterval(progressInterval);
                setTasks(prev => prev.map(t =>
                    t.id === task.id ? { ...t, progress: 100, status: "success" } : t
                ));
                void refresh();
            })
            .catch(err => {
                clearInterval(progressInterval);
                setTasks(prev => prev.map(t =>
                    t.id === task.id ? { ...t, status: "error", errorMessage: describeApiError(err) } : t
                ));
            });
```

and add `refresh` to the `useCallback` dependency array: `}, [refresh]);`.

4. Right before `return (` of the page, add:

```tsx
    if (!ready) return null;
    if (me?.role === "faculty" && !me.verified) {
        return <AccessNotice title="Paper uploads aren't available on a faculty trial"
            message="Trial faculty accounts can generate mock papers. An admin can verify your account to enable uploads." />;
    }
    const allowance = !me
        ? "Uploading anonymously: 5 papers a day. Sign in as a student for 20."
        : me.limits.upload === null
            ? "Unlimited uploads."
            : `${me.usedToday.upload} of ${me.limits.upload} uploads used today.`;
```

5. Under the heading's `<p>` (after "Contribute to the academic community…"), add:

```tsx
                    <p className="mt-3 text-sm text-primary">{allowance}</p>
```

6. Show the error text: in the task row, after the progress-bar block, add:

```tsx
                                            {task.status === "error" && task.errorMessage && (
                                                <p className="mt-2 text-xs text-red-400">{task.errorMessage}</p>
                                            )}
```

- [ ] **Step 5: Gate the syllabus page (`frontend/src/app/syllabus/page.tsx`)**

1. Change `import { cn, API_BASE_URL } from "@/lib/utils";` to `import { cn } from "@/lib/utils";` and add:

```tsx
import { useAuth } from "@/components/AuthProvider";
import { AccessNotice } from "@/components/AccessNotice";
import { apiFetch, describeApiError } from "@/lib/api";
```

2. At the top of `SyllabusUploadPage()`, add `const { ready, me } = useAuth();`.
3. Replace the `fetch(`${API_BASE_URL}/api/v1/syllabus/upload`, …)` chain (lines 56–77) with:

```tsx
        apiFetch("/api/v1/syllabus/upload", { method: "POST", body: formData })
        .then((res) => res.json())
        .then((data) => {
            clearInterval(progressInterval);
            setProgress(100);
            setProcessState("success");
            setExtractedModules(data.data.modules);
        })
        .catch(err => {
            clearInterval(progressInterval);
            setProcessState("error");
            setErrorMessage(describeApiError(err));
        });
```

4. Right before `return (`, add:

```tsx
    if (!ready) return null;
    if (!me) return <AccessNotice title="Faculty only" message="Sign in with a verified faculty account to upload syllabi." showSignIn />;
    if (!(me.role === "admin" || (me.role === "faculty" && me.verified))) {
        return <AccessNotice title="Verified faculty only" message="Syllabus uploads need a verified faculty account. Ask an admin to verify you." />;
    }
```

- [ ] **Step 6: Gate the generate page (`frontend/src/app/generate/page.tsx`)**

1. Replace `import { API_BASE_URL } from "@/lib/utils";` with:

```tsx
import { API_BASE_URL } from "@/lib/utils";
import { useAuth } from "@/components/AuthProvider";
import { AccessNotice } from "@/components/AccessNotice";
import { apiFetch, describeApiError } from "@/lib/api";
```

2. At the top of `GeneratePage()`, add `const { ready, me, refresh } = useAuth();`.
3. In `fetchSyllabus`, replace the `try { … }` body with:

```tsx
        try {
            const res = await apiFetch(`/api/v1/syllabus/${encodeURIComponent(subjectCode.trim())}`);
            const data = await res.json();
            setSyllabus(data.data);
        } catch {
            setSyllabus(null);
        } finally {
```

(keep the existing `finally` body).

4. In `generatePaper`, replace the `fetch(…)` call and the `if (!response.ok) { … }` block (lines 216–226) with:

```tsx
            let response: Response;
            try {
                response = await apiFetch("/api/v1/generate/mock-paper-stream", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ subject, sections: sectionsWithPool })
                }, API_URL);
            } catch (error) {
                alert(describeApiError(error));
                setIsGenerating(false);
                return;
            }
```

5. After the streaming loop finishes (just before the outer `catch (error)` of `generatePaper`), add `void refresh();` so the usage counter updates.
6. Right before the component's main `return (` (after every hook), add:

```tsx
    if (!ready) return null;
    if (!me) {
        return <AccessNotice title="Faculty only" message="Sign in with a faculty account to generate mock papers." showSignIn />;
    }
    if (me.role !== "faculty" && me.role !== "admin") {
        return <AccessNotice title="Faculty only" message="Mock paper generation is for faculty accounts." />;
    }
```

7. Under the page heading, show usage:

```tsx
                <p className="text-sm text-primary">
                    {me.limits.generate === null
                        ? "Unlimited papers."
                        : `${me.usedToday.generate} of ${me.limits.generate} papers used today${me.role === "faculty" && !me.verified ? " (trial)" : ""}.`}
                </p>
```

- [ ] **Step 7: Type-check, lint and build**

Run: `cd frontend && npx tsc --noEmit -p . && npx eslint src && npx next build`
Expected: no errors; routes include `/welcome`.

- [ ] **Step 8: Manual role walkthrough**

Use two browsers or private windows, each with a different personal Microsoft account:
1. New account → redirected to `/welcome` → choose **Student** → lands on `/papers`. Upload shows "0 of 20 uploads used today". Generate shows "Mock paper generation is for faculty accounts". The Syllabus link is hidden.
2. Another new account → choose **Faculty (trial)** → lands on `/generate` with "0 of 3 papers used today (trial)". Upload shows the trial notice. Generate a paper; the counter becomes 1.
3. Signed out: upload works and shows the anonymous allowance. Generate asks you to sign in.
4. A 6th anonymous upload in one day shows the limit message with the reset time.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/AccessNotice.tsx frontend/src/app/welcome/page.tsx frontend/src/components/AuthProvider.tsx frontend/src/app/upload/page.tsx frontend/src/app/syllabus/page.tsx frontend/src/app/generate/page.tsx
git commit -m "feat(frontend): role picker, per-role page access and usage messages

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Admin users page

**Files:**
- Create: `frontend/src/app/admin/users/page.tsx`

**Interfaces:**
- Consumes: `GET /api/v1/admin/users`, `PATCH /api/v1/admin/users/{id}`, `useAuth`, `apiFetch`, `describeApiError`, `AccessNotice`.

- [ ] **Step 1: Create `frontend/src/app/admin/users/page.tsx`**

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth, type Role } from "@/components/AuthProvider";
import { AccessNotice } from "@/components/AccessNotice";
import { apiFetch, describeApiError } from "@/lib/api";

type AdminUser = {
    id: number;
    email: string | null;
    name: string | null;
    role: Role | null;
    verified: boolean;
    createdAt: string | null;
    usedToday: { generate: number; upload: number; syllabus: number };
};

export default function AdminUsersPage() {
    const { ready, me } = useAuth();
    const [users, setUsers] = useState<AdminUser[]>([]);
    const [error, setError] = useState("");
    const isAdmin = me?.role === "admin";

    const load = useCallback(async () => {
        try {
            const response = await apiFetch("/api/v1/admin/users");
            setUsers(((await response.json()) as { users: AdminUser[] }).users);
            setError("");
        } catch (e) {
            setError(describeApiError(e));
        }
    }, []);

    useEffect(() => {
        if (isAdmin) void load();
    }, [isAdmin, load]);

    const update = async (id: number, change: { role?: Role; verified?: boolean }) => {
        try {
            await apiFetch(`/api/v1/admin/users/${id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(change),
            });
            await load();
        } catch (e) {
            setError(describeApiError(e));
        }
    };

    if (!ready) return null;
    if (!isAdmin) return <AccessNotice title="Admins only" message="This page is for PrepWise admins." showSignIn={!me} />;

    return (
        <div className="container mx-auto max-w-6xl px-4 py-12">
            <h1 className="mb-6 text-3xl font-bold">Users</h1>
            {error && <p className="mb-4 text-sm text-red-400">{error}</p>}
            <div className="overflow-x-auto rounded-xl border border-border">
                <table className="w-full text-sm">
                    <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
                        <tr>
                            <th className="p-3">User</th>
                            <th className="p-3">Role</th>
                            <th className="p-3">Verified</th>
                            <th className="p-3">Today (gen / up / syl)</th>
                            <th className="p-3">Joined</th>
                        </tr>
                    </thead>
                    <tbody>
                        {users.map((u) => (
                            <tr key={u.id} className="border-t border-border">
                                <td className="p-3">
                                    <div className="font-medium">{u.name ?? "-"}</div>
                                    <div className="text-xs text-muted-foreground">{u.email ?? "-"}</div>
                                </td>
                                <td className="p-3">
                                    <select
                                        value={u.role ?? ""}
                                        disabled={u.id === me?.id}
                                        onChange={(e) => void update(u.id, { role: e.target.value as Role })}
                                        className="rounded-md border border-border bg-background px-2 py-1"
                                    >
                                        {u.role === null && <option value="">(not chosen)</option>}
                                        <option value="student">Student</option>
                                        <option value="faculty">Faculty</option>
                                        <option value="admin">Admin</option>
                                    </select>
                                </td>
                                <td className="p-3">
                                    <input
                                        type="checkbox"
                                        checked={u.verified}
                                        disabled={u.role !== "faculty"}
                                        onChange={(e) => void update(u.id, { verified: e.target.checked })}
                                        aria-label={`Verified for ${u.email ?? u.id}`}
                                    />
                                </td>
                                <td className="p-3 tabular-nums">
                                    {u.usedToday.generate} / {u.usedToday.upload} / {u.usedToday.syllabus}
                                </td>
                                <td className="p-3 text-muted-foreground">
                                    {u.createdAt ? new Date(u.createdAt).toLocaleDateString() : "-"}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
```

- [ ] **Step 2: Type-check, lint and build**

Run: `cd frontend && npx tsc --noEmit -p . && npx eslint src && npx next build`
Expected: no errors; routes include `/admin/users`.

- [ ] **Step 3: Manual check**

As admin: open **Admin** in the navbar and tick **Verified** for the trial faculty account from Task 14. In that account's window, reload: the badge loses "(trial)", Generate shows "of 25", and Upload and Syllabus are available. Your own role dropdown is disabled. A non-admin visiting `/admin/users` sees "Admins only".

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/admin/users/page.tsx
git commit -m "feat(frontend): admin page to verify faculty and change roles

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Deployment configuration and documentation

**Files:**
- Modify: `backend/Dockerfile`, `docker-compose.yml`, `README.md`

- [ ] **Step 1: Trust nginx's forwarded client IP (`backend/Dockerfile`)**

Change the last line to:

```dockerfile
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
```

uvicorn reads the trusted proxy list from `FORWARDED_ALLOW_IPS` (set in compose, next step).

- [ ] **Step 2: Pass the new settings (`docker-compose.yml`)**

Add under `environment:`:

```yaml
      AZURE_CLIENT_ID: ${AZURE_CLIENT_ID}
      IP_HASH_SALT: ${IP_HASH_SALT}
      GENERATE_LIMIT_TRIAL: ${GENERATE_LIMIT_TRIAL:-3}
      GENERATE_LIMIT_VERIFIED: ${GENERATE_LIMIT_VERIFIED:-25}
      UPLOAD_LIMIT_ANON: ${UPLOAD_LIMIT_ANON:-5}
      UPLOAD_LIMIT_STUDENT: ${UPLOAD_LIMIT_STUDENT:-20}
      UPLOAD_LIMIT_FACULTY: ${UPLOAD_LIMIT_FACULTY:-10}
      SYLLABUS_LIMIT: ${SYLLABUS_LIMIT:-10}
      # The container is only reachable through nginx on this network, so trust its X-Forwarded-For
      FORWARDED_ALLOW_IPS: "*"
```

- [ ] **Step 3: Document it in `README.md`**

1. In **Environment Variables**, add to the `.env` example:

```env
# Microsoft sign-in (Entra app registration, see "Authentication")
AZURE_CLIENT_ID=<application-client-id>
# Random secret used to hash anonymous uploaders' IP addresses
IP_HASH_SALT=<long-random-string>
# Optional daily limits (defaults shown)
GENERATE_LIMIT_TRIAL=3
GENERATE_LIMIT_VERIFIED=25
UPLOAD_LIMIT_ANON=5
UPLOAD_LIMIT_STUDENT=20
UPLOAD_LIMIT_FACULTY=10
SYLLABUS_LIMIT=10
```

   and note the frontend's `frontend/.env.local`: `NEXT_PUBLIC_AZURE_CLIENT_ID=<same id>`.

2. Add a section **🔐 Authentication** covering:
   - **Roles table:** copy section 3 of the spec verbatim.
   - **How auth works:** the browser signs in with Microsoft via MSAL and sends an access token. FastAPI verifies the signature against Microsoft's published keys, checks audience, issuer (per tenant), expiry and the `access_as_user` scope, then loads the role from Postgres. The frontend only hides buttons; every rule is enforced by the API. Roles are self-declared because the college's Microsoft directory isn't available to this project; trial faculty get a small daily limit, and an admin verifies real faculty. Anonymous uploads are limited per IP address, stored only as a salted hash.
   - **Setup:** Task 1 step 1 of this plan, items 1–7.
   - **Admin commands:**

```bash
python backend/admin_cli.py make-admin you@outlook.com        # after signing in once
python backend/admin_cli.py reprocess --id 7                  # preview re-running paper 7
python backend/admin_cli.py reprocess --id 7 --apply          # write it
docker exec -it prepwise-backend python backend/admin_cli.py reprocess --all   # on the server
```

   - **Deployment note:** nginx must *overwrite* the client IP header (`proxy_set_header X-Forwarded-For $remote_addr;`, or `$http_cf_connecting_ip` behind Cloudflare) so visitors can't fake their IP to reset the anonymous limit.
   - **Tests:** `python -m pip install -r backend/requirements-dev.txt && python -m pytest`; optional `TEST_DATABASE_URL` for the SQL tests (never production).

- [ ] **Step 4: Full verification**

Run:

```bash
python -m pytest -v
cd frontend && npx tsc --noEmit -p . && npx eslint src && npx next build
```

Expected: all backend tests pass (Postgres tests skipped unless configured); the frontend builds.

- [ ] **Step 5: Commit**

```bash
git add backend/Dockerfile docker-compose.yml README.md
git commit -m "docs: auth setup, roles, admin commands and deployment settings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Before deploying (manual)**

1. Set `AZURE_CLIENT_ID` and `IP_HASH_SALT` on the server and `NEXT_PUBLIC_AZURE_CLIENT_ID` in Vercel.
2. Update the nginx config as described in step 3.
3. Merge to `default` only after the pull request's CI (the tests) passes. The image build runs on push to `default`.
4. After the new image starts, run `make-admin` for your account on the server.
