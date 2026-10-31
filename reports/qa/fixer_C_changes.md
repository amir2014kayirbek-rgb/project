# Fixer C: backend, bot and delivery changelog

Scope: `aralshield/api/` (main.py, plus new verify.py and demo.py), `aralshield/db.py`, `aralshield/voice/` (templates.py, bot.py, notify.py, plus new lang.py), `aralshield/cli.py`, `aralshield/__init__.py`, `tests/conftest.py`, `tests/test_api.py`, `tests/test_core.py` (one test), and the new tests `tests/test_db_store.py`, `tests/test_voice_{notify,templates,bot}.py`.

No pipeline command was run, and `data/aralshield.sqlite` was not modified. The smoke test used a copy of the DB in the scratchpad.

**Verification:** `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest -q tests` gives **139 passed** (the whole suite, including A and B tests), run 2026-10-31.

**Smoke test on a copy of the real DB:**
- `init()` migrates `notifications`, adding the station, issue_date and level columns.
- `/v1/health` returns `stale`, because the live issue is from 10-27.
- `/v1/summary` returns "Актуальный прогноз ещё не выпущен" and an accuracy line labelled "архив тестовых лет 2020–2025, 26 июля 2025 — 24 августа 2025".
- `/v1/demo` returns kyzylorda, 2025-04-03 → 04-04, with `available: true`.
- `/v1/reports/eye` falls back to `eye_metrics_2025.json` until `2025v2` exists.

Line numbers below refer to the current files.

## High

| ID | Change (file:line) | Verification |
|---|---|---|
| C1 path traversal | `tag` is validated as `^\d{4}(v\d+)?$` and `month` as `^\d{4}(-(0[1-9]\|1[0-2]))?$`, using FastAPI `Query(pattern=…)` (api/main.py:40-41, 424, 450, 465). `_safe_file` resolves the path and requires `parent == base` (main.py:386). Error texts no longer echo paths (main.py:397-401, 429). The default tag is `config.EYE_TAG`, with a fallback to the newest existing valid tag (`_tags`, main.py:404). | `test_api.py::test_path_traversal_rejected` (4 variants, including backslashes, → 422). `test_maps_month_validated_and_404_without_path`. |
| C2 / K4 re-issue identity | `db.save_forecasts(items, replace_mode=None)` now does `INSERT … ON CONFLICT(station,issue_date,horizon,mode) DO UPDATE`, so the id is preserved. `replace_mode` deletes the rows of that mode that are missing from the new set, in the same transaction (db.py:161). Notify dedups on (subscriber, channel, station, issue_date) using only `status='sent'`, and resends only when the level rose (notify.py:139-160). | `test_voice_notify.py::test_reissue_keeps_id_and_does_not_resend` (save twice + notify twice → 1 message; a red re-issue → 1 more). `test_db_store.py::test_save_forecasts_upsert_preserves_id_and_replace_mode`. |
| C3 dry-run / failures | Dry-run, no token, or no SMS gateway → `status='dry_run'`, which is excluded from dedup. The SMS is written to the outbox in dry-run too, with the phone masked as `+7700***4567`. `failed` rows are retried on the next run. SMS below orange is counted as `skipped`. An upsert never overwrites a `sent` row (notify.py:103-202). | `test_dry_run_does_not_block_real_send`, `test_failed_send_is_retried`, `test_sms_below_orange_is_skipped_not_failed`. |
| C4 stale forecasts | `db.local_today()` (UTC+5) and a per-station `latest_forecasts` (db.py:83, 191). `templates.when_text(day, lang, today)` is relative to today, with a fallback to the absolute date "29 октября (четверг)" / "29 қазан (бейсенбі)" (templates.py:208, lang.py). Notify skips stale stations (with a log warning) and days before today. Bot `/forecast` shows "⚠️ Это прогноз от …" and hides past days (bot.py:278). The API gains `issue_date`, `issued_at`, `stale`, `age_hours` and per-horizon `past`. `/v1/health` uses a 30 h threshold and `status` ok\|stale\|degraded (main.py:233-250, 589). The summary marks old issues and drops past days. | `test_stale_issue_is_not_sent`, `test_forecast_view_marks_stale_and_hides_past_days`, `test_day_words_are_relative_to_today_not_issue`, `test_stale_forecast_flags`, `test_health_stale_and_strict`, `test_latest_forecasts_is_per_station`. |
| C5 invented NE source | The source clause is added only when `source_sector` is known, in both the message and the summary (templates.py:278-281, 352-355). | `test_summary_without_source_and_all_green`. |
| C6 offline demo | New `api/demo.py` implements the K8 rule: event_date comes from replay, issue = event−1, h1, backtest, and the station is the one with the highest p among y=1. Bot `/demo` sends "ДЕМО: архивное событие 4 апреля 2025", the message in the user's language and role, the SMS, and the parents text for teachers (bot.py:328). New `GET /v1/demo`. | `test_demo_view_uses_replay_event_offline`, `test_demo_event_rule_picks_dusty_station_with_highest_p`. |

