# QA tester #02 — station ingestion and dust-day labels (`aralshield/truth/isd.py`)

Scope: NOAA ISD parsing (WND/VIS/MW1/OC1/REM), the SYNOP fallback, the dust-day rule (spec 10.2, Appendix Б), completeness (22.3), and the plausibility of the event counts.
Data checked (read-only): 108 raw files in `data/raw/isd/` (503,154 rows), `data/clean/observations.parquet` (287,043 terms), `dust_events.parquet`, `completeness.csv`.
Re-running `parse_isd` in memory on the raw files gives exactly the parquet (287,043 rows, 0 report-type differences), so the numbers below describe the shipped data.
`pytest -k "isd or truth or synop"`: 2 passed. Neither test covers the AAXX-format remarks described in T02-1.

### T02-1: SYNOP fallback reads the wind group `Nddff` as `7wwW1W2` in AAXX-format remarks, producing about 1,500 false dust days
Severity (proposal): **critical**
Location: `aralshield/truth/isd.py:73-83` (`groups[3:]` assumes the new remark layout)
Evidence / reproduction:
- Two REM layouts exist in the raw files:
  - New: `SYN06438062 42998 30403 ...` (136k rows). Here `groups[3:]` correctly skips `len+index`, `iRixhVV` and `Nddff`.
  - Old: `SYN082AAXX  08061 35746 11596 73204 ...` (148k rows, 2000-01-01 to 2013-04-30). The tokens are `AAXX, YYGGi, IIiii, iRixhVV, Nddff`, so `groups[3:]` starts at `iRixhVV`. When total cloud cover N=7, the `Nddff` group (`73204` = N7, 320°, 4 m/s) matches `^7(\d\d)…` and the wind direction becomes ww=32, a "dust storm".
- Agreement with MW1 where both exist: 84.3% for AAXX rows and 99.9% for new-format rows. With a format-aware parser, AAXX agreement rises to 96.9%.
- Of the 240k synoptic rows without MW1 (they fall back to REM), the current code yields a dust ww in **2,213** rows. A correct parser yields **10**, because almost all of these rows have ix=2 ("no weather group").
- Spot check, Kazalinsk 2000-01-03 (labelled `dust=1`, `criterion=code`, `ww_codes=7`):
  - Raw `SYN076AAXX 03001 35849 42998 70503 …` and `… 32998 70704 …`: ix=2, so no weather was reported.
  - VIS was 20 km and the wind 3–5 m/s, in January.
  - `70704` is N7/070°/4 m/s, which the code turned into ww=07.
- Impact, from recomputing in memory with `build_events(save=False)`:
  - **1,496 station-days flip from 1 to 0**: aralsk 419, kazalinsk 389, kyzylorda 410, zhosaly 278.
  - Mean dust days per year, 2000–2024, current → fixed: aralsk 80.4 → 63.7, **kazalinsk 19.8 → 4.2**, kyzylorda 61.1 → 44.7, zhosaly 41.6 → 30.4.
  - Example years at Kazalinsk: 2000 goes 21 → 0 and 2011 goes 40 → 2. The apparent drop at Kazalinsk from about 30/yr to about 1/yr after 2013 is an artefact of the remark format change, not real climate.
  - The fake codes include 33–35, so they also inflate `strong` and the `suspicious` (code with VIS > 10 km) flag: 658 days.

Expected vs actual: ww should come only from a real `7wwW1W2` group in section 1, when ix ∈ {1,4} (or 7, see T02-4). Instead it comes from `Nddff` in every AAXX report with N=7.
Suggested fix:
- Tokenise after `SYN`. If token 0 contains `AAXX`, skip `YYGGi IIiii`.
- Then read `iRixhVV` and take ix. Skip `Nddff` and an optional `00fff`.
- Scan for the 7-group only when ix allows it, and stop at `333/555/222`.
- Add AAXX regression tests: `…35746 11596 73204…` must give NaN, and `…41/// 83502 … 74742` must give 47.

