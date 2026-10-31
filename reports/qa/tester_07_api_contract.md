# Tester 07: API contract and functional correctness

Scope: `aralshield/api/main.py` and `aralshield/db.py` against spec 24.1/24.2/FR-07. Tested against the live server http://127.0.0.1:8000 on 2026-10-27/28, with the DB opened read-only (`file:data/aralshield.sqlite?mode=ro`).
DB state: backtest forecasts 2019-12-30..2025-12-31 and live forecasts only for issue date 2026-10-27; `dust_events` end on 2025-08-25.
These checks passed: the accuracy numbers match an independent recomputation (30d/365d, per station, h0/h2: n, events, Brier, AP, TP, POD, FAR are all identical). Validation returns 422 for a bad `mode`, `horizon`, `period`, date, `lang` or `send_hour`. Unknown ids return 404. 405 and 404 work for unknown routes. Each (station, horizon, day) has no duplicates within a mode, since `day - issue_date == horizon` holds for all 26,316 rows.

### T07-1: Path traversal in `/v1/reports/eye?tag=` returns any `.json` file on disk (open, no key)
- Severity (proposal): High
- Location: `aralshield/api/main.py:268-282` (same pattern in `:303-311` `/v1/sources/preview?tag=` and `:292-300` `/v1/sources/maps`)
- Evidence: `GET /v1/reports/eye?tag=/../../frontend/package` returned 200 with the contents of `frontend/package.json`. `tag=/../../frontend/tsconfig` returned `tsconfig.json`. On Windows, `reports\eye_metrics_\..\..\X.json` is normalised lexically, so the intermediate directory does not need to exist.
- Expected vs actual: the endpoint should only serve the metric reports. Actually it reads any JSON file reachable by a relative path.
- Suggested fix: validate `tag` with `^\d{4}$` (also for `month`/`tag` in the sources endpoints), or resolve the path and check `p.resolve().parent == REPORTS.resolve()`.

### T07-2: `/v1/summary` invents a source sector ("северо-восточная часть") when none exists
- Severity (proposal): High (misleading public text)
- Location: `aralshield/voice/templates.py:208` (`SECTOR8.get(top.get("source_sector") or "", "NE")`), called from `main.py:179-184`
- Evidence: all 12 live rows have `source_sector = NULL` (DB, and `/v1/forecasts` shows `"source_sector":null`). The summary still says: "Наибольший риск — сегодня (вторник) (Кызылорда), источник — северо-восточная часть высохшего дна."
- Expected vs actual: omit the source clause when it is unknown. Actually the text always states NE.
- Suggested fix: append the source phrase only when `source_sector` is set.

### T07-3: Live outputs go stale after midnight: "сегодня (вторник)" is shown on Wednesday and past days are still served
- Severity (proposal): Medium
- Location: `main.py:123-146` (latest issue date with no age check), `main.py:161-176`, `templates.summary_message` (`when_text(issue_date, day)`)
- Evidence: at 2026-10-28 04:23 UTC (09:23 local), `/v1/summary` still says "сегодня (вторник)". `/v1/messages/preview?settlement=aralsk&horizon=0` returns "Аральск, сегодня (вторник)…4%". `/v1/forecasts` returns `horizons[0].day = 2026-10-27`, which is yesterday. `/v1/health` correctly reports `forecast_fresh:false`, but the other endpoints carry no staleness flag.
- Expected vs actual: compute relative days against `local_today()` and drop past horizons, or add `stale: true` / `age_hours`. Actually yesterday is labelled "today".
- Suggested fix: compute `when_text` from `local_today()`, filter out `day < today`, and expose `stale` in `/v1/forecasts`.

### T07-4: `issued_at` is hard-coded to 06:00+05:00 and does not match the real issue time
- Severity (proposal): Medium
- Location: `main.py:141`
- Evidence: the live rows have `created_at = 2026-10-27T18:46:18+00:00` (23:46 local), but the API returns `"issued_at":"2026-10-27T06:00:00+05:00"`. Backtest issues get the same fabricated 06:00.
- Expected vs actual: per spec 24.2, `issued_at` is the actual issue timestamp. Actually it is a constant clock time.
- Suggested fix: derive `issued_at` from `created_at` (converted to +05:00) for live forecasts, or document it as a nominal issue time.

### T07-5: `/v1/forecasts?settlement=<village>` returns the station's identity, not the requested settlement
- Severity (proposal): Medium (contract)
- Location: `main.py:104-110,140`
- Evidence: `GET /v1/forecasts?settlement=baikonur` returns `"settlement":{"id":"zhosaly","name_ru":"Жосалы","lat":45.5,...}`. `/v1/accuracy?settlement=baikonur` returns zhosaly statistics with no station field.
- Expected vs actual: per spec 24.2, the `settlement` object should echo the requested settlement (`id`, `name_kk`, `name_ru`), with the proxy station given separately. Actually a client asking about Baikonur sees "Жосалы".
- Suggested fix: return the settlement object plus `"forecast_station": "zhosaly"`, and echo `settlement`/`station` in `/v1/accuracy`.

