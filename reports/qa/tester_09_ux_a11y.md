# Tester 09: UX, accessibility and visual behaviour (browser)

Environment: built-in browser, own tab (tab-5), http://localhost:8000, desktop pane width about 775 px, plus the `mobile` preset (375x812). Tested on 2026-10-27 and 2026-10-28; latest forecast issued 2026-10-27. All 5 tabs were checked in ҚАЗ and РУС. Contrast ratios come from a script that compares each element's computed colour with its background, using the WCAG formula.
Not verified: the real print dialog (`window.print()` was not opened; only the `@media print` CSS was read). No downloads were triggered: export was tried only with an invalid key, which returns 401.
Console: the app produced no errors. The only 401/404 entries came from my own probe requests.

### T09-1: Stale forecast not flagged; "Завтра/Ертең" shows today's date
- Severity (proposal): High (NFR-11, 15.5 honesty)
- Location: Карта рисков; `components/RiskTab.tsx` (no freshness check), `/health`
- Evidence / reproduction: On 2026-10-28 the panel shows "шығарылым: 2026-10-27". The default horizon "Ертең" opens the detail for "Арал · 2026-10-28", and the message reads "ертең (сәрсенбі)". 2026-10-28 is today, not tomorrow. Reports → "Жүйе күйі" shows `"status": "degraded", "forecast_fresh": false`, but the risk screen shows no warning (no `.err` elements).
- Expected vs actual: Expected a visible "прогноз устарел / сниженная уверенность" banner, with horizon labels based on the current date. Actual: a 1-day-old forecast is presented as current.
- Suggested fix: Compare `issued_at` with today (Asia/Qyzylorda). Show a localized warning banner and label horizons by actual date, or hide horizons that are in the past.

### T09-2: Swipe (шторка) on the sources map does nothing until the slider is moved, and resets after a layer switch
- Severity (proposal): High (25.2 "сравнение шторкой")
- Location: Источники пыли; `components/SourcesTab.tsx` `SwipeClip` (effect deps `[map, overlay, pct]`)
- Evidence / reproduction: On load the slider sits at 50 and `classes_2025.png` has `style.clipPath = ""`, so the model layer covers the whole RGB image. After 20× ArrowLeft (value 30) the clip becomes `inset(0px 0px 0px 75.3px)`. After clicking "Шаң көзі ықтималдығы" the new overlay has `clipPath = ""` while the slider still shows 30.
- Expected vs actual: The slider position and the clip should always match. Actually they only match after the user moves the slider.
- Suggested fix: Re-run the clip when the overlay mounts. Use a callback ref or state for the overlay, add `layer` / `layers.data` to the deps, and listen to the overlay `load` event.

### T09-3: Leaflet controls cover the swipe slider (mobile) and the sticky header
- Severity (proposal): High on phone / Medium
- Location: Источники пыли, all maps; `styles.css` (`.swipe{z-index:500}`, `.top{z-index:1000}` vs Leaflet `.leaflet-bottom/top` z-index 1000)
- Evidence / reproduction: In the mobile preset, `document.elementFromPoint` at the centre of the range input returns `DIV.leaflet-control-attribution`. The slider is hidden under the attribution and cannot be tapped; only its label is visible. On the risk tab, scrolling at mobile width makes "© OpenStreetMap contributors" draw over the sticky header (screenshot).
- Expected vs actual: Expected the slider to be operable and the header always on top. Actually both are overlapped by Leaflet controls.
- Suggested fix: Raise `.swipe` above 1000 or move it above the map. Give `.top` a higher z-index (for example 1100), or set `isolation:isolate` on `.mapwrap`.

### T09-4: WCAG AA contrast failures (level badges and accent colour)
- Severity (proposal): Medium (NFR-09, 25.3)
- Location: `styles.css` `.badge` / `--l-green`, `--l-orange`, `--accent`; `components/common.tsx` LevelBadge
- Evidence (computed, 12.5 px / 600, needs 4.5:1):
  - Green badge "маловероятен/екіталай": white on #2f8a4c = **4.32**
  - Orange badge "ықтимал": white on #e07a1f = **3.01**
  - Active "ҚАЗ" button: white on #b8612e = **4.39**
  - Footer link "API": #b8612e on #f6f3ee = **3.96**
  - Archive quality gates "✓": #2f8a4c on white = **4.32**
