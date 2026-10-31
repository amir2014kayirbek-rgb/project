# QA tester #03: weather adapters and the daily live forecast

Scope: `aralshield/weather/openmeteo.py`, `aralshield/forecast/predict.py` (+ `features.py`, `db.save_forecasts`, `/v1/forecasts`, `/v1/health`).
Method: code reading; `issue_daily(save=False)` run on 2026-10-27 with `ARALSHIELD_DATA` pointed at a temp copy of `data/features`, so the project DB and raw zone were not touched; failure injection by monkeypatching `requests.get`, `time.sleep`, `fetch_*` and `isd.download_station_year`; test-set replay with `models/forecast.joblib`; GET on the running API on 2026-10-28.
**Checks that passed:** UTC→UTC+5 shift (every local day has 24 h, edges excepted); m/s units on every call; `forecast_days=4` fully covers D+2 (last full local day 2026-10-30); `past_days=21` is enough for `precip_14` (non-NaN from day 8); horizon mapping `day = issue + h` matches `add_persistence` (lag = h+1); CAMS failure only adds a note, acceptable for an auxiliary input.

### T03-1: Persistence features are always NaN in live mode, yet forecasts are not marked degraded
- **Severity (proposal):** High
- **Location:** `predict.py:119-131` (`_recent_events`), `predict.py:166-168`, `predict.py:183`
- **Evidence / reproduction:**
  - NOAA returns 404 for `…/2026/<isd>.csv` (log: `нет данных 35746099999 за 2026`) for all four stations.
  - The cached 2025 file ends at `2025-08-24T21:00`.
  - `_recent_events` downloads only `local_today().year` and never looks at the previous year, so in early January persistence is NaN even when NOAA is healthy.
  - In the live run every row shows the note «нет свежих наблюдений…», but `degraded=0`. The stored forecast confirms it: `/v1/forecasts?settlement=aralsk` → `"degraded":false`, and `/v1/health` → `degraded_forecasts: 0`.
  - Only 4.1 % of training rows have NaN `dust_lag`; live has 100 %.
  - Replaying the test years (2020-2025) with `dust_lag`/`dust_lag_7d` set to NaN raises the mean predicted p: h1 0.102→0.117 (+14 %), h2 0.103→0.127 (+24 %), against a base rate of 0.107. The vote calibration therefore drifts upward.
- **Expected vs actual:**
  - Expected: a missing input is shown as degraded or simplified, or the model is trained to handle the missing value (spec 21.2, 22.3).
  - Actual: the forecast is presented as a full model forecast.
- **Suggested fix:**
  - Read the previous year too, and fall back to the cached file when the download fails.
  - Set `degraded=1` (or a separate `inputs_missing` flag) when the persistence features are NaN.
  - Optionally train a variant with the persistence features masked.

### T03-2: A missing feature appears as a "driver" in the explanations
- **Severity (proposal):** Medium
- **Location:** `predict.py:69-81` (`_drivers`)
- **Evidence:**
  - In the live run `dust_lag_7d` («пыльные дни за неделю», direction "up") is a top-4 driver in 10 of 12 rows, with contributions +0.13…+0.31, even though the value is NaN.
  - In a test replay with the value set to NaN, the SHAP of `dust_lag_7d` has mean +0.092; when it is observed as 0, the mean is −0.252.
  - The API passes it on: `"drivers":["station_code","dust_lag_7d"]`.
  - `station_code` («особенности пункта») is also the first or second driver almost everywhere, which tells the user nothing.
- **Expected vs actual:** Explanations should reflect real inputs (spec 13.6). Instead, a missing-data artifact is shown to users as a reason.
- **Suggested fix:** Drop features whose value is NaN, and `station_code`, from the user-facing drivers. Keep them in `inputs_json`.

### T03-3: The fallback is labelled "rule-B2-fallback" but is actually climatology or a constant 0.1
- **Severity (proposal):** Medium
- **Location:** `predict.py:108-116`, `predict.py:191-195`
- **Evidence:**
  - With the weather fetch failing, `row` is an empty DataFrame and `rule.predict` raises `AttributeError: 'DataFrame' object has no attribute 'gust_max'`. The bare `except` then returns climatology.
  - Output: aralsk p=0.213 (October climatology), **yellow**, `model_version="rule-B2-fallback"`, `degraded=1`; the same day's model forecast was 0.037 (green), a yellow-vs-green flip.
  - With no `forecast.joblib`, every row is p=0.1, `green`, version "rule-B2-fallback".
  - The B2 rule itself could only run with weather present (fitted rule: g=12 m/s, n=0 days).
