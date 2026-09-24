# PrepWise — Accounts & Access (Part A) Design

**Date:** 2026-09-25
**Status:** Approved 2026-09-25 (upload limits revised at approval)
**Scope:** Part A of the "proper product" work. Part B (upload moderation) gets its own spec.

## 1. Goal

Show real, working authentication and role-based access in PrepWise, a portfolio project that recruiters and visitors will try on the live site.

- Anyone can browse, search, and download published papers without signing in, and upload a few papers a day.
- Signing in (Microsoft account) raises the upload allowance for students and unlocks syllabus upload and mock-paper generation for faculty.
- Every rule is enforced by the FastAPI backend. The frontend only hides what a user can't use.
- NVIDIA API credits are protected: generation needs a faculty account, and every upload path has a daily limit.

**Done when:** a visitor can open the live site, sign in with any Microsoft account, pick a role, and see the student/faculty difference enforced — including by direct API calls that bypass the UI.

## 2. Decisions

| Decision | Choice | Why |
|---|---|---|
| Audience | One college, but a showcase; college IT won't be involved | Auth must work for recruiters, not pass a real tenant rollout |
| Identity provider | Microsoft (Entra ID), app registered in the owner's own free directory, accepting any work/school/personal Microsoft account | College runs on Microsoft 365; no IT approval needed |
| Where login runs | In the browser with MSAL (`@azure/msal-browser` + `@azure/msal-react`) | Frontend (Vercel) and backend are on different domains; cross-site cookies are unreliable, and the generation stream must keep calling the backend directly |
| Where access is decided | FastAPI validates a Microsoft access token on every protected request and looks up the role in Postgres | Backend is the only gatekeeper; can't be bypassed from the browser |
| Becoming faculty | Self-declared at first sign-in → **trial** faculty; admin can mark **verified** | Recruiters get instant access; verification tier shows deliberate design |
| Admin bootstrap | Tracked CLI command `make-admin`, not an email allowlist | Email claims must not drive authorization in multi-tenant apps (the 2023 "nOAuth" class of bugs) |
| Quotas | Daily limits per user (signed in) or per IP address (anonymous), atomic in SQL, reset at UTC midnight | Protects credits; no race between parallel requests |
| Anonymous uploads | Allowed, 5/day per IP address | Community uploads are the main selling point; signing in is an upgrade, not a gate |
| Error hygiene | Folded into this part | Every endpoint is touched anyway |
| Re-feeding papers | Admin `reprocess` command, preview by default | Re-run a stored paper through the pipeline without re-uploading it (re-uploading duplicates rows) |

## 3. Roles and permissions

| Capability | Anonymous | Student | Faculty (trial) | Faculty (verified) | Admin |
|---|---|---|---|---|---|
| Browse / search / download papers | ✓ | ✓ | ✓ | ✓ | ✓ |
| Upload papers | ✓ 5/day per IP | ✓ 20/day | – | ✓ 10/day | ✓ unlimited |
| Upload syllabi | – | – | – | ✓ 10/day | ✓ unlimited |
| View syllabi (used by the generator) | – | – | ✓ | ✓ | ✓ |
| Generate mock papers | – | – | ✓ 3/day | ✓ 25/day | ✓ unlimited |
| Manage users (role, verified) | – | – | – | – | ✓ |

- A new user has **no role** until they pick Student or Faculty once. Until then, besides the public endpoints, they can only call `/me` and `/me/role` (signed-in uploads return `403 role_required`, prompting the picker).
- Trial faculty can only generate mock papers (reading syllabi is part of generation). They cannot upload papers or syllabi. Syllabus uploads have their own daily counter.
- **Anonymous limit caveat:** the limit is per IP address, so everyone behind one NAT (e.g. campus Wi-Fi) shares the 5/day. Signing in as a student avoids it.
- The role choice is one-time. Only admin can change it afterwards.
- Nobody can choose `admin` through the API.
- **Syllabus replace rule:** a verified faculty user may create a syllabus for a subject code that has none. Replacing an existing one requires being its uploader or admin. Legacy syllabi (`uploaded_by` is NULL) can only be replaced by admin.
- All limits are environment variables (section 8).

## 4. Architecture

```
Browser (Next.js + MSAL) ──loginRedirect──► Microsoft identity platform
        ▲                                         │ user signs in
        └────────── ID + access tokens ───────────┘   (MSAL keeps them in sessionStorage)

Browser ── Authorization: Bearer <access token> ──► FastAPI
                                                     ├─ auth/tokens.py   verify signature + claims
                                                     ├─ auth/users.py    user row, role, quota (Postgres)
                                                     └─ auth/deps.py     route dependencies → 401 / 403 / 429
```

