# QA tester #15: test suite quality, code quality, performance

Environment: Windows 11, project `.venv`, API running at http://127.0.0.1:8000 (live DB: 26 316 forecasts, of which 26 304 are backtest; 37 380 dust_events; 0 subscribers). About 60 HTTP requests in total. Every write went to temp dirs under the scratchpad; no project file was edited apart from this report.
Baseline: `pytest -q tests` gives **22 passed, 1 warning in 4.45 s** (the warning is a Starlette/httpx deprecation).

### T15-1: notify.run makes 1–2 SQLite connections and commits per subscriber, so 100k subscribers takes about 50 min (NFR-05)
- Severity (proposal): High
- Location: `aralshield/voice/notify.py:311-329`, `aralshield/db.py:62-72`
- Evidence: a scratch script used a temp `ARALSHIELD_DATA`, 12 orange live forecasts, N subscribers, `notify.run(dry_run=True)` and a counting wrapper on `sqlite3.connect`.
  - N=1 000: first run 29.3 s with 1 503 connects; second run (dedup path) 8.1 s with 753 connects.
  - N=5 000: first run 148.2 s with 7 503 connects; second run 27.8 s with 3 753 connects.
  - Cost is linear at about 30 ms per sent message, so 100k subscribers would take about 49 min before any Telegram/SMS latency. Each message runs a dedup `SELECT` (new connection plus `PRAGMA journal_mode=WAL`) and then an `INSERT` (new connection plus commit and fsync). The outbox file is reopened for every message (lines 322-324).
- Expected vs actual: the morning dispatch should finish by about 06:15 for 100k subscribers. Actual: tens of minutes, an N+1 pattern.
- Suggested fix: load the existing `notifications` for the current `forecast_id`s in one query into a set. Write all rows with a single `executemany` in one transaction. Keep one outbox handle open. Set `PRAGMA synchronous=NORMAL` under WAL. Send through a rate-limited async queue (Telegram allows about 30 msg/s, so even the sending side needs about 1 h for 100k).

### T15-2: /v1/priorities with a large limit takes about 1 s and returns 2.7 MB, built row by row in Python
- Severity (proposal): Medium
- Location: `aralshield/api/main.py:344-353`, `aralshield/shield/priority.py:208-221`
- Evidence (5 calls each, median of the warm calls):
  - `limit=6000&classes=надёжный высокий,высокий,средний`: first call 1 720 ms, warm **940 ms**. JSON is 2 701 750 B (376 315 B gzipped).
  - The default `/v1/priorities`: warm 368 ms, 902 KB JSON.
  - In-process profile on 6 000 features: `to_geojson` 344 ms (a `to_dict("records")` loop with `np.cos` on every row), `json.dumps` 434 ms. Coordinates are written at full float precision.
- Expected vs actual: a map layer should load in well under 1 s. Actual: about 1 s server time plus a 2.7 MB parse in the browser.
- Suggested fix: build the polygons vectorised once inside `_shield_state` and cache the serialised GeoJSON per class set. Round coordinates to 5 decimals. Return centroids plus `cell_km` and draw squares on the client.

### T15-3: queries on `forecasts` by `mode` scan the whole table (no index on mode/issue_date)
- Severity (proposal): Medium (it grows with the backtest archive)
- Location: `aralshield/db.py:26-34,129-135`, `aralshield/api/main.py:457-459`
- Evidence (`EXPLAIN QUERY PLAN` on a read-only connection to `data/aralshield.sqlite`):
  - `latest_forecasts()` (`... WHERE mode='live' AND issue_date=(SELECT MAX(issue_date) ... WHERE mode='live')`): `SCAN forecasts`, 8.6 ms, to return 12 rows out of 26 316.
  - The `/v1/health` aggregate: `SCAN forecasts`, 8.3 ms.
  - Calendar queries use `date LIKE '2023-%'`. LIKE is case-insensitive by default, so only the `station=` prefix of the index is used.
  - `ask_feedback` join: `SCAN n` over all `notifications`, with no index on `(channel,status)` or `f.day`. That table grows by up to 100k rows per day.
