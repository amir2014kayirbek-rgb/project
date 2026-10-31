# QA tester #11 — Telegram bot logic (`aralshield/voice/bot.py`)

Method: static review plus an end-to-end dispatch simulation: a real aiogram 3.31 `Dispatcher` with the project `router`, a fake `BaseSession` that records Bot API calls, and synthetic `Update` objects. It ran against a temporary DB (`ARALSHIELD_DATA` pointed at an OS temp dir, and the script asserted the DB path). The real DB was not used, and no project file was edited.
**Verified OK:** all 5 menu labels dispatch to the right handler in both languages (10/10). `/stop` cascades `subscriptions` (FK and `PRAGMA foreign_keys=ON`). `notify` callback data `fb:<st>:<YYYY-MM-DD>:<ans>` splits into 4 parts and is at most 31 bytes, under Telegram's 64-byte limit. The worst-horizon tie-break (earliest) matches `notify.run`. A user with no station gets the bilingual `no_sub`. `set_my_commands` covers all 6 spec commands.

### T11-1: Teacher "Переслать родителям" button and doctor 3-day summary are not implemented
- **Severity (proposal):** High (spec 25.1 role versions; scenario 2 "кнопкой «Переслать»")
- **Location:** `bot.py:198-199`, `bot.py:56/86` (`UI[*]["parents"]` is never used), `notify.py:91`
- **Evidence:** In the simulation, `/forecast` for role=teacher at orange sent 2 messages, both with `reply_markup=None`. `notify.run` calls `_tg_send(token, chat_id, text)` without `buttons`. A grep finds no `switch_inline_query` and no use of `UI[..]["parents"]`. For a doctor, `/forecast` returns the same single-day long message, not a short 3-day summary (`templates.summary_message` is used only by the API, `api/main.py:184`).
- **Expected vs actual:** The spec expects a separate ready-to-forward parents text with a share button. Actually, the parents line is only embedded inside the teacher's own message.
- **Suggested fix:** Send the parents text as a separate message with an `InlineKeyboardButton(switch_inline_query=...)` (or a copy-text button) in both `/forecast` and `notify`. For doctors, add a 3-day compact summary.

### T11-2: `/forecast` labels days relative to the issue date, so a stale forecast is shown as "сегодня"
- **Severity (proposal):** High (safety: wrong day in a warning)
- **Location:** `bot.py:181-193` → `templates.when_text` (`templates.py:142-146`)
- **Evidence:** The latest issue was 2026-10-22 and today is 2026-10-27 (a Tuesday). The output was `🟢 сегодня (четверг)… 🟠 завтра (пятница): вероятен — 50%`. No issue date or staleness warning is shown, and nothing checks the forecast's age.
- **Expected vs actual:** Expected relative words computed against the real today, or an explicit "выпуск от 22.10" note, or a refusal when the issue is older than 1 day. Actually, the bot silently presents a 5-day-old forecast as current.
- **Suggested fix:** Compare `issue_date` with `date.today()` (Asia/Qyzylorda). If they differ, print the issue date and use absolute dates or `no_fc`.

### T11-3: A global `MAX(issue_date)` hides stations whose latest run is older
- **Severity (proposal):** Medium
- **Location:** `db.py:129-135` (used by `bot.py:181` and `notify.py:64`)
- **Evidence:** Kazalinsk had an issue from 2026-10-22, then an Aralsk-only issue from 2026-10-27 was saved. `/forecast` for the Kazalinsk subscriber then returned "Прогноз ещё не выпущен". `notify.run` uses the same query, so Kazalinsk subscribers silently get nothing.
- **Suggested fix:** Take the max issue per station (`WHERE issue_date=(SELECT MAX(issue_date) FROM forecasts WHERE mode=? AND station=f.station)`). Combine this with the staleness guard from T11-2.

### T11-4: A stale wizard button silently overwrites the role with `resident`
- **Severity (proposal):** Medium
- **Location:** `bot.py:157-166` (`d.get("role", "resident")`); old inline keyboards are never removed
- **Evidence:** A teacher subscribed (aralsk/orange), then sent `/settings` → `lang:ru` → `st:kazalinsk`, then tapped `lvl:red` on an older message. The DB then held `{'role': 'resident', 'station': 'kazalinsk', 'min_level': 'red'}`. The role step was skipped without any warning. Wizard keyboards from earlier `/start` or `/settings` runs stay clickable and mix into the new draft.
- **Expected vs actual:** Expected an incomplete draft to be rejected. Actually, it is committed with a default role.
- **Suggested fix:** Require all of `lang`, `station` and `role` in `on_level`. Store the wizard `message_id` in the draft and ignore callbacks from other messages. Alternatively, use aiogram FSM (`StatesGroup`) instead of the global `_draft`.

