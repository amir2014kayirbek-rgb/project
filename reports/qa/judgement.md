# AralShield QA: judgement (judge, 2026-10-28)

Input: 15 tester reports (`tester_01` … `tester_15`, about 200 raw findings). Tester IDs are `Txx-n`; judge IDs are `A*`, `B*`, `C*`, `D*` (one fixer each).
Spot checks done for every critical or high item (read-only):
- T02-1: `_ww_from_synop('SYN082AAXX 08061 35746 11596 73204')` returns 32.0, and the Kazalinsk 2000-01-03 string returns 7.0.
- T02-2: all 9,719 FM-15 rows are at kyzylorda.
- T01-1: hist_forecast vs ERA5 wind hours are identical 100% at kyzylorda/zhosaly and 22% at aralsk/kazalinsk; soil moisture is identical 100% everywhere.
- T06-1: `REPORTS / 'eye_metrics_x/../../frontend/package.json'` `.exists()` is True.
- T05-1: the shapefile ring has a positive (CCW) signed area.
- T14-1: the PS 5.1 parser gives "string is missing the terminator" at line 12.
- Confirmed by reading the code: `db.save_forecasts` (INSERT OR REPLACE), `notify.run` (dry-run stored as `sent`, dedup ignores status), `templates.summary_message` (`"NE"` fallback), `predict.issue_daily` (persistence note does not set degraded), `SwipeClip` deps, `classify.weak_labels` and `worldcover_agreement`, `sector_source` (`nan_to_num`), `ensure_sector_distance` (untagged cache), `backtest_to_db` (no drivers or source).
- Tester line numbers are often wrong (e.g. `dataset.py:169`, `notify.py:311`), but every claim was found at the right function.

## Summary

| Severity | A data/ML | B Eye/Shield | C backend/bot | D frontend/docs | Total |
|---|---|---|---|---|---|
| critical | 2 | 0 | 0 | 0 | **2** |
| high | 6 | 3 | 6 | 7 | **22** |
| medium | 9 | 11 | 21 | 10 | **51** |
| low | 9 | 2 | 10 | 3 | **24** |
| invalid | | | | | **1** |
| wontfix | | | | | **16** |

## Global rules for all fixers
1. **Do not run pipeline commands** (`cli truth/eye/dataset/train/shield/backtest/daily/notify/all`) and never write to `data/`, `models/`, `reports/*.json|md` or `data/aralshield.sqlite`.
   - Verify in memory, on temp copies, or with `ARALSHIELD_DATA=<tmp>`.
   - The lead will regenerate everything in this order: truth → eye `--tag 2025v2` (uses the existing `data/eye/s2_composite_2025v2.tif`; `build_composite` skips existing files) → dataset → train → shield `--tag 2025v2` → backtest → daily.
2. **Honesty is fixed at the generators, not in the artefacts.** That means `train.py` (report writer and gates), `eye/classify.py` (metrics JSON), `shield/priority.py` (metrics JSON) and `README.md` "Ограничения". Never edit the generated `reports/*.md|json` by hand.
3. **File ownership is strict.**
   - `tests/test_core.py` belongs to **C**. A and B add only new `tests/test_forecast_*`, `test_truth_*`, `test_eye_*` and `test_shield_*` files. If your change breaks a `test_core.py` assertion, report it to the lead instead of editing.
   - `pytest -q tests` must pass for each fixer's slice.
4. **New user-visible strings need both ru and kk**, or a code that D localises.

## Fixer A — data & ML (`truth/`, `weather/`, `forecast/`, `config.py`)

