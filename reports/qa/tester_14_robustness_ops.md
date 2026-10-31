# QA tester #14: robustness, operations, developer experience

Environment: Windows 11, Windows PowerShell 5.1.26100 (ANSI code page windows-1251), Python 3.10.11 (.venv), Node 22.22.3.
Checks that passed: `pytest` 22 passed (one StarletteDeprecationWarning about httpx), `npx tsc -b --noEmit` exit 0, `package-lock.json` valid (lockfileVersion 3), and `daily.ps1` and `serve.ps1` parse cleanly.
No heavy pipeline step was run. Repros used a temp `ARALSHIELD_DATA` and scratch scripts only.

### T14-1: `scripts/setup.ps1` (README quick start step 1) does not parse in Windows PowerShell 5.1
- **Severity (proposal):** High (a fresh clone cannot complete step 1)
- **Location:** `scripts/setup.ps1:12`, `README.md:38`
- **Evidence / reproduction:** `[Parser]::ParseFile('scripts\setup.ps1')` reports `12:18 TerminatorExpectedAtEndOfString`. The file is UTF-8 without a BOM, so PS 5.1 reads it as windows-1251. In that code page, byte 0x93 of the en dash in "~30–40 мин" becomes a smart quote, which PowerShell treats as a string delimiter. Parsing the same text explicitly as UTF-8 gives 0 errors. A parse error stops the whole script from running, so no venv, pip install or npm step happens.
- **Expected vs actual:** `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1` should set up the environment. Instead it fails before any line executes. It works only under PowerShell 7 (`pwsh`).
- **Suggested fix:** save every `.ps1` as UTF-8 *with BOM*, or keep code strings ASCII-only. Add a CI or test check that parses the scripts with PS 5.1.

### T14-2: Re-running `daily` makes `notify` send the same alert again; dry-run and failed sends are wrongly treated as "done"
- **Severity (proposal):** High (spec 22.5 idempotency; duplicate SMS/Telegram messages to residents)
- **Location:** `aralshield/db.py:125` (`INSERT OR REPLACE`), `aralshield/voice/notify.py:75-94`, `notify.py:99-114`
- **Evidence / reproduction:** scratch script with a temp DB, one subscriber, and the same forecast saved twice:
  `ids after daily#1: [1]`, then notify (dry-run) gives `sent: 1`. The next notify(dry_run=False) gives `skipped: 1`, because the dry-run row was stored as `status='sent'`. A second `save_forecasts` of the same item gives `ids: [2]`, and notify then gives `sent: 1` again. The outbox now holds 2 copies, and the notifications table keeps an orphan row with `forecast_id=1`.
  Also: a send with `ok=False` is stored as `failed`, but the `done` check (l.75) does not look at status, so failed sends are **never retried**. `ask_feedback` has no deduplication and asks "Было пыльно?" again on every notify run.
- **Expected vs actual:** running J6 or J8 again for the same date should not resend, should retry failures, and should not let a dry-run block the real send. Today all three behave the other way.
- **Suggested fix:** use `INSERT … ON CONFLICT(station,issue_date,horizon,mode) DO UPDATE` so the row `id` stays the same. Deduplicate on `(station, issue_date, horizon, subscriber, channel)` with `status='sent'`. Do not write notification rows on dry-run. Add a `feedback_asked` flag.

### T14-3: `aralshield.cli --help` crashes whenever stdout is not a console
- **Severity (proposal):** Medium
- **Location:** `aralshield/cli.py:129-130` (`parse_args` runs before `_setup_logging`, which is where stdout is reconfigured to UTF-8)
- **Evidence / reproduction:** `.venv/Scripts/python.exe -m aralshield.cli --help | head` raises `UnicodeEncodeError: 'charmap' codec can't encode character '\u04b1'` (the "ұ" in "Тұз"). It works only when `PYTHONIOENCODING=utf-8` is set.
- **Expected vs actual:** help should print in any context (pipe, redirect, CI, scheduler log). Instead there is a traceback.
- **Suggested fix:** move the `reconfigure(encoding="utf-8")` loop to the top of `main()`, before `ArgumentParser`.

### T14-4: `daily.ps1` ignores Python failures: notify still runs and the task exit code hides the failure
- **Severity (proposal):** Medium (NFR-01/NFR-12)
- **Location:** `scripts/daily.ps1:4,8-9`
- **Evidence / reproduction:** in PS 5.1, `$ErrorActionPreference="Stop"; & python.exe -c "sys.exit(3)"; Write-Output ...` prints `continued after failure, LASTEXITCODE=3`. `Stop` does not apply to exit codes of native programs, so a failed `daily` is followed by `notify`, which sends the previous day's "latest" forecast. Task Scheduler's "Last Run Result" shows only notify's exit code.
- **Expected vs actual:** if daily fails, the script should stop and exit non-zero. Today it carries on.
- **Suggested fix:** add `if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` after each call. Write a transcript or log with `Start-Transcript`.

