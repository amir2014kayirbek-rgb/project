# QA tester #13: requirements traceability (spec FR/NFR and module outputs vs implementation)

Scope: spec `reports/qa/_spec_report_text.txt` §11.2, 12.7, 13.2–13.9, 14.1–14.6, 15.1–15.6, 20.3–20.4, 22.2, 23.2, 24.1–24.2, 25.1–25.2, 26.1, 36.1, compared with `aralshield/**`, `frontend/src/**`, `tests/**`, `scripts/**`, `reports/*`. Running API checked by GET only (127.0.0.1:8000, 2026-10-27).
Status: **I** Implemented · **P** Partial · **M** Missing · **D** Deviates (works differently from spec). "Doc" says whether README "Ограничения" mentions it.

## 1. Coverage matrix

| Req | Status | Evidence / note | Doc |
|---|---|---|---|
| FR-01 daily issue by 06:00 | I | `scripts/daily.ps1` (schtasks 05:30) → `cli daily` + `notify`; `/v1/health` fresh=true | – |
| FR-02 P for 0–24/24–48/48–72 h + level | I | `predict.py:143-210`; `/v1/forecasts?settlement=aralsk` returns 3 horizons, `level`, `levels_by_group` | – |
| FR-03 explanation: wind, dryness, source sector | P/D | SHAP top-4 `predict.py:69-81`; live drivers today = `station_code`, `dust_lag_7d` (T13-2); source only if wind > 8 m/s | no |
| FR-04 Telegram subscription (lang, place, role, threshold) | I | `bot.py:112-169`; only 4 station points can be chosen, not villages | – |
| FR-05 KZ/RU templates by role | I | `templates.py:15-117`, 6 action groups; tests `test_core.py:128` | – |
| FR-06 Kazhydromet link in every message | P | missing in green messages and in the all-green `/forecast` list (T13-4) | no |
| FR-07 public accuracy 30/365 d | D | `/v1/accuracy` uses the last 30/365 days of the 2020–25 **backtest** archive; live forecasts are never verified (T13-6) | partly |
| FR-08 "Было пыльно?" feedback | I | `notify.py:99-114`, `bot.py:240`, `POST /v1/feedback`; `/v1/feedback/stats` empty | – |
| FR-09 panel: risk map, event archive | I | `RiskTab.tsx`, `ArchiveTab.tsx` (calendar, accuracy, replay) | – |
| FR-10 source maps by month | P | one season (2025), swipe RGB↔classes, no change map | yes |
| FR-11 priorities + what-if | P | `ShieldTab.tsx`, `POST /v1/scenarios`; no "current plan" variant, no side-by-side comparison (T13-5) | no |
| FR-12 GeoJSON/KML/SHP export | I | `priority.py:224-255`, `GET /v1/priorities/export` (needs key, audited) | – |
| FR-13 SMS via gateway | P | `notify.py:42-55` posts to a generic gateway or writes `data/outbox`; `send_hour` is ignored, so the evening SMS for herders (25.1) is not sent | no |
| FR-14 e-mail summary | M | only `/v1/summary` text; `channel="email"` falls through to the Telegram branch (`notify.py:80-90`) | no |
| FR-15 public API (C) | I | `/docs`, `/v1/*` | – |
| FR-16 villages without stations (C) | P | villages mapped to the nearest station (`/v1/settlements` `forecast_station`) | n/a |
| FR-17 voice (C) / FR-18 app (W) | M | expected for C/W | n/a |
| NFR-01 timeliness 99 % | P | scheduler only; no success tracking or alert on a missed run | no |
| NFR-02/03/04/05 uptime, 2 s, 15 min, 100k | M | nothing measured; notify sends one HTTP call at a time (`notify.py:66-93`) | no |
| NFR-06 encryption, panel roles | M | HTTP, plain SQLite; no panel login or roles; API keys only via CLI | no |
| NFR-07 minimal data | I | subscriber row: chat_id, lang, role, station; feedback hashed (`db.py:35-47`) | – |
| NFR-08 reproducibility | P | `model_version`, `inputs_json.fetched_at` saved, but no raw forecast input is archived (T13-8) | no |
| NFR-09 colour + word + icon | I | `common.tsx:11-19` LevelBadge, emoji in messages; WCAG contrast not audited | – |
| NFR-10 KZ = RU | P | `i18n.ts`; RU-only strings: H4 table headers (`ArchiveTab.tsx`), explanation `notes`, API errors | no |
| NFR-11 degraded mode | P | rule-B2 fallback works, but missing station data does not set `degraded` (T13-2) | partly |
| NFR-12 observability | P | `reports/pipeline.log`, `/v1/health`; no alerting | no |
| 11.2 M0 event catalogue | P | `truth/isd.py` WMO codes and visibility; no MODIS manual check (24.1 `проверено по MODIS`) | yes |
| 12.7 Eye: class map + per-pixel probability / uncertainty | I | `classify.py:133-150` (`source_prob`, uncertainty band) | – |
| 12.7 16 sectors × 300 km, distance-weighted | I | `classify.py:189-230`, `data/features/sector_source.json` | – |
| 12.7 change map, monthly cadence | M | single 2025 composite | yes |
| 12.5 RF vs U-Net | P | RF only (300/400 trees); weak labels → OA 0.999 is circular | yes |
| 12.6 block-CV 10 km, κ, F1, IoU, CI | I | `classify.py:102-125`; WorldCover vegetation recall **0.16** (`eye_metrics_2025.json`) | no |
| 12.6 MODIS plume check | M | – | yes |
| 13.2 feature groups | I/D | `features.py:17-28`; all groups present (CAMS only for B3); extra `station_code`; persistence also for h1/h2 (spec: h0 only) | no |
| 13.3 leakage rules | P/D | time split + 7-day gap OK (`train.py:38-50`); the 2025 source map is used for 2000–2025 rows (spec: past months only); no leave-one-year-out CV | no |
| 13.4 B0–B3, M1, M2, M2-abl; monotone; isotonic; Optuna | I/D | `train.py:52-150`; small grid search, not Optuna; B3 only h0, 2024–25 | – |
| 13.5 BS, BSS, PR-AUC, reliability, POD/FAR/CSI, block bootstrap | I | `metrics.py`, `forecast_report.md`, figures | – |
| 13.6 per-forecast SHAP, H3 ablation with CI | P | H3 gate uses point estimate (T13-3) | no |
| 13.7 H4 contingency, NW/W wind subset | I | `train.py:271-294`, panel `H4Block` | – |
| 13.8 per-group thresholds | I | `models/thresholds.json`, `choose_thresholds` | – |
| 13.9 fallbacks | I | `_fallback_prob` rule B2 → climatology | – |
| 14.1 attribution: upwind index / SHAP / back-trajectory | P | upwind-index sector only (`predict.py:84-97`); no trajectories; SHAP never shows source features | no |
| 14.2 priority index, 1×1 km | D | 2×2 km cells; one bay-average wind rose instead of per-cell P_ij (`priority.py:32-49,104-110`); M_i has no road access | no |
| 14.3 what-if "plan vs rating" | P | rating vs random-suitable proxy (T13-5) | no |
| 14.4 sensitivity, Kendall τ, ≥80 % robust | I | `priority.py:117-135`; τ_mean 0.459 (fails gate, T13-3) | no |
| 14.6 4 classes, cell table, export, annual report | P | classes and export OK; no annual report | – |
| 15.2 four levels + action | I | `templates.py`, `level_for` | – |
| 15.3 message schema incl. source | P | source line is dropped when `source_sector` is null (all backtest rows) | – |
| 15.5 yellow not sent to orange subscribers | I | `notify.py:69` min_level filter | – |
| 15.6 / 25.2 panel screens | P | Risk, Archive, Sources, Shield, Reports (print = PDF) exist; **Admin missing**; no click-history on risk map; no table export in Archive | no |
| 22.2 J1/J2 obs + labels daily | P/M | `daily` reads current-year ISD for persistence only (`predict.py:119-131`), never writes `dust_events` | no |
| 22.2 J3 forecast + own archive | P | fetched (`openmeteo.py:90-97`), **not archived** | no |
| 22.2 J4 CAMS, J5 features, J6 forecast, J7 publish | I/P | J7 has no input-quality report beyond `notes` | – |
| 22.2 J8 dispatch | P | Telegram + SMS outbox; no e-mail; no per-subscriber time | no |
| 22.2 J9 verify yesterday | M | no live verification (T13-6) | no |
| 22.2 J10 ERA5 append, J11 monthly Eye, J12 yearly layers | M | manual `cli all` only | J11/J12 yes |
| 23.2 forecast gates (PR-AUC>best B, CI, BSS>0, ECE≤.05, per-station, wind top-3) | I | `train.py:462-481`, all ✓ h0–h2; monotonicity is enforced but not tested | – |
| 23.2 Eye gates OA≥.8, F1≥.75, plume p<.05 | P | OA/F1 trivially met on weak labels; plume test missing | yes |
| 23.2 Shield gate τ≥0.7 | **fails** | `shield_metrics.json` `gate_rank_stability_ge_0_7: false` | no |
| 24.1 tables | P | present: settlements, stations, dust_events, forecasts (+drivers), subscribers, subscriptions, notifications, feedback, api_keys, audit_log; missing: observations (parquet), weather_forecasts, features, source_maps, sector_features, priority_cells (files); `model_versions` created but never written | no |
| 24.2 API | I/D | all 11 routes present (+calendar, summary, reports, preview); `/v1/priorities` open instead of key (README documents it); rate limit `main.py:55` | yes |
| 25.1 bot commands | I/P | /start /forecast /settings /accuracy /help /stop; no "Переслать родителям" button, no send-time setting, no demo command | no |
| 26.1 tests | P | 21 unit/API tests; none for model invariants, shield formula/scenario, bot dialogs, integration, load | – |
| 36.1 Eye slider + RF vs U-Net | P | swipe RGB↔classes; no U-Net | yes |
| 36.1 replay held-out storm (2025-04-04) | P | `forecast_metrics.json.replay`, ArchiveTab chart; aralsk h1 p=0.70, kyzylorda 0.83, kazalinsk 0.24 | – |
| 36.1 SHAP for the replayed event | M in UI | `drivers_h1` computed but never rendered (T13-1) | no |
| 36.1 bot demo via QR | P | live alerts only when level ≥ threshold (T13-7) | no |
| 36.1 "1000 ha plan vs rating" | P | rating vs random, 1200 ha (T13-5) | no |
| 36.4 honest limitations slide | P | README only; panel has no limitations view; gaps T13-3 missing | – |

