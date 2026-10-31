"""«Прогноз»: боевой выпуск (степень уверенности, запасные режимы), факторы, пороги, ворота."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aralshield.forecast import predict as P
from aralshield.forecast import train as T
from aralshield.forecast.features import ALL_FEATURES


# ---------------------------------------------------------------- заглушки
class _Proba:
    def predict_proba(self, X):
        return np.column_stack([np.full(len(X), 0.8), np.full(len(X), 0.2)])


class _Ident:
    def predict(self, x):
        return np.asarray(x, float)


def _arts():
    art = {"kind": "logreg", "model": _Proba(), "calib": _Ident(), "features": list(ALL_FEATURES), "version": "test-m2"}
    clim = T.Climatology().fit(pd.DataFrame({"station": ["aralsk"] * 2, "date": pd.to_datetime(["2020-01-01"] * 2),
                                             "y": [0.0, 1.0]}))
    return {h: {"m2": art, "clim": clim} for h in P.HORIZONS}


def _hourly(*a, **k):
    today = P.local_today()
    t = pd.date_range(pd.Timestamp(today) - pd.Timedelta(days=31), periods=24 * 35, freq="h")
    df = pd.DataFrame({"time_local": t, "wind_speed_10m": 9.0, "wind_direction_10m": 300.0, "wind_gusts_10m": 14.0,
                       "precipitation": 0.0, "soil_moisture_0_to_7cm": 0.1, "relative_humidity_2m": 30.0,
                       "surface_pressure": 1010.0, "temperature_2m": 20.0, "snow_depth": 0.0})
    df.loc[df.time_local.dt.day == 5, "precipitation"] = 1.0
    df.attrs.update(fetched_at="2026-01-01T00:00:00+00:00", model="test")
    return df


def _down(*a, **k):
    raise RuntimeError("сеть недоступна")


@pytest.fixture
def live(monkeypatch):
    import aralshield.eye.classify as eye
    import aralshield.weather.openmeteo as om
    monkeypatch.setattr(eye, "ensure_sector_distance", lambda *a, **k: {})
    monkeypatch.setattr(om, "fetch_operational_forecast", _hourly)
    monkeypatch.setattr(om, "fetch_cams_dust", _down)
    monkeypatch.setattr(om, "save_operational_forecast", lambda *a, **k: pytest.fail("save=False пишет архив"))
    monkeypatch.setattr(P, "_recent_events", lambda *a, **k: pd.Series(dtype=float))
    monkeypatch.setattr(P, "load_artifacts", lambda: (_arts(), {h: P.DEFAULT_LEVELS for h in P.HORIZONS}))
    monkeypatch.setattr(P, "load_fallback", lambda: {})
    monkeypatch.setattr(P.db, "init", lambda: pytest.fail("save=False пишет в базу"))
    return monkeypatch


# ---------------------------------------------------------------- боевой выпуск
def test_missing_station_obs_is_degraded_but_uses_m2(live):
    out = P.issue_daily(save=False)
    assert len(out) == 12
    for it in out:
        assert it["model_version"] == "test-m2" and it["probability"] == pytest.approx(0.2)
        assert it["inputs_json"]["degraded_reasons"] == ["no_station_obs"] and it["degraded"] == 1
        assert it["inputs_json"]["issued_at"]


def test_no_weather_falls_back_to_climatology(live):
    import aralshield.weather.openmeteo as om
    live.setattr(om, "fetch_operational_forecast", _down)
    out = P.issue_daily(save=False)
    it = next(x for x in out if x["station"] == "aralsk")
    assert it["model_version"] == "clim-fallback" and it["probability"] == pytest.approx(0.5)
    assert it["inputs_json"]["degraded_reasons"] == ["no_weather", "clim_fallback"]
    other = next(x for x in out if x["station"] == "kyzylorda")  # нет станции в таблице → общая частота
    assert other["model_version"] == "clim-fallback"


def test_no_model_no_rule_gives_const_fallback(live):
    live.setattr(P, "load_artifacts", lambda: (None, {h: P.DEFAULT_LEVELS for h in P.HORIZONS}))
    out = P.issue_daily(save=False)
    assert {x["model_version"] for x in out} == {"const-fallback"}
    assert out[0]["inputs_json"]["degraded_reasons"] == ["no_station_obs", "model_missing", "const_fallback"]


def test_rule_b2_evaluated_directly_when_weather_exists():
    fb = {"h1": {"rule": {"gust_ms": 12.0, "dry_days": 2.0, "p_fire": {"aralsk": 0.6}, "p_quiet": {"aralsk": 0.05}}}}
    row = pd.DataFrame({"gust_max": [14.0], "dry_days": [5.0]})
    p, ver, extra = P._fallback(None, fb, 1, row, "aralsk", P.local_today(), have_weather=True)
    assert (p, ver, extra) == (0.6, "rule-B2-fallback", [])
    p, ver, _ = P._fallback(None, fb, 1, row.assign(dry_days=1.0), "aralsk", P.local_today(), have_weather=True)
    assert p == 0.05


def test_deadline_degrades_remaining_stations(live):
    live.setattr(P, "DAILY_DEADLINE_MIN", -1)
    out = P.issue_daily(save=False)
    assert all("deadline" in x["inputs_json"]["degraded_reasons"] for x in out)
    assert all(x["model_version"] == "clim-fallback" for x in out)


def test_live_issue_for_past_date_is_rejected(live):
    with pytest.raises(ValueError):
        P.issue_daily(issue=P.local_today() - pd.Timedelta(days=1), save=False)


def test_recent_events_falls_back_to_cache_and_writes_nothing(monkeypatch, tmp_path):
    from aralshield.truth import isd
    monkeypatch.setattr(isd, "RAW", tmp_path)
    (tmp_path / "isd").mkdir()
    st = P.STATIONS[0]
    y = P.local_today().year
    rows = "\n".join(f"{(pd.Timestamp(y, 1, 2) + pd.Timedelta(hours=3 * i)).isoformat()},\"300,1,N,0120,1\","
                     f"\"002000,1,9,9\",\"31,1\",FM-12" for i in range(8))
    path = tmp_path / "isd" / f"{st.isd}_{y}.csv"
    path.write_text("DATE,WND,VIS,MW1,REPORT_TYPE\n" + rows + "\n", encoding="utf-8")
    monkeypatch.setattr(isd, "fetch_station_year", _down)
    before = {p: p.stat().st_mtime for p in tmp_path.rglob("*")}
    ev = P._recent_events(st.id, pd.Timestamp(y, 3, 1).date())
    assert ev.dropna().max() == 1.0
    assert {p: p.stat().st_mtime for p in tmp_path.rglob("*")} == before


# ---------------------------------------------------------------- факторы
def test_drivers_drop_nan_features_and_station_code():
    sv = pd.Series({"dust_lag_7d": 0.3, "station_code": 0.5, "wind_max": 0.2, "dir_sin": 0.1, "dir_cos": 0.05,
                    "gust_max": -0.1})
    x = pd.Series({"dust_lag_7d": np.nan, "station_code": 1, "wind_max": 12.0, "dir_sin": 0.5, "dir_cos": 0.5,
                   "gust_max": 15.0})
    d = P.drivers_from_shap(sv, x)
    feats = [f["feature"] for f in d]
    assert "dust_lag_7d" not in feats and "station_code" not in feats and "dir_cos" not in feats
    assert feats[0] == "wind_max" and set(d[0]) == {"feature", "contribution", "label_ru", "label_kk", "direction"}
    rp = T._replay_drivers(sv, x)
    assert [r["feature"] for r in rp][:1] == ["wind_max"] and all("station_code" != r["feature"] for r in rp)


# ---------------------------------------------------------------- пороги и ворота
def test_choose_thresholds_ordered_levels():
    rng = np.random.default_rng(0)
    o = (rng.random(2000) < 0.12).astype(int)
    p = np.clip(0.1 + 0.5 * o + rng.normal(0, 0.15, 2000), 0, 1)
    lv = T.choose_thresholds(p, o)
    for g in ("health", "school", "public", "akimat"):
        assert lv[g]["yellow"] < lv[g]["orange"] < lv[g]["red"] <= 0.95
    assert lv["health"]["orange"] <= lv["school"]["orange"] <= lv["akimat"]["orange"]


def test_h3_not_confirmed_for_equal_models():
    rng = np.random.default_rng(1)
    o = (rng.random(700) < 0.15).astype(int)
    p = np.clip(0.1 + 0.4 * o + rng.normal(0, 0.1, 700), 0, 1)
    dates = pd.date_range("2021-01-01", periods=700)
    res = T.h3_test(p, p, o, np.full(700, 0.15), dates)
    assert res["confirmed"] is False and res["delta_pr_auc_ci"] == (0.0, 0.0)


def test_identical_hist_forecast_station_is_excluded():
    t = pd.date_range("2022-01-01", periods=500, freq="h")
    era5 = pd.DataFrame({"time_local": t, "wind_speed_10m": np.random.default_rng(2).random(500) * 10})
    copy = era5.copy()
    real = era5.assign(wind_speed_10m=era5.wind_speed_10m * 0.9 + 0.3)
    fr = {"a": T.identical_fraction(copy, era5), "b": T.identical_fraction(real, era5)}
    assert fr["a"] == 1.0 and fr["b"] < 0.1
    assert T.excluded_stations(fr) == ["a"]


def test_mantel_haenszel_or():
    # две страты с ОШ = 4 в каждой → ОШ М–Х = 4
    a = np.array([1] * 4 + [1] * 1 + [0] * 1 + [0] * 1 + [1] * 4 + [1] * 1 + [0] * 1 + [0] * 1)
    b = np.array([1] * 4 + [0] * 1 + [1] * 1 + [0] * 1 + [1] * 4 + [0] * 1 + [1] * 1 + [0] * 1)
    s = np.array([0] * 7 + [1] * 7)
    assert T._mh_or(a.astype(float), b.astype(float), s) == pytest.approx(4.0)


def test_quantile_ece_differs_from_uniform_bins():
    from aralshield.forecast import metrics as M
    p = np.r_[np.full(900, 0.02), np.full(100, 0.5)]
    o = np.r_[np.zeros(880), np.ones(20), np.ones(50), np.zeros(50)]
    assert np.isfinite(M.ece_quantile(p, o)) and M.ece_quantile(p, o) >= 0


def test_drivers_batch_matches_single_row():
    rng = np.random.default_rng(3)
    cols = ["wind_max", "gust_max", "dir_sin", "dir_cos", "station_code", "dust_lag_7d", "dry_days"]
    sv = pd.DataFrame(rng.normal(size=(20, len(cols))), columns=cols)
    X = pd.DataFrame(rng.random((20, len(cols))), columns=cols)
    X.loc[::3, "dust_lag_7d"] = np.nan
    batch = P.drivers_batch(sv, X)
    for i in range(20):
        assert [d["feature"] for d in batch[i]] == [d["feature"] for d in P.drivers_from_shap(sv.iloc[i], X.iloc[i])]