### T11-5: Feedback callback is not validated (date format, subscriber, was-notified)
- **Severity (proposal):** Medium (pollutes FR-08 / scenario 6 QC data)
- **Location:** `bot.py:240-248`, `db.py:174-176`
- **Evidence:** `fb:aralsk:not-a-date:yes` from user 1 and `fb:aralsk:2099-12-31:no` from user 999 (never subscribed, never notified) were both stored and acknowledged with "Спасибо!". Rows: `{'date': 'not-a-date'}`, `{'date': '2099-12-31'}`. Callback data is client-supplied and must not be trusted.
- **Suggested fix:** Validate `date.fromisoformat(parts[2])` and require it to be no later than today. Accept feedback only if a `notifications` row exists for this subscriber, station and day. Otherwise call `cb.answer()` silently.

### T11-6: `/stop` does not delete all subscriber data; the feedback hash is re-identifiable
- **Severity (proposal):** Medium (spec 15.5; the docstring at `bot.py:7` and the reply "ваши данные удалены")
- **Location:** `db.py:42-47` (no FK on `notifications.subscriber_id`), `db.py:155-159`, `db.py:169-171`
- **Evidence:** After `/stop`, `subscribers` and `subscriptions` were empty, but the row `notifications {subscriber_id:1, channel:'telegram', status:'sent', sent_at}` remained. The `feedback` rows with `subscriber_hash '0b3a916bfcf5a0ac'` also remained, and that equals `subscriber_hash('telegram','1')`. The hash is unsalted SHA-256 truncated to 64 bits of `telegram:<id>`. Telegram IDs are about 10^10, so the hash can be reversed by enumeration (inference, not executed). A non-subscriber who sends `/stop` (user 555) also gets "деректеріңіз жойылды".
- **Suggested fix:** Delete or anonymise `notifications` for the subscriber (or add `ON DELETE CASCADE`). Use a secret-salted HMAC for `subscriber_hash` and delete that user's feedback on `/stop`, or document the retention. Reply with `no_sub` when nothing was deleted.

### T11-7: The user id is stored as the "chat id"; group chats are not handled
- **Severity (proposal):** Medium
- **Location:** `bot.py:116,125,134,147,160,165,176,211,235,245` (all use `from_user.id`); there is no `F.chat.type` filter
- **Evidence:** The wizard was completed by user 77 inside group chat `-100500`. The stored `chat_id` was `'77'`, not the group. `notify` then sends to `77`, which Telegram rejects with 403 when the user never opened a private chat with the bot (Telegram behaviour, not executed). Meanwhile any group member can click another member's wizard buttons. The spec targets school and parent chats and stores the "идентификатор чата" (15.5).
- **Suggested fix:** Either restrict the bot to private chats (`router.message.filter(F.chat.type == "private")`), or key subscriptions by `chat.id` and limit settings to group admins.

### T11-8: Invalid or unknown callbacks are never answered (client spinner hangs)
- **Severity (proposal):** Low
- **Location:** `bot.py:123-124, 136-137, 149-150, 243-244`; there is no catch-all callback handler
- **Evidence:** `lang:xx`, `st:nowhere`, `role:hacker`, `fb:aralsk:2025-01-01:maybe` and `zzz` each produced zero Bot API calls, so no `AnswerCallbackQuery` was sent. `on_level` answers with the cryptic toast "/start" (`bot.py:163`).
- **Suggested fix:** Call `await cb.answer()` (or answer with a localised "устарело, нажмите /start") on every early return, and add a final `@router.callback_query()` fallback.

### T11-9: `/accuracy`: wrong period, backtest mixed in, blocking import, jargon, swallowed errors
- **Severity (proposal):** Medium
- **Location:** `bot.py:207-221`, `api/main.py:212-228, 240-259`
- **Evidence:**
  - (a) The bot uses `period="365d"`, but spec 25.1 says "за 30 дней" (FR-07 asks for 30 and 365).
  - (b) `_verification` has no `mode` filter and anchors the window at the last verified day, not today. With only backtest rows from 2021 the bot prints the 2021 window as the current accuracy: `Из 20 оповещений… подтвердились 10. (2021-05-01 — 2021-06-29, POD 0.333, FAR 0.5)`.
  - (c) The first `/accuracy` imports FastAPI, pandas and the whole API (measured 9.2 s), and then pandas runs synchronously inside the async handler. This blocks the event loop for all users.
  - (d) "POD/FAR" is not "в понятной форме" for residents.
  - (e) `except Exception: a = {}` hides every error without logging.
