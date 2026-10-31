# QA tester 01: ML validity and data leakage (forecast module)

Scope: `aralshield/forecast/{features,dataset,train,metrics,predict}.py`, `reports/forecast_report.md`, `reports/forecast_metrics.json`.
Method: code review plus read-only recomputation from `data/features/dataset.parquet`, `models/forecast.joblib`, `data/raw/{era5,hist_forecast}` and `data/clean/dust_events.parquet`. Scratch scripts were kept outside the project. `pytest -q tests`: 22 passed.
**Reproduced exactly:** M2 test PR-AUC 0.587/0.582/0.575 (h0/h1/h2), BSS 0.379/0.373/0.371 and the h1 group thresholds all match the report.

**Checked, no problem found:** persistence lag = h+1 (`features.py:130`, the event on day t-1 is known at 06:00 on day t, and a unit test covers it); precipitation and dry-day features use `shift(1)` (`features.py:102-116`); there is a 7-day gap before the valid and test periods (`train.py:44-45`); Brier, BSS, POD, FAR, CSI and PR-AUC follow their standard definitions (`metrics.py`); the weekly block bootstrap pools all stations in the same week, which is correct for cross-station correlation; the H4 series are date-contiguous (9497 days, no duplicates), so positional `shift(-k)` is safe.

### T01-1: The "operational forecast" check is half reanalysis: for 2 of 4 stations the hist-forecast cache equals ERA5
Severity (proposal): **critical**
Location: `aralshield/forecast/train.py:230-267`, `aralshield/weather/openmeteo.py:101-108,133-141`, `reports/forecast_report.md` (section "Проверка на архиве оперативных прогнозов")
Evidence / reproduction: I merged `data/raw/hist_forecast/<st>_2022-01-01_2025-12-31.parquet` with `data/raw/era5/<st>_*.parquet` on `time_local` (35,064 h) and counted the hours where both files give the same value:
- kyzylorda and zhosaly: 100.0% of wind_speed_10m, wind_gusts_10m, temperature_2m, precipitation and soil_moisture hours are identical. The daily wind_max correlation is 1.00 and the bias is 0.00.
- aralsk and kazalinsk: only about 22% of wind hours are identical (wind_max bias -0.80 / -0.65 m/s, corr 0.91), but soil_moisture is 100% identical and precipitation is about 95% identical.

When I split `evaluate_operational` by station group (same model and rows, h0):
- aralsk + kazalinsk (real forecast data, n=2237, 252 events): ERA5 inputs give PR-AUC 0.602 / BSS 0.409; operational inputs give **0.552 / 0.364**.
- kyzylorda + zhosaly (ERA5 copy, n=2610): 0.594 / 0.380 vs 0.597 / 0.384, i.e. no change, as you would expect from identical inputs.

Expected vs actual: The reported "operational" PR-AUC 0.573 [0.510–0.624] and BSS 0.374 are meant to measure the cost of switching to forecasts. Actually about half of the rows use reanalysis, so the cost is understated roughly 2×: the real-data drop is -0.05 PR-AUC, not -0.02. Why the API returned ERA5-identical series is speculative (perhaps `best_match` with no `models` parameter falls back to a reanalysis-like source for those grid points).
Suggested fix: Pass an explicit `models=` (e.g. `ecmwf_ifs025,gfs_seamless`) to the historical-forecast request. Add a sanity assertion that operational and ERA5 series are not identical, then re-run. Until then, report the operational check only for the stations with genuine data.

### T01-2: h1/h2 skill is "perfect prognosis": weather features of the target day are ERA5 actuals at every horizon
Severity (proposal): **high**
Location: `aralshield/forecast/dataset.py:198-208`, `features.py:39-123`, report sections h1/h2
Evidence: `build()` computes `daily_features` once per station from ERA5 for day D. It reuses those features for h=0,1,2; only `dust_lag` and `dust_lag_7d` change. This shows up in the results:
- B0 and B2 metrics are identical across horizons (PR 0.166 / 0.329).
- M2 is almost flat: 0.587 → 0.582 → 0.575.
- The operational check (T01-1) covers only h0, and only about 0–24 h lead (a splice of the first hours of successive runs).

