# QA tester 06: backend security review

Scope: `aralshield/api/main.py`, `db.py`, `__init__.py`, `voice/notify.py`, `voice/bot.py`, tested against the local server at http://127.0.0.1:8000 (uvicorn 0.54, FastAPI 0.162, Starlette 1.7, requests 2.34). About 60 requests were sent in total. All `qa06-*` test data (subscriptions, a temporary API key, a canary file) was deleted afterwards, and a final check found 0 rows left.
Checked and found OK: every SQL query is parameterized (the only f-string, `db.py:125`, builds a constant column list). The allowlists on `/layers/{name}` and `/figures/{name}` hold (`..%2F`, `..%5C` → 404). The `/assets` mount does not allow traversal. FastAPI rejects JSON sent as `text/plain` or with no Content-Type (422), so the simple-request CSRF path is closed. `/v1/events` for 10 years returns 3,515 rows in 0.27 s. `/v1/scenarios` with 5,000 ids takes 0.05 s. `/v1/accuracy?period=9999d` takes 0.18 s. Revoking a key works: after `revoked=1` the response is 401.

### T06-1: Unauthenticated path traversal reads any `.json` file on disk
- **Severity (proposal):** High
- **Location:** `api/main.py:268-282` (`_report`, `/v1/reports/eye?tag=`), `main.py:303-311` (`/v1/sources/preview?tag=`), `main.py:291-300` (`/v1/sources/maps?month=`, needs a key)
- **Evidence:** `tag` is inserted straight into a path (`REPORTS / f"eye_metrics_{tag}.json"`). Windows resolves `..` without checking whether the intermediate folder exists.
  - `GET /v1/reports/eye?tag=x/../../frontend/package` → 200, returns `frontend/package.json`. Backslashes work too (`tag=x\..\..\frontend\tsconfig` → 200).
  - Outside the project: `tag=x/../../../../../AppData/Local/Temp/claude/.../scratchpad/qa06_canary` → 200 `{"qa06":"canary-outside-project"}`.
  - `/v1/sources/preview?tag=x/../../../frontend/package-lock` → 200 with the whole file in `layers[*].bounds`. `/v1/sources/maps?month=x/../../../frontend/package` with a key → 200.
  - The 404 message also echoes the resolved path: `отчёт eye_metrics_x/../../../nonexistent.json ещё не построен`.