### 4.1 Microsoft Entra setup (one app registration)

- Supported account types: *any organizational directory and personal Microsoft accounts*.
- Platform **Single-page application**, redirect URIs: `http://localhost:3000`, `https://prepwise-opal-three.vercel.app`.
- **Expose an API:** Application ID URI `api://<client-id>`, scope `access_as_user`.
- **Manifest:** access token version **2** (`requestedAccessTokenVersion: 2`). Personal Microsoft accounts can't get tokens for a custom API without it.
- One registration serves both the SPA (client) and the API (resource).

### 4.2 Token validation (backend `auth/tokens.py`)

Using PyJWT with `PyJWKClient` against `https://login.microsoftonline.com/common/discovery/v2.0/keys` (keys cached; refetched on unknown `kid`):

1. Signature is RS256 and verifies against Microsoft's published key.
2. `aud` equals `AZURE_CLIENT_ID`.
3. `iss` equals `https://login.microsoftonline.com/{tid}/v2.0`, where `tid` is the token's own tenant claim (multi-tenant pattern).
4. `exp` / `nbf` valid (60 s leeway).
5. `scp` (space-separated) contains `access_as_user`.
6. User identity is the pair (`tid`, `oid`). `name` / `preferred_username` are stored for display only and never used for authorization.

Any failure → `401` with `WWW-Authenticate: Bearer`.

### 4.3 Backend modules

| Module | Responsibility |
|---|---|
| `backend/auth/tokens.py` | `verify_access_token(token) -> Claims`. No database access. |
| `backend/auth/users.py` | Postgres access: `get_or_create_user`, `set_role`, `list_users`, `update_user`, `consume_quota`, `refund_quota`. |
| `backend/auth/deps.py` | FastAPI dependencies: `optional_user`, `current_user`, `require_role(...)`, `quota(action)`. |
| `backend/api/me.py` | `GET /me`, `POST /me/role`. |
| `backend/api/admin.py` | `GET /admin/users`, `PATCH /admin/users/{id}`. |
| `backend/admin_cli.py` | `make-admin <email>` — lists matching accounts and asks for confirmation if more than one matches. `reprocess` — see 4.5. Tracked in git (unlike `dev-scripts/`) so it ships in the Docker image. |
| `backend/services/paper_metadata.py` | `normalize_subject_code()` — uppercase, spaces/hyphens/underscores removed (`aiml 201` → `AIML201`) — and `to_paper_fields()` mapping LLM metadata to `papers` columns. Used by upload and `reprocess`. |
| `backend/api/routes.py` | Existing endpoints gain dependencies; error handling cleaned up (section 7). |

`users.py` is injected through FastAPI dependencies so tests can replace it with an in-memory fake.

### 4.4 Frontend

| Piece | Responsibility |
|---|---|
| `src/lib/auth/msal.ts` | MSAL config and `PublicClientApplication` (client-only). Requests scope `api://<client-id>/access_as_user`. |
| `src/components/AuthProvider.tsx` | `"use client"` wrapper with `MsalProvider`; loads `/me` after sign-in and exposes `{ user, role, verified, signIn, signOut }`. |
| `src/lib/api.ts` | `apiFetch()` — when signed in, `acquireTokenSilent` (falls back to redirect) and attaches `Authorization`; when signed out, sends no token (anonymous upload). Maps 401 → sign-in prompt, 403 → "not allowed", 429 → "daily limit reached, resets in …". Used for upload, syllabus, generate (including the SSE stream) and admin calls. Browse/search stay plain `fetch`. |
| `src/app/welcome/page.tsx` | One-time Student / Faculty picker, shown when `/me` returns no role. Faculty option is labelled with the trial limit. |
| `src/app/admin/users/page.tsx` | Admin-only table: role, verified toggle, today's usage. |
| `Navbar` | "Sign in with Microsoft" button, or name + role badge + sign-out. Links shown per role. |
| Upload page | Works signed out, showing "N of 5 uploads left today — sign in as a student for 20". Trial faculty see "Paper uploads need verified faculty or a student account". |
| Syllabus / Generate pages | Show a sign-in or "faculty only" message instead of the form when the user can't use it. |

### 4.5 Reprocess command

Runs where the PDFs are (the server), e.g. `docker exec prepwise-backend python backend/admin_cli.py reprocess --id 7`.

```
python backend/admin_cli.py reprocess (--id N [--id M ...] | --all) [--apply]
```

For each selected paper:

