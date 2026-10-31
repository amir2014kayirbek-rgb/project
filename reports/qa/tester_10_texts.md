# QA tester #10: language quality and template correctness (RU/KK)

Method: a scratch script called `forecast_message` for 9 roles × 4 levels × 4 day offsets × 2 `degraded` values × 2 languages (576 messages), plus `sms_message`, `summary_message` and `feedback_question`. I also rendered real backtest orange/red rows from the DB (read-only), called `/v1/messages/preview` and `/v1/summary` on :8000, and read `bot.py` UI, `i18n.ts`, `components/*.tsx` and `DRIVER_LABELS`. Kazakh judgements marked (uncertain) need a native speaker, as section 25.4 already requires.

### T10-1: Russian typo in the doctor red-level action
- Severity (proposal): High (doctors see it on every red alert)
- Location: `aralshield/voice/templates.py:47`
- Current text: `усилите готовность поликлиники`
- Proposed text: `усильте готовность поликлиники`
- Why: «усилите» is not a valid imperative. The verb is усилить, so the imperative is усильте.

### T10-2: Most yellow/orange/red messages exceed 400 characters
- Severity (proposal): High (breaks the 25.1 principle)
- Location: `templates.py` `forecast_message`
- Current: 336 of 576 generated messages are over 400 characters when the wind and source lines are present. Every non-green role/level combination exceeds it at least once. Maximum: KK teacher red = 615, RU teacher orange = 557, KK doctor orange = 556. A real backtest row with no source line still gives KK teacher red = 453 and RU = 412. The disclaimer plus URL alone is 106 characters (RU) and 90 (KK).
- Proposed: shorter disclaimer, RU `Инфопрогноз, не официальное предупреждение: kazhydromet.kz` and KK `Ақпараттық болжам, ресми ескерту емес: kazhydromet.kz`. Drop the «около N км» part. Shorten the degraded line to `⚠️ Данные неполные, уверенность ниже.` / `⚠️ Деректер толық емес, сенімділік төмен.` For the teacher, send the text for parents as a separate message (the 25.1 «Переслать родителям» button, see suggestion 1).
- Why: section 25.1 sets a 400-character limit.

### T10-3: The daily summary invents a dust source when it is unknown
- Severity (proposal): High (a factual claim with no data behind it)
- Location: `templates.py:208`, `SECTOR8.get(top.get("source_sector") or "", "NE")`
- Current (live `/v1/summary?lang=kk`): `Ең жоғары қауіп — бүгін (сейсенбі) (Қызылорда), шаң көзі — құрғаған теңіз түбінің солтүстік-шығыс бөлігі.` In the DB, `source_sector` is NULL for every live row, including Kyzylorda.
- Proposed: when the source is unknown, leave out the source clause. RU `Наибольший риск — Кызылорда, сегодня (вторник), 22%.` KK `Ең жоғары қауіп — Қызылорда, бүгін (сейсенбі), 22%.`
- Why: the north-east fallback becomes a statement sent to the akimat and ДЧС that nothing supports.

### T10-4: Kazakh case endings after numbers are hard-coded
- Severity (proposal): Medium
- Location: `templates.py:115` (`{n}-нан {hit}`); `api/main.py:258` (`text_kk`)
- Current: `дәлдігі: {n}-нан {hit}` is only correct for n=6, 9, 10, 30 and similar (алты-дан, он-нан). It gives `7-нан 5`, which should be `7-ден 5`, and `8-нан` (should be `8-ден`), `4-нан` (`4-тен`), `5-нан` (`5-тен`), `20-нан` (`20-дан`). In the accuracy text, `12 ескертудің 7 расталды` is missing the possessive.
- Proposed: a helper `kk_abl(n)` that picks -нан/-нен/-дан/-ден/-тан/-тен from the last spoken numeral (бір→ден, екі→ден, үш→тен, төрт→тен, бес→тен, алты→дан, жеті→ден, сегіз→ден, тоғыз→дан, он→нан, жиырма→дан, отыз→дан, қырық→тан, елу→ден, жүз→ден). Alternatively, avoid the case ending: `дәлдігі: 7/10 (10 ескертудің 7-уі расталды)`. Also `{n} ескертудің {tp}-і расталды` (uncertain: the vowel of -і/-ы/-уі follows the numeral).
- Why: Kazakh vowel harmony and consonant assimilation decide the suffix.