- Expected vs actual: index seeks are expected. Actual: full scans on every `/v1/health`, `/v1/summary`, bot `/forecast` and `notify.run` call.
- Suggested fix: `CREATE INDEX ix_fc_mode_issue ON forecasts(mode, issue_date)`. Use `date BETWEEN 'YYYY-01-01' AND 'YYYY-12-31'` instead of LIKE. Add `CREATE INDEX ix_notif_status ON notifications(channel, status, forecast_id)`.

### T15-4: `_verification` loads the full join into pandas on every request, and mixes backtest with live
- Severity (proposal): Medium
- Location: `aralshield/api/main.py:212-228` (used by `/v1/accuracy`, `/v1/summary` via `_orange_hits(30)`, and the bot's `/accuracy`)
- Evidence:
  - The join returns 8 082 rows (27 ms in SQL, plan `SCAN f`) whatever the `period` is. The period filter is applied afterwards in pandas.
  - Warm medians: `/v1/accuracy?period=30d` 89 ms, `?period=2500d` 90 ms (identical, which confirms the data is not filtered in SQL), `/v1/summary` 133 ms.
  - The query has no `f.mode` filter. `drop_duplicates(keep="last")` after sorting by `day` alone picks backtest or live rows in an arbitrary order.
- Suggested fix: push `f.day > ?` and `f.mode` into SQL. Cache the result keyed on `(MAX(forecasts.id), MAX(dust_events.date))`.

### T15-5: bot handlers are async but do blocking SQLite and pandas work on the event loop (NFR-03 ≤2 s under concurrency)
- Severity (proposal): Medium
- Location: `aralshield/voice/bot.py:175-221` (also `_lang` at :91, called before every reply)
- Evidence:
  - Every handler calls synchronous `db.*`, which opens a new connection each time.
  - `/accuracy` imports `aralshield.api.main` (this builds the FastAPI app) and runs `_verification` for about 90 ms of blocking work. One aiogram process serialises all users behind this work.
  - `accuracy()` silently replaces any exception with `{}` (`bot.py:213-216`), so a failure looks like "not enough data".
  - The latency claim is inferred from the API timings above; no live bot was run.
- Suggested fix: wrap DB and pandas calls in `await asyncio.to_thread(...)`. Move the accuracy computation into a service module shared by the API and the bot. Log the exception before falling back.

### T15-6: critical logic has no tests (section 26)
- Severity (proposal): Medium
- Location: `tests/test_core.py`, `tests/test_api.py` (the only test imports are metrics, features, isd, templates, db and api.main)
- Evidence: these areas have no tests:
  - `shield/priority.py`: `_geometry` bearing/sector convention (the "wind blows from the cell to the settlement" sign is easy to flip), `exposure`, `scenario`, `classify_priority`.
  - `eye/*`.
  - `forecast/train.py`: `Climatology`, `Persistence`, `WindRule.fit`, `choose_thresholds`.
  - `forecast/predict.py`: `issue_daily` degraded path, `_fallback_prob`, `level_for`.
  - `voice/notify.py`: role level, worst-horizon choice, dedup on re-run.
  - `voice/bot.py`: none.
  - API success paths for `/v1/accuracy`, `/v1/priorities`, `/v1/scenarios`, `/v1/events/calendar`, `/v1/feedback`, and rate limiting.
- Two behaviours found by hand that a test would pin down:
  - `_fallback_prob(None, ...)` returns the constant `0.1`, which is "green" for every group under `DEFAULT_LEVELS`. The emergency mode with no model therefore can never warn.
  - `choose_thresholds` on a synthetic perfectly separable set (10 events at p=0.97, 990 non-events at p<0.3) gives `orange=0.31` for all four groups, so the per-group differentiation collapses. In the shipped `models/thresholds.json`, health has `orange 0.11` and `red 0.58`, so the red band is very wide.
- Suggested fix: add unit tests for `_geometry` (a cell due north of a settlement gives sector N), `scenario` (selecting no cells gives 0 % reduction, and the result is monotonic in `cover`), and notify dedup (a second run sends 0). Add an API test for `/v1/scenarios` on a tiny parquet fixture.

### T15-7: the test run has side effects outside its temp dir and leaks temp dirs
- Severity (proposal): Low
- Location: `tests/conftest.py:5`, `aralshield/__init__.py:29-40`, `aralshield/config.py:17-18`
- Evidence:
  - Importing the package during tests copies certifi to `C:\ProgramData\aralshield\cacert.pem` (the file exists, 235 863 B).
  - `config` runs `mkdir` on `ROOT/models` and `ROOT/reports` (harmless today because they exist).
  - `_load_dotenv` reads the real project `.env` into the test process. A real `TELEGRAM_BOT_TOKEN` could leak into future notify tests.
  - `tempfile.mkdtemp` is never removed: 7 `aralshield_test_*` dirs remain in `%TEMP%`.
- Expected vs actual: tests should be hermetic. Actual: they write system-level files and load production secrets.
- Suggested fix: use a session fixture with `tmp_path_factory` plus cleanup. Set `ARALSHIELD_SKIP_DOTENV=1` in conftest and honour it. Make `MODELS`/`REPORTS` overridable by env.

### T15-8: duplicated logic and dead code
- Severity (proposal): Low
- Location and evidence:
  - `forecast/predict.py:134-140` `_events_no_save` re-implements the dust criterion of `truth/isd.py:133-159`, although `build_events(obs, save=False)` already exists and is used by the tests. If `DUST_WW` or the visibility rule changes in one place, the persistence feature and the truth catalogue will diverge silently.
  - `api/main.py:291-311`: the `source_maps` and `source_preview` bodies are identical copies.
  - pyflakes (run from a scratch `--target` install) reports three unused imports: `api/main.py:15` (`timedelta`), `truth/isd.py:9` (`io`), `tests/test_core.py:4` (`date`).
- Suggested fix: call `isd.build_events(..., save=False)` in `_recent_events`. Extract a shared `_overlay_payload(tag)` helper. Remove the unused imports.

### T15-9: error swallowing and magic numbers
- Severity (proposal): Low
- Location and evidence:
  - `predict.py:114` uses a bare `except Exception` to fall back to climatology without logging. An `IndexError` from an empty row and a real model bug look the same.
  - `bot.py:215` (see T15-5).
  - Magic numbers: `0.1` (`predict.py:116`), `0.6`/`0.2`/`0.75`/`0.25`/`0.4` in `choose_thresholds` (`train.py:179-191`), `3700` days (`main.py:193`), the weights `0.35/0.25/0.25/0.15` (`priority.py:75`).
  - Inconsistent naming: `issue_date` vs `day` vs `date` for the same concept across tables, and `M` used both for the metrics module and for the suitability column.
- Suggested fix: name the constants in `config.py`, use `log.exception` in the fallback paths, and keep a glossary of field names.

### T15-10: small runtime and code-health items (verified by reading the code)
- Severity (proposal): Low
- Location and evidence:
  - `api/main.py:51-65`: rate-limit `_hits` keeps an entry for every IP forever and is per process (no shared limit with several workers).
  - `db.py:67`: `PRAGMA journal_mode=WAL` is re-issued on every connect (it persists in the file; WAL is confirmed set, `PRAGMA journal_mode` gives `wal`). `synchronous` is left at FULL.
- Checked and OK:
  - `_shield_state` lru_cache memory is small: 21 653 cells at about 5.1 MB, `d` 2.1 MB, `sec` 2.1 MB, `maxsize=1`.
  - `/v1/forecasts` (38 ms), `/v1/events/calendar` (43 ms, 41 KB) and `POST /v1/scenarios` (40–51 ms for `rating` at 1 000 ha, `rating` at 200 000 ha and `random` at 50 000 ha) are fast.

## Improvement suggestions
1. Batch the notify pipeline: one transaction and one dedup query per run, an async send queue, and a regression benchmark (5k subscribers under 10 s).
2. Precompute the heavy read models (priorities GeoJSON, verification frame) at build or ingest time. Add `ix_fc_mode_issue` and a `notifications` index, and stop using `LIKE` for date ranges.
3. Extend section 26 tests to cover shield geometry/scenario, threshold selection, predict fallback and notify dedup. Make conftest hermetic (no `.env`, no ProgramData writes, temp cleanup).
