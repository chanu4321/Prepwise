# PrepWise — Upload Moderation (Part B) Design

**Date:** 2026-09-26
**Status:** Approved in chat 2026-09-26; awaiting review of this written spec
**Scope:** Part B of the "proper product" work: upload auto-checks, review queue, duplicate detection, unique stored filenames, upload limits, background processing, and cleanup of the duplicates already in production. Duration awareness and the OCR line-grouping fix are separate items that come after this one.

## 1. Goal

Keep the public paper collection junk-free and duplicate-free without making uploaders wait.

- Uploads return in seconds; OCR, metadata and indexing run in the background, and the uploader sees the result (Live, Under review, Rejected, Failed).
- Clean uploads go live automatically. Suspicious ones wait in a review queue that only admins see and act on.
- The same file can never be stored twice, and likely copies (a different scan of the same paper) are caught for review.
- Two uploads with the same filename no longer overwrite each other on disk.
- Browsing, search, downloads and the mock-paper generator only ever use live papers.

**Done when:** an anonymous visitor uploads a clean paper and sees it go live within a few minutes; uploading the same file again is refused at once; a second scan of a live paper lands in the admin review queue next to the original; and the existing duplicates (rows 2/3/4 and 10/11) are gone from production.

## 2. Decisions

| Decision | Choice | Why |
|---|---|---|
| What gets held | Only flagged uploads; clean ones go live immediately | Keeps the community-upload feel; admins only see what needs a human |
| Reviewers | Admins only | Small project; faculty moderation is not needed |
| Exact copies | Refused at upload (same SHA-256 as any non-failed paper), no quota used | Certain, cheap, and the uploader gets an instant answer |
| Likely copies | Flagged for review, shown side by side with the matching paper | Different scans of one paper have different bytes but the same questions |
| Processing | Background, one worker thread in the backend; `papers` table is the job queue (option B) | Survives restarts without new infrastructure; one job at a time keeps NVIDIA spend predictable; avoids Cloudflare's 100 s cutoff |
| Limits | 10 MB and 20 pages per file, refused before processing | Real papers are 1–5 pages of phone photos, far below both |
| Admin powers | Edit details anywhere, approve/reject, take down/restore, retry, delete permanently, dedupe command | "Admin is sudo" |
| Quota on later failure/rejection | Not refunded | Keeps the worker free of quota bookkeeping; admins can retry failed papers |

## 3. Paper lifecycle

```
upload ──► processing ──► live ◄──────┐
              │   │                   │ approve / restore
              │   └──► review ────────┤
              │           │           │
              │           └──► rejected (reject / take down) ──► (restore) ──► live
              └──► failed (3 attempts) ──► (retry) ──► processing
```

| Status | Meaning | Public? | Has a vector? |
|---|---|---|---|
| `processing` | Waiting for or inside the worker | No | No |
| `live` | Published | Yes | Yes |
| `review` | Flagged by a check; waiting for an admin | No | Yes |
| `rejected` | Refused by an admin, or taken down | No | Yes (kept so later copies are still detected) |
| `failed` | Processing failed 3 times | No | No |

Any status can be deleted permanently by an admin (row, vector, and file when no other row uses it).

## 4. Data model

New `papers` columns, added in `init_db()` with `ADD COLUMN IF NOT EXISTS` (existing rows become `live`):

| Column | Type | Notes |
|---|---|---|
| `status` | `TEXT NOT NULL DEFAULT 'live'`, CHECK in the five statuses | |
| `review_reasons` | `JSONB NOT NULL DEFAULT '[]'` | List of flags, see §6 |
| `status_note` | `TEXT` | Shown to the uploader: rejection reason, failure message |
| `file_sha256` | `TEXT` | Hex digest of the stored file |
| `upload_key` | `TEXT` | `secrets.token_hex(16)`; names the stored file and lets an anonymous uploader poll status |
| `attempts` | `INTEGER NOT NULL DEFAULT 0` | Worker attempts so far |
| `retry_at` | `TIMESTAMP` | Worker lease and back-off; see §7 |
| `reviewed_by` | `INTEGER REFERENCES users(id)` | Last admin who changed status or details |
| `reviewed_at` | `TIMESTAMP` | |

Indexes:
- `CREATE UNIQUE INDEX IF NOT EXISTS papers_active_sha256 ON papers (file_sha256) WHERE status <> 'failed'` — a database-level guard against two identical files uploaded at the same moment. Existing rows have a NULL hash until the dedupe command fills it in, so the index can be created before cleanup.
- `CREATE UNIQUE INDEX IF NOT EXISTS papers_upload_key ON papers (upload_key)`.
- `CREATE INDEX IF NOT EXISTS papers_status ON papers (status)`.