## Medium

| ID | Change | Verification |
|---|---|---|
| C7 length / parents | Shorter disclaimer ("Инфопрогноз, не официальное предупреждение: kazhydromet.kz"), distance dropped, compact wind line ("После 7 сухих дней — северо-восточный ветер до 15 м/с"), short degraded line, and KK wind direction in the ablative. The parents text is a separate message with a `t.me/share/url` "Переслать родителям" button, in both notify and bot `/forecast` (templates.py:289-298). | `test_all_messages_fit_limits`: 9 roles × 4 levels × public level × 5 day offsets (including absolute dates) × 2 languages, all ≤400; SMS ≤70 in the normal and degraded variants. `test_teacher_gets_separate_parents_message_with_share_button`. |
| C8 FR-06 link | Green messages, the `/forecast` list and the summary all end with the disclaimer and URL. | `test_core.py::test_green_message_is_short` updated (2 lines, the second has the link). Template tests. |
| C9 accuracy | New `api/verify.py`: mode/date filters run in SQL (`BETWEEN`) with a deterministic order. Modes are `auto\|live\|backtest`: live windows end today, backtest windows end at the last verified day. Counts are always present; `pr_auc` and `brier` are null when undefined. `period ≥ 1d`. Texts say "Архив тестовых лет 2020–2025, {from} — {to}". The bot uses 30 days in plain words, with no POD/FAR, and `log.exception`. | `test_accuracy_backtest_is_labelled_and_deterministic`, `test_contract_validation` (0d → 422). |
| C10 settlement echo | `settlement` echoes the requested settlement and `forecast_station` is added. The preview header reads "Саксаульский (по станции Аральск)" / "… (Арал станциясы бойынша)". | `test_settlement_maps_to_station`, `test_preview_header_and_kk_placeholder`. |
| C11 subscriptions | `set_subscription` is an upsert per (subscriber, station); omitted fields are kept, and `exclusive=True` is used by the bot (single place). `upsert_subscriber` keeps lang/role when they are None. The API accepts settlement ids and `DELETE …&station=` (db.py:205-234, main.py:SubscriptionIn). | `test_set_subscription_is_upsert_per_station`, `test_contract_validation` (baikonur → zhosaly). |
| C12 privacy | Feedback hash is HMAC-SHA256 with `ARALSHIELD_HASH_PEPPER`. Without it, a random secret is stored in `data/aralshield.sqlite.pepper`, which is git-ignored by the existing `aralshield.sqlite*` rule, and a warning is logged. `/stop` deletes notifications, feedback requests and feedback (new and legacy hashes). The bot replies `no_sub` when nothing was deleted. Outbox phones are masked. | `test_stop_deletes_feedback_and_notifications`, `test_subscriber_hash_is_keyed`, dry-run test. |
| C13 feedback validation | The date must be ISO, within today−7…today, and match a `sent` notification. Otherwise the bot gives a silent `cb.answer()` and the API returns 422. | `test_feedback_requires_notification_and_recent_date`, `test_feedback_validation`. |
| C14 ask once | New table `feedback_requests`, marked only after a successful send. Uses `local_today()`. | `test_feedback_asked_once` (3 runs → 1, 0, 0). |
| C15 token in logs | `_tg_send` and `_sms_send` log only the exception type and HTTP status. `TokenMaskFilter` masks `bot\d+:[\w-]+` and is attached to the CLI handlers. | `test_token_is_not_logged`. |
| C16 internal access | `TrustedHostMiddleware` (localhost, 127.0.0.1, ::1, testserver plus `ARALSHIELD_ALLOWED_HOSTS`). The key is compared with `hmac.compare_digest`; when a key is configured, localhost no longer bypasses it. `cli serve --host <non-loopback>` refuses to start without `ARALSHIELD_INTERNAL_KEY`. | `test_security_headers_hidden_internal_routes_and_host` (foreign Host → 400), `test_internal_methods_reject_outsiders`. |
| C17 priorities DoS | `limit ≤ 7000`. The serialised bytes are cached per (mtime, classes, min_suit, limit) with `lru_cache(16)`. Adds `total_matching` and `returned`. | `test_contract_validation` (7001 → 422). |
| C18 stale wizard | The draft stores the wizard `message_id` and a 30-minute TTL, with eviction above 10k drafts. Each step requires the previous ones (`on_level` needs lang, station and role). Buttons from other messages get "Это меню устарело, нажмите /start". | `test_stale_wizard_button_does_not_commit_default_role`. |
| C19 private chats | `router.message.filter(F.chat.type == "private")` and the matching callback filter (bot.py:35-36), documented in the docstring. | code review |
| C20 blocking bot | All DB and pandas work goes through `asyncio.to_thread`. Accuracy comes from `api.verify`, so `api.main` and FastAPI are not imported. | `test_voice_bot.py` (pure view functions) |
| C21 email | `channel` in subscriptions and feedback is `Literal["telegram","sms"]`, so `email` → 422. Notify skips unknown channels with one log line per channel. | `test_contract_validation`, `test_unknown_channel_is_skipped`. |
| C22 low-p health wording | If the group level is ≥ orange while the public level is < orange (health/school groups): "пылевой день возможен, повышенный риск для вас / для детей / для пациентов" (KK "мүмкін, сізге / балаларға / науқастарға қауіпті") (templates.py:242). | `test_health_group_wording_below_public_orange`. |
| C23 notify N+1 | One connection and one transaction per run: one query for subscribers, one for the latest issue, one for dedup, one `executemany`, and one outbox handle per file. `synchronous=NORMAL`. | `test_notify_5000_subscribers_dry_run_is_fast` (5k dry-run, about 1 s here; limit 15 s). |
| C24 KK/RU numerals | `voice/lang.py`: `kk_abl`, `kk_poss`, `plural_ru`, `date_text`. Used in the summary, accuracy texts and dry days. | `test_kk_ablative` (1, 4, 5, 6, 7, 8, 10, 20, 21, 40, 100), `test_kk_possessive_and_ru_plural`. |
| C25 wording | «усильте»; actions start with a verb ("держите лекарства при себе, если у вас астма"). The red public action is now concrete ("наденьте маску при выходе"). Red items that repeated the disclaimer were removed. KK help changed to "Қазгидромет ескертулерін қадағалаңыз". Also applied: T10-13 KK choices (өткізбеңіз, жүгінуі, ғимарат ішінде, өскіндер) and T10-15 bot UI ("Қызғылт сары және одан жоғары"). | `test_texts_corrections` |
| C26 degraded_reasons | Per horizon and in the explanation (K2). Read from `inputs_json.degraded_reasons`. For old rows without codes, they are reconstructed from `notes` (no_weather / no_station_obs / model_missing). | `test_forecast_freshness_fields_and_settlement_echo` |
| C27 what-if | `plan ∈ {custom, rating, random, nearest}` via B's `P.plan_cells` + `P.scenario(..., last_fraction)`, so the area is exact. Random adds `distribution` from `P.random_plan_distribution` (mean/p5/p95, 50 draws). Plan results are cached (`_plan_result`). Custom ids are de-duplicated; unknown ids → 422. Per Fixer B's notes, `unknown_ids` is checked before the call and removed from the response. | `test_scenarios_plans_and_unknown_ids` (synthetic 30-cell rating; area exactly 1000 ha for each plan). |

