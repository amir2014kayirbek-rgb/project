# QA tester #05: «Щит» (saxaul-planting priority)

Scope: `aralshield/shield/priority.py`, `data/shield/*` (read-only), `reports/shield_metrics.json`, API `/v1/priorities`, `POST /v1/scenarios`, `/v1/priorities/export`.
Method: read-only scratch scripts (recomputed `build_cells`/`wind_rose`/`_geometry` from cached rasters/ERA5, pyshp 3.1.6 read-back, KML parse), and FastAPI `TestClient` without lifespan for `/v1/scenarios` and `/v1/priorities`. The live server on :8000 returned 429 «слишком много запросов» because the per-IP limit is shared with other testers.

**Checked and OK (no finding):**
- Wind convention: `sector_of` is meteorological "from" (0°→N, 11.26°→NNE). `_geometry` (priority.py:94-100) takes the bearing from the settlement to the cell. Toy cells 50 km N/E/S/W of Aralsk gave sectors 0/4/8/12, so P_ij is the share of hours with wind blowing from the cell toward the settlement. Correct.
- Distance is in km (UTM 32641 m / 1000) and L=100 km. The population vector follows `SETTLEMENTS` order in both `exposure` and `scenario`.
- A manual loop over 3 cells (17618, 124, 6213) reproduces `priority()` exactly. The parquet `priority`, `S` and `M` match a fresh recomputation (max diff 0).
- The API `_shield_state` lon/lat→x,y round-trip error is ≤3e-9 m against `build_cells`, and the cell_id order is identical.
- Aggregation from 250 m to 2 km uses k=8. Masks: valid>0.8, water<0.5. The 106 robust cells are all inside q≥0.9.
- Scenario math S′=S·(1−cover·M) matches §14.3. API `plan=rating, area_ha=1000` gives 175.9176, the same as `shield_metrics.json`.
- GeoJSON rings are closed and CCW for all 2166 features (RFC 7946 OK). The KML parses and has 2166 Placemarks.