- **Suggested fix:** Move the verification and accuracy logic into a lightweight module (for example `forecast/verify.py`) and import it at module load. Run it with `asyncio.to_thread`. Show 30d live data (and 365d), and say when backtest data is included. Drop POD/FAR for non-specialist roles. Add `log.exception`.

### T11-10: `/settings` cannot change the send time and shows no current values; `send_hour` is dead
- **Severity (proposal):** Low/Medium (spec 25.1: "смена пункта, роли, порога, времени рассылки, языка")
- **Location:** `bot.py:112-117` (it just restarts the wizard), `bot.py:166`, `notify.py:59` (`send_hour` is not selected or used)
- **Evidence:** The bot never asks for a time, and `set_subscription` always stores 6. The "done" text promises "до 06:15", while scenario 2 says 06:30. The bot offers only 4 stations (`STATIONS`) even though 12 `SETTLEMENTS` exist (for example Байконур and Саксаульский).
- **Suggested fix:** Make `/settings` a menu showing the current values with per-field edit buttons (including time), and honour `send_hour` in notify. Optionally list settlements and map each to its station.

### T11-11: Bot messages exceed the 400-character limit
- **Severity (proposal):** Low (spec 25.1 "до 400 знаков"; overlaps with the templates review)
- **Location:** `templates.py:149-180`, delivered by `bot.py:199`
- **Evidence:** With all fields filled (wind, source, degraded), red-level messages measured: kk teacher 603, kk doctor 522, kk resident 484, ru teacher 542, ru resident 472. In the `/forecast` run, ru teacher was 486 and ru doctor 445.
- **Suggested fix:** Shorten the disclaimer and URL, and send the parents text separately (see T11-1).

### T11-12: Role design touches health data, and parents get patient instructions
- **Severity (proposal):** Low (ethics; needs clarification against spec 15.5 "Данные о здоровье не собираются")
- **Location:** `bot.py:33-35,63-65` (role `asthma` "Человек с астмой/ХОБЛ" is stored in `subscribers.role`); `templates.py:132` (`parent → health`)
- **Evidence:** At red level the parent role received "оставайтесь в помещении; следуйте плану действий от вашего врача" (simulation output). Choosing `asthma` records a diagnosis next to the chat id. `ROLE_GROUP` (thresholds) and `ROLE_ACTIONS` (advice) are otherwise consistent: doctor uses health thresholds with doctor actions, and farmer uses public thresholds with farmer actions.
- **Suggested fix:** Rename the role to a neutral "чувствительная группа / хочу ранние оповещения", or add a privacy note. Give parents child-oriented public actions.

### T11-13: In-memory `_draft` and the feedback flow are fragile (minor)
- **Severity (proposal):** Low
- **Location:** `bot.py:109`, `notify.py:99-113`, `bot.py:247`
- **Evidence:**
  - `_draft` lives only in memory. It is lost on restart (the wizard then falls back to `kk` and `on_level` answers "/start"). Abandoned drafts are never evicted. Keys are per user and handlers are single-threaded asyncio (`handle_as_tasks=True`), so no cross-user race was found.
  - `ask_feedback` keeps no record of what it already asked, so each `notify.run` on the same day re-asks the question. The spec (section 30) mentions 2 issues a day.
  - `InaccessibleMessage` has no `edit_text` (verified `hasattr == False`). If Telegram delivers the callback on an inaccessible message, `cb.message.edit_text` raises AttributeError. *Speculative: depends on when Telegram marks a message inaccessible.*
- **Suggested fix:** Use aiogram FSM with a storage backend, add a `feedback_asked` marker, and guard with `isinstance(cb.message, Message)`.

## Improvement suggestions
1. Add an aiogram test harness like the one used here (fake `BaseSession` plus `dp.feed_update`) to `tests/`, so the wizard, menu filters and callback validation are regression-tested without a token.
2. Introduce a single "current forecast for station X as of today" function shared by the bot, notify and the API, with a staleness policy (fixes T11-2 and T11-3 everywhere).
3. Write a short data-retention note for the bot (what `/stop` deletes and what stays anonymised) and make the code match it.