| ID | Sev | Title | Files | Done means |
|---|---|---|---|---|
| A1 (T02-1) | critical | SYNOP fallback reads `Nddff` as `7wwW1W2` in AAXX remarks: ~1,500 false dust days; Kazalinsk 19.8 → ~4/yr | truth/isd.py | Parser tokenises after `SYN`, skips `AAXX YYGGi IIiii`, reads `iRixhVV`/ix, skips `Nddff` and the optional `00fff`, takes 7-group only in section 1 when ix ∈ {1,4} (ix=7 see A21), stops at 333/555/222. `tests/test_truth_synop.py`: `…35746 11596 73204…` → NaN; `SYN076AAXX 03001 35849 42998 70704` → NaN; `…41/// 83502 … 74742` → 47; new layout unchanged. In-memory rebuild shows Kazalinsk 2000 ≈0 dust days. |
| A2 (T01-1) | critical | "Operational forecast" check is ~half ERA5 (kyzylorda/zhosaly hist-forecast == ERA5), cost understated ~2× | weather/openmeteo.py, forecast/train.py | Historical-forecast request passes explicit `models=` (e.g. `ecmwf_ifs025`); cache filename includes the model, so the lead refetches. `evaluate_operational` computes per-station fraction of wind hours identical to ERA5, excludes stations ≥95% identical, writes `operational.identical_frac` and `operational.excluded_stations` to metrics, and the report text names them. Unit test with synthetic identical frames → station excluded. |
| A3 (T01-2, T01-10) | high | h1/h2 skill is perfect-prognosis (ERA5 actuals of target day); CAMS comparison not like-for-like | forecast/train.py (report writer) | `forecast_metrics.json` `h1/h2.weather_inputs="era5_actual_upper_bound"`. `forecast_report.md` labels h1/h2 numbers "верхняя граница (фактическая погода дня)" and adds a caveat to the CAMS section. Optional: lead-time evaluation via Open-Meteo `previous_day1/2`. |
| A4 (T01-3, T13-3 A-part) | high | H3 gate "true" on +0.003 point difference; paired bootstrap CIs contain 0 | forecast/train.py | `H3_source_features_help` = lower bound of paired weekly block-bootstrap CI of ΔPR-AUC(M2 − M2-abl) > 0. `H3_delta_pr_auc_ci` and ΔBSS CI are stored. Report says "H3 не подтверждена" when false. Test on synthetic equal models → false. |
| A5 (T02-2) | high | Duplicate synoptic term keeps arbitrary record; FM-15 METAR replaces SYNOP in 21% of Kyzylorda terms | truth/isd.py | Stable sort on (time_utc, priority FM-12 > SY-MT > FM-16 > FM-15), then keep first. The number of conflicts is logged. Test with a mixed FM-12/FM-15 term keeps FM-12. |
| A6 (T02-3) | high | Visibility criterion labels blowing snow (36–39), mist/fog (10–12, 28) and precipitation (20–29) as dust | truth/isd.py (+ `predict._events_no_save` via A26) | `by_vis` only if ww is NaN or ww ∉ {10–12, 20–29, 36–99}. Wind uses `>` threshold per spec, or the choice is documented. Test: ww=38, VIS 3 km, 11 m/s → not dust. |
| A7 (T03-1, T13-2, T03-9, T14-7 part) | high | Live persistence features always NaN, yet `degraded=0`; previous year never read; live path overwrites raw ISD files with 0-byte files | forecast/predict.py, truth/isd.py | `_recent_events` reads current + previous year and falls back to the existing cached file when the download fails. It never writes a 0-byte file or overwrites raw in live mode (snapshot to `raw/isd_live/` or memory only), and `save=False` writes nothing. Missing persistence → `degraded=1` plus `inputs_json.degraded_reasons` (see contract K2), while **M2 is still used** (only weather/model absence triggers the fallback). Test with monkeypatched download. |
| A8 (T13-1 A-part, T03-11) | high | Backtest archive has no drivers/source, so the replayed storm (demo 2:10) shows `drivers:[]`, `source_sector:null`; split ignores GAP_DAYS | forecast/predict.py | `backtest_to_db` fills `drivers_json` (same schema as live, filtered per A9) and `source_sector`/`source_km` (via `_source` on cached ERA5 hourly); at minimum all rows with public level ≥ yellow. Uses `train.split()["test"]` (gap respected), `model_version` from artefact, `inputs_json.weather="ERA5 (perfect-prog)"`, and calls the atomic `db.save_forecasts(items, replace_mode="backtest")` (K4). Verified on a temp DB copy. |
| A9 (T03-2, T07-13 drivers) | medium | NaN features and `station_code` shown as user-facing "drivers" | forecast/predict.py, forecast/train.py (`replay.drivers_h1`) | `_drivers` and `drivers_h1` drop NaN-valued features and `station_code` (they stay in `inputs_json`). Test. |
| A10 (T03-3, T15-9, T15-6 part) | medium | Fallback labelled `rule-B2-fallback` while actually climatology/const 0.1; bare `except` | forecast/predict.py | B2 is evaluated directly when weather exists. Labels are `rule-B2-fallback` / `clim-fallback` / `const-fallback` with matching reason codes. The `except` is narrowed and logged. Tests for each path, plus `choose_thresholds` on a synthetic set. |
| A11 (T03-4 A-part) | medium | `issue_daily(issue=past)` silently uses today's data; real issue time not stored | forecast/predict.py | In live mode, `issue != local_today()` raises ValueError. `inputs_json.issued_at` = real UTC timestamp (C exposes it, K5). |
| A12 (T03-6, T14-6) | medium | Retry policy can delay the run by hours; retries 4xx; sleeps after the last try | weather/openmeteo.py, truth/isd.py, forecast/predict.py | 3 attempts, (connect 10 s, read 60 s) timeouts. No retry on 4xx except 429 (honour Retry-After). No sleep after the last attempt. `issue_daily` has a deadline (≤15 min) after which remaining stations go degraded. Test with stubbed `requests.get`/`time.sleep`. |
| A13 (T02-5, T02-6, T01-7) | medium | Completeness counts METAR-only/empty terms; Kazalinsk has almost no ww reporting; inhomogeneous years use test-year labels | truth/isd.py, forecast/dataset.py | `n_terms` counts only FM-12/SY-MT terms with VIS or a weather indicator. `completeness.csv` gains `ww_report_rate`. Station-years below a documented threshold are label-unknown (`reliable=False`) for training/eval. `inhomogeneous_years` medians use TRAIN_YEARS only. |
| A14 (T01-4) | medium | BSS reference climatology from train only (base rate 17% → 11%) | forecast/train.py | B0 is fitted on train+valid for test BSS. Report also gives BSS vs sample climatology. |
| A15 (T01-5) | medium | B2 baseline degenerate (`dry_days ≥ 0`), 8 distinct values; B3 not in gate | forecast/train.py | Adds a calibrated continuous wind baseline (isotonic gust per station). Constrains N ≥ 1, or reports that dryness adds nothing. The gate uses the best of B0–B3 where B3 exists. |
| A16 (T01-6) | medium | H4 "confirmed" is confounded by season and autocorrelation | forecast/train.py | Mantel-Haenszel OR with year-month strata and a block-permutation p-value. Per-lag conclusion (lag 2 not significant if so). `H4.conclusion_ru` and `conclusion_kk`. |
| A17 (T03-5) | medium | Operational wind/gust ~10–16% below ERA5 → under-warning bias | forecast/train.py (report text) | Bias is quantified on the stations with genuine hist-forecast (A2) and written to report limitations. Quantile mapping is optional. |
| A18 (T01-8) | low | Valid set reused 4×; h1 thresholds applied to all horizons | forecast/train.py | Thresholds chosen per horizon (out-of-fold if cheap). Optuna/LOYO-CV is wontfix. |
| A19 (T01-9) | low | ECE gate weak for a rare event | forecast/train.py, metrics.py | Quantile-binned ECE added to metrics and report. |
| A20 (T01-11, T13-9) | low | Static 2025 source map used for 2000–2025 rows (13.3 leakage) | forecast/train.py, predict.py | Report states it explicitly. `inputs_json.sector_map` takes its tag from `sector_source.json["tag"]`. |
| A21 (T02-4) | low | ix=7 / AW1 (table 4680) not distinguished (latent) | truth/isd.py | ix=7 → wawa mapped, or ignored for the code criterion. Test. |
| A22 (T02-7, T03-10, T14-7, T03-7 no-op) | low | 404 cached forever; open-year caches frozen; non-atomic writes; "day earlier" no-op | truth/isd.py, weather/openmeteo.py | 404 marker with TTL. Open/current-year files are refetched by age. Writes go to `*.tmp` + `os.replace`. Start date really one day earlier. Report marks 2025 as a partial year (ends 2025-08-24). |
| A23 (T03-7) | low | Partial edge days; `dry_days` starts at 1 with unknown history | forecast/features.py, weather/openmeteo.py | `dry_days` NaN until the first wet day; `past_days ≥ 31`; days with < 24 h are dropped for wind features. Measure the effect and keep it if it is ≤0.002 PR-AUC. |
| A24 (T10-18) | low | DRIVER_LABELS wording | forecast/predict.py | Use the T10-18 labels (min humidity, snow cover, consistent `жел жағындағы`). |
| A25 (T13-8, T14-9 A-part) | low | No own operational-forecast archive; artefact lacks provenance | weather/openmeteo.py, forecast/predict.py, train.py | Each live fetch is saved to `raw/oper_forecast/<station>_<issue>.parquet`. Artefact + metrics store dataset sha256 and lib versions. A `model_versions` row is written via `db.execute`. |
| A26 (T15-8 A-part) | low | `_events_no_save` duplicates the dust rule; unused `io` import | forecast/predict.py, truth/isd.py | `_recent_events` uses `isd.build_events(save=False)`; the import is removed. |