## Low

| ID | Change | Verification |
|---|---|---|
| C28 text polish | SMS lines start with a verb ("Уведите скот в укрытие"). KK SMS placeholder and preview 404 follow `lang`. The summary follows Appendix В ("Наибольший риск — Аральск, завтра (вторник), 71%.", "за 30 дней", URL, all-green line "Пылевых дней не ожидается."). Feedback question per T10-12. Probabilities above 95% are shown as ">95%" / "95%-дан жоғары". | template tests, `test_preview_header_and_kk_placeholder` |
| C29 send_hour | `notify --hour H` filters `send_hour = H`; when H ≥ 12, only days from tomorrow are sent (evening SMS). The bot wizard has a time step (06:00 / 07:00 / 19:00). `/settings` first shows the current values. | `test_send_hour_filter_and_evening_skips_today` |
| C30 SMS degraded | Degraded variant: "… (неточно). …" / "… (шамамен). …", within 70 characters. | `test_all_messages_fit_limits`, `test_texts_corrections` |
| C31 callbacks | Every early return answers the callback. A fallback `@router.callback_query()` was added. The `isinstance(cb.message, Message)` guard is in place. Role prompt has a privacy note, and `asthma` is renamed "Чувствительная группа (ранние оповещения)". Parent role gets child-oriented actions. | wizard test, template tests |
| C32 contract gaps | Unknown scenario ids → 422. Empty `settlement` → 422. `from > to` → 400. `/v1/events/calendar?mode=` (default `backtest`). A past `issue_date` with no data returns 404 "прогноза за эту дату выпуска нет". `maps?month=YYYY-MM` is honoured if a monthly overlay exists, otherwise 404 (it is no longer replaced by the year). | `test_contract_validation`, maps test |
| C33 hardening | Rate limiter: empty/old keys are swept every 60 s; 429 responses carry `Retry-After` and the security headers. `fid` is bounded to 1…2^63−1 (422 instead of 500). Internal routes are hidden from OpenAPI. Added `X-Frame-Options: DENY` and CSP `frame-ancestors 'none'; object-src 'none'; base-uri 'self'`. Added `cli apikey --list/--revoke` (by owner or 12-hex id). `/v1/events` access goes to `audit_log`. | `test_rate_limit_has_retry_after`, `test_security_headers…`, `test_api_key_list_and_revoke`, `test_contract_validation` |
| C34 CA bundle and hermetic tests | SHA-256 compare plus atomic replace. Prefers `%LOCALAPPDATA%`. Only `GDAL_CURL_CA_BUNDLE` is set; `CURL_CA_BUNDLE` uses `setdefault`. The local username was removed from the docstring. `ARALSHIELD_SKIP_DOTENV=1` is honoured. conftest sets the env, removes tokens, and deletes the temp dir in `pytest_sessionfinish`. | whole suite |
| C35 ops | UTF-8 reconfigure happens before argparse (`.venv/Scripts/python.exe -m aralshield.cli --help \| head` now works). `RotatingFileHandler` (5 MB × 3). `/v1/health?strict=1` → 503 unless the status is ok. | manual pipe check, `test_health_stale_and_strict` |
| C36 DB efficiency | Indexes `ix_fc_mode_issue`, `ix_fc_mode_station_issue`, `ix_notif_status`, `ix_notif_dedup`. Calendar and accuracy use `BETWEEN`. Shared `_overlay_payload`. WAL is set once in `init()`. | `test_indexes_exist` |
| C37 .cpg | The shapefile zip now includes `priority_top.cpg` (main.py `priorities_export`). | code review (B writes the `.cpg`) |

