# QA tester #08: frontend code review (React/Leaflet/Chart.js)

Scope: `frontend/src/**`, checked against live API at http://127.0.0.1:8000 (date 2026-10-27) and the built panel (`dist` is newer than `src`) in a browser.
Baseline checks that passed: `npx tsc -b --noEmit` exits 0. No console errors on any tab. All `useAsync` call sites list every captured variable in deps. StrictMode double effects are guarded by the `alive` flag. `localStorage` read and write are both in try/catch. Hash routing falls back to `risk`. Chart.js setup is correct per lazy chunk: react-chartjs-2 5.2 registers the Line/Bar/Radar controllers itself (`dist/index.js:161-170`), and scales and elements are registered in each chunk. Field names in `api.ts` match the real JSON for every field the UI reads.

### T08-1: Swipe ("шторка") clip is not applied on first load or after switching layer
- **Severity (proposal):** Major (the core interaction on the Sources tab)
- **Location:** `components/SourcesTab.tsx:15-31` (`SwipeClip` effect deps `[map, overlay, pct]`) and `:50-53`
- **Evidence:** I opened `/#sources`, waited 2.5 s and read the overlay `<img>` styles. With the slider at 50 %, `classes_2025.png` had `clipPath = ""` (no clip). After I moved the slider to 40 it became `inset(0px 0px 0px 149.4px)`. When I then clicked "Вероятность источника", the new `source_prob_2025.png` again had `clipPath = ""`.
- **Root cause:** `overlay` is a stable ref object. The effect runs at mount, before the async `layers` data has created the `<ImageOverlay>`, so `apply()` returns early. When `key={layer}` remounts the overlay, the effect does not re-run. A clip is applied only after a map move or a slider change.
- **Expected vs actual:** The model layer should be cut at the slider position at all times. Instead it covers the RGB snapshot completely until the user interacts.
- **Suggested fix:** Pass the layer/url into `SwipeClip` and add it to the deps, or call `apply` from the overlay's `load`/`add` event (`eventHandlers={{ add: apply }}`). Also listen to `resize`.

### T08-2: Risk detail shows the previous settlement's message and drivers under the new header
- **Severity (proposal):** Major (a resident message for the wrong settlement is displayed)
- **Location:** `components/RiskTab.tsx:73` (`<Detail>` has no `key`), `hooks.ts:10` (keeps `data` while loading), `RiskTab.tsx:150`
- **Evidence:** In the browser, Аральск was selected and I clicked the Жосалы card. 30 ms later the header read `Жосалы · 2026-10-28` but the `<pre>` read `🟢 Аральск, завтра (среда): …`. It corrected itself about 1.5 s later. The explanation drivers (`exp`) have the same stale behaviour, and so does the horizon switch.
- **Expected vs actual:** While loading, show "…" or a loading state. Actually, stale content from another settlement is shown with no loading indicator.
- **Suggested fix:** Use `<Detail key={selected.settlement.id} …>`, or have `useAsync` reset `data` to `null` when deps change. Render `msg.loading ? "…" : …`.

### T08-3: Shield map silently truncates cells (limit=6000); legend counts disagree with the map
- **Severity (proposal):** Minor/Major
- **Location:** `api.ts:164` (`limit: 6000`), `ShieldTab.tsx:89-93` (legend uses `report.class_counts`)
- **Evidence:** The `/v1/reports/shield` `class_counts` are надёжный высокий 106, высокий 2060, средний 4330, a total of 6496. `/v1/priorities?min_suitability=0&classes=…&limit=6000` returns 6000 features, of which средний is 3834, so 496 cells are missing. The legend in the browser shows "средний — 4330". The response is 2.7 MB raw (368 KB gzip) and took about 1.5 s.
- **Expected vs actual:** Either all cells are drawn, or the UI says it shows the top N. Actually, low-priority cells drop off without any notice.
- **Suggested fix:** Raise the limit to cover the 3 classes (≤ 20000 is allowed by the API), or display "показано N из M".