Spec 13.3 says: "для горизонтов 1–2 используются прогнозные, а не фактические значения погоды".
Expected vs actual: The report presents "завтра/послезавтра" PR-AUC 0.58 as forecast skill. It is really skill given perfect knowledge of the next day's weather. Real 24–72 h skill is unmeasured and certainly lower.
Suggested fix: Evaluate h1/h2 on archived runs at true lead time (e.g. Open-Meteo `previous_day1/2` variables or the ECMWF open-data archive). Until then, label the h1/h2 numbers in the report as an upper bound.

### T01-3: H3 gate reports "true" for h1 on a +0.003 point difference; the paired bootstrap shows no effect at any horizon
Severity (proposal): medium
Location: `train.py:479` (`H3_source_features_help` = point PR-AUC comparison), report "Ворота качества" h1
Evidence: Paired weekly block bootstrap of the M2 − M2-abl difference on the same test rows (n=300, `metrics.block_bootstrap`):

| Horizon | ΔPR-AUC CI | ΔBSS CI |
|---|---|---|
| h0 | [-0.009, +0.004] | [-0.009, +0.002] |
| h1 | [-0.004, +0.009] | [-0.003, +0.008] |
| h2 | [-0.012, +0.003] | [-0.006, +0.004] |

All intervals contain 0, and the point difference is negative for h0 and h2. Spec 13.6 requires PR-AUC **and** BSS comparisons with bootstrap intervals.
Expected vs actual: The honest conclusion is "H3 not confirmed". The gate shows true/false/false.
Suggested fix: Base the gate on a paired-bootstrap CI of the difference (lower bound > 0). State the negative result explicitly in the report.

### T01-4: BSS is inflated by a stale training climatology (base-rate drift 17% → 11%)
Severity (proposal): medium
Location: `train.py:50-60` (B0 fit on train only), `train.py:378,387`
Evidence: The event base rate is 0.172 in train, 0.101 in valid and 0.108 in test, but B0's mean prediction on test is 0.176. M2 gets a free recalibration from the isotonic fit on 2016–2019; B0 does not. M2 h1 BSS depends on the reference climatology:

| Reference climatology | M2 h1 BSS |
|---|---|
| Train (reported) | 0.373 |
| Train + valid | 0.353 |
| Valid only | 0.335 |
| In-sample test station × month | 0.307 |

B0's own ECE is 0.069, so it would fail the 0.05 calibration gate.
Expected vs actual: BSS > 0 still holds, but its size is overstated by about 0.02–0.07.
Suggested fix: Fit the climatology reference on train+valid, the latest pre-test years. Report BSS against a sample climatology as well.

### T01-5: Baseline B2 is weaker than necessary, which inflates the margin and the "CI separated" gate
Severity (proposal): medium
Location: `train.py:83-106` (WindRule), `train.py:466-468` (gate)
Evidence: The CSI search picked gust ≥ 12 m/s with **dry_days ≥ 0**, so the dryness half of the rule is degenerate. Its probability form has only 8 distinct values, which caps PR-AUC. Simple alternatives on the h1 test set, using the same information:
- raw `gust_max` as a score: PR-AUC 0.434;
- per-station isotonic(gust), fit on train: PR-AUC 0.442 / BSS 0.194.

The reported B2 is 0.329 / 0.143. Spec 23.2 compares against the best of **B0–B3**, but B3 (CAMS) is not in `quality_gates`.
Expected vs actual: M2 (0.582) still beats every baseline, so the conclusion holds. The margin and "best baseline" claims are overstated, and M1 (0.427) is the more informative comparator.
Suggested fix: Add a calibrated continuous wind baseline. Constrain N ≥ 1 or report that dryness adds nothing. Include B3 in the gate wherever it is available.

### T01-6: H4 "confirmed" is confounded by the shared seasonal cycle and serial dependence
Severity (proposal): medium
Location: `train.py:271-293`
Evidence: The Fisher test pools 9,200 autocorrelated days. When I stratified by year-month with a Mantel-Haenszel odds ratio:

| Lag | Naive OR | MH OR (year-month strata) |
|---|---|---|
| 0 | 2.37 | 1.87 |
| 1 | 2.04 | 1.56 |
| 2 | 1.45 | 1.03 |

The lag-2 association vanishes. The reported p-values (e.g. 1.2e-08 at lag 2) assume independent days. The lag-0 OR is the largest, which fits a common synoptic driver at least as well as transport from Aralsk.
Expected vs actual: The report says "H4 подтверждается: связь значима" for all lags. A defensible conclusion is that there is co-occurrence at lags 0–1 beyond seasonality, nothing at lag 2, and that transport is not proven.
Suggested fix: Use an MH or conditional-logistic test with season strata and a block-bootstrap or permutation p-value. Report the NW/W subset the same way.