## 2. Findings

### T13-1: Replay of the held-out storm has no event-level SHAP or source in the panel
Severity (proposal): High (demo moment 36.1 2:10)
Spec reference: 36.1 (1:20, 2:10), 13.6, 14.1
Evidence: `train.py:315-320` stores `replay.series.<st>.drivers_h1`, typed in `api.ts:79`, but `ArchiveTab.tsx:124-125,178-182` plots the **global** mean |SHAP| (`r.h1.shap_top`). `backtest_to_db` (`predict.py:214-236`) saves no `drivers_json` or `source_sector`, so `GET /v1/forecasts?settlement=aralsk&mode=backtest&issue_date=2025-04-03` returns `"drivers":[]`, `"source_sector":null` for the red 70 % day, and the preview message has no source line. The replay drivers are wind_max, gust_max, station_code, soil_moisture, dp_24h. No source or dry-day feature appears, so the scripted line "сухая корка в наветренном секторе" is not supported by the data.
Suggested fix: render `drivers_h1` for the chosen station under the replay chart. Compute SHAP and `_source` in `backtest_to_db`, at least for the replay window. Change the demo narrative to the actual drivers.

### T13-2: Live run without station data reports "not degraded" and cites the missing feature as a cause
Severity (proposal): Medium
Spec reference: FR-03, NFR-11, 26.2
Evidence: `/v1/forecasts/26306/explanation` → `notes: ["нет свежих наблюдений станции — признак персистентности пропущен"]`, `degraded:false`, yet `dust_lag_7d` ("пыльные дни за неделю") is listed as an **up** driver. The public `drivers` list is exactly `["station_code","dust_lag_7d"]`. `predict.py:160-161` records the note, but `row_degraded` (`:183`) ignores it, so `/v1/health` reports `"ok"`. `station_code` (2nd in global SHAP) is shown to users as "особенности пункта", which is not a physical reason.
Suggested fix: set `degraded=1` (reduced confidence) when persistence is missing, or retrain without persistence for this case. Leave NaN features and `station_code` out of user-facing drivers.