### T08-4: Selected cells survive a min-suitability change: hidden cells are counted and sent to the scenario
- **Severity (proposal):** Minor
- **Location:** `ShieldTab.tsx:22,95` (`setMinSuit` does not touch `selected`), `:38`, `:110`
- **Evidence (code):** Select cells, then raise the slider to 0.9 (only 35 features remain). `selected` still holds the filtered-out ids. The "Выбранные на карте (n)" button shows the old n, and `run("custom")` posts `cell_ids` that are not visible.
- **Suggested fix:** Intersect `selected` with the ids in `cells.data` on reload, or clear the selection when the filter changes.

### T08-5: GeoJSON layer is fully re-created on every cell click
- **Severity (proposal):** Minor (performance)
- **Location:** `ShieldTab.tsx:74` (`key={`${minSuit}-${selected.size}-${lang}`}`)
- **Evidence:** react-leaflet 4 `GeoJSON` already calls `layer.setStyle(props.style)` when `style` changes (`node_modules/react-leaflet/lib/GeoJSON.js:8-14`). The `style` memo depends on `selected`. Putting `selected.size` in the key tears down and rebuilds up to 6000 polygons and their tooltips on each click. On a slider change, `useAsync` keeps the old data while the key changes, so the layer is rebuilt twice (old data, then new data).
- **Suggested fix:** Drop `selected.size` from the key and keep `minSuit`/`lang`. Key on the loaded data identity rather than on `minSuit`.

### T08-6: Cell tooltip shows a raw settlement id ("защищает: baikonur")
- **Severity (proposal):** Minor (UX/i18n)
- **Location:** `ShieldTab.tsx:58-60`
- **Evidence:** In `/v1/priorities` the feature properties have `"protects": "baikonur"`, and the tooltip prints `${p.protects}` verbatim in both languages. Raw data strings are also concatenated into tooltip HTML without escaping. The data comes from the backend, so this is low risk.
- **Suggested fix:** Map the id to a name through `settlements.data` (`name_ru`/`name_kk`) and escape interpolated values.

### T08-7: Export download has no error handling for network failures and revokes the URL immediately
- **Severity (proposal):** Minor
- **Location:** `ShieldTab.tsx:44-54`
- **Evidence (code):** `fetch` sits outside try/catch, so a network error or server down produces an unhandled promise rejection and no message in the UI. `URL.revokeObjectURL(a.href)` runs synchronously after `a.click()`, which is known to cancel downloads in some browsers (older Firefox/Safari). The `<a>` is never attached to the DOM. There is no busy state, so double-clicks start duplicate downloads. I did not test with a real key; without a key the API returns 401 as expected.
- **Suggested fix:** Wrap the download in try/catch/finally with `setErr`. Revoke via `setTimeout(() => URL.revokeObjectURL(u), 0–1000)`. Append and remove the anchor, and disable the buttons while busy.

### T08-8: Superseded requests are never aborted; the slider fires a heavy request per step
- **Severity (proposal):** Minor
- **Location:** `hooks.ts:7-16` (no `AbortController`), `ShieldTab.tsx:95`
- **Evidence (code + API):** Each `onChange` step of the range input fires a 2.7 MB `/v1/priorities` request, and the old ones keep running. The server limits `/v1/` to `RATE_LIMIT=120` requests/min/IP (`api/main.py:52-64`). Each tab switch also unmounts the tab and re-fetches everything. I did not reproduce hitting a 429.
- **Suggested fix:** Pass an `AbortSignal` into `fn` and abort it in the effect cleanup. Debounce the slider, or apply it on `pointerup`/`change`.

### T08-9: Quality-gate and split rendering is hard to read
- **Severity (proposal):** Trivial/Minor
- **Location:** `ArchiveTab.tsx:151-157`
- **Evidence (browser):** The gates render as `"B2best_baseline"`, because a string value is concatenated to its key with no separator. The keys are raw English identifiers (`pr_auc_ci_separated` …) in both languages. The split line reads `train 2000–2015 (3853)`, where 3853 is `split[3]`, the event count, but it has no label, and `n = 22419` is not shown. The B2 rule renders `≥ 0 сухих дней` (the API returns `rule_B2.dry_days = 0`).
- **Suggested fix:** Add i18n labels for the gate keys, render `best_baseline: B2`, and label the counts ("событий").