1. Find its PDF from `papers.file_path`. If the file is missing, report it and skip.
2. Run the current upload pipeline: header OCR + metadata extraction (`process_pdf`), full-text OCR (`extract_full_text`).
3. Normalize the subject code.
4. **Without `--apply` (default):** print old → new metadata per paper and write nothing.
5. **With `--apply`:** update the Postgres row (metadata only; `id`, `filename`, `file_path`, `uploaded_by` unchanged) and upsert the Qdrant point (new vector from the new full text as `passage`, payload with the new metadata and full text).

If a step fails for a paper, that paper is reported and left unchanged; the rest continue. A summary lists updated, unchanged, skipped, and failed papers. It uses the same OCR/LLM/embedding services as upload, so it picks up whichever OCR engine is current.

Subject codes are also normalized on upload from now on, so new papers stay consistent with reprocessed ones.

## 5. Data model

Added in `init_db()` using idempotent statements (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE … ADD COLUMN IF NOT EXISTS`). Existing data is untouched.

```sql
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

CREATE TABLE IF NOT EXISTS usage_daily (
    subject  TEXT NOT NULL,   -- 'user:<users.id>' or 'ip:<sha256(ip + IP_HASH_SALT)>'
    action   TEXT NOT NULL CHECK (action IN ('generate', 'upload', 'syllabus')),
    day      DATE NOT NULL,
    count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (subject, action, day)
);

ALTER TABLE papers ADD COLUMN IF NOT EXISTS uploaded_by INTEGER REFERENCES users(id);
ALTER TABLE syllabi ADD COLUMN IF NOT EXISTS uploaded_by INTEGER REFERENCES users(id);
```

**Atomic quota check** (one statement; no row returned means the limit is reached):

```sql
INSERT INTO usage_daily (subject, action, day, count)
VALUES (%(subject)s, %(action)s, (now() AT TIME ZONE 'UTC')::date, 1)
ON CONFLICT (subject, action, day)
DO UPDATE SET count = usage_daily.count + 1
WHERE usage_daily.count < %(limit)s
RETURNING count;
```

Admin skips the quota entirely. `refund_quota` decrements (never below 0). Anonymous IPs are stored only as salted SHA-256 hashes, never in plain form. Anonymous uploads leave `papers.uploaded_by` NULL.

**Client IP:** read from `request.client.host`, with uvicorn started with `--proxy-headers --forwarded-allow-ips=<nginx address>` so the address is the visitor's, not nginx's. If Cloudflare sits in front of nginx, nginx must forward the real client IP (`CF-Connecting-IP`) as `X-Forwarded-For`.

## 6. API

| Endpoint | Access | Quota |
|---|---|---|
| `GET /documents`, `GET /documents/{id}/download`, `POST /search/semantic` | anyone | – |
| `GET /me` | signed in (creates the user row on first call; updates `last_seen_at`) | – |
| `POST /me/role` `{ "role": "student" \| "faculty" }` | signed in, role not yet set (else `409`) | – |
| `POST /documents/ingest` | anyone (token optional): anonymous 5/IP, student 20, trial faculty `403`, verified faculty 10, admin ∞ | upload |
| `POST /syllabus/upload` | verified faculty, admin + replace rule (trial faculty `403`) | syllabus (10, admin ∞) |
| `GET /syllabus/{code}` | faculty, admin | – |
| `POST /generate/mock-paper`, `POST /generate/mock-paper-stream` | faculty, admin | generate (trial 3 / verified 25 / admin ∞) |
| `GET /admin/users`, `PATCH /admin/users/{id}` `{ role?, verified? }` | admin | – |

`GET /me` response: `{ id, name, email, role, verified, limits: { generate, upload, syllabus }, usedToday: { generate, upload, syllabus } }`.

## 7. Error handling

- **Auth errors** use a stable shape: `{ "detail": "<human message>", "code": "<machine code>" }`.
  - `401 not_authenticated` / `invalid_token` — missing, malformed, expired, wrong audience/issuer/scope.
  - `403 role_required` / `forbidden` — signed in but not allowed (including the syllabus replace rule).
  - `409 role_already_set`.
  - `429 quota_exceeded` — with a `Retry-After` header (seconds until UTC midnight).
- **Server errors:** a global exception handler logs the full traceback (`logger.exception`) and returns `500 { "detail": "Something went wrong. Please try again.", "code": "internal_error" }`. The existing `raise HTTPException(500, detail=str(e))` patterns are replaced so raw exception text never reaches the browser.
- **SSE stream:** error events carry a generic message; details go to the log.
- **Quota refund:** if generation fails before the first question is streamed (or the non-streaming endpoint fails), the generate slot is refunded. If the upload pipeline fails before the paper is saved, the upload slot is refunded.
- **Logging:** `logging.basicConfig` in `main.py`; `print(...)` in `routes.py` and services replaced by module loggers. Tokens are never logged.

## 8. Configuration

| Variable | Where | Default |
|---|---|---|
| `AZURE_CLIENT_ID` | backend | — (required) |
| `GENERATE_LIMIT_TRIAL` | backend | `3` |
| `GENERATE_LIMIT_VERIFIED` | backend | `25` |
| `UPLOAD_LIMIT_ANON` | backend | `5` (per IP) |
| `UPLOAD_LIMIT_STUDENT` | backend | `20` |
| `UPLOAD_LIMIT_FACULTY` | backend | `10` (verified faculty) |
| `SYLLABUS_LIMIT` | backend | `10` (verified faculty) |
| `IP_HASH_SALT` | backend | — (required; random string) |
| `NEXT_PUBLIC_AZURE_CLIENT_ID` | frontend | — (required) |

`docker-compose.yml` passes the backend variables through. New backend dependencies: `PyJWT[crypto]`; dev/test: `pytest`, `httpx2`.

## 9. Testing

Backend tests with `pytest` + FastAPI `TestClient`, in `backend/tests/`. No network and no database needed for the default run.

- **Fixtures:** a test RSA key pair; a helper that mints tokens with chosen claims; `PyJWKClient` patched to return the test public key; `auth/users.py` replaced by an in-memory fake through dependency overrides; OCR, embedding, and LLM services stubbed.
- `test_startup.py` — the app imports, `/health` is 200, every expected route is registered (this would have caught the `backend.` import break).
- `test_tokens.py` — missing / malformed / expired / wrong-audience / wrong-issuer / missing-scope tokens → 401; a valid token → claims.
- `test_permissions.py` — a matrix of {anonymous, no-role, student, trial faculty, verified faculty, admin} × every protected endpoint → expected status.
- `test_me.py` — first `/me` creates the user; role can be set once (`409` after); `admin` can't be self-assigned (`422`).
- `test_quota.py` — generate: trial 3 then `429` with `Retry-After`, verified 25, admin unlimited, refund when generation fails before the first question. Upload: anonymous 5 per IP (a second IP gets its own 5), student 20, trial faculty `403`, verified faculty 10, admin unlimited; the stored subject for an IP is a hash, not the address.
- `test_syllabus_rules.py` — trial faculty `403`; owner can replace; other verified faculty `403`; legacy (NULL owner) is admin-only.
- `test_errors.py` — an unexpected exception returns the generic 500 body, not the exception text.
- `test_paper_metadata.py` — `aiml 201` → `AIML201`, `cse-432` → `CSE432`, empty/None handled.
- `test_reprocess.py` — with pipeline, database, and Qdrant stubbed: preview writes nothing; `--apply` updates metadata and the vector but not `id`/`filename`/`file_path`/`uploaded_by`; missing PDF is skipped and reported; a failure on one paper doesn't stop the others.
- **Optional SQL test:** runs the real quota statement against `TEST_DATABASE_URL` when set (e.g. a Neon branch); skipped otherwise.
- **CI:** a `test` job in `.github/workflows/deploy.yml` runs `pytest`; the image build depends on it.

Frontend: `next build` and ESLint must pass (Vercel builds the frontend). Manual end-to-end check: sign in with a personal Microsoft account on localhost, pick each role, confirm the table in section 3.

## 10. Implementation order and first risk

1. **Spike first:** register the Entra app and confirm that a *personal* Microsoft account can obtain an access token for `access_as_user` and that the backend validation in 4.2 accepts it. This is the one piece that depends on Microsoft configuration rather than our code.
2. Backend auth modules + tests.
3. Protect existing endpoints + error-handling cleanup.
4. `admin_cli.py` (`make-admin`, `reprocess`) + subject-code normalization.
5. Frontend MSAL, `/welcome`, `apiFetch`, gating, admin page.
6. README "How auth works" section (token validation, roles, trial vs verified, why roles are self-declared) and the admin commands.

## 11. Out of scope

- Part B: upload auto-checks, duplicate detection, review queue, unique stored filenames, upload size limit, background OCR. Anonymous uploads make Part B's junk filtering more important.
- Question-output formatting fix.
- Google or other sign-in providers.
- Parked for after Part A: generation timeout (GLM reasoning takes 2–3 min per question; reduce thinking without losing quality), switching OCR to `nemotron-ocr-v2` with Tesseract fallback, production paper-generation issues.