- **Expected vs actual:**
  - Expected (spec 21.2): use B2 when the model is missing; when the weather is missing, use and *label* climatology.
  - Actual: the version label is wrong. A constant 0.1 is not B2, and the bare `except` hides errors.
- **Suggested fix:**
  - Use the labels `clim-fallback` and `const-fallback`.
  - When the model is absent but weather exists, evaluate B2 directly (gust ≥ G and dry_days ≥ N).
  - Catch only the expected exception and log it.

### T03-4: No freshness check (spec 22.3), and `issued_at` is hard-coded
- **Severity (proposal):** Medium
- **Location:** `openmeteo.py:97`, `predict.py:204-205`, `api/main.py:139`
- **Evidence:**
  - `fetched_at` is the download time, not the NWP model run time. Nothing compares the data's age to the 12 h limit; the only freshness logic in the code is `/v1/health` comparing `issue_date` to today.
  - The stored forecast was created at `2026-10-27T18:46:18Z` (23:46 local), but the API reports `"issued_at":"2026-10-27T06:00:00+05:00"`.
  - The h=0 forecast for "today" was actually issued with 15 minutes of the day left.
  - Related: `issue_daily(issue=<past date>)` silently uses *today's* forecast and today's NOAA year (`predict.py:124, 158`).
- **Suggested fix:**
  - Store the real issue time.
  - Mark rows degraded when `now - model_run > 12 h`, or when the issue time is later than about 06:00 for h=0.
  - Reject `issue != local_today()` in live mode, or use the historical-forecast API for it.

### T03-5: Reanalysis→forecast gap — operational winds and gusts are systematically lower than the ERA5 training data
- **Severity (proposal):** Medium (measured on 21 days at one station)
- **Location:** `openmeteo.py:90-98` vs `openmeteo.py:59-65`; no bias correction anywhere
- **Evidence:** For aralsk, 2026-10-06…26, I compared the live `past_days` hourly data with ERA5 archive data for the same days through `daily_features`:
  - Mean ratio operational/ERA5: `wind_max` 0.90, `gust_max` 0.84, `hours_gt8` 0.25.
  - On 2026-10-20: gust 12.9 vs 14.3, hours >8 m/s 1 vs 4.
  - The B2 threshold is 12 m/s and the model is monotone in wind, so the live system is biased toward under-warning.
  - Soil moisture and RH matched (ratios 0.99 and 0.98).
- **Suggested fix:** Quantile-map the operational wind/gust to ERA5 using `hist_forecast` (2022+), or train/calibrate on the historical-forecast archive (spec 13.3).

### T03-6: The retry policy can block the 04:30–05:30 window for hours, retries 4xx, and never alerts
- **Severity (proposal):** Medium
- **Location:** `openmeteo.py:36-48`
- **Evidence (`time.sleep` stubbed and recorded):**
  - Connection error: 5 calls, sleeps `[5,10,20,40,80]` = 155 s, plus up to 5×180 s timeouts, so about 17.6 min per call.
  - HTTP 429: sleeps `[30,60,90,120,150]` = 450 s.
  - HTTP 400: retried 5 times, although a 4xx response is permanent.
  - The final sleep happens *after* the last failure, which is wasted.
  - `issue_daily` makes 2 such calls per station, sequentially, for 4 stations. The worst case is about 2.3 h before degraded output appears.
  - Spec 22.5 calls for 3 attempts followed by a team alert; there is no alert.
- **Suggested fix:**
  - Use 3 attempts; do not retry 4xx other than 429, and honour `Retry-After`.
  - Use a short connect timeout, no sleep after the last attempt, and an overall deadline per run.
  - Log or alert on final failure.

### T03-7: Partial edge days and the truncated dry-spell counter
- **Severity (proposal):** Low
- **Location:** `features.py:98`, `features.py:106-116`, `openmeteo.py:80-81`
- **Evidence:**
  - The first local day of the live window has 19 hours (05:00–23:00 local, because the window starts at 00 UTC), but the `n_hours < 18` rule keeps it as a full day. ERA5 history has the same issue on 2000-01-01.
  - The comment «Берём на сутки раньше» is not implemented: both branches of `date(y0,1,1) if y0 > 1940 else date(y0,1,1)` are identical.
  - `dry_days` starts at 1 on the first row, where the history is unknown, and in the live run reaches only 22 on the issue day. ERA5 for the same dry spell would continue counting toward the 30 cap; 15.8 % of training rows have `dry_days > 21`.
  - Measured impact is small: capping test `dry_days` at 22 changes h0 PR-AUC from 0.587 to 0.586.