Stored files: new uploads are saved as `backend/papers/<upload_key>.pdf`; `papers.filename` stays the uploader's name (used for display and as the download name). Existing rows keep their paths.

Qdrant: the point payload gains `status`. Every vector write goes through `VectorService.upsert_paper_vector(paper_id, vector, text, metadata, status)` with `status` required, so no caller (worker, reprocess) can drop it. Status changes and admin edits update the payload in place with `set_payload`, without re-embedding.

## 5. Upload flow (`POST /documents/ingest`, returns in seconds)

In order; every refusal before step 6 leaves the daily quota untouched and deletes any file already written:

1. Existing checks: role chosen, upload allowed for this account, filename rules, `%PDF` header.
2. **Size:** over 10 MB → `413 file_too_large` ("This file is over 10 MB. …"). Uses `UploadFile.size`, and stops copying past the limit if the size is unknown.
3. Save to `backend/papers/<upload_key>.pdf`, computing SHA-256 while copying.
4. **Pages:** count with poppler (`pdf2image.pdfinfo_from_path`, already in the image). More than 20 → `400 too_many_pages`; unreadable PDF → `400 invalid_file`.
5. **Exact copy:** a non-failed paper with the same hash → `409 duplicate_paper` with `paperId` and `paperStatus` in the body. Message: live → "This paper is already on PrepWise."; processing/review → "This paper was already uploaded and is being checked."; rejected → "This file was already reviewed and not accepted."
6. Consume quota (`enforce_quota`).
7. Insert the row as `processing` with `upload_key`, hash and uploader. A unique-index violation (a simultaneous identical upload) → refund quota, delete file, same `409 duplicate_paper`.
8. Wake the worker and return `202 {"id", "uploadKey", "filename", "status": "processing"}`.

`ApiError` gains an optional `extra: dict` merged into the JSON body, so the 409 can carry `paperId` and `paperStatus` next to `detail` and `code`.

nginx must allow the body through: `client_max_body_size 11m;` in the prepwise server block (its default is 1 MB). The app check gives the friendly message; nginx is the real guard against huge bodies.

## 6. Automatic checks

Run by the worker on the full OCR text and extracted details. Any flag → `review`; no flag → `live`. The worker never rejects. Thresholds were measured on the 14 papers in production on 2026-09-26.