### T13-3: Two quality-gate results misrepresented or not documented (H3 and Shield τ)
Severity (proposal): Medium (honesty claim, 23.2 / 36.4)
Spec reference: 13.6 (H3 "с бутстреп-интервалами"), 23.2, 36.4
Evidence: `train.py:479` marks H3 true when `M2.pr_auc > M2-abl.pr_auc`. At h1 it shows ✓ for 0.582 vs 0.579 with overlapping CIs; at h0/h2 the ablated model is better. So H3 is **not** confirmed. `shield_metrics.json`: τ_mean 0.459 (min 0.185) < 0.7, so `gate_rank_stability_ge_0_7:false`. `ShieldTab.tsx:99-103` prints τ without a pass/fail mark. README "Ограничения" mentions neither.
Suggested fix: gate H3 on the CI of ΔPR-AUC (paired bootstrap). Show the Shield gate as ✗. Add both to README and the limitations slide; per 13.9, the Eye stays for attribution and planting.

### T13-4: FR-06 (Kazhydromet link in every message) not met for green messages
Severity (proposal): Medium (M-priority requirement)
Spec reference: FR-06, 15.5, 25.1
Evidence: `templates.py:157-158` returns a single line for green, and `test_core.py:145` enforces this. `bot.py:197-200` sends the 3-day list with no disclaimer when all horizons are green. GET `/v1/messages/preview?settlement=aralsk&horizon=1` → `🟢 Аральск, завтра (среда): … 2%.` with no link. `/v1/summary` has the disclaimer but no URL.
Suggested fix: append a short disclaimer line with the URL to green messages and to the `/forecast` list, and update the test.