- Passing values for comparison: yellow badge (dark text #1f1c18 on #c9a400) = 7.1, red badge = 5.44, muted text = 6.7.
- Suggested fix: Darken green to about #23703c, use dark ink on orange (or #b85a0f with white), and darken the accent to about #9c4f22. Re-check the map fills against the grey basemap (3:1 for non-text).

### T09-5: Untranslated Russian/English text in Kazakh mode
- Severity (proposal): Medium (NFR-10, 25.4 "тексты в файлах переводов")
- Location: several; mostly API-provided strings and literals hard-coded in components
- Evidence (ҚАЗ selected, `html lang="kk"`):
  - Risk: "нет свежих наблюдений станции — признак персистентности пропущен" (`exp.data.inputs.notes`, RiskTab.tsx).
  - Archive:
    - Card "H4: Аральск → Кызылорда" (literal, ArchiveTab.tsx:186) and "H4 подтверждается: связь значима".
    - H4 table headers "сдвиг", "0 сут", "OR (ветер NW–W)".
    - "CAMS — прогноз/анализ приземной пыли, порог…" and "Open-Meteo Historical Forecast (склейка первых часов прогонов)…".
    - Gate keys shown raw: `pr_auc_beats_best_baseline`, `ece_le_0_05`, etc.
    - "Train/valid/test: train 2000–2015".
  - Sources:
    - "пиксели, не участвовавшие в обучении; эталон — ESA WorldCover 2021…".
    - "⚠ согласие модели со слабой разметкой…".
    - Class table rows "Потенциальный источник / Песок / Влажный солончак / Растительность / Вода".
    - KPI "F1 «источник»" and "блочная пространственная CV…".
  - Shield: the export error "401: нужен действующий ключ API (заголовок X-API-Key)".
  - Reports:
    - SMS preview "(SMS отправляется только при оранжевом и красном уровне)".
    - PNG figure titles and axes in Russian ("Модели против базовых линий…", "Глобальная важность признаков…").
    - Role options are bilingual ("Учитель / Мұғалім").
  - SHAP chart (Archive): raw feature ids (`wind_max`, `station_code`, …) in both languages, although the Risk tab already has `label_kk`/`label_ru`.
  - Leaflet controls: "Zoom in/Zoom out" (English).
- Suggested fix: The API should return `*_kk`/`*_ru` pairs (or keys) for notes, conclusions, meaning and error details. Move the literals into `i18n.ts`, map gate and feature ids to labels, and render the figures per language or add a localized caption.

### T09-6: Colour is not always duplicated by word and icon (spec 15.2)
- Severity (proposal): Medium (15.2, NFR-09)
- Location: RiskTab.tsx (map markers, card bars), ArchiveTab.tsx (Calendar)
- Evidence:
  - Map station markers have permanent tooltips with name and % only (for example "Арал 2%"), with no level word or icon.
  - Non-station settlements are coloured dots only.
  - Card horizon bars are colour-only (`aria-hidden`, value only in `title`).
  - Archive calendar: 365 colour-only cells; the legend uses plain colour squares without ●▲◆■; the container is `role="img" aria-label="2024"` with no other information.
- Expected vs actual: Each level should show colour + word + icon. Actually several views show colour only.
- Suggested fix: Add the icon to map tooltips and marker shapes. Draw the icon in calendar cells or use pattern/shape, use the LevelBadge icons in the legend, and give the calendar a text summary (for example "оранжевый+: N дней, подтверждено M").

### T09-7: Scenario result keeps the old language after switching ҚАЗ→РУС
- Severity (proposal): Low–Medium (NFR-10)
- Location: Приоритеты посадок; `components/ShieldTab.tsx` (`result.label` stored as a translated string)
- Evidence: Run "AralShield рейтингі бойынша" in ҚАЗ, then press РУС. The card reads "AralShield рейтингі бойынша: 1 200 га · Снижение пылевой нагрузки…" (mixed languages).
- Suggested fix: Store the plan id and translate it at render time.

### T09-8: Keyboard-only users cannot select planting cells or map points
- Severity (proposal): Medium (WCAG 2.1.1)
- Location: ShieldTab.tsx (`layer.on("click")`), RiskTab/BaseMap (`preferCanvas` CircleMarkers)
- Evidence: Tab order (30× Tab, logged via `focusin`) runs: tabs → ҚАЗ/РУС → map container → zoom ± → Leaflet link → horizon → 4 station cards → role select → API link. Focus outline is visible on all of them, which is good. No marker or cell is ever focusable, so "Картада таңдалғандар" (custom scenario) cannot be used without a mouse. There is also no skip-link past the map.
- Suggested fix: Add a keyboard alternative, such as a sortable top-N table with checkboxes, and a "skip map" link. Tabs use `role="tab"` without `tabpanel`/`aria-controls` or arrow-key support: complete the ARIA tabs pattern or drop the roles.

### T09-9: Missing or non-localized accessible names
- Severity (proposal): Low
- Location: common.tsx `Seg`, SourcesTab.tsx:103, ShieldTab.tsx:133, ReportsTab.tsx:38–41, 53
- Evidence:
  - Seg groups are named `horizon`, `layer`, `period`, `h` (English ids).
  - Reports selects have `aria-label="h"` and `"channel"`.
  - The Sources station `<select>` has no label at all (`aria-label` null, not inside a `<label>`).
  - The API-key input has only a placeholder.
  - Figures use `alt="pr_auc_models"` etc.
  - The scenario table's first `<th>` is empty.
- Suggested fix: Use `t()` keys for all aria-labels, add `<label>` elements, and write descriptive localized alt text.

### T09-10: Phone layout: oversized sticky header, hidden tabs, unusable calendar
- Severity (proposal): Medium (25.3 "карта рисков и архив должны работать на телефоне")
- Location: `styles.css` `@media (max-width:1000px)`, `.calendar`
- Evidence (375x812):
  - The sticky header is 208 px tall, about 26% of the screen.
  - The tab strip overflows (last buttons at right=391/478 px). "Есептер" is off-screen with only a thin scrollbar as a cue, and labels wrap to 2–3 lines.
  - Archive calendar cells are 3.9×3.9 px, and their `title` tooltips cannot be reached by touch.
  - On the risk map, the "Жосалы/Қызылорда" labels are cut off at the right edge.
  - There is no horizontal page scroll (scrollWidth = 375); tables sit inside `.t-scroll`.
- Suggested fix:
  - Make the header non-sticky, or collapse it on small screens, with the tabs in a single compact row or a select.
  - Show the calendar as a monthly list or a 12×31 grid with tap details on phone.
  - Fit map bounds to the stations.

### T09-11: Chart readability
- Severity (proposal): Low
- Location: Reports figures (`ReportsTab.tsx`), Archive replay/SHAP charts (`ArchiveTab.tsx`)
- Evidence:
  - Reports PNGs (naturalWidth 910–1300) are shown at 347 px. Axis and legend text becomes about 5 px, unreadable, and there is no click-to-enlarge.
  - The replay line uses curve smoothing: between 24% (04-01) and 25% (04-02) the curve rises above both points and suggests values that do not exist.
  - Observation markers at 100 are clipped at the top edge.
  - The y-axis has no unit title.
  - Decimal separators are inconsistent: comma on the SHAP axis ("0,05") vs point elsewhere ("0.079").
- Suggested fix: Open figures in a lightbox or link the full size, set `tension: 0`, use `clip: false` / y max of 105, add a "%" axis title, and use one number locale.

### T09-12: Minor UX inconsistencies
- Severity (proposal): Low
- Evidence:
  - (a) What-if is titled "1 000 га", but the result says "1 200 га" (cell granularity, not explained).
  - (b) SMS preview counter "56 / 70" counts the Russian placeholder note, not a real SMS.
  - (c) After a horizon switch the old message stays without a loading state: the preview briefly still showed "ертең … 2%" under "Бүрсігүні" until the fetch finished (`useAsync` keeps stale data).
  - (d) "Жүйе күйі" is raw JSON, not a readable status card.
  - (e) Export buttons without a key are simply disabled, with no inline hint; the hint is only in the heading.
- Suggested fix:
  - (a) Explain the rounding.
  - (b) Hide the counter when there is no SMS.
  - (c) Dim or replace the text while `loading`.
  - (d) Render the health status as localized status rows.

## Improvement suggestions
1. Add an automated a11y and i18n gate. Run axe-core (Playwright) on the 5 tabs in both languages, and add a test that fails when Cyrillic Russian-only words appear in `lang="kk"` render output. This covers T09-4/5/9.
2. Make data freshness a first-class UI element: a global banner driven by `/health.forecast_fresh`, with dates shown next to horizon labels (T09-1).
3. Build one `LevelMark` component (colour + icon + word) used by map markers, calendar, bars and legends, so spec 15.2 holds in every view (T09-6).