### T10-5: Russian number agreement
- Severity (proposal): Low
- Location: `templates.py:25` (`after_dry`); `api/main.py:257`
- Current: `после 21 сухих дней`; `Из 1 оповещений … подтвердились 1`; `Из 21 оповещений`.
- Proposed: a `plural_ru()` helper, giving `после 21 сухого дня`, `Из 1 оповещения … подтвердилось 1`, `Из 21 оповещения`. A simpler option for dry days: `после сухой недели` / `после 3 недель без дождя` (the 15.3 wording).
- Why: dry_days ≥ 3 regularly reaches 21–31 (live rows have 22–24).

### T10-6: Actions that do not start with a verb, and a mixed mood
- Severity (proposal): Medium
- Location: `templates.py:31-34` (public yellow/orange), KK `:81-83`; bot help
- Current: RU `людям с астмой — держать лекарства при себе`; KK `демікпесі барлар дәрісін өзімен алып жүрсін`. These sit next to imperatives such as «закройте окна».
- Proposed: RU `держите лекарства при себе, если у вас астма`; KK `демікпеңіз болса, дәріңізді жаныңызда ұстаңыз` (uncertain).
- Why: 25.1 says each action starts with a verb, and the list mixes infinitive and 3rd-person forms with 2nd-person imperatives. Appendix В uses the old form, so update it at the same time.

### T10-7: The red-level action repeats the disclaimer; a Kazakh wording error in the bot help
- Severity (proposal): Medium
- Location: `templates.py:35,48,55` and KK `:85,98,105`; `bot.py:81`
- Current: red-level actions include `сверьтесь/сверяйтесь с предупреждениями Казгидромета` (two different verb forms in one file), and the next line is the disclaimer with the same meaning. KK help says `🔴 өте ықтимал — далада аз болу, Қазгидрометті тексеру`, which reads as "inspect Kazhydromet".
- Proposed: replace the red public action with a concrete one: `наденьте маску при выходе` / `далаға шықсаңыз, бетперде киіңіз` (uncertain). Use one verb form, `сверьтесь`. Change the KK help to `Қазгидромет ескертулерін қадағалау`.
- Why: this saves about 45 characters (see T10-2), keeps terminology the same, and removes a semantic error.

### T10-8: SMS wording
- Severity (proposal): Low
- Location: `templates.py:59-60,109-110`
- Current: RU red `Завтра сильная пыльная буря. Скот в укрытие. AralShield` has no verb and no probability word, and it is not parallel with KK `Малды қорада ұстаңыз`. No SMS names the place. All SMS are ≤ 69 characters, which is within the limit.
- Proposed: RU red `Послезавтра сильная пыльная буря. Уведите скот в укрытие. AralShield` (68 at the longest day word). With the place: `Аральск: послезавтра вероятна пыльная буря. Скот — в укрытие. AralShield` is 72, too long, so the place only fits if «AralShield» is dropped.
- Why: 25.1 says "first line = place, day, level", and actions start with a verb.

### T10-9: Russian placeholder returned for a Kazakh SMS preview
- Severity (proposal): Low
- Location: `api/main.py:175`
- Current: `/v1/messages/preview?...&lang=kk&channel=sms` returns `(SMS отправляется только при оранжевом и красном уровне)`.
- Proposed: KK `(SMS тек қызғылт сары және қызыл деңгейде жіберіледі)`, chosen by `lang`.
- Why: this mixes languages in the Kazakh preview on the Reports tab. The 404 `нет прогноза` is also RU-only.