### T13-5: The what-if cannot show "1000 ha by plan vs 1000 ha by rating"
Severity (proposal): Medium (demo moment 36.1 3:10, scenario 5 in 20.2)
Spec reference: 14.3, 20.2, FR-11, 36.1
Evidence: `ScenarioIn.plan ∈ {custom, rating, random}` (`main.py:377-397`). There is no forestry "current plan" layer, and "random suitable" stands in for it. `ShieldTab.tsx:23,39` keeps only the last result, so the two variants are never shown side by side. Cells are 2×2 km, so the request becomes 1200 ha. The headline reduction is 0.037 % (`shield_metrics.json` by_rating), which reads as negligible. The real message is the people-weighted ratio, 175.9 vs 5.3 for random, about 33×, and it is not shown.
Suggested fix: add `plan="current"` (sample polygons, e.g. near existing plantings or roads) and keep two results in a comparison table with the ×-ratio. Label the area as "≈1200 га (3 ячейки)".

### T13-6: "Accuracy for 30/365 days" is backtest data, and live forecasts are never verified (J2/J9)
Severity (proposal): Medium
Spec reference: FR-07, 15.5 ("за прошлый месяц"), 22.2 J2/J9
Evidence: `_verification` (`main.py:212-228`) does not filter on `mode` and anchors the window at `df.day.max()`. `/v1/accuracy?settlement=aralsk&period=30d` → 2025-07-26…2025-08-24 (test archive). `/v1/summary` prints "Точность оповещений оранжевого уровня: 5 из 10" without a period. `issue_daily` parses fresh ISD data only in memory (`_events_no_save`, `predict.py:134`), so live forecasts are never joined to outcomes. README mentions only that NOAA data arrives late.
Suggested fix: label the statistic "архив тестовых лет 2020–2025, период …" in API, bot and summary. Add a J2/J9 step to `daily` that writes yesterday's `dust_events` so live accuracy can build up.