## API changes for frontend (Fixer D, `api.ts`)

All changes add fields; existing fields keep their names and types unless noted below.

### `GET /v1/forecasts` (list and `?settlement=`)
Each item gains:
- `settlement` is now **the requested settlement**: `{id, name_kk, name_ru, lat, lon}`. In list mode it is the station's own settlement, so `id` = station id. With `?settlement=baikonur`, `settlement.id` is `"baikonur"`.
- `forecast_station: string`
- `mode: "live" | "backtest"`
- `issue_date: string` (YYYY-MM-DD)
- `issued_at: string`: ISO with `+05:00`, from `inputs_json.issued_at`, else `created_at`; nominal 06:00 for backtest.
- `stale: boolean`, true when `issue_date < today` (Kyzylorda time).
- `age_hours: number`
- `disclaimer_kk: string`

Each `horizons[]` item gains:
- `past: boolean`, true when `day < today`.
- `degraded_reasons: string[]`, codes from {`no_weather`, `no_station_obs`, `model_missing`, `clim_fallback`, `const_fallback`, `deadline`}.

`drivers` never contains `station_code` or NaN features.

Errors:
- `?settlement=` (empty) → 422.
- A past `issue_date` with no data → 404 "прогноза за эту дату выпуска нет".

### `GET /v1/forecasts/{id}/explanation`
Adds `degraded_reasons`, `mode`, `issue_date` and `issued_at`. `drivers` excludes `station_code`. An id outside 1…2^63−1 → 422.