### T14-5: The schtasks command in `daily.ps1` breaks when pasted into PowerShell (and the path has spaces)
- **Severity (proposal):** Medium (ops instructions)
- **Location:** `scripts/daily.ps1:3`, `README.md:61`
- **Evidence / reproduction:** passing the documented arguments from PS 5.1 to a script that prints argv gives the `/TR` value `'powershell -ExecutionPolicy Bypass -File " C:\\Users\\Админ\\...\\daily.ps1\\'`. The quote is unbalanced, a stray space is added and a backslash trails the path. `\"` is a cmd.exe escape, not a PowerShell one, and this project path contains spaces. The README code blocks are all `powershell`, so users will paste it there.
- **Expected vs actual:** a working task. Instead the task gets a broken command line. Also no `/RU` or "run whether logged on" option and no "start when available", so a laptop that is asleep at 05:30 skips the run (inferred from schtasks defaults, not tested).
- **Suggested fix:** document it for cmd.exe explicitly, or give a PowerShell `Register-ScheduledTask` snippet using `-WorkingDirectory` and `-Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable)`.

### T14-6: On network loss the retry budget can push the daily run past 06:00 (NFR-01)
- **Severity (proposal):** Medium
- **Location:** `aralshield/weather/openmeteo.py:36-48`, `aralshield/truth/isd.py:37-51`, `aralshield/forecast/predict.py:157-175`
- **Evidence (from the code):** `_get` makes 5 tries with `timeout=180` and back-off sleeps of 5+10+20+40+80 s. It also sleeps after the final failure and retries 4xx errors such as 400, because `HTTPError` is a `RequestException`. ISD makes 3 tries (timeout 120, sleeps 3+6+12). Each station calls weather, ISD and CAMS in sequence.
  - When the connection is refused quickly: about 155+21+155 ≈ 331 s per station, about 22 min for 4 stations.
  - When the connection hangs until timeout: about (900+155)+(360+21)+(900+155) ≈ 41 min per station, about 2.8 h in total.
  - The task starts at 05:30. There is no global deadline or circuit breaker.
- **Expected vs actual:** the degraded B2 forecast should be issued before 06:00 (spec 21.2 / NFR-11). Today it can be hours late. 429 responses are retried without a log line.
- **Suggested fix:** set a run deadline (for example 15 min). After the first connection error, skip the remaining stations for the same host. Do not retry 4xx except 429. Do not sleep after the last attempt.

### T14-7: ISD cache semantics: a transient 404 is cached forever, and `force=True` drops a good cache when the network is down
- **Severity (proposal):** Low
- **Location:** `aralshield/truth/isd.py:35-51`, `predict.py:124-127`
- **Evidence:** on 404, `path.write_text("")` writes an empty file. With `force=False` (used by `truth`), that empty file is never downloaded again. In `_recent_events`, `force=True` plus three failures raises `RuntimeError`, which is caught, so the call returns an empty series even though `RAW/isd/<isd>_<year>.csv` from yesterday exists. Persistence is then lost needlessly.
  Cache writes (`write_bytes`, `to_parquet` in `openmeteo.py:82,129,140,156`) are not atomic. An interrupted write leaves a truncated file that is trusted forever (inferred, not reproduced).
  Also `openmeteo.py:79-81`: the comment says "take one day earlier", but the code `date(y0,1,1) if y0>1940 else date(y0,1,1)` is a no-op.
- **Suggested fix:** fall back to the existing file when a forced refresh fails. Store 404 as a marker that has a TTL. Write to `*.tmp` and then `os.replace`.

### T14-8: Observability (NFR-12, 22.5): no team alerting, `/v1/health` is always HTTP 200, the log never rotates
- **Severity (proposal):** Medium
- **Location:** `aralshield/api/main.py:455-467`, `aralshield/cli.py:32-35`
- **Evidence:** `grep` finds no alert path to the team: no admin Telegram chat, email or webhook. Spec 22.5 says "после трёх неудач команда получает оповещение". `health()` returns a dict with `"status": "degraded"` but still HTTP 200, so an uptime monitor that checks the status code cannot tell. `pipeline.log` uses a plain `FileHandler` and is shared by serve, bot, daily and apikey, with no rotation. The log lives inside the OneDrive folder, which syncs and may lock it (speculative).
- **Suggested fix:** return 503 when the forecast is stale or degraded, or add `/v1/health?strict=1`. Use a `RotatingFileHandler`. Add `ALERT_TELEGRAM_CHAT_ID` and send to it from `issue_daily` when degraded or on an exception.