### T13-7: The bot demo depends on the weather
Severity (proposal): Medium (demo 36.1 2:40, checklist 36.4 "работает офлайн")
Spec reference: 36.1, 36.4, 25.1
Evidence: `notify.run` (`notify.py:69-72`) sends only when a live level ≥ the subscriber threshold. Today all levels are green. The bot has no demo/replay command. The panel preview (`api.ts:152`) does not pass `mode`/`issue_date`, although the API supports them: `…preview?mode=backtest&issue_date=2025-04-03` returns the red teacher message. There is no "Переслать родителям" button (only a text line, `templates.py:174-176`).
Suggested fix: add a `/demo` bot command and a panel toggle that render the 2025-04-04 replay message (KZ/RU). Add an inline "Переслать" button (switch_inline_query) for teachers.

### T13-8: Own forecast archive (J3) and model registry not kept, so live forecasts cannot be reproduced
Severity (proposal): Low–Medium
Spec reference: 22.2 J3 ("стратегически важный актив"), 24.1 `weather_forecasts`, `model_versions`, NFR-08, 23.6
Evidence: `fetch_operational_forecast` (`openmeteo.py:90-97`) returns the data without saving it. The `model_versions` table is created (`db.py:48`) but no code writes to it. `forecasts.inputs_json` holds only `fetched_at`.
Suggested fix: write each fetched hourly frame to `data/raw/oper_forecast/<station>_<issue>.parquet` and insert a `model_versions` row at train time, with the hash of `forecast.joblib`.

### T13-9: Leakage rule 13.3 broken for source features
Severity (proposal): Low (H3 null, so impact on metrics is small)
Spec reference: 13.3 ("карты источников — только из прошлых месяцев")
Evidence: `dataset.py:193-198` applies the single Jun–Sep 2025 source map (`sector_source.json`) to every row from 2000 to 2025, test rows included. The replay storm (2025-04-04) also predates the composite. README calls the map "static" but does not flag the temporal leak.
Suggested fix: add a README line, or exclude source features from test-year rows that precede the composite.

### T13-10: Undocumented missing M/S items: Admin screen and roles, e-mail channel, send time
Severity (proposal): Low (hackathon), but should be listed
Spec reference: 25.2 "Администрирование", 24.3, NFR-06, FR-14, FR-13 / 25.1
Evidence: no admin tab (`App.tsx:70-75`); keys only via `cli apikey`. `SubscriptionIn` accepts `channel="email"` but `notify.py:80-90` sends any non-SMS channel through Telegram. `send_hour` is stored but never used.
Suggested fix: reject `email` until an e-mail sender exists, and add these items to README "Ограничения".

## 3. Improvement suggestions
1. Make the demo path self-contained: one "Демо: буря 2025-04-04" button in the panel that shows the replay chart, the event SHAP (`drivers_h1`), the source sector, and the KZ/RU and SMS messages. Mirror it with a `/demo` bot command, so 36.1 works offline and in calm weather.
2. Generate the README limitations and an in-panel "Ограничения" card from `forecast_metrics.json` / `shield_metrics.json` gate flags (H3, τ, Eye weak labels, WorldCover veg recall 0.16, static map), so failed gates are always shown.
3. Add the cheap invariant tests from 26.1: monotonicity in wind, rain does not raise P, Shield `scenario` reduces load only in the chosen cells, and green messages carry the Kazhydromet link.