### `GET /v1/messages/preview` (text)
- Settlements without their own station get the header "Байконур (по станции Жосалы)" / "Байқоңыр (Жосалы станциясы бойынша)".
- The level is the role group's level (`role` → ROLE_GROUP).
- For `role=teacher` at orange or red, the parents text is appended after a blank line.
- The SMS placeholder and the 404 detail follow `lang`.
- Day words are relative to today for live data and to `issue_date` for `mode=backtest`, so the demo shows "завтра".

### `GET /v1/demo` (new, K8)
Returns `{event_date, issue_date, station, horizon: 1, mode: "backtest", replay_p, available: boolean, forecast_id: number|null, settlement: {...}, label_ru, label_kk}`, or 404 when `forecast_metrics.json` has no replay. D can use it, or the same rule client-side, for the demo toggle. The preview call is `preview?settlement={station}&mode=backtest&issue_date={issue_date}&horizon=1`.

### `GET /v1/accuracy`
- New parameter `mode = auto | live | backtest` (default `auto`). `period` must be ≥ `1d`.
- The response always contains: `period, mode, as_of, horizon, station, settlement, from, to, n, events, orange_alerts, orange_confirmed, events_warned, POD, FAR, brier, pr_auc, text_ru, text_kk, source`.
- `from`, `to`, `POD`, `FAR`, `brier` and `pr_auc` can be `null`. `note` is optional.
- The texts name the archive period when `mode == "backtest"`. D should label the KPI "архив тестовых лет" when `mode === "backtest"`.

### `GET /v1/events/calendar`
New parameter `mode` (default `backtest`). The response adds `mode`.

### `GET /v1/health`
- `status` is `"ok" | "stale" | "degraded"`.
- New fields: `stale: boolean`, `last_issued_at`, `age_hours`, `stale_threshold_hours: 30`, `stale_stations: string[]`, `issued_today: boolean`.
- `forecast_fresh` is kept and equals `!stale`.
- `?strict=1` → HTTP 503 when the status is not `ok`.