### T14-9: Reproducibility gaps (23.6, 22.4, NFR-08)
- **Severity (proposal):** Medium
- **Location:** `requirements.txt:19,21-22`, `aralshield/forecast/train.py:33,439-443`
- **Evidence:**
  - `certifi`, `pytest` and `httpx` are unpinned. Installed versions are certifi 2026.10.23, pytest 9.1.1 and httpx 0.28.1, and pytest warns that "Using httpx with starlette.testclient is deprecated". Transitive dependencies (starlette 1.7.0, pystac 1.15.2) are not locked. The upper bound "Python 3.10+" is not tested.
  - `forecast.joblib` and `forecast_metrics.json` store only the constant `MODEL_VERSION="forecast-lgbm-1.0.0"` and `created`. `grep` finds no dataset hash, no git commit, and no lightgbm, sklearn or Python versions.
  - Seeds are fixed (good): `train.py:123,135` (`seed=42, deterministic=True`) and `eye/classify.py:79,108`.
- **Suggested fix:** pin every package, or generate a `requirements.lock` with `pip freeze`. Store `sha256(dataset.parquet)`, `git rev-parse HEAD` and library versions in the artifact and in `forecasts.inputs_json`.

### T14-10: What a `git add .` would commit tomorrow (simulated with a temp `--git-dir`, `ls-files -o --exclude-standard`)
- **Severity (proposal):** Low / Info
- **Location:** `.gitignore`
- **Evidence:** 82 files, 16.3 MB in total. The largest are `models/forecast.joblib` 3.1 MB, `data/features/dataset.parquet` 2.8 MB, `data/eye/rgb_2025.png` 2.6 MB, `data/clean/observations.parquet` 1.8 MB, and the shield exports at about 3.6 MB. node_modules, dist, .venv, raw (198 MB), `*.tif`/`*.npy` (118 MB) and the sqlite file (10 MB) are correctly excluded.
  - Secret scan of the text files: no tokens or keys, and no `.env` file is present. One personal item: the docstring at `aralshield/__init__.py:14` contains the local username.
  - Questionable inclusions: `reports/qa/_spec_report_text.txt` (334 KB, the full case document; check you have the right to publish it), `frontend/tsconfig.tsbuildinfo` (a build artefact), `.claude/launch.json` (harmless), and `reports/qa/*` tester notes.
  - On a fresh clone, `models/eye_rf.joblib` and `data/eye/*.tif` are ignored, so `/v1/health` reports `"eye": false`, and `shield` cannot be re-run without the 15–25 min `eye` step.
- **Suggested fix:** ignore `*.tsbuildinfo`. Decide whether to publish `reports/qa/`. Mention in the README that eye artefacts are rebuilt by `cli eye`.

### T14-11: The GDAL CA-bundle workaround also redirects `requests` and overrides a user's `CURL_CA_BUNDLE`
- **Severity (proposal):** Low
- **Location:** `aralshield/__init__.py:16,27-38`
- **Evidence:** after `import aralshield`, `requests.Session().merge_environment_settings(...)['verify']` returns `C:\ProgramData\aralshield\cacert.pem`, because requests honours `CURL_CA_BUNDLE`. Line 37 overwrites an existing `CURL_CA_BUNDLE`, for example a corporate proxy CA, because the guard checks only `GDAL_CURL_CA_BUNDLE`. The copy is refreshed only when the file size differs, so an older or other file of the same size in a shared `C:\ProgramData` stays trusted. That is a theoretical risk on multi-user hosts.
- **Suggested fix:** set only `GDAL_CURL_CA_BUNDLE`. Use `setdefault` for `CURL_CA_BUNDLE`. Compare hashes, not sizes, or use a per-user `%LOCALAPPDATA%` cache.

## Improvement suggestions
1. Add a smoke test for the fresh-clone path: parse the `.ps1` files under PS 5.1, run `cli --help` through a pipe, and run `pytest`. Run it on `windows-latest` CI with PowerShell 5.1.
2. Add tests for spec 22.5 and NFR-11: patch `requests.get` to raise and check that `issue_daily` returns `degraded=1` within a time budget; check that two runs of `daily` followed by `notify` send exactly once. `conftest.py:5` also leaks one `mkdtemp` directory per run, so clean it up.
3. Add a one-command duty runbook (`scripts/daily.ps1 -Force`, a manual run as required by spec 22.5) that logs to `reports/daily_<date>.log` and exits non-zero on a degraded or failed run, so Task Scheduler history and monitoring can see it.