### T02-2: Duplicate synoptic terms keep an arbitrary record; METAR (FM-15) replaces the SYNOP in 21% of Kyzylorda terms
Severity (proposal): **high**
Location: `aralshield/truth/isd.py:115` (`sort_values("time_utc")` uses the default quicksort, which is not stable, then `drop_duplicates(keep="first")`)
Evidence / reproduction:
- Kyzylorda (38062) has 33,585 synoptic terms with both an FM-12 and an FM-15 record. In 7,178 of them the FM-15 METAR was kept: 9,719 FM-15 rows in total, against 0 at the other stations.
- The kept METAR rows lose the dust signal:
  - Visibility for CAVOK is `999999`, so it parses as NaN: 22% of kept FM-15 rows.
  - ww is NaN in 87.8% of them, because METAR has no SYN group and its weather sits outside MW1.
  - The wind also differs, e.g. 2013-05-01 00Z: SYNOP `040,…,0030` (with VIS 20 km), METAR `020,…,0040`.
- Recomputed with FM-12 preferred (in memory), Kyzylorda dust days rise by 1–6 per year, e.g. 2015 46 → 51, 2017 51 → 57, 2023 47 → 52.
- The spec's "дубль удаляется, расхождение записывается в журнал" ("the duplicate is removed and the discrepancy is logged") is not implemented: no discrepancy is logged.

Expected vs actual: one deterministic record per term, preferring FM-12 > SY-MT > FM-16 > FM-15. Actually: whichever record quicksort happens to leave first.
Suggested fix: sort with `kind="stable"` on `(time_utc, report_type_priority)` and log the conflicts. Count only FM-12/SY-MT terms toward `n_terms` (T02-5).

### T02-3: The visibility criterion labels blowing snow and mist as dust
Severity (proposal): **high**
Location: `aralshield/truth/isd.py:140-141` (`no_fog_precip = ww.isna() | (ww < 40)`)
Evidence / reproduction:
- `ww < 40` still admits several non-dust codes:
  - 10 (mist), 11–12 (shallow fog)
  - 13–19
  - 20–29 (precipitation or fog in the past hour, including 28 "fog")
  - **36–39 (drifting/blowing snow)**
- Among the 3,363 terms that pass `by_vis`, there are ww=38: 128, 36: 33, 39: 18, 10: 350 and 28: 16.
- Of the 424 days labelled `criterion=="vis"` only:
  - 96 have every triggering term as blowing snow, e.g. aralsk 2000-02-03, ww=38, VIS 2–4 km, wind 10–12 m/s.
  - 251 have every triggering term as mist or fog (10/11/12/28).
  - Vis-only days are concentrated in winter: Dec 88, Jan 110, Feb 86, against Apr 16, May 6.
- Minor point: the spec says the wind must be "выше порога" (above the threshold), but the code uses `>=` 6 m/s, and the threshold has no reference.

Expected vs actual: per spec 10.2, low visibility should count only when there is no precipitation and no fog. Snow, mist and fog days are being counted.
Suggested fix:
- Make the visibility criterion a whitelist: `ww` is NaN or in {0–9 non-dust: 0–5}.
- Alternatively exclude {10–12, 20–29, 36–99}. Also check AA1 (precipitation amount) and temperature (e.g. TMP < 0 with snow cover) when ww is NaN.

### T02-4: The automatic-station code table (ix=7, wawa table 4680) is not distinguished
Severity (proposal): low (latent; the data currently has almost no ix=7)
Location: `aralshield/truth/isd.py:69-84`. AW1 is never read.
Evidence:
- In table 4680 (automatic stations, ix=7 / ISD `AW1`), wawa 30–35 means **fog**, not dust storm.
- Currently ix=7 occurs in about 3.6k AAXX rows per station only because of the misparse (in T02-1 the second token was not iRixhVV). With correct parsing, ix ∈ {1,2,A,…} and only 3 AW1 rows exist in the whole archive (`73`, `22`, `00`).
- MW2/MW3 add a dust code in only 3 rows, all non-synoptic FM-15, so ignoring them is acceptable.

Expected: if the station is automated (as the spec warns in 10.3), wawa must be mapped (4680 → 4677) before applying `DUST_WW`. Currently any future ix=7 or AW1 fog code 30–35 would become a "dust storm".
Suggested fix: honour ix, and map AW1/wawa through a 4680 → dust set: wawa 07, 08 (dust/sand raised); 27–29 only with sand; 06 = haze. Do not use 30–35.