### T10-10: Relative day words and the ISO-date fallback
- Severity (proposal): Medium
- Location: `templates.py:142-146,175,190-192`; `bot.py:189-193`
- Current: «сегодня / завтра / ертең» is computed from `issue_date`, not from the send date. If the daily issue fails, `/forecast` shows yesterday's «завтра», which is now today. For an offset ≥ 3 the output is `2026-10-29 (бейсенбі)` and, in SMS, `2026-10-29 вероятна пыльная буря`.
- Proposed: compute the offset from `local_today()`. The fallback should be `29 октября (четверг)` / `29 қазан (бейсенбі)` (KK month names қаңтар…желтоқсан).
- Why: a wrong day word is a wrong forecast, and an ISO date is not ordinary user language.

### T10-11: Summary layout and wording against Appendix В
- Severity (proposal): Low
- Location: `templates.py:63-65,113-115,214`
- Current: `Наибольший риск — завтра (вторник) (Аральск)` and `ертең (сейсенбі) (Арал)` have two pairs of brackets in a row. `Точность оповещений оранжевого уровня: 7 из 10.` gives no period. The summary has no Kazhydromet URL. The "highest risk" line appears even when every level is green (live: 22%).
- Proposed: RU `Наибольший риск — Аральск, завтра (вторник), 71%.` and `Точность оповещений оранжевого уровня за 30 дней: 7 из 10.` KK `Ең жоғары қауіп — Арал, ертең (сейсенбі), 71%.` and `Соңғы 30 күндегі қызғылт сары деңгейлі ескертулердің дәлдігі: 10-нан 7.` Add the URL. When everything is green: `Пылевых дней не ожидается.` / `Шаңды күн күтілмейді.`
- Why: this follows Appendix В. Also, `/accuracy` counts orange and red together while the summary says «оранжевого уровня», so the terms should match.

### T10-12: The feedback question departs from Appendix В
- Severity (proposal): Low
- Location: `templates.py:61,111`
- Current: RU `Вчера мы предупреждали о пыли в пункте Аральск.` sounds bureaucratic. KK `Кеше Қызылорда елді мекенінде…` calls a city a «елді мекен», which sounds odd.
- Proposed: RU `Вчера мы предупреждали о пыли в вашем пункте (Аральск). Было пыльно?` KK `Кеше сіздің елді мекеніңізде (Қызылорда) шаң болатынын ескерткен едік. Шаң болды ма?`
- Why: this matches the Appendix В wording and avoids inflecting the place name.

### T10-13: Kazakh word choices (all uncertain, for the native-speaker review)
- Severity (proposal): Low
- Location: `templates.py` KK actions
- Current → Proposed:
  - `далада өтетін сабақтарды тоқтатыңыз` ("stop") → `далада өтетін сабақтарды өткізбеңіз` (as in 15.3)
  - `өтініштер көбеюіне` («өтініш» means a written application) → `науқастардың жүгінуі көбеюіне`
  - `далада аз болыңыз` → `далада болу уақытын азайтыңыз`
  - `үйде болыңыз` (RU says «в помещении») → `ғимарат ішінде болыңыз`
  - `егінді жабыңыз` (RU says «всходы») → `өскіндерді жабыңыз`
- Why: these bring the Kazakh closer to the Russian meaning and read more naturally.

### T10-14: Word order in the Kazakh wind line; "km" from where?
- Severity (proposal): Low
- Location: `templates.py:74-76`, RU `:26`
- Current: `Солтүстік-шығыс бағыттан секундына 15 метрге дейін жел күтіледі (7 құрғақ күннен кейін).` and `… шамамен 80 км.` / `… около 80 км.`
- Proposed: `7 құрғақ күннен кейін солтүстік-шығыстан секундына 15 метрге дейін жел күтіледі.` (15.3 word order). Source line: `… елді мекеннен шамамен 80 км жерде.` / `… в ~80 км от пункта.`, or drop the distance (T10-2).
- Why: putting the condition in brackets after the verb is unnatural, and the distance has no reference point.