### T07-6: `/v1/accuracy?period=0d` silently returns the whole history
- Severity (proposal): Medium
- Location: `main.py:226` (`if days:`), `:240` (pattern `^\d{1,4}d$` allows 0)
- Evidence: `period=0d` returns `{"period":"0d","from":"2020-01-01","to":"2025-08-24","n":8082,...}`. My recomputation agrees, and it covers all 5.6 years.
- Expected vs actual: expected a 422 for 0 days. Actually the response is all-time stats labelled "0d".
- Suggested fix: change the pattern to `^[1-9]\d{0,3}d$`, or use `if days is not None`.

### T07-7: The `/v1/accuracy` window ends at the last verified day, not today, so "30d" means Jul–Aug 2025
- Severity (proposal): Medium (FR-07 transparency)
- Location: `main.py:225` (`end_ts = df.day.max()`), `:183` (`_orange_hits(30)` for the summary)
- Evidence: on 2026-10-28, `GET /v1/accuracy` returns `"period":"30d","from":"2025-07-26","to":"2025-08-24"`, which is 14 months old. The summary line "Точность оповещений оранжевого уровня: 5 из 10" gives no date. `source` says the forecasts are a mix of backtest and live, but every verified row is `mode='backtest'`.
- Expected vs actual: FR-07 asks for public accuracy for the last 30 and 365 days. The response should either use a today-anchored window (empty, with a note) or state clearly that the figures are archive or backtest results. Actually stale backtest figures are presented as current.
- Suggested fix: add `as_of`/`mode` fields, mention the dates in the summary text, and allow an optional `end` or `mode` parameter.

### T07-8: Accuracy can mix live and backtest rows for the same day and drops duplicates arbitrarily (latent)
- Severity (proposal): Medium (latent; no overlapping days in the current DB)
- Location: `main.py:212-224`
- Evidence: the SQL has no `mode` filter and no `ORDER BY`. `drop_duplicates(["station","day"], keep="last")` after `sort_values("day")` keeps whichever row comes last in SQL order. A pandas check with the same calls kept `backtest` for row order [live, backtest] and `live` for [backtest, live]. Once observations for live days are loaded, the public score would depend on this row order.
- Suggested fix: filter on `mode` (a parameter, default `live`, falling back to `backtest`), or prefer `live` deterministically with `ORDER BY mode='live'`, and report the mode.

### T07-9: Accuracy hides false alarms when the window has no events
- Severity (proposal): Low/Medium
- Location: `main.py:246`
- Evidence: `GET /v1/accuracy?settlement=zhosaly&period=11d` returns `{"period":"11d","n":10,"note":"недостаточно проверенных прогнозов..."}`. Independently, that window has 10 verified days, 1 orange alert and 0 events, so FAR = 1.0 is hidden. The response also drops `from`/`to`/`horizon`, so its schema changes. Latent: `pr_auc` is NaN when every day in the window is dusty, and `round(nan)` is not caught the way POD/FAR are, so Starlette `allow_nan=False` would return a 500 (code reading, not reproduced).
- Suggested fix: always return counts (`orange_alerts`, `orange_confirmed`, `FAR`) and set `pr_auc`/`brier` to null when undefined.

### T07-10: `set_subscription` deletes the subscriber's other stations and a re-POST resets unspecified fields
- Severity (proposal): Medium
- Location: `db.py:149-152`, `main.py:408-424`
- Evidence: I posted `{"chat_id":"qa07-a","station":"aralsk","min_level":"yellow","send_hour":7}` and then `{"chat_id":"qa07-a","station":"kazalinsk"}`. The DB then held one row: `kazalinsk, orange, 6, lang kk`. The aralsk subscription was gone and min_level/send_hour were reset to the defaults. `station:"baikonur"` gives a 404 because only station ids are accepted, although spec 24.1 says subscriptions are per settlement and `/v1/forecasts` accepts settlements.
- Expected vs actual: per spec, "создание и изменение подписки": the subscriber–settlement table supports multiple places, and a change should not wipe other fields.
- Suggested fix: use `INSERT ... ON CONFLICT(subscriber_id, station) DO UPDATE`, add a separate DELETE per station, accept settlement ids via `_station_for`, and use PATCH semantics for optional fields.

