# QA tester #12: notification and feedback pipeline (J8, FR-08, FR-13, FR-14, NFR-04)

Scope: `aralshield/voice/notify.py`, `aralshield/voice/templates.py`, notification/subscription/feedback parts of `aralshield/db.py`, `level_for`/`levels_json` in `aralshield/forecast/predict.py`, `models/thresholds.json`.
Method: scratch scripts in the OS temp dir; `ARALSHIELD_DATA` pointed at a fresh `tempfile.mkdtemp()` before import (asserted `DB_PATH` was inside it). I inserted synthetic forecasts and subscribers and ran `notify.run(dry_run=True)`. For the "real send" paths I set a fake `TELEGRAM_BOT_TOKEN` and/or `SMS_GATEWAY_URL=http://sms.invalid` and stubbed `notify.requests.post`. No network calls, no CLI, real DB untouched.
**Passed checks:**
- Role to group mapping (`ROLE_GROUP`) is correct. At p=0.12, only parent/asthma/doctor (health, orange ≥0.11) were notified with min_level=orange.
- The horizon choice picks the most dangerous level, with ties going to the earliest horizon.
- A re-run with unchanged forecast ids does not resend.
- SMS texts are all ≤70 Cyrillic chars (max 69: RU orange "Послезавтра...").
- Feedback `callback_data` `fb:<station>:<YYYY-MM-DD>:<ans>` matches `bot.on_feedback` (4 parts, ≤28 bytes).
- Telegram messages for degraded forecasts carry the "Упрощённый режим" line.

### T12-1: Re-issuing a forecast (INSERT OR REPLACE) gets new ids, so every subscriber is notified again
- **Severity (proposal):** High
- **Location:** `aralshield/db.py:125` (`INSERT OR REPLACE`, AUTOINCREMENT id), dedup key in `aralshield/voice/notify.py:75-76`
- **Evidence:**
  - Issued 3 forecasts for aralsk, then ran notify: 3 sent.
  - Called `save_forecasts` again for the same `issue_date` (h1 p 0.12→0.13). Forecast ids went from `[1,2,3]` to `[4,5,6]`.
  - The next `notify.run`: `sent: 3` again. `notifications` then held rows for forecast_id 2 **and** 5 for subscribers 1-3, the outbox had 6 messages, and 3 notifications became orphans (`forecast_id NOT IN forecasts`).
- **Expected vs actual:** A same-day re-issue (manual `daily` rerun, J7 retry) should not spam users. Actually, it sends a duplicate alert, and the history linking the old ids is broken.
- **Suggested fix:** Use `INSERT ... ON CONFLICT(station,issue_date,horizon,mode) DO UPDATE` (this keeps the id). Or dedup on `(station, issue_date, horizon/day, subscriber, channel)`, and resend only if the level went up.

### T12-2: A dry run (or a run with no token) marks notifications `sent`, so the later real run sends nothing
- **Severity (proposal):** High
- **Location:** `notify.py:82` (`... if not dry_run else True`), `notify.py:85-89`, `notify.py:92-94`
- **Evidence:**
  - 1 telegram and 1 sms subscriber, orange h1. `run(dry_run=True)` returned `sent: 2`.
  - I then set a fake token and SMS gateway with stubbed HTTP and ran `run(dry_run=False)`. Result: `{'sent': 0, 'skipped': 2}` with **0 HTTP calls**.
  - In the dry run the SMS was not even written to the outbox (only `telegram_<date>.txt` existed), yet it was recorded as `sent`.
- **Expected vs actual:** A dry run should have no side effects on delivery state. Actually, after a rehearsal or a token outage (`not token` takes the same path), the real alert for that forecast is never delivered.
- **Suggested fix:** Do not insert into `notifications` when `dry_run or not token`, or store `status='dry_run'` and exclude it from the dedup check. Write SMS to the outbox in dry-run mode as well.

### T12-3: Failed sends are recorded and never retried; yellow SMS subscribers always count as "failed"
- **Severity (proposal):** Medium
- **Location:** `notify.py:75-79` (the dedup ignores `status`), `notify.py:80-82`, `templates.py:185-186`
- **Evidence:**
  - Stub returned `ok=False` → `failed: 1`, row `status='failed'`. With Telegram healthy again, run 2 gave `skipped: 1, sent: 0`, so the alert was lost.
  - An SMS subscriber with `min_level=yellow` and a yellow forecast: `sms_message` returns `None`, so `failed: 1` and a `failed` row on every issue. Nothing goes to the outbox.