- **Suggested fix:**
  - Request `past_days ≥ 31` and start one UTC day earlier.
  - Use NaN for `dry_days` while the counter has not seen a wet day or the start of the series.
  - Require all 24 hours for wind features.

### T03-8: Re-issuing the forecast changes row ids, so notifications are sent twice
- **Severity (proposal):** High (cross-module)
- **Location:** `db.py:125` (`INSERT OR REPLACE`), `voice/notify.py:75, 92` (dedup by `forecast_id`)
- **Evidence:**
  - On a temp DB, saving the same (station, issue_date, horizon, mode) twice changed `id` from 1 to 2. `INSERT OR REPLACE` deletes the old row and inserts a new one.
  - `notify` checks `notifications WHERE forecast_id=?`, so after a manual re-run of `daily` (spec 22.5 "manual mode") every subscriber is messaged again.
  - The old notification rows are orphaned (the FK join at `notify.py:102-103` loses them).
  - This breaks the idempotency required by spec 22.5.
- **Suggested fix:** Use `INSERT … ON CONFLICT(station,issue_date,horizon,mode) DO UPDATE SET …`, which keeps the id, or dedup notifications by (station, issue_date, subscriber, channel).

### T03-9: `issue_daily(save=False)` still writes to the raw zone and overwrites raw files
- **Severity (proposal):** Low/Medium
- **Location:** `predict.py:124` (`force=True`), `truth/isd.py:35-45`, `predict.py:148`
- **Evidence:**
  - Every call re-downloads `data/raw/isd/<isd>_<year>.csv` and overwrites it, or writes a 0-byte file on 404.
  - Observed: four 0-byte `*_2026.csv` files, timestamped 23:46, in the project raw zone.
  - Spec 22.1 says the raw zone "never changes" and should carry load timestamps.
  - `save=False` also runs `db.init()`.
- **Suggested fix:** Write snapshots to `raw/isd_live/<year>_<fetched_at>.csv` (append-only), and skip all writes when `save=False`.

### T03-10: Raw-file caches never refresh, and there is no ERA5 top-up job (J10)
- **Severity (proposal):** Low (latent; the current data is complete)
- **Location:** `openmeteo.py:74-77`, `126-127`, `136-137`, `146-149`
- **Evidence:**
  - Cache keys are filename-only (`{station}_{y0}_{y1}.parquet`, `wind_…`, `cams_{start}_{end}`) and are re-read whenever the file exists.
  - A block containing the current year is fetched with `end=date.today()` and then frozen; for example, running with `YEARS` up to 2026 caches `*_2025_2026` as partial forever.
  - Today `*_2025_2025` is complete (max `2026-01-01 04:00`).
  - The same freezing applies to `truth`: the cached ISD 2025 file stops at 2025-08-24, so the 2025 test labels end there (dataset: latest label 2025-08-24).
- **Suggested fix:** Mark files that contain the current or last year as "open" and refetch them when older than N days, or store fetch metadata and refresh by age.

### T03-11: The `backtest_to_db` archive is "perfect-prog", and the test split differs from training
- **Severity (proposal):** Low
- **Location:** `predict.py:214-236`
- **Evidence:**
  - Uses ERA5 reanalysis features, not operational forecasts, but is labelled as forecasts with `model_version` hard-coded and `degraded=0`.
  - Ignores the 7-day `GAP_DAYS` gap that `train.split` applies: 84 rows from 2020-01-01…07 are included.
  - `DELETE` and `save_forecasts` run in separate connections, so the operation is not atomic.
  - Crashes with `TypeError` if the model is absent.
- **Suggested fix:**
  - Label the archive `mode='backtest-era5'`, or build it from `hist_forecast` for 2022+.
  - Reuse `split()["test"]`, run everything in one transaction, and take the version from the artifact.

## Improvement suggestions
1. Add a small, declarative input-quality step before J6 (wind 0–60 m/s, ≥ 24 h per day, model-run age ≤ 12 h, persistence present). Its result should drive a single `degraded` / `quality` field with reason codes instead of free-text `notes`.
2. Archive the raw Open-Meteo JSON response of every live run (J3 "own forecast archive"). This gives provenance (spec 21.2) and a future operational training set that fixes T03-5.
3. Add pytest cases with a mocked `requests.get` covering: Open-Meteo down, CAMS down, NOAA 404, January rollover, and re-issue without duplicate notifications.