## Fixer B — Eye & Shield (`eye/`, `shield/`)

| ID | Sev | Title | Files | Done means |
|---|---|---|---|---|
| B1 (T04-1 B-part) | high | "Independent" WorldCover check is circular: labels come from WorldCover; on unlabeled pixels the model is worse than a constant | eye/classify.py | `worldcover_agreement` evaluates only pixels with weak label 0 (optionally excluding their 10 km blocks) and reports the majority-class baseline. JSON: `independent: false`, `note_ru`/`note_kk` = "согласие с WorldCover, не независимая проверка". |
| B2 (T04-4) | high | Composite gaps and the AOI edge count as "no source" in F_s(θ); Kyzylorda F_s ≈0 everywhere | eye/classify.py | `sector_source` normalises by the weighted area of *valid* pixels per sector and returns `coverage` per sector; `sector_source.json` gains `coverage` (contract K1). Test: synthetic disk p=1 with half NaN → F_s=1 in covered sectors, coverage≈0.5. Stations/sectors with coverage < 0.5 are listed in eye metrics. |
| B3 (T05-1) | high | Shapefile rings are CCW, so every cell is a "hole" in ESRI readers | shield/priority.py | `w.poly` gets clockwise exterior rings (GeoJSON/KML stay CCW). Test reads back with pyshp: all signed areas < 0. |
| B4 (T05-2 B-part) | medium | No `.cpg`; SHP/KML lack the §14.6 fields; N/F width 40 | shield/priority.py | Writes `priority_top.cpg` (`UTF-8`). Adds fields `area_ha`, `prot_pop`, `stab`, `source`, with numeric widths ≤19 (C zips the `.cpg`, C37). |
| B5 (T05-6, T13-3 B-part) | medium | Sensitivity τ on the union of top-200s is ill-posed; base combo included; M weights never varied | shield/priority.py | Base combo excluded. Reports τ, top-N overlap and RBO. M weights perturbed as in the spec. `gate_rank_stability_ge_0_7` keeps its definition (τ) and is never silently swapped; any new gate gets a new key. |
| B6 (T04-6) | medium | `sector_distance.json` cached without tag, so it goes stale after the 2025v2 switch | eye/classify.py | `ensure_sector_distance(tag=EYE_TAG)` stores the tag and recomputes when the tag differs or `source_prob_<tag>.tif` is newer. Return shape unchanged. |
| B7 (T04-2) | medium | Weak-label thresholds are scene percentiles (classes always exist); empty `bare` → NaN/crash | eye/classify.py | Guards against empty `bare`. Thresholds are computed once and persisted as `label_thresholds` in eye metrics, so they are not renormalised per scene; document. |
| B8 (T04-3 B-part) | medium | CV OA 0.999 is uninformative (labels are threshold functions of the features) | eye/classify.py | `cv.informative=false`, `cv.meaning_ru`/`meaning_kk`; CI for κ/F1 if cheap. |
| B9 (T04-8) | medium | Double balancing inflates wet-salt area; `proba[:,0]` assumes all 5 classes present | eye/classify.py | Columns are taken via `rf.classes_`. Area is either prior-corrected or flagged `area_note`. Test with a missing class. |
| B10 (T05-3) | medium | `protected_population` ≈ whole region or 0 | shield/priority.py | Definition is effective people Σ_j N_j·E_ij/ΣE_i (or the top-80% share), documented in metrics. |
| B11 (T05-4) | medium | Far Kyzylorda/Baikonur dominate; no distance cap | shield/priority.py | `d ≤ SOURCE_RADIUS_KM` applied (or a sensitivity row with and without), plus a per-settlement contribution table in `shield_metrics.json`. |
| B12 (T05-5) | medium | M is a rank over the whole rectangle; poorly suitable cells reach top classes | shield/priority.py | Ranks over valid cells only; minimum M (e.g. ≥0.4) for "высокий"/"надёжный высокий". The missing road-access term is documented (wontfix W9). |
| B13 (T05-7, T05-8, T13-5 B-part) | medium | "1000 ha" is 1200 ha; random baseline includes S=0 cells; no "plan vs rating" comparator | shield/priority.py | Exact area via a partial last cell. `plan_cells(...)` supports `rating`, `random` (S > median & M > 0.5, mean/p5/p95 over 50 draws) and `nearest` (suitable cells closest to settlements, a proxy for current practice) (K7). `comparison_1000ha.area_ha == 1000`. |
| B14 (T15-2) | medium | `to_geojson` row loop: ~1 s and 2.7 MB for 6000 cells | shield/priority.py | Vectorised, coordinates rounded to 5 dp, same output schema; ≤300 ms for 6000 features (measured in test or log). |
| B15 (T05-10 part) | low | Export boxes are lat/lon-aligned, not UTM squares | shield/priority.py | Exports the true UTM corners reprojected to WGS84 (KZ border mask is W9). |
| B16 (T04-7) | low | EPSG:4326 PNG stretched in Web Mercator (≤2.3 km offset) | eye/classify.py | PNG rendered on an EPSG:3857 grid with corner bounds, or documented in metrics. |