- **Expected vs actual:** Transient Telegram/SMS errors (network, 429) should be retried within the 06:00-06:15 window, and "not applicable for the channel" should not count as a failure. Actually, a failure is permanent and the `failed` metric is polluted.
- **Suggested fix:** In the dedup, select only `status='sent'`, and add bounded retry/backoff. Before the hit check, clamp SMS `min_level` to orange (the API/bot allow yellow for any channel) and count that case as `skipped`.

### T12-4: Stale forecasts are sent with a wrong relative day
- **Severity (proposal):** Medium
- **Location:** `db.py:129-135` (`latest_forecasts` = MAX(issue_date), no freshness check), `notify.py:64-65`
- **Evidence:** Saved only an issue from 5 days ago (h0, p=0.6) and ran notify. It sent `🔴 Аральск, сегодня (четверг): пылевой день очень вероятен — 60%.` while today is Tuesday.
- **Expected vs actual:** If today's J6/J7 failed, J8 should send nothing (or a "no forecast" ops alert). Actually, it sends an old forecast labelled "сегодня".
- **Suggested fix:** In `run()`, require `issue_date == predict.local_today()` (and skip `day < today`). Log or alert when the forecast is stale.

### T12-5: No evening-before SMS for shepherds; `send_hour` is stored but ignored
- **Severity (proposal):** Medium
- **Location:** `notify.py:57-96` (single pass, no time logic), `db.py:39,149` (`send_hour`), `api/main.py:415`
- **Evidence:**
  - `grep send_hour` finds it only in the schema and API/db setters; `notify.py` never reads it.
  - The SMS for the most dangerous horizon is sent whenever J8 runs (06:00), including h0 "Бүгін ...".
- **Expected vs actual:** Spec scenario 3 and the role list (line 1587) say "чабан — SMS вечером накануне, а не утром", and line 2389 says "два выпуска в сутки". Actually, there is only a morning run, and `send_hour` is a dead setting.
- **Suggested fix:** Add an evening J8 slot (e.g. 19:00 local) that sends SMS for h1. Filter subscriptions by `send_hour` against the local hour.

### T12-6: No e-mail digest (FR-14); `channel='email'` subscribers are pushed to the Telegram API
- **Severity (proposal):** Medium
- **Location:** `notify.py:80-91` (every non-sms channel goes to Telegram), `api/main.py:409,433` (accepts `email`)
- **Evidence:**
  - I subscribed `channel=email, chat_id=director@school.kz, role=teacher` and ran with a fake token. The stubbed `sendMessage` was called with `chat_id='director@school.kz'`.
  - In dry runs, it is written to `telegram_<date>.txt`.
  - `templates.summary_message` (the 3-day digest) is used only by `GET /v1/summary` and is never sent.
- **Expected vs actual:** Spec 15.1, scenario 4 and FR-14 call for a daily e-mail digest to school directors and chief doctors. Actually, there is no e-mail path, and real Telegram would return 400, which T12-3 then never retries.
- **Suggested fix:** Add an email branch (SMTP or provider) that sends `summary_message` daily regardless of level. Until then, reject `email` in `SubscriptionIn`.

### T12-7: Feedback question is re-asked on every run; timing uses the host date, not local UTC+5
- **Severity (proposal):** Medium (re-ask), Low (date)
- **Location:** `notify.py:99-114`; `notify.py:101` vs `predict.local_today()` (`predict.py:47-48`)
- **Evidence:**
  - Two subscribers notified about a forecast with `day=yesterday`. Three consecutive `run()` calls returned `feedback_asked: 2` each time. Nothing records that the question was asked, so each J8 rerun (or an evening run, T12-5) would send "Было пыльно?" again.
  - Feedback is asked after yellow alerts too (`asked: 1`), which is acceptable per 15.4.
  - The date uses `date.today()` (host time zone), while forecasts use `local_today()` (UTC+5). On this host both gave 2026-10-27. On a UTC host, a run between 00:00 and 05:00 local (or on a UTC-x cloud host) looks at the wrong "yesterday". This is a code-level inconsistency, not reproduced. The same applies to the SMS outbox file name at `notify.py:46`.
- **Expected vs actual:** Spec 15.4 and scenario 6 say the question is asked once, the day after. Actually, it is asked on every run.
- **Suggested fix:** Add `feedback_asked_at` to `notifications` (or a `feedback_requests` table) and filter on it. Use `predict.local_today()`.

### T12-8: Group thresholds make "вероятен" appear at very low probabilities
- **Severity (proposal):** Medium (ethics, 15.5 "не кричать «волки»")
- **Location:** `models/thresholds.json` (health orange 0.11, yellow 0.07), `templates.py:17-18,159-161`
- **Evidence:**
  - At p=0.12, parents/asthma/doctors received `🟠 Аральск, завтра (среда): пылевой день вероятен — 12%.`
  - All groups also share red=0.58, so akimat red is not "higher" than health red, despite 13.8 ("акимат — высокий порог").