### T08-10: Localization gaps (spec 25.4: "все тексты … в файлах переводов, а не в коде")
- **Severity (proposal):** Minor
- **Location:** many places. `grep` counts lines with Cyrillic in components: ArchiveTab 26, RiskTab 18, SourcesTab 16, ShieldTab 14.
- **Evidence:** Some strings are Russian-only in kk mode: `ArchiveTab.tsx:186` "H4: Аральск → Кызылорда", `:200` table header "сдвиг", `:204` "сут", `:76` calendar title "· пыль". `aria-label` values are English: "horizon", "period", "layer", "h", "channel". Backend `detail` errors are always Russian. The i18n key `not_built` is never used.
- **Suggested fix:** Move all of these into `i18n.ts` and localize the aria-labels.

### T08-11: Accessibility attributes are incomplete (spec 26.1: keyboard and labels)
- **Severity (proposal):** Minor
- **Location:** `App.tsx:70-74`, `ArchiveTab.tsx:71`, `ShieldTab.tsx:133`, `ReportsTab.tsx:47`, `SourcesTab.tsx:103`
- **Evidence:** The tabs have `role="tablist"`/`role="tab"`, but there is no `aria-controls`, no `role="tabpanel"` on `<main>`, and no arrow-key handling. The calendar is `role="img"` with `aria-label="2024"` only, so the 366 cells' information sits in mouse-only `title`. The API-key password input has only a placeholder and no label. Figure `alt` is the file id (`pr_auc_models`). The station `<select>` in SourcesTab has no label. The Leaflet map polygons and markers cannot be reached by keyboard; the cards cover the stations on the Risk tab, but nothing covers Shield cell selection.
- **Suggested fix:** Use the ARIA tabs pattern, give the calendar a text summary, and add a visible or `aria-label` label to the inputs and selects.

### T08-12: 16→8 wind sector mapping is asymmetric
- **Severity (proposal):** Trivial
- **Location:** `i18n.ts:96-104`
- **Evidence:** NNE becomes "северо-восток" but NNW becomes "север". SSE becomes "юг" but SSW becomes "юго-запад". ENE becomes "восток" but WNW becomes "северо-запад". In the live forecast, Аральск h1 `wind_dir_sector: "NE"` and h0 `"NNE"` both show as "северо-восток".
- **Suggested fix:** Use a consistent rule, or use the full 16-point names.

### T08-13: Interface drift and scenario area label
- **Severity (proposal):** Info/Minor
- **Location:** `api.ts`, `ShieldTab.tsx:105,117`
- **Evidence:** `HorizonReport.at_levels` is declared but the API sends `binary` (unused). These real fields are missing from the types: `Health.last_created_at`, `EyeReport.cv.confusion_matrix`, `ShieldReport.base_params` / `sensitivity.top_overlap_min`, `Explanation.inputs.sector_map`, `Accuracy.horizon/source`. None of these break rendering. Separately, the heading says "что если: 1 000 га", but the API rounds up to whole 400 ha cells (`ceil(1000/400)=3`), so the result shows "1 200 га" (`comparison_1000ha.area_ha = 1200`).
- **Suggested fix:** Sync the types with the API, or generate them from `/openapi.json`. Label the result as "≈1 200 га (3 ячейки)".

## Improvement suggestions
1. Replace `useAsync` with a small query cache (or add an AbortSignal plus reset-on-deps), so stale data (T08-2) and repeated fetches on tab switch (T08-8) disappear together.
2. Generate `api.ts` types from FastAPI `/openapi.json` (with Pydantic response models on the backend) so drift like T08-13 is caught by `tsc`.
3. Add a minimal Playwright smoke test per tab: swipe clip present, detail header and message agree, no console errors, plus axe-core for a11y. This would have caught T08-1, T08-2 and T08-11.