### T01-7: The inhomogeneous-years filter uses test-year labels and is inconsistent
Severity (proposal): low
Location: `aralshield/forecast/dataset.py:169-192`
Evidence: The per-station median uses all years 2000–2025, including the test years, and removed **kazalinsk 2022** (5 events) from the test set. The kazalinsk counts by year are 2014:1, 2015:0, 2016:1, 2017:7, 2018:2, 2019:3, 2022:5. The years 2016, 2018 and 2022 were excluded, but 2017 (7 > 0.25×23) is kept between them. Aralsk falls from about 125 to 24–53 days per year and is never flagged.
I measured the impact by re-adding kazalinsk 2022 to the h1 test: PR-AUC 0.582 → 0.578, BSS 0.373 → 0.372. The effect is small, but this is selection on test outcomes.
Suggested fix: Compute the medians and thresholds from the training years only, or from metadata such as station automation dates. Flag whole regimes, not individual years.

### T01-8: The validation period is used four times, and thresholds tuned for h1 are applied to every horizon
Severity (proposal): low
Location: `train.py:138-145` (early stopping, grid choice and isotonic fit, all on `va`), `train.py:415`, `predict.py:198,226`
Evidence: The same 4,711 rows (476 events) drive early stopping, the 12-config grid choice, the isotonic calibration and the group thresholds. The thresholds are picked on in-sample calibrated valid predictions, from h1 only, and `level_for` uses them for h0 and h2 too. The red level is 0.58 for every group.
The test transfer is acceptable: school POD/FAR is 0.60/0.51 on valid vs 0.62/0.48 on test. Spec 13.3 also asks for leave-one-year-out CV inside training; it is not implemented, and a grid search is used instead of Optuna.
Suggested fix: Cross-fit the calibration (leave-one-year-out within train+valid). Choose thresholds per horizon on out-of-fold predictions.

### T01-9: The ECE ≤ 0.05 gate is weak for a rare event
Severity (proposal): low
Location: `metrics.py:242-252`, `train.py:476`
Evidence: There are 10 equal-width bins, and 79% of M2 test predictions fall in [0, 0.1). ECE is dominated by that one bin: 0.011 at h0.
Suggested fix: Also report quantile-binned ECE and the per-bin reliability with bootstrap bands, using the existing `reliability()` function.

### T01-10: The CAMS comparison (B3) is not like-for-like
Severity (proposal): low
Location: `train.py:197-226`, report "Сравнение с CAMS"
Evidence: M2 uses same-day ERA5 actuals (T01-2), while CAMS dust is an independent model product, isotonically recalibrated on only 2022-08..2023. "PR-AUC 0.599 vs 0.150" therefore compares hindsight weather with a forecast.
Suggested fix: Compare CAMS with M2 driven by operational inputs (after T01-1 is fixed), and state this caveat in the report.

### T01-11 (spec deviation): The Eye source map is static (2025) and is used for features back to 2000
Severity (proposal): low
Location: `dataset.py:193-197`, `predict.py:206` (`"sector_map": "eye-rf 2025"`)
Evidence: Spec 13.3 requires "Карты источников — только из прошлых месяцев" (source maps only from previous months). A map from a test year is applied to the 2000–2015 training rows. This is acknowledged in the report. It has no measurable effect, since the H3 delta is about 0 (T01-3), but it is formally future information.
Suggested fix: Rename the claim to "static directional weight" or build per-year composites.

## Improvement suggestions
1. Add an automated leakage and integrity test suite. It should assert that operational and ERA5 inputs differ, that per-horizon features differ beyond persistence once forecast inputs exist, that `inhomogeneous_years` uses train-only statistics, and that gates use paired CIs.
2. Rewrite the report claims: h1/h2 numbers are a perfect-prognosis upper bound; H3 is not confirmed; H4 holds at lags 0–1 after seasonal stratification, and not at lag 2; the operational cost is about -0.05 PR-AUC on the stations with genuine forecast data.
3. Use cross-fitted calibration and thresholds per horizon and per group, and add a calibrated continuous-wind baseline and B3 to the quality gates.