- **Expected vs actual:** A low threshold for health groups is per spec (13.8), but the wording "вероятен"/"ықтимал" ("likely") with 12% contradicts itself and erodes trust. Actually, the level word is tied to the group level, not to the probability.
- **Suggested fix:** For health groups, phrase orange as "возможен, повышенный риск для вас" (or show the base word from the public level). Review the per-group red thresholds.

### T12-9: Telegram messages exceed the 400-character limit; teacher gets no "Переслать родителям" button
- **Severity (proposal):** Low
- **Location:** `templates.py:149-180`, `notify.py:91` (`_tg_send(..., buttons=None)`)
- **Evidence:**
  - All 9 roles × 3 levels × 2 languages were generated with every optional field, including degraded. Lengths ranged 395-604, and 53/54 were over 400; the maximum was kk teacher red at 604.
  - For the teacher subscriber, the stubbed call had no `reply_markup` (`'reply_markup' in data` → False).
- **Expected vs actual:** Spec line 1586 caps messages at 400 characters. Line 1587 and scenario 2 call for a teacher forward button. Actually, the messages are longer, and the teacher gets the parents' text inline only.
- **Suggested fix:** Shorten the disclaimer/URL (e.g. move the URL to a button) and add an inline "Переслать родителям" button (switch_inline_query) for teacher.

### T12-10: SMS carries no degraded flag, no place name and no official-warning reference
- **Severity (proposal):** Low
- **Location:** `templates.py:59-60,109-110,183-193`
- **Evidence:** For a degraded red forecast, the SMS outbox held `+7700\tЕртең күшті шаңды дауыл. Малды қорада ұстаңыз. AralShield`, with no degraded marker. The Telegram message did have one. The raw phone is stored in plaintext in `outbox/sms_<date>.txt`, whereas Telegram entries are hashed (`notify.py:47` vs `:88`).
- **Expected vs actual:** Users of degraded forecasts should know confidence is reduced (FR-06 and 21.2). Actually, the SMS is indistinguishable. The spec template also omits the place, so that part is acceptable.
- **Suggested fix:** Use a short degraded variant (e.g. "(болжам шамамен)") within 70 characters. Hash or mask phones in the outbox.

### T12-11: The "7 из 10" accuracy statement is not delivered and its source mixes modes and dates
- **Severity (proposal):** Low (from code reading; not executed)
- **Location:** `api/main.py:180-184,210-236`, `templates.py:65,115`
- **Evidence:**
  - `_orange_hits(30)` → `_verification` filters by horizon only, not `mode`, so live and backtest rows are mixed. `drop_duplicates(keep="last")` picks one arbitrarily.
  - The 30-day window ends at the **latest verified day** (`df.day.max()`), not today, so it may describe 2025 backtest data.
  - It uses the `public` level column, not each group's level.
  - The template omits "за 30 дней" (spec Appendix В). The digest is not sent by J8 (T12-6), and the bot `/accuracy` uses another path.
- **Suggested fix:** Filter `mode='live'`, set end=`local_today()`, compute the stat per group, and add the period to the text.

### T12-12: Sequential sending is unlikely to meet NFR-04 (15 minutes) at target scale
- **Severity (proposal):** Medium (measured locally; extrapolation is speculative)
- **Location:** `notify.py:66-94` (2 SQLite connections per subscriber, synchronous `requests.post` with a 20 s timeout, no concurrency or rate control)
- **Evidence:** 3000 telegram subscribers on 4 stations in a dry run (no network) took **121.9 s = 40.6 ms/subscriber**. That was on an OneDrive-synced temp path on Windows, so it may be inflated. Extrapolated to NFR-05's 100k subscribers, that is about 68 min **before** any network latency. Telegram's ~30 msg/s limit and 429 errors are not handled.
- **Expected vs actual:** NFR-04 requires all subscribers within 15 minutes. Actually, a single-threaded loop does not scale.
- **Suggested fix:** Load notifications in one query, batch the inserts in one transaction, and send through an async/worker queue at about 25 msg/s with 429 `retry_after` handling.

## Improvement suggestions
1. Introduce a `deliveries` state machine (`pending → sent | failed(retry n) | dry_run | skipped_channel`) keyed on `(station, issue_date, horizon, subscriber, channel)`. This fixes T12-1/2/3/7 together and makes the J8 stats trustworthy.
2. Split J8 into channel jobs: morning Telegram (06:00), evening SMS (h1), daily e-mail digest and feedback (once). Each job has a freshness guard on `issue_date == local_today()`.
3. Add a pytest suite on a temp `ARALSHIELD_DATA` covering these scenarios (re-issue, dry-run then real run, stale forecast, email channel, message length ≤400 and SMS ≤70).