### T05-1: Shapefile polygons are written counter-clockwise, so every cell is a "hole"
- **Severity (proposal):** High (forestry GIS deliverable, §14.6)
- **Location:** priority.py:202-205 (`_cell_polygon` ring SW→SE→NE→NW) reused at priority.py:248 `w.poly([...])`
- **Evidence:** Reading `data/shield/priority_top.shp` back gives a positive shoelace signed area for shape 0 (+0.000477). `all(signed_area<0)` is False for the 2166 shapes. pyshp itself warns: "Shape was entirely made up of interior holes (defined by counter-clockwise orientation in the shapefile format)".
- **Expected vs actual:** The ESRI spec requires clockwise exterior rings. Here all rings are CCW, so ArcGIS and strict readers may treat the cells as holes, giving zero or negative area and broken rendering or selection.
- **Suggested fix:** Pass `ring[::-1]` to `w.poly` (or use pyshp's rewind helper). Keep CCW for GeoJSON and KML.

### T05-2: Shapefile has no .cpg, and the Shapefile/KML exports lack fields required by §14.6
- **Severity (proposal):** Medium
- **Location:** priority.py:243-253; api/main.py:362-366 zips only shp/shx/dbf/prj
- **Evidence:** `data/shield/priority_top.cpg` does not exist and DBF LDID byte 29 = 0. Reading the DBF as cp1251 turns `высокий` into `РІС‹СЃРѕРєРёР№`. Fields are only cell_id, priority, class, suitab and protects, each N/F/C with size=40 (dBase N/F widths over ~20 are non-standard; reader impact not verified).
- **Expected vs actual:** §14.6 asks for area, suitability, accessibility, protected settlements with their population, and contribution to reduction. The SHP and KML have no area_ha, protected_population, stability or source fields. Cyrillic is readable only if the user guesses UTF-8.
- **Suggested fix:** Write `priority_top.cpg` containing `UTF-8` and add it to the zip. Add area_ha/stab/prot_pop fields. Use widths of 10-19 for numeric fields.

### T05-3: `protected_population` is close to the whole regional population, or 0
- **Severity (proposal):** Medium (misleading in UI tooltip and exports)
- **Location:** priority.py:178
- **Evidence:** Total N = 509,500. The median `protected_population` over non-zero cells is 485,000, and the top value counts are 485000 (2662), 500500 (2136) and 506500 (1631). 4336 cells have 0, all because S==0 (E[i].max()=0 fails the strict `>`), even when M=1.0. The UI tooltip shows e.g. "protects: baikonur (455 000)" for cell 17618.
- **Expected vs actual:** The value should be the population a cell meaningfully protects. A 10%-of-max threshold over 12 settlements makes it nearly constant, so it has no information value.
- **Suggested fix:** Use the population of settlements that make up the top-k cumulative share (e.g. 80%) of E_i, or report Σ_j N_j·E_ij/ΣE_i ("effective protected people"). Document the definition.

### T05-4: Distant Kyzylorda/Baikonur dominate the index; no distance cut-off
- **Severity (proposal):** Medium (model design; spec §14.7 acknowledges heuristic)
- **Location:** priority.py:104-109 (no radius); config.py:105 `SOURCE_RADIUS_KM=300` is used only by the Eye module
- **Evidence:** Kyzylorda is 250–648 km from the cells (median 414) and carries 27% of total E; Baikonur (79–458 km) carries 24.7%. Together they hold 54.5% of E inside the top-200. `protects` = kyzylorda for 6672 of 21653 cells (31%). With 330,000·e^(−4.1), the far city outweighs the nearby small settlements (Akespe 0.3%, Zhalanash 1.3%).
- **Expected vs actual:** Exponential decay with L=100 km lets one large far city steer the rating. Settlements beyond typical transport range should be capped.
- **Suggested fix:** Apply `d_ij ≤ SOURCE_RADIUS_KM` (or a sensitivity row with and without a cap), and/or use log(N). Show per-settlement contribution in the report.

### T05-5: Suitability M is a relative rank; it is anti-correlated with S and lacks road access
- **Severity (proposal):** Medium
- **Location:** priority.py:72-77; spec §14.2 (M includes «доступность от дорог»; Kim et al. ≈15% suitable)
- **Evidence:** M>0.5 covers 58.1% of cells, against about 15% in Kim et al.; M is percentile ranks, so no absolute agronomic threshold exists. Spearman(M,S) = −0.76, because NDVI/NMDI drive both the "not a source" class and M. The top classes ("высокий"/"надёжный высокий") include cells with M=0.082 (median 0.43); the top-1 cell has M=0.486. There is no road-access term. Ranks are computed over the whole raster rectangle before the valid/water filter (line 86), so dropped cells shift the percentiles.
- **Expected vs actual:** Expected an absolute suitability mask (e.g. M=0 below an agronomic threshold) plus road access, as in the spec. Actually M is a soft rank multiplier, so poorly suitable cells reach the top classes.
- **Suggested fix:** Add a minimum M (e.g. ≥0.4) for "высокий", rank only valid cells, add an access term, and calibrate so the suitable share is about 15–25%.

### T05-6: The sensitivity metric (Kendall τ on the union of top-200s) is ill-posed; gate fails at 0.459
- **Severity (proposal):** Medium (gate from spec line 1417: «ранговая корреляция топ-участков … не ниже 0,7»)
- **Location:** priority.py:117-135, 195
- **Evidence:** Recomputing all 36 combos reproduces τ_union mean 0.459 and min 0.185 (L=50, vt=6, p=2). On the same combos, τ over all cells = 0.892, Spearman over all = 0.976 and top-200 overlap = 0.78. τ_union by suit_power is 0.45/0.62/0.31 (p=0.5/1/2) and by L is 0.35/0.67/0.47/0.35. vt has almost no effect (0.45–0.47). τ_union measures the ordering among about 240 near-tied cells, and the union size varies (200–269). The base combo itself is included (τ=1, overlap=1).
- **Expected vs actual:** The metric should reflect whether the top list is stable. τ_union mostly penalises reshuffling inside the top. Separately, spec "веса пригодности" is implemented as an exponent `suit_power`, while the weights 0.35/0.25/0.25/0.15 and `cover` are never varied.
- **Suggested fix:** Exclude the base combo. Report top-N overlap/Jaccard or rank-biased overlap, plus τ of the base top-N against their ranks in each scenario. Vary the M weights as the spec says. Re-state the gate on the chosen metric, and don't silently swap it.

### T05-7: The "1000 ha" comparison is really 1200 ha; the cell is 4× the spec size
- **Severity (proposal):** Low–Medium
- **Location:** priority.py:25 (`CELL_KM=2`), 159, 183; api/main.py:391; spec line 771 (1×1 km)
- **Evidence:** `comparison_1000ha.area_ha = 1200` (3 cells × 400 ha). API `plan=rating, area_ha=1` returns `area_ha 400`, and `area_ha=1000` returns 1200. The UI always sends 1000 and shows "1 200 га". Each cell is assumed to be fully planted (400 ha). Total reduction for 1000 ha is 0.037%.
- **Expected vs actual:** Expected the §14.3 "1 000 га" budget on 1 km (100 ha) cells, which gives exactly 10 cells. Actually the result is 20% over budget at a coarser grid.
- **Suggested fix:** Use 1 km cells, or support partial planting of the last cell (scale cover by area fraction). Rename the key to the real area.

### T05-8: The random baseline is weak, so the 33× advantage is overstated
- **Severity (proposal):** Low–Medium
- **Location:** priority.py:185-191; api/main.py:394-397
- **Evidence:** The pool `M>0.5` contains 31.9% cells with S==0 (zero effect by construction). by_rating = 175.9 people-eq vs random mean = 5.26. The API random plan is a single draw seeded by `int(area_ha)`, so repeated clicks give identical results (5.488 for 1000 ha twice).
- **Expected vs actual:** Expected a fair comparator (§14.3: "по текущему плану") showing a distribution. Actually it is a strawman that includes non-source cells.
- **Suggested fix:** Draw from cells with S above the median and M>0.5, report mean ± p5–p95, and add a "nearest-to-settlements" heuristic baseline.

### T05-9: `POST /v1/scenarios` silently accepts unknown or duplicate cell_ids
- **Severity (proposal):** Low
- **Location:** api/main.py:398-404; priority.py:148
- **Evidence:** `{"cell_ids":[999999,-5]}` returns 200 with `cells 0, area_ha 0, total_reduction_pct 0.0` and echoes the bad ids. `[17618,17618,17618]` returns `cells 1` but `cell_ids` has 3 entries.
- **Expected vs actual:** Expected 422 for ids not in the rating, and a de-duplicated echo.
- **Suggested fix:** Validate the ids against `cells.cell_id`, reject unknown ones, and return the unique ids that were actually used.

### T05-10: Export geometry is lat/lon-aligned boxes rather than the UTM cells; no country mask
- **Severity (proposal):** Low (the country part is speculation)
- **Location:** priority.py:208-212, 86; config.py:88 (AOI comment «казахстанская часть»)
- **Evidence:** Meridian convergence over the cells is −0.47° to −3.84°. The exported boxes overlap their E neighbours on average by 1.06 ha (max 2.1) and their N neighbours by 2.40 ha (max 3.4) of 400 ha. 8872 cells lie west of 60°E, outside UTM zone 41 (scale stays within 0.9993–1.0031, OK). No KZ border mask is applied: 5224 cells (818 of the 2166 exported) are south of 45.0°N. *Speculative:* some of these may be on the Uzbek side of the dry bed; this needs a border layer to confirm.
- **Suggested fix:** Export the true UTM square corners reprojected to WGS84, and intersect the cells with the KZ boundary (or flag the country).

## Improvement suggestions
1. Add a unit test for exports: shapefile ring orientation, the presence of `.cpg`, a GeoJSON/KML round-trip, and the required §14.6 fields.
2. Add a per-settlement contribution table and a distance cap to `shield_metrics.json`, so reviewers can see which settlements drive the rating.
3. Redefine the stability gate on top-N overlap/RBO (base combo excluded) and add the M-weight perturbations the spec asks for.