### T02-5: Completeness counts terms that are not synoptic and records with no observations
Severity (proposal): medium
Location: `aralshield/truth/isd.py:113-115, 147, 159`
Evidence:
- `n_terms = g.size()` counts any row at a 3-hourly :00 time, including 2,541 Kyzylorda terms that exist **only** as FM-15 METAR (no SYNOP, usually no ww).
- It also counts rows whose VIS and ww are both NaN. At aralsk, VIS is NaN in 37.9% of kept terms and ww in 74%. Most ww NaN values are legitimate (ix=2).
- So `reliable` (≥ 6 of 8) means "6 timestamps", not "6 synoptic observations able to show dust". Reported completeness is ≥ 0.94 everywhere, even where the code criterion is effectively absent (T02-6).

Suggested fix: count a term only for FM-12/SY-MT records that have a valid SYN block, or VIS plus a present-weather indicator.

### T02-6: Kazalinsk labels have almost no information; event rates vs spec
Severity (proposal): medium (data finding that affects training and evaluation)
Evidence (after the T02-1 fix, 2000–2024 means):
- Storm days (ww 30–35, 98) per year: aralsk 25.3, zhosaly 18.4, kyzylorda 14.3, **kazalinsk 0.8**.
- All dust days per year: aralsk 63.7, which matches the spec's "≈60 суток на побережье" ("≈60 days on the coast"); kyzylorda 44.7, of which 37.8 days have ww 06–09 and 14.3 storm days (spec: 17–24 storm days).
- ww is reported at Kazalinsk on only 79 days per year: MW1 is present at 4–12% of its terms, and ix=1 at 6%, against 22% at Aralsk.
- The current labels (19.8/yr) at Kazalinsk come almost entirely from T02-1, so after the fix Kazalinsk becomes nearly all negatives.

Expected: a station or year without present-weather reporting should be flagged as unreliable for the code criterion. It should not become thousands of "no dust" negatives.
Suggested fix:
- Add a per-station/year "ww reporting rate" to `completeness.csv`.
- Exclude station-years below a threshold (e.g. < 10% of terms with ix=1) from training, or treat them as label-unknown.
- Reconsider whether to keep Kazalinsk in phase 1 or use Aralsk/Zhosaly as its proxy.

### T02-7: Downloads cache permanently empty, and the archive is truncated in 2025
Severity (proposal): low
Location: `aralshield/truth/isd.py:40-43, 52-53`
Evidence:
- All four `*_2026.csv` files are 0 bytes, written 2026-10-27 at 23:46, today, mid-2026. A 404 (or a transient S3 gap) writes `""`, and since the path then exists it is never retried without `force=True`.
- All 2025 files end at exactly `2025-08-24T21:00:00`, for every station. As a result:
  - 2025 rows in `completeness.csv` (dust_days 53/30/22/19) cover only about 236 days.
  - The calendar in `build_events` ends at the last observation, so the gap after Aug 24 is invisible, not shown as an explicit gap.
- `TEST_YEARS` includes 2025.

Suggested fix:
- Store a 404 marker with a timestamp and retry it after N days.
- Extend the calendar to Dec 31 of each requested year, so missing days show as explicit gaps.
- Mark 2025 as a partial year in the report and in the evaluation.

### Checked, no defect found
- Wind (WND): speed is in 0.1 m/s with missing value `9999`, direction missing `999`, calm `C` gives 0. VIS missing `999999`. Quality codes in raw: VIS {1,5,9}, WND {1,5,9,2×4}, with no erroneous codes 3 or 7.
- Gust (OC1): ÷10, missing `9999`.
- Range checks: wind 0–60 m/s, VIS 0–100 km.
- Local day: UTC+5, terms 21Z(prev)…18Z, giving 8 terms.
- The `DUST_WW` set matches Appendix Б; 98 is in Б but not in the text of 10.2. `STRONG_WW` = {33,34,35,98}.
- Section 333 is ignored, and the existing test covers this.
- Spot check aralsk 2000-04-09: MW1=31 at 06Z/09Z, VIS 4 km, wind 10 m/s, so `code+vis` is correct.

### Improvement suggestions
1. Add golden-file regression tests built from real REM strings of both layouts (AAXX and the new one) and from a mixed FM-12/FM-15 term. Assert per-station-year dust-day totals within a tolerance in CI.
2. Use the past-weather group (AY1 / W1W2 = 3, "dust storm in the past period"; 43,931 AY1 rows exist) as an extra code signal or a consistency check. This helps on days when the dust occurred between terms.
3. Write a per-term `ww_source` (`MW1` | `SYN` | `none`) and `ix` into `observations.parquet`, so label provenance can be audited and filtered downstream.