### T07-11: Feedback is not deleted on unsubscribe, accepts future dates, and its "anonymous" hash is reversible
- Severity (proposal): Medium (privacy / data quality)
- Location: `db.py:155-176`, `main.py:432-451`
- Evidence: `POST /v1/feedback {"chat_id":"qa07-fb","station":"aralsk","date":"2999-01-01","answer":"yes"}` returned 200, and the public `/v1/feedback/stats` now shows `[{"station":"aralsk","date":"2999-01-01","answer":"yes","n":1}]`. `DELETE /v1/subscriptions?channel=telegram&chat_id=qa07-fb` returned `{"deleted":false}` and the row stays. The stored hash `99a99f7f21b6b558` equals `sha256("telegram:qa07-fb")[:16]`, so it can be recomputed from any known chat_id. Feedback from non-subscribers is also accepted.
- Suggested fix: require `date <= today` (and within N days), delete feedback by hash in `delete_subscriber`, and use a keyed HMAC with a server secret.
- **Leftover test data:** feedback row `id=1` (hash `99a99f7f21b6b558`, date 2999-01-01) could not be removed through the API, and I was not allowed to write to the DB. Please delete it manually. The qa07-a subscriber was deleted through the API.

### T07-12: `latest_forecasts()` uses one global MAX(issue_date), so a station that misses a run disappears from the summary (latent)
- Severity (proposal): Low/Medium
- Location: `db.py:129-135` (used by `/v1/summary`); compare the per-station max in `main.py:134-136`
- Evidence (code): `issue_date=(SELECT MAX(issue_date) FROM forecasts WHERE mode=?)` has no station correlation. If one station's daily run fails, the summary silently omits that town, while `/v1/forecasts` still shows its older forecast. In the current DB all stations share 2026-10-27, so this is not reproducible now.
- Suggested fix: correlate the subquery on station (`f2.station=f.station`) and flag stations whose issue date is older than today.

### T07-13: Contract gaps compared with spec 24.2
- Severity (proposal): Low
- Location: `main.py:113-119,123-146,344-404`
- Evidence:
  - `drivers` lists every "up" SHAP feature, even on green days, including the meaningless `station_code` (aralsk h1: `["station_code","dust_lag_7d"]`). Backtest forecasts have `drivers:[]` and an explanation with `inputs:{}`.
  - The last backtest issue (2025-12-31) returns a "3-day forecast" with one horizon.
  - `settlement=` (empty) returns a list instead of an object.
  - `/v1/forecasts?issue_date=2024-05-01` (live) returns 404 "запустите cli daily", which is misleading for a past date.
  - `/v1/sources/maps?month=2026-09` ignores the month (`split("-")[0]`).
  - `/v1/priorities` and `POST /v1/scenarios` have no auth, although the spec says "по ключу" and "панель".
  - `POST /v1/scenarios {"cell_ids":[999999999]}` returns 200 with `cells:0` instead of a 4xx. Duplicate ids are deduplicated in `cells`, but the `cell_ids` echo is not.
  - `/v1/events` with `from > to` returns an empty 200 rather than a 400 (code).
  - `/v1/events/calendar` uses `mode='backtest'` only, so year=2026 returns `forecasts_h1: []` even though live forecasts exist.
- Suggested fix: limit `drivers` to the top 3 "up" features for yellow-and-above and exclude `station_code`, return a 400 or 422 for empty or unknown inputs, and add API keys to the spec'd "по ключу" methods.

### T07-14: The OpenAPI document has no response schemas, error codes or security scheme
- Severity (proposal): Low
- Location: all routes in `main.py` (no `response_model`), `:314` `/layers/{name}`
- Evidence: in `/openapi.json`, every 200 response is `{"application/json":{"schema":{}}}`. `/layers/{name}` (PNG) is declared as JSON. 401/403/404 are not documented, `components.securitySchemes` is absent (X-API-Key is a plain header parameter), and `/docs` loads Swagger UI from cdn.jsdelivr.net, so it breaks offline.
- Suggested fix: add Pydantic response models (ForecastOut, AccuracyOut), `responses={404:...}`, and `APIKeyHeader` security.

### T07-15: `/v1/health` marks a forecast stale at local midnight and reports it as "degraded"
- Severity (proposal): Low
- Location: `main.py:455-467`
- Evidence: the forecast issued at 23:46 local on 10-27 had `status:"ok"`. At 09:23 local on 10-28 the response is `{"status":"degraded","forecast_fresh":false,"degraded_forecasts":0}`. Freshness is a calendar-date equality check, so every day between 00:00 and the morning run reports "degraded", and staleness is merged with model degradation.
- Suggested fix: use an age threshold (`now - created_at < 30h`) and give staleness its own status value.

## Improvement suggestions
1. Add typed Pydantic response models that match spec 24.2 (settlement echo, `forecast_station`, `issued_at`, `stale`, `mode`, `as_of`), and add contract tests in `tests/` that call the endpoints through `TestClient`.
2. Validate every path-forming or user-supplied parameter (`tag`, `month`, `period`, feedback `date`, scenario `cell_ids`) with strict patterns or ranges.
3. Make accuracy deterministic and transparent: an explicit `mode` (live/backtest), an `as_of` date, counts that are always present, and the same numbers reused in the summary text.