| Code | Rule | Measured |
|---|---|---|
| `unreadable` | Fewer than 300 letters in the OCR text. When set, `not_exam_paper` and `possible_duplicate` are skipped (they'd be meaningless) | Smallest real paper: 806 letters |
| `not_exam_paper` | Text lacks the word "marks", or lacks a time mention (`time` or `N hrs/hours`), or has fewer than 2 of: section, attempt, answer, compulsory, examination, semester | All 14 pass; the lowest has 6 exam words |
| `missing_details` | Subject code, subject name or year is empty after extraction. Stored with the missing field names | Paper 8 (no subject code) would have been flagged |
| `possible_duplicate` | Among the 5 nearest papers by embedding (any status with a vector: live, review, rejected; excluding itself), questions-only text overlap ≥ 0.5. Stored with `paperId` and `overlap` for each match | Real copies: 0.83–1.00. Different papers: ≤ 0.07, including same subject and session (the two "Cloud Sec Major 2023" papers: 0.03) |

Text overlap: `RAGService._questions_only` on both texts (drops the exam header and page markers), lowercase words `[a-z0-9]+`, 3-word shingles, Jaccard similarity. Constants: `MIN_READABLE_LETTERS = 300`, `DUPLICATE_OVERLAP = 0.5`, `DUPLICATE_CANDIDATES = 5`.

`review_reasons` examples:

```json
[{"code": "possible_duplicate", "paperId": 3, "overlap": 0.83},
 {"code": "missing_details", "fields": ["subject_code"]}]
```

## 7. Background worker

`services/paper_worker.py`: one daemon thread started in `main.lifespan` after `init_db()` and stopped on shutdown. It is disabled when `PAPER_WORKER_ENABLED=false` (tests set this). Production runs one uvicorn process, so there is exactly one worker. The claim query below stays correct if that ever changes.

**Claim** the oldest due job:

```sql
UPDATE papers SET attempts = attempts + 1, retry_at = now() + interval '15 minutes'
WHERE id = (SELECT id FROM papers
            WHERE status = 'processing' AND (retry_at IS NULL OR retry_at <= now())
            ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED)
RETURNING id, filename, file_path, attempts;
```

The 15-minute `retry_at` is a lease. If the process dies mid-job (deploy, crash), the paper becomes due again once the lease runs out, and processing resumes with no manual step.

**Process** (a plain function, `process_paper(paper, deps)`, tested without the thread):
1. Header OCR and metadata extraction with the existing retry. `_extract_metadata_with_retry` moves here from `routes.py`.
2. Full-text OCR (`extract_full_text`).
3. Embed the full text. No vector → error.
4. Run the checks in §6.
5. Write the vector with the decided status (`live` or `review`).
6. One `UPDATE`: details, `status`, `review_reasons`, `status_note`, `retry_at = NULL`.

**On error:** log it. If `attempts` < 3, set `retry_at = now() + 2 minutes`. Otherwise set `status = 'failed'` with note "We couldn't process this file. Try uploading it again later." Step 5 is idempotent, so a retry after a failure in step 6 is safe.

**Idle:** when nothing is due, wait on a `threading.Event` until the next scheduled retry or lease expiry (at least 1 s, at most 1 hour). The upload endpoint and admin retry set the event, so new work starts at once. A fixed 10 s poll would keep the Neon database awake around the clock.

## 8. What uploaders see

- `GET /documents/uploads/{upload_key}` (no sign-in; the key is unguessable) → `{"id", "filename", "status", "note", "uploadedAt"}`. A 32-hex-character key is required; an unknown key → 404.
- `GET /me/uploads` (signed in) → the user's latest 50 uploads in the same shape, newest first.
- Notes by status:
  - `review`: "An admin will check this paper before it's published."
  - `rejected`: the admin's reason.
  - `failed`: the failure message.
  - `live` and `processing`: none.
- **Upload page:**
  - The browser refuses files over 10 MB before sending them.
  - After a successful upload, each file shows **Processing…** and polls its key every 5 s until the status settles:
    - **Live**, with a link to the PDF
    - **Under review**
    - **Rejected**, with the reason
    - **Failed**
  - Polling stops after 15 minutes with "Still processing. Check back later."
  - A `409 duplicate_paper` shows "Already on PrepWise", with a link when the existing paper is live.
  - Signed-in users also see a **Your uploads** list from `/me/uploads`.

## 9. Visibility

Live papers only, everywhere public:
- `GET /documents` and `GET /documents/{id}/download` (non-live → 404).
- `POST /search/semantic`: vector filter plus `status = 'live'` in the SQL join.
- RAG retrieval for the generator.

`VectorService.search_similar(query, limit, with_payload=True)` only returns live points, implemented as `must_not status in [processing, review, rejected, failed]`, so legacy points without a status still count as live until the dedupe command tags them. The duplicate check uses a separate `VectorService.nearest_papers(vector, limit, exclude_id)` over every stored vector (only live, review and rejected papers have one).

`reprocess` skips `processing` and `failed` rows and writes each vector with the row's current status.

## 10. Admin tools

API (`api/admin.py`, all `require_role("admin")`):

| Endpoint | Does |
|---|---|
| `GET /admin/papers?status=review` | Papers in one status (default `review`), newest first. Returns `{"papers": [...], "counts": {status: n}, "matches": {id: details}}`. Each paper has details, status, note, reasons, uploader (name/email or "Anonymous"), `uploadedAt`, reviewer, and a 1,500-character preview of its questions-only text. `matches` holds the details of every paper referenced by a `possible_duplicate` reason, so the page can show both side by side. |
| `PATCH /admin/papers/{id}` | Edit `filename`, `subjectCode`, `subjectName`, `semester`, `year`, `time`, `marks` in any status. The subject code is normalized; the Qdrant payload is updated when a vector exists. |
| `POST /admin/papers/{id}/status` `{"status", "note"?}` | Allowed transitions only; see the list below. Anything else → `409 invalid_transition`. Sets `reviewed_by` and `reviewed_at`, and updates the Qdrant `status` first so that a Qdrant failure leaves nothing changed. |
| `DELETE /admin/papers/{id}` | Deletes the vector, the row, and the file unless another row points at the same path. Returns 204. |
| `GET /admin/papers/{id}/file` | The PDF, whatever its status. |

Allowed status transitions:
- **Approve:** review → live.
- **Reject:** review → rejected. A note is required.
- **Take down:** live → rejected. A note is required.
- **Restore:** rejected → live.
- **Retry:** failed → processing. This resets `attempts` and `retry_at` and wakes the worker. If an active paper already has the same hash, the unique index turns this into a `409 duplicate_paper`.

**Admin → Papers page** (`/admin/papers`, linked in the navbar next to Users for admins):
- Tabs for Review, Live, Rejected, Failed and Processing, each with its count.
- Each card shows the paper's details (editable in place), its flags in plain words, and an "Open PDF" link.
- A card flagged `possible_duplicate` shows the matched paper side by side: both sets of details, both PDF links, and "83% same questions".
- Buttons depend on the tab:

| Tab | Buttons |
|---|---|
| Review | Approve, Reject (asks for a reason), Delete |
| Live | Take down (asks for a reason), Delete |
| Rejected | Restore, Delete |
| Failed | Retry, Delete |
| Processing | none |

Delete asks for confirmation.

## 11. Cleaning up existing duplicates (`admin_cli.py dedupe`)

`python admin_cli.py dedupe [--keep ID ...] [--apply]`, preview by default:

1. Hashes every stored file. A missing file is reported and skipped.
2. Groups papers that share a hash, or whose texts (from Qdrant) overlap by 0.5 or more under the §6 rule. Uses union-find, so chains of copies end up in one group.
3. In each group, keeps the `--keep` id if one is given, otherwise the lowest id. Prints each group with the kept and removed ids, filenames, and whether each match is exact or near (with the overlap).
4. `--apply` only:
   - For each removed paper, deletes the vector and the row, and deletes the file unless the kept row uses the same path. Rows 3/4 and 10/11 share one file each, because same-name uploads overwrote it.
   - Writes `file_sha256` for every remaining row.
   - Sets the Qdrant `status` payload on every point from its row.

Expected on production today: groups {2, 3, 4} and {10, 11}. The two "Cloud Sec Major 2023" papers (5 and 7) are different papers and stay. By default 2 ("AI Major 2023(1).PDF") is kept; `--keep 3` keeps the cleaner name.

## 12. Error codes (new)

| HTTP | Code | When |
|---|---|---|
| 413 | `file_too_large` | Over 10 MB |
| 400 | `too_many_pages` | Over 20 pages |
| 409 | `duplicate_paper` | Exact copy exists (body adds `paperId`, `paperStatus`) |
| 409 | `invalid_transition` | Admin status change not allowed from the current status |
| 400 | `invalid_request` | Reject or take down without a reason |
| 503 | `search_index_unavailable` | Qdrant couldn't be updated during an admin change; nothing was changed |
| 404 | `not_found` | Unknown upload key, paper id, or a non-live paper on public routes |

## 13. Testing

Tests use the existing fakes and `TEST_DATABASE_URL` (never production); the worker thread stays off.

- **Upload:**
  - Refusals: over 10 MB, over 20 pages, a non-PDF.
  - An exact duplicate returns 409 with `paperId`, and no quota is used.
  - The simultaneous-duplicate race (unique index) refunds the quota and deletes the file.
  - Two same-name uploads get different stored paths, and neither overwrites the other.
  - The 202 response has the documented shape.
- **Checks:** unit tests for each rule.
  - Use the calibration cases: a copy at 0.83 is flagged; the Cloud Sec pair at 0.03 is not.
  - Include threshold edges.
  - Unreadable text skips the other checks.
- **Worker:**
  - `process_paper`: clean paper → `live`; flagged paper → `review` with reasons; the vector carries the status.
  - An error schedules a retry; the third error marks the paper failed.
  - Claiming skips papers whose `retry_at` is in the future.
  - An expired lease is picked up again.
- **Visibility:** non-live papers are absent from the list, download, semantic search and RAG retrieval.
- **Admin:**
  - Every allowed transition, plus a rejected one.
  - Reject and take-down require a note.
  - Edit updates both the database and the payload.
  - Delete keeps a file another row still uses.
  - Non-admins get 403.
- **Dedupe:**
  - Grouping, including chains and `--keep`.
  - Preview writes nothing.
  - `--apply` removes rows, vectors and files correctly, and backfills hashes and status tags.
- **Uploader status:** the upload-key endpoint (unknown key → 404) and `/me/uploads` (own uploads only).
- **Frontend:** `npm run build` and lint on the touched files. Manually, the upload page goes through processing to a final status, and the admin page is walked through in the browser.

## 14. Deployment

1. Merge PR #4 first (this branch is stacked on it), then this PR.
2. nginx: add `client_max_body_size 11m;` to the prepwise server block. Do it together with the parked Cloudflare 525 fix.
3. On the server: `docker compose pull && docker compose up -d`. `init_db` adds the columns, and existing papers stay live.
4. `docker compose exec backend python backend/admin_cli.py dedupe` to preview, then `... dedupe --apply [--keep 3]`.
5. Vercel redeploys the frontend from `default`.

## 15. Out of scope

- Refunding the daily quota when a paper later fails or is rejected.
- Notifying uploaders (email or push) about review outcomes.
- The PDF of a pending paper is not viewable by its uploader.
- Duration awareness (1 h minor vs 3 h major): next item.
- OCR tuning (1600 px, retries, deskew and line grouping): the item after that.
- RAG's retrieval still collapses papers with the same filename (PR #3). It becomes mostly unnecessary once duplicates are gone, and is left as is.