### `GET /v1/priorities`
- `limit` max is **7000** (default 2000).
- The FeatureCollection adds top-level `total_matching: number` and `returned: number` (K6).
- Feature properties are unchanged (B's `to_geojson`).

### `POST /v1/scenarios`
- `plan` now includes `"nearest"`.
- The response is B's `scenario()` plus:
  - `cell_ids` (unique, in planting order)
  - `plan`
  - `requested_area_ha` (null for custom)
  - for `random`: `distribution: {mean, p5, p95, n_draws, pool, pool_cells}`
- `area_ha` is the exact area (last cell partially planted).
- Custom with unknown ids → 422; custom with no ids → 400.

### `GET /v1/reports/eye`, `/v1/sources/preview`, `/v1/sources/maps`
- `tag` (or `month`) is optional. The default is `ARALSHIELD_EYE_TAG` (2025v2), falling back to the newest existing tag (currently 2025).
- The sources payload adds `tag`.
- An invalid tag or month → 422.

### Internal routes
`/v1/subscriptions` and `/v1/feedback` are hidden from OpenAPI.
- `SubscriptionIn.channel` accepts only `telegram|sms`. `station` accepts a settlement id. lang/role/min_level/send_hour are optional (null keeps the stored value). The response is `{ok, station}`.
- `DELETE …&station=` removes one place.
- Feedback needs a date within the last 7 days and a matching sent notification, otherwise 422.

### All responses
`X-Frame-Options: DENY` and a CSP (`frame-ancestors 'none'; object-src 'none'; base-uri 'self'`); scripts and images are not restricted. Requests with a foreign `Host` → 400. To serve the panel on a LAN IP or hostname, set `ARALSHIELD_ALLOWED_HOSTS` (D: please document it in `.env.example`).

## Other interface changes (lead and other fixers)
- `db.save_forecasts(items, replace_mode=None)`: K4 is implemented, so A's `inspect.signature` check now takes the atomic path.
- `db.latest_forecasts()` returns the latest issue per station.
- `db.local_today()` is new.
- `db.set_subscription(..., min_level=None, send_hour=None, exclusive=False)`.
- `templates.when_text(day, lang, today=None)` is a new signature; the issue argument was removed. `forecast_message`, `sms_message` and `summary_message` accept `today=`. `summary_message` takes `acc` as a dict (the old tuple still works).
- `notify.run(dry_run, hour=None, today=None)`: the stats keys are now `sent`, `failed`, `skipped`, `dry_run_written`, `feedback_asked`, `dry_run`, `sms_gateway`, `stale_stations` and `unknown_channel`. Dry-run counts appear in `dry_run_written`, no longer in `sent`.
- New CLI options: `notify --hour H`, `apikey --list`, `apikey --revoke OWNER_OR_ID`. The `--tag` default is `EYE_TAG`.
- Schema migration on the next `db.init()`, which runs on API start and in every CLI step: `notifications` gains `station`, `issue_date` and `level`; new table `feedback_requests`; new indexes. The migration is additive only.

## Left open, and why
- **Kazakh native review (25.4).** All new or changed KK strings need it: actions, `м/с дейін`, `Арал түбінің`, `сізге/балаларға қауіпті`, possessive suffixes from `kk_poss`. To stay within 400 characters, the KK source line says "Арал түбінің … бөлігі" and the RU one says "северо-восток высохшего дна".
- **Doctor "short 3-day summary" (25.1).** The `/forecast` list already is a compact 3-day view. A separate digest for doctors is not implemented.
- **Bot place choice.** The bot still offers the 4 stations, not all 12 settlements. The API accepts settlements.
- **Old feedback hashes.** Existing `feedback` rows keep the unsalted hash. They no longer match new HMAC answers, and `/stop` deletes them through the legacy hash.
  - The QA leftover row from tester 07 (feedback dated `2999-01-01`) is still in `data/aralshield.sqlite`. The lead should delete it, because I may not write to the real DB.
- **Out of scope (W1/W12).** Rate limiting is per process. Telegram sending is still sequential; there is no 30 msg/s async queue.
- **`/v1/health` is now `stale` on the real DB.** The last live issue is 2026-10-27. This is the intended behaviour, and the lead's `daily` run fixes it.