B also adds `tests/test_eye_sector.py` and `tests/test_shield_geometry.py`: a cell due north → sector N; an empty selection → 0% reduction; reduction monotone in `cover`; ring orientation; `.cpg` present.

## Fixer C — backend, bot, delivery (`api/`, `db.py`, `voice/`, `cli.py`, `__init__.py`, tests)

| ID | Sev | Title | Files | Done means |
|---|---|---|---|---|
| C1 (T06-1, T07-1) | high | Path traversal: `/v1/reports/eye?tag=`, `/v1/sources/preview?tag=`, `/v1/sources/maps?month=` read any `.json` | api/main.py | `tag` pattern `^\d{4}(v\d+)?$`, `month` `^\d{4}(-\d{2})?$`; resolved path must be inside REPORTS / EYE_DIR. Errors do not echo paths. Default tag = `config.EYE_TAG` (K1). Tests: `x/../../frontend/package` → 422. |
| C2 (T03-8, T12-1, T14-2) | high | Re-issuing a forecast gets new ids, so every subscriber is notified again; orphan notifications | db.py, voice/notify.py | `save_forecasts` uses `INSERT … ON CONFLICT(station,issue_date,horizon,mode) DO UPDATE` (id preserved) (K4). Notify dedups on (station, issue_date, subscriber, channel) with `status='sent'` and resends only if the level rose. Test: save twice + notify twice → 1 message. |
| C3 (T12-2, T12-3) | high | Dry run / no token stored as `sent`, so the real alert is never sent; failures never retried; yellow SMS counted failed | voice/notify.py | Dry-run/no-token → `status='dry_run'`, excluded from dedup; SMS also written to outbox in dry-run. `failed` retried on the next run. SMS below orange → `skipped`. Tests for all three. |
| C4 (T12-4, T11-2, T11-3, T07-3, T07-4, T07-12, T07-15, T10-10, T03-4 C-part) | high | Stale forecasts presented as current: notify/bot/API say "сегодня" for yesterday's issue; global MAX(issue_date) hides stations | db.py, voice/{notify,bot,templates}.py, api/main.py | One helper gives per-station latest issue. `when_text` is relative to `local_today()`, with an absolute "29 октября (четверг)"/"29 қазан (бейсенбі)" fallback. Notify sends nothing (logs warning) if `issue_date != local_today()` and skips `day < today`. Bot `/forecast` shows the issue date and a warning when stale. API adds `issued_at` (from `created_at`/A11), `stale`, `age_hours`, `past` (K5). `/v1/health` uses a 30 h age threshold and status ok\|stale\|degraded. Tests with a 5-day-old issue. |
| C5 (T07-2, T10-3) | high | Summary invents source "северо-восточная часть" when `source_sector` is NULL | voice/templates.py | Source clause only when the sector is known; test with NULL. |
| C6 (T13-7 C-part) | high | Bot demo depends on the weather (demo 2:40) | voice/bot.py, templates.py | `/demo` renders the replay event message (K8) in the user's language, labelled "ДЕМО: архивное событие {date}", plus SMS text. Works offline from the DB. |
| C7 (T10-2, T11-11, T12-9, T11-1) | medium | Telegram messages exceed 400 chars; no teacher "Переслать родителям" | voice/templates.py, bot.py, notify.py | Shorter disclaimer, distance dropped. Parents text sent as a separate message with a forward/share button in `/forecast` and notify. Test over all role×level×day×lang with all fields: ≤400 Telegram, ≤70 SMS. |
| C8 (T13-4) | medium | FR-06: no Kazhydromet link in green messages, `/forecast` list or summary | voice/templates.py, bot.py, tests/test_core.py | Every message, the list and the summary carry the link; `test_core.py:145` updated. |
| C9 (T07-6..T07-9, T13-6, T12-11, T15-4, T11-9 a/b/d/e) | medium | Accuracy: backtest shown as current, window anchored at last verified day, live/backtest mixed, `0d` = all, FAR hidden | api/main.py (or a new `api/verify.py`), voice/bot.py, templates.py | Filters (`mode`, dates) are pushed into SQL and deterministic. Response has `mode`, `as_of`, `from`/`to`, always-present counts, and `pr_auc`/`brier` null when undefined. `period ≥ 1d`. Text and summary say "архив тестовых лет 2020–2025, {from}–{to}". Bot uses 30 days in plain words (no POD/FAR) and `log.exception`. |
| C10 (T07-5, T10-17 C-part) | medium | `/v1/forecasts?settlement=baikonur` echoes Zhosaly | api/main.py, templates.py | `settlement` echoes the requested id/names; `forecast_station` added (K5). Preview header "Байконур (по станции Жосалы)". |
| C11 (T07-10) | medium | `set_subscription` deletes the subscriber's other stations and resets fields | db.py, api/main.py | Upsert per (subscriber, station); per-station delete; settlement ids accepted via `_station_for`; unspecified fields preserved. |
| C12 (T06-4, T07-11, T11-6, T12-10 mask) | medium | Feedback hash reversible; feedback/notifications survive `/stop`; plaintext phones in outbox | db.py, voice/bot.py, notify.py | HMAC with `ARALSHIELD_HASH_PEPPER` (K9). `/stop` deletes feedback by hash and notifications. Bot replies `no_sub` when nothing was deleted. Outbox masks phones (`+7700***1234`). |
| C13 (T11-5, T06-8) | medium | Feedback accepts any date / non-notified users | voice/bot.py, api/main.py | ISO date ≤ today and ≥ today−7, and a matching notification required; otherwise a silent `cb.answer()` / 422. |
| C14 (T12-7) | medium | "Было пыльно?" re-asked on every notify run; host date used | voice/notify.py, db.py | Asked once (marker column/table); uses `local_today()`. Test: 3 runs → 1 ask. |
| C15 (T06-3) | medium | Bot token written to logs on network errors | voice/notify.py, cli.py | Logging filter masks `bot\d+:[\w-]+`; notify logs the exception type only. Test with a fake token. |
| C16 (T06-5) | medium | Internal methods trust client IP; any Host; `==` key compare | api/main.py, cli.py | TrustedHostMiddleware (localhost, 127.0.0.1, configured host); `hmac.compare_digest`; `cli serve` refuses a non-loopback `--host` without `ARALSHIELD_INTERNAL_KEY`. |
| C17 (T06-2, T08-3 C-part) | medium | `/v1/priorities?limit=20000` stalls the whole API | api/main.py | `limit ≤ 7000`. Serialised response cached per (mtime, classes, min_suit, limit). FeatureCollection carries `total_matching` (K6). |
| C18 (T11-4) | medium | Stale wizard button commits `role=resident` | voice/bot.py | `on_level` requires lang, station and role. Callbacks from other wizard messages are ignored. Draft TTL. |
| C19 (T11-7) | medium | user id stored as chat id; group chats unhandled | voice/bot.py | Router filtered to private chats (documented). |
| C20 (T11-9c, T15-5) | medium | Bot does blocking SQLite/pandas and imports FastAPI on the event loop | voice/bot.py | DB/pandas via `asyncio.to_thread`; shared accuracy service without importing `api.main`. |
| C21 (T12-6, T13-10 C-part) | medium | `channel=email` silently sent to the Telegram API | api/main.py, voice/notify.py | `email` rejected (422) in subscriptions/feedback; notify skips unknown channels with a log line. The email digest is wontfix W5. |
| C22 (T12-8) | medium | Health groups get "вероятен — 12%" | voice/templates.py | For health/school groups below the public orange threshold the wording is "возможен, повышенный риск для вас" (ru/kk). |
| C23 (T15-1, T12-12 part) | medium | Notify is N+1 (2 connections/commit per subscriber, ~30 ms each) | voice/notify.py, db.py | One dedup query, one transaction, one outbox handle. Test: 5k subscribers dry-run < 15 s on temp DB. |
| C24 (T10-4, T10-5) | medium | KK ablative/possessive after numbers hard-coded; RU plurals | voice/templates.py, api/main.py | `kk_abl(n)` / `plural_ru()` helpers (e.g. in `voice/lang.py`) used in accuracy text and `after_dry`. Tests for 1, 4, 5, 7, 8, 10, 20, 21. |
| C25 (T10-1, T10-6, T10-7) | medium | Typo «усилите»; actions not starting with a verb; red action repeats disclaimer; KK help wording | voice/templates.py, bot.py | «усильте»; imperative verbs first; red public action concrete; one verb form; KK help per T10-7. |
| C26 (from T03-1/T13-2) | medium | Expose reduced-confidence reasons | api/main.py, templates.py | `degraded_reasons` in `/v1/forecasts` horizons and explanation (K2); degraded message line unchanged. |
| C27 (T13-5 C-part, T05-7 C-part) | medium | API what-if lacks plan comparator and exact area | api/main.py | `plan ∈ {custom, rating, random, nearest}` via B's `plan_cells` (K7); returns exact `area_ha`, `cells`, unique `cell_ids`; random returns mean/p5/p95. |
| C28 (T10-8, -9, -11..-15, -19) | low | Text polish batch | voice/templates.py, bot.py, api/main.py | SMS verbs; KK SMS placeholder and 404 by `lang`; summary per Appendix В incl. "за 30 дней" + URL + all-green line; feedback question; KK word choices (flag for native review); bot UI wording; display cap ">95%". |
| C29 (T12-5, T11-10) | low | `send_hour` dead; no evening SMS; `/settings` shows nothing | voice/notify.py, bot.py, cli.py | `notify --hour H` filters subscriptions by `send_hour`; `/settings` shows current values with a time choice. |
| C30 (T12-10) | low | SMS has no degraded marker | voice/templates.py | Short degraded variant ≤70 chars. |
| C31 (T11-8, T11-12, T11-13) | low | Unanswered callbacks; asthma role privacy; InaccessibleMessage | voice/bot.py, templates.py | Every early return answers the callback; fallback handler; privacy note for the sensitive role; parent role gets child-oriented actions; `isinstance(cb.message, Message)` guard. |
| C32 (T05-9, T07-13 rest) | low | Contract gaps | api/main.py | Unknown scenario ids → 422; empty `settlement` → 422; `from > to` → 400; calendar `mode` param; past-date live 404 text fixed; `maps?month` honoured or rejected. |
| C33 (T06-6, T06-9, T06-10) | low | API hardening | api/main.py, cli.py, db.py | Rate-limit evicts empty keys, `Retry-After`, headers on 429; `fid` bounded (no 500); internal routes `include_in_schema=False`; `X-Frame-Options: DENY` + basic CSP; `cli apikey --list/--revoke`; `/v1/events` audited. |
| C34 (T06-7, T14-11, T15-7) | low | CA bundle size-only check, global `CURL_CA_BUNDLE` override; tests not hermetic | aralshield/__init__.py, tests/conftest.py | SHA-256 compare; only `GDAL_CURL_CA_BUNDLE` set, `CURL_CA_BUNDLE` via setdefault; conftest uses tmp_path_factory + cleanup and skips `.env` (`ARALSHIELD_SKIP_DOTENV=1`). |
| C35 (T14-3, T14-8 part) | low | `cli --help` crashes when piped; log never rotates; health always 200 | cli.py, api/main.py | UTF-8 reconfigure before argparse; RotatingFileHandler; `/v1/health?strict=1` → 503 when stale/degraded. |
| C36 (T15-3, T15-10, T15-8 C-part) | low | Full scans; LIKE dates; duplicated overlay code; unused import | db.py, api/main.py | Indexes `forecasts(mode, issue_date)` and `notifications(channel,status,forecast_id)`; `BETWEEN` for years; shared `_overlay_payload`; WAL set once. |
| C37 (T05-2 C-part) | low | SHP zip omits `.cpg` | api/main.py | Zip includes `cpg` when present. |