### T10-15: Bot UI consistency
- Severity (proposal): Low
- Location: `bot.py:66,192-193,221`
- Current: `🟠 Қызғылт сары және жоғары` next to `🟡 Сары және одан жоғары`. `/forecast` lines read `🟠 ертең (сейсенбі): ықтимал — 70%`, with no subject and in lower case. `/accuracy` appends `POD 0.5, FAR 0.3` and uses `period="365d"`, while 25.1 says 30 days. The UI key `parents` is never used: there is no «Переслать родителям» button.
- Proposed: `🟠 Қызғылт сары және одан жоғары`; `🟠 Ертең (сейсенбі): шаңды күн болуы ықтимал — 70%`. Replace POD/FAR with `поймано 5 из 6 событий` / `6 оқиғаның 5-і анықталды`.
- Why: consistent wording, and no jargon for residents.

### T10-16: Untranslated or Russian-only strings in the panel
- Severity (proposal): Medium (Kazakh and Russian are equal per 15.5)
- Location: `ArchiveTab.tsx:76,83,151,186,200,204`; `SourcesTab.tsx:92,94`; `RiskTab.tsx:112`; `i18n.ts:48`
- Current: H4 table headers `сдвиг`, `сут`, `ветер NW–W`, `P(Кз | Ар)` and the title `Аральск → Кызылорда` are RU-only. Calendar tooltip ` · пыль` and legend `дн.` are RU-only. `Train/valid/test` is English. `F1 «источник»` is RU-only. `cv.meaning` (from `eye/classify.py:155`) is RU-only. The forecast card header shows the ISO date `hz.day`. KK `replay`: `оқиғаның қайталануы` means "recurrence of the event".
- Proposed: move these strings into `i18n.ts`. KK equivalents: `ығысу`, `тәул.`, `жел СБ–Б`, `Арал → Қызылорда`, `шаң`, `күн`, `F1 «шаң көзі»`. Replay: KK `Тест жылындағы оқиғаны қайта қарау`, RU `Повтор события из тестового года`. Show dates as `28 қазан` / `28 октября`.
- Why: the i18n.ts header comment says all interface strings live there.

### T10-17: Role names and places differ between channels
- Severity (proposal): Low
- Location: `bot.py:34,64`; `RiskTab.tsx:146`; `ReportsTab.tsx:36`; `/v1/messages/preview?settlement=baikonur`
- Current: the farmer role is `Фермер / чабан` in the bot but `Чабан / Шопан` in the panel. The panel has no `asthma` role. A preview for Байқоңыр returns a message headed `Жосалы`.
- Proposed: use `Фермер / чабан` · `Фермер / шопан` everywhere. Head the message `Байқоңыр (Жосалы станциясы бойынша)` / `Байконур (по станции Жосалы)`.
- Why: consistent terminology, and the user sees the place they chose.

### T10-18: DRIVER_LABELS wording
- Severity (proposal): Low
- Location: `forecast/predict.py:35-38`
- Current: `rh_min` is labelled `влажность воздуха` / `ауа ылғалдылығы`. `snow_depth` is `снег` / `қар`. `upwind_index` uses `жел жақтағы`, while the next key uses `жел жағындағы`. `gust` in i18n.ts is `екпін` on its own.
- Proposed: `минимальная влажность воздуха` / `ауаның ең төмен ылғалдылығы`; `снежный покров` / `қар жамылғысы`; `жел жағындағы құрғақ қабық`; `жел екпіні`.
- Why: the labels should be accurate and use one spelling.

### T10-19: "100%" sounds like a certain verdict
- Severity (proposal): Low
- Location: `templates.py:156`; real backtest messages show `очень вероятен — 100%`
- Proposed: cap the displayed value at `>95%` / `95%-дан жоғары`.
- Why: it contradicts the bot's own line «вероятность, а не приговор» / «үкім емес, ықтималдық».

## Improvement suggestions
1. Implement the teacher «Переслать родителям / Ата-аналарға жіберу» button using the existing `UI["parents"]` and send the text for parents separately. That alone brings teacher messages near 400 characters.
2. Add a unit test that renders every role × level × day × language with the wind and source lines present and asserts ≤ 400 characters (≤ 70 for SMS), at least one leading verb per action, the URL present, and no Latin or ISO-date tokens in the body.
3. Put number-dependent wording (KK ablative and possessive suffixes, RU plurals) and day and month names in one `voice/lang.py` helper, and run the whole KK set through the native-speaker proofreading required by 25.4 before launch.