- **Expected vs actual:** Expected: only `eye_metrics_<year>.json` files are served. Actual: any JSON file the server account can read is served (configs, tokens stored as JSON, other users' projects).
- **Suggested fix:** Validate `tag`/`month` with `Query(pattern=r"^\d{4}(-\d{2})?$")`, or check `p.resolve().is_relative_to(REPORTS)` and use an allowlist of existing files, as `/layers` already does. Do not echo paths in errors.

### T06-2: `/v1/priorities?limit=20000` lets one client block the whole API
- **Severity (proposal):** High (availability)
- **Location:** `api/main.py:344-353` (limit up to 20000, sync GeoJSON build), `main.py:47` (`GZipMiddleware` compresses inside the event loop)
- **Evidence:** One request with `limit=20000&classes=<all 4 classes>` returns 8,928,819 B (1.2 MB gzipped) in 4.1–5.1 s. With 4 parallel requests of this kind from one IP, `GET /v1/stations` took **18.29 s** (normal is 0.016 s). The 4 requests together took 20.4 s. The rate limit (120/min/IP) does not help, because a handful of requests per minute is enough.
- **Expected vs actual:** Expected: open endpoints are cheap and bounded. Actual: a few unauthenticated requests stall every endpoint, including `/v1/health`.
- **Suggested fix:** Cap the open endpoint at about 2,000 cells and serve the full set through the keyed export. Cache the serialized GeoJSON per (classes, M, limit), or precompute a static file. Use a cost-weighted rate limit for heavy routes.

### T06-3: Telegram bot token written to logs on network errors
- **Severity (proposal):** Medium
- **Location:** `voice/notify.py:35-38` (token in the URL path, then `log.warning("Telegram: %s", e)`), `cli.py:32-35` (logs go to `reports/pipeline.log`)
- **Evidence:** The same call pattern was reproduced with a fake token against an unresolvable host. Logged line: `Telegram: HTTPSConnectionPool(host='api.telegram.org.invalid', port=443): Max retries exceeded with url: /bot123456:QA06-FAKE-SECRET-TOKEN/sendMessage ...`. During real outages the full token goes to stdout and `reports/pipeline.log`. The log file is git-ignored but stays on disk and often gets attached to bug reports.
- **Expected vs actual:** Expected: secrets never reach logs (spec 27.4 "Секреты"). Actual: the full token is logged.
- **Suggested fix:** Log `type(e).__name__` only, or redact with `str(e).replace(token, "***")`. Add a logging filter that masks `bot\d+:[\w-]+`.

### T06-4: Feedback "anonymization" can be reversed by brute force and survives `/stop`
- **Severity (proposal):** Medium (personal data, spec 15.5 / 27.1)
- **Location:** `db.py:169-176` (`sha256(f"{channel}:{chat_id}")[:16]`, no salt or key), `voice/notify.py:88` (hash written next to the alert text in `data/outbox`), `notify.py:46-47` (dry-run SMS outbox stores **plaintext phone numbers**), `db.py:155-159` (`delete_subscriber` leaves `feedback` rows)
- **Evidence:** A scratch brute force recovered `chat_id=5123456789` from its 16-hex hash in 0.39 s (456,790 tries) at **1.17 M hashes/s on one Python thread**. Telegram user IDs fit in about 10^10, which is about 2.4 h on one core, minutes with multiprocessing, and seconds on a GPU. Kazakhstan mobile numbers (`+77xxxxxxxxx`, about 10^9) take about 15 min. `/stop` removes `subscribers`/`subscriptions` only, so the reversible feedback rows remain.
- **Expected vs actual:** Expected: the data cannot be linked back to a person after `/stop`. Actual: it is pseudonymous, reversible, and kept.
- **Suggested fix:** Use `hmac.new(SECRET_PEPPER, ...)` with the pepper kept in `.env`/a secret manager. Delete or re-key feedback for the subscriber on `/stop`. Mask phone numbers in the outbox, for example `+7700***1234`.

### T06-5: Internal methods trust the client IP: no Host check, XFF is honored, proxy setup is fragile
- **Severity (proposal):** Medium
- **Location:** `api/main.py:79-86` (`require_internal`), `cli.py:95` (`uvicorn.run` with default `proxy_headers=True`, `forwarded_allow_ips=127.0.0.1`)
- **Evidence:**
  - Any Host header is accepted: `POST /v1/subscriptions` with `Host: rebind.attacker.example:8000` → 200 `{"ok":true}` (test row `qa06-rebind` was deleted). *Speculative, not executed:* a DNS-rebinding page in the operator's browser could therefore call `POST/DELETE /v1/subscriptions` (unsubscribe any chat_id) and `/v1/feedback` as "localhost".
  - XFF is honored for local callers: the same request with `X-Forwarded-For: 8.8.8.8` → 403. So `request.client.host` comes from that header for anything connecting via 127.0.0.1.
  - *Conditional:* behind a same-host reverse proxy that does **not** set XFF, every Internet client looks like 127.0.0.1, which makes the internal methods public while `ARALSHIELD_INTERNAL_KEY` is empty (the `.env.example` default).
  - When a key is set it is compared with `==` (`main.py:82`), which is not constant-time (minor).
- **Expected vs actual:** Expected: internal methods are reachable only by project services (spec 24.3). Actual: access depends on network topology.
- **Suggested fix:** Always require `ARALSHIELD_INTERNAL_KEY`, refuse to start without it when `--host` is not loopback, and compare with `hmac.compare_digest`. Add `TrustedHostMiddleware(allowed_hosts=[...])`.

### T06-6: Rate limiter grows without bound and can be bypassed
- **Severity (proposal):** Low/Medium
- **Location:** `api/main.py:51-65`
- **Evidence (code-verified; no memory load test, per the request budget):**
  - `_hits` is a `defaultdict(deque)` whose keys are never removed. Each distinct client IP leaves an entry forever (IPv6 /64 rotation, or XFF rotation through a trusted local proxy, see T06-5).
  - Only `/v1/*` is limited. `/layers`, `/figures`, `/assets` and `/docs` are not.
  - The state is per process.
  - The 429 response is returned before the security headers are added and has no `Retry-After`.
- **Expected vs actual:** Expected: bounded memory and an effective throttle (spec 24.3). Actual: unbounded growth and an easy bypass.
- **Suggested fix:** Delete keys whose deque is empty, or use a TTL/LRU dict (for example `cachetools.TTLCache(maxsize=100_000, ttl=60)`). Key on the /64 for IPv6. Add `Retry-After`. Use Redis or a proxy-level limit if running more than one worker.

### T06-7: CA bundle copy in `C:\ProgramData\aralshield` checks size only
- **Severity (proposal):** Low (only matters on a shared Windows machine)
- **Location:** `aralshield/__init__.py:225-236`
- **Evidence:** `icacls C:\ProgramData\aralshield` shows `BUILTIN\Users:(CI)(WD,AD,WEA,WA)`, so any local user can create files there. The existing `cacert.pem` is read-only for Users. Integrity is checked only with `dst.stat().st_size != src.stat().st_size`. The env var `CURL_CA_BUNDLE` is also read by `requests` (`merge_environment_settings`), so this file becomes the trust store for the Telegram and SMS calls as well, not only GDAL. *Speculative scenario:* another local user creates `cacert.pem` before the first run, padded to the certifi size and containing their own root CA. The file is never replaced, and TLS to api.telegram.org (with the bot token) can be intercepted.
- **Suggested fix:** Compare a SHA-256 of the contents, not the size. Prefer a per-user location (`%LOCALAPPDATA%`), or create the directory with an owner-only ACL. Set only `GDAL_CURL_CA_BUNDLE`, not the global `CURL_CA_BUNDLE`.

### T06-8: Bot and feedback input validation gaps
- **Severity (proposal):** Low
- **Location:** `voice/bot.py:354-359`, `api/main.py:432-445`, `bot.py:223`
- **Evidence:**
  - `on_feedback` checks the station and answer but not `parts[2]` (date). Any string up to the 64-byte callback limit is stored in `feedback.date`.
  - Neither the bot nor `/v1/feedback` checks that the user was actually notified for that station and date, so a subscriber can submit votes for any station and day. *Speculative:* forging callback data needs a modified MTProto client.
  - `_draft` (in-memory wizard state) has no size limit or expiry.
  - The bot has no command rate limit, which spec 27.4 requires.
- **Suggested fix:** Parse the date with `date.fromisoformat` and accept only the last few days. Require a matching `notifications` row. Add an LRU/TTL for `_draft` and a per-user throttle.

### T06-9: API key lifecycle and audit gaps
- **Severity (proposal):** Low
- **Location:** `db.py:180-194`, `cli.py:103-107`, `api/main.py:188-197`
- **Evidence:** Keys are 192-bit random values stored as unsalted SHA-256 and looked up by an indexed equality query. That is acceptable, and timing attacks are not practical. However, there is no CLI to revoke, list or expire keys; revocation only works by hand-editing SQL (verified: 401 after `UPDATE api_keys SET revoked=1`). `/v1/events` access is not written to `audit_log`; only `/priorities/export` is. The temporary key `qa06-test` was created and deleted afterwards.
- **Suggested fix:** Add `cli apikey --revoke/--list`, an `expires_at` column, and `db.audit(who["owner"], "events", station)`.

### T06-10: Unhandled 500, public docs, missing browser security headers
- **Severity (proposal):** Low
- **Location:** `api/main.py:149-151`, `main.py:44`, `main.py:55-69`
- **Evidence:**
  - `GET /v1/forecasts/99999999999999999999999/explanation` and `/9223372036854775808/explanation` → `500 Internal Server Error` (an SQLite `OverflowError`; no traceback is leaked).
  - `/docs` and `/openapi.json` are public (200, 15.5 KB) and list the internal `/v1/subscriptions` and `/v1/feedback` routes.
  - Responses have no `Content-Security-Policy` or `X-Frame-Options`, so the panel can be framed. They do carry `server: uvicorn`.
- **Suggested fix:** Use `fid: int = Path(ge=1, le=2**63-1)`. Mark the internal routes `include_in_schema=False`, or disable docs in production. Add `X-Frame-Options: DENY` and a basic CSP in the existing middleware.

## Improvement suggestions
1. Add one shared helper for path parameters (an allowlist plus a `resolve().is_relative_to(base)` check) and use it for every file-serving route. This closes the T06-1 class of bugs.
2. Run the internal methods (`/v1/subscriptions`, `/v1/feedback`) on a separate router or port that always requires a key, and refuse to start with an empty `ARALSHIELD_INTERNAL_KEY` on a non-loopback host.
3. Add `pytest` security regressions: traversal in `tag`/`month`, a cap on the `/v1/priorities` size, no secret in log output, and feedback deleted on `/stop`.