## Fixer D — frontend, docs, ops (`frontend/`, `README.md`, `scripts/`, `requirements.txt`, `.gitignore`, `.env.example`)

| ID | Sev | Title | Files | Done means |
|---|---|---|---|---|
| D1 (T14-1) | high | `scripts/setup.ps1` (quick-start step 1) fails to parse in Windows PowerShell 5.1 | scripts/*.ps1 | All `.ps1` saved UTF-8 with BOM (or ASCII-only). `[Parser]::ParseFile` gives 0 errors for each script under PS 5.1. |
| D2 (T08-1, T09-2) | high | Eye swipe clip not applied on load or after layer switch (demo 0:40) | components/SourcesTab.tsx | Clip re-applied on overlay add/load, on layer/url change and on resize; the slider position always matches the clip. |
| D3 (T09-1) | high | Stale forecast not flagged; "Ертең" shows today's date | components/RiskTab.tsx, api.ts, i18n.ts | Localised banner when `stale` (K5); horizons labelled by actual date; past horizons hidden or marked. |
| D4 (T13-1 D-part) | high | Replay shows global SHAP, not the event's drivers (demo 2:10) | components/ArchiveTab.tsx, api.ts | Renders `replay.series[st].drivers_h1` for the chosen station with `DRIVER_LABELS`-equivalent ru/kk labels. |
| D5 (T13-7 D-part) | high | No offline demo path in the panel | components/ReportsTab.tsx (or RiskTab), api.ts | "Демо: событие {replay.event_date}" toggle; preview calls `mode=backtest&issue_date=event_date−1&horizon=1` (K8), ru/kk + SMS. |
| D6 (T04-1, T04-3, T13-3 D-parts) | high | UI overstates validation: "Независимая проверка", OA 0.999 KPI, H3 ✓, Shield τ shown without ✗ | components/SourcesTab.tsx, ArchiveTab.tsx, ShieldTab.tsx | Label "Согласие с WorldCover (не независимая проверка)" + majority baseline. OA/κ KPI tiles hidden when `cv.informative === false`. H3 gate from the CI-based field with its CI. Shield gate shows ✗ when false. |
| D7 (T01-1/2/3/6/11, T02-6/7, T03-5, T04-9, T05-4/5/10, T13-6/9/10, W*) | high | README "Ограничения" incomplete | README.md | Lists: operational check limited to stations with genuine forecasts; h1/h2 = upper bound; H3 not confirmed (if so); H4 lag result; static 2025 map leakage; Eye weak labels + WorldCover agreement not independent; Shield τ gate fails; accuracy = backtest archive, live verification impossible due to NOAA ISD latency; 2025 partial year; Kazalinsk label quality; 2×2 km cells, no road access, no border mask; e-mail, admin, U-Net and monthly maps not implemented; reanalysis→forecast wind bias. Wording consistent with the regenerated reports (the lead re-checks numbers). |
| D8 (T08-2, T09-12c) | medium | Detail shows the previous settlement's message and drivers while loading | hooks.ts, components/RiskTab.tsx, ReportsTab.tsx | `useAsync` resets `data` on deps change (or `key` on Detail); loading state shown. |
| D9 (T09-3) | medium | Leaflet controls cover the swipe slider (mobile) and the sticky header | styles.css | Slider operable at 375 px (`elementFromPoint` hits the input); header above map controls. |
| D10 (T08-10, T09-5, T10-16, T08-9, T09-7, T10-17 D-part) | medium | Russian/English strings in Kazakh mode; raw gate keys | i18n.ts, components/* | All literals moved to `i18n.ts`; gate keys, reason codes (K2), H4 headers, Eye texts localised; backend `*_kk` fields used where provided; scenario label translated at render time; role names consistent with the bot. |
| D11 (T09-4) | medium | WCAG AA contrast failures | styles.css, common.tsx | Badges, accent and ✓ ≥4.5:1 (darker green/orange/accent per T09-4). |
| D12 (T09-6) | medium | Colour not duplicated by word and icon | RiskTab.tsx, ArchiveTab.tsx, common.tsx | One `LevelMark` (colour + icon + word) in tooltips, bars, calendar legend; calendar text summary. |
| D13 (T08-11, T09-8, T09-9) | medium | ARIA tabs incomplete; unlabeled inputs; no keyboard path for shield cells | App.tsx, components/* | tabpanel/aria-controls/arrow keys; localised aria-labels; labels for select/key input; descriptive alts; top-N table with checkboxes as keyboard alternative; skip-map link. |
| D14 (T09-10) | medium | Phone layout: 208 px sticky header, hidden tabs, 4 px calendar cells | styles.css, ArchiveTab.tsx | Compact header on ≤600 px; tabs fit or scroll visibly; calendar usable (month list or larger cells). |
| D15 (T08-3 D, T08-4, T08-5, T08-8) | medium | Shield map silently truncates; stale selection; layer rebuilt per click; no abort/debounce | api.ts, ShieldTab.tsx, hooks.ts | `limit 7000`; "показано N из M" from `total_matching` (K6); selection intersected with loaded ids; key without `selected.size`; AbortSignal + debounced slider. |
| D16 (T13-5 D, T05-7 D, T08-13 label, T09-12a) | medium | What-if shows one result; "1 000 га" vs "1 200 га" | ShieldTab.tsx, api.ts | Two plans side by side (e.g. nearest vs rating) with ×-ratio of people-weighted reduction; area shown as returned (K7). |
| D17 (T14-4) | medium | `daily.ps1` ignores Python exit codes; notify runs after a failed daily | scripts/daily.ps1 | `if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` after each call; transcript log. |
| D18 (T14-5) | low | Documented schtasks command breaks in PowerShell with spaces in path | README.md, scripts/daily.ps1 | `Register-ScheduledTask` snippet with `-WorkingDirectory` and `-StartWhenAvailable`, plus an evening notify run if C29 lands. |
| D19 (T08-6, T08-7, T08-13, T09-11, T09-12 b/d/e) | low | Frontend polish batch | components/*, api.ts | `protects` id → name (escaped); export try/catch, delayed revoke, busy state; types synced (`forecast_station`, `stale`, `degraded_reasons`, `total_matching`…); charts `tension:0`, % axis title, figure lightbox; SMS counter hidden for placeholder; health as status rows; inline export hint. |
| D20 (T14-9 D, T14-10) | low | Unpinned deps; build artefacts not ignored; env docs | requirements.txt, .gitignore, .env.example, README.md | certifi/pytest/httpx pinned; `*.tsbuildinfo` ignored; `.env.example` documents `ARALSHIELD_EYE_TAG`, `ARALSHIELD_HASH_PEPPER`, `ARALSHIELD_INTERNAL_KEY`; README says eye artefacts are rebuilt by `cli eye`. |

## Cross-fixer contracts
- **K1 Eye tag (A→B, C, D).**
  - A adds `EYE_TAG = os.environ.get("ARALSHIELD_EYE_TAG", "2025v2")` to `config.py`.
  - B uses it as the default in `train_and_map`, `shield.run`, `build_cells` and `ensure_sector_distance`.
  - C uses it in the CLI `--tag` default, in the `/v1/reports/eye` and `/v1/sources/*` defaults, and in the tag regex `^\d{4}(v\d+)?$`.
  - D passes no tag (it relies on the API defaults).
  - `sector_source.json` keeps `stations: {id: [16 floats]}` (no None, so `dataset.load_sector_source` still works). B adds `coverage: {id: [16 floats]}` and `tag`.
- **K2 Reduced confidence (A→C→D).** A writes `inputs_json.degraded_reasons: list[str]` with codes from {`no_weather`, `no_station_obs`, `model_missing`, `clim_fallback`, `const_fallback`, `deadline`}.
  - `degraded` (int) = 1 iff the list is non-empty.
  - Only `no_weather` and `model_missing` switch to the fallback probability; `no_station_obs` keeps M2.
  - C exposes `degraded_reasons` per horizon and in the explanation. D localises the codes in `i18n.ts`.
- **K3 Drivers schema (A→C→D).** Live and backtest `drivers_json` use the same list schema `{feature, contribution, label_ru, label_kk, direction}`, with NaN features and `station_code` excluded. C's `_fc_out` keeps `drivers` = up-features. The replay `drivers_h1` stays a list of `{feature, contribution}`.
- **K4 Forecast storage (C→A).**
  - C changes `db.save_forecasts(items, replace_mode: str | None = None)` to an upsert that preserves `id`. When `replace_mode` is set, rows of that mode are deleted in the same transaction.
  - A's `backtest_to_db` calls it with `replace_mode="backtest"`. Mode values stay `live` and `backtest`.
- **K5 Forecast freshness/identity fields (A→C→D).** Each item of `/v1/forecasts` gains:
  - `settlement` (the requested id/names) and `forecast_station`;
  - `issue_date`;
  - `issued_at`: ISO +05:00 from `inputs_json.issued_at`, else `created_at`;
  - `stale: bool` (issue_date < local_today) and `age_hours: float`;
  - per-horizon `past: bool` and `degraded_reasons`.

  `/v1/health` gains `stale: bool`, and `status` ∈ ok|stale|degraded. D consumes these and updates `api.ts`.
- **K6 Priorities.** B's `to_geojson(cells)` signature is unchanged and coordinates are rounded to 5 dp. C adds a top-level `total_matching: int` and caps `limit` at 7000. D requests `limit=7000` and shows N of `total_matching`.
- **K7 Scenarios.**
  - B provides `plan_cells(cells, plan, area_ha, d, seed=0) -> (ids, last_fraction)` and `scenario(..., last_fraction=1.0)`, which returns the exact `area_ha`.
  - Random plans return `{mean, p5, p95}` of `people_weighted_reduction`.
  - C's API exposes `plan ∈ {custom, rating, random, nearest}` and returns `area_ha`, `cells`, unique `cell_ids` and (for random) the distribution.
  - D renders two plans side by side.
- **K8 Demo event.** The source of truth is `/v1/reports/forecast` → `replay.event_date`. The demo uses issue_date = event_date − 1, horizon 1, mode `backtest`, and the station with the highest p among the `replay.series` stations with y=1. Never hard-code 2025-04-04, because A1/A6 relabelling may change the event. C's `/demo` and D's toggle use the same rule.
- **K9 Secrets/env.** C reads `ARALSHIELD_HASH_PEPPER` (if absent: a random per-install value stored outside git, with a log warning) and `ARALSHIELD_INTERNAL_KEY`. D documents both in `.env.example`.
- **K10 Metrics JSON keys consumed by D.**
  - forecast:
    - `gates.h{h}.H3_source_features_help`, `H3_delta_pr_auc_ci`;
    - `h1/h2.weather_inputs`;
    - `operational.excluded_stations`;
    - `H4.conclusion_ru/kk`.
  - eye: `external_worldcover.independent`, `note_ru/kk`, `baseline_majority`, `cv.informative`, `cv.meaning_ru/kk`.
  - shield: `gate_rank_stability_ge_0_7` (τ, unchanged), `top_overlap_mean`, `comparison_1000ha.area_ha`.

  D must tolerate both the old and new keys until the lead regenerates the artefacts.

## Invalid / wontfix

| Item | Verdict | Reason |
|---|---|---|
| T08-12 (16→8 sector mapping "asymmetric") | invalid | Mapping is a consistent clockwise rounding (NNE→NE, ENE→E, …, NNW→N); not asymmetric. At most a cosmetic ½-sector bias; D may switch to 16-point names during D10. |
| W1 T12-12/T15-1 (part), NFR-02/03/05 | wontfix | Async send queue at 30 msg/s, 100k-subscriber load tests and uptime SLAs are beyond a local hackathon build (C23 removes the N+1). |
| W2 NFR-06, T13-10 (part) | wontfix | TLS, panel login/roles and an Admin screen are out of scope for a local demo; documented in README (D7). |
| W3 24.1 tables / PostgreSQL+PostGIS | wontfix | SQLite + parquet is an accepted local substitute. |
| W4 T04-9, 12.5/12.6/12.7 | wontfix | U-Net, monthly composites, 10–20 m resolution, S1/DEM/GLCM, change map and the MODIS plume check would need new data and compute; documented (D7). |
| W5 T12-6 email digest (FR-14) | wontfix | No mail infrastructure; the channel is rejected instead (C21). |
| W6 J9/T13-6 live verification, J10–J12 schedulers | wontfix | NOAA ISD for 2026 returns 404 and 2025 ends at 08-24, so there is nothing to verify live; documented. |
| W7 T04-5 SCL/WorldCover resampling | wontfix | Requires re-downloading and rebuilding the composite; v2 is already built and verified against v1 (median diff ≤0.0014). |
| W8 T07-14 Pydantic response models / OpenAPI schemas | wontfix | Large refactor with no user impact; types are synced by hand (D19). |
| W9 T05-5 road access, T05-10 KZ border mask, T13-5 real forestry plan | wontfix | No road, border or plan layers in the open data used; documented, with "nearest" as the proxy (B13). |
| W10 T01-8 Optuna / leave-one-year-out CV | wontfix | Grid search is acceptable; per-horizon thresholds are covered by A18. |
| W11 T03-5 quantile mapping (code) | wontfix | Documentation only (A17) until a clean operational archive exists (A2/A25). |
| W12 T06-6 multi-worker/Redis rate limit | wontfix | Single uvicorn worker locally. |
| W13 T11-13 aiogram FSM persistent storage | wontfix | An in-memory TTL draft (C18) is enough for the demo. |
| W14 T14-8 team alerting channel | wontfix | No ops team; health strict mode (C35) is enough for a monitor. |
| W15 FR-17/FR-18 voice and mobile app | wontfix | C/W priority in the spec. |
| W16 T14-10 publishing `reports/qa/` and spec text | wontfix (lead decision) | Repository publication policy is the lead's call; D only ignores `*.tsbuildinfo`. |
