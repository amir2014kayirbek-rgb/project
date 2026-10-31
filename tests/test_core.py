"""Модульные тесты: разбор сводок, признаки, метрики, тексты, API (раздел 26)."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from aralshield.forecast import metrics as M
from aralshield.forecast.features import add_persistence, daily_features, sector_of
from aralshield.truth import isd
from aralshield.voice import templates


# ---------------------------------------------------------------- «Правда»
def _isd_frame(rows):
    cols = ["DATE", "WND", "VIS", "MW1", "REM", "REPORT_TYPE"]
    return pd.DataFrame(rows, columns=cols)


def test_parse_isd_reads_wind_visibility_and_ww():
    df = _isd_frame([
        ["2018-05-27T00:00:00", "360,1,N,0120,1", "004000,1,9,9", "31,1", "", "FM-12"],
        ["2018-05-27T03:00:00", "340,1,N,0150,1", "999999,9,9,9", "", "SYN10435746 31596 83415 10088 20006 73133 8637/=", "FM-12"],
        ["2018-05-27T03:30:00", "340,1,N,0150,1", "004000,1,9,9", "", "", "FM-15"],  # не синоптический срок
    ])
    o = isd.parse_isd(df, "aralsk")
    assert len(o) == 2
    assert o.wind_speed.tolist() == [12.0, 15.0]
    assert o.visibility_m.iloc[0] == 4000 and np.isnan(o.visibility_m.iloc[1])
    assert o.ww.tolist() == [31.0, 31.0]  # второй — из группы 7wwW1W2 сводки


def test_ww_from_synop_ignores_section_333():
    rem = "SYN08235746 31695 30216 10133 21025 30093 40157 52031 83100 333 70233="
    assert np.isnan(isd._ww_from_synop(rem))


def _obs(times, ww, vis, wind):
    return pd.DataFrame({"time_utc": pd.to_datetime(times, utc=True), "ww": ww, "visibility_m": vis,
                         "wind_speed": wind, "station": "aralsk"})


def test_events_by_code_by_visibility_and_completeness():
    day1 = [f"2020-04-01T{h:02d}:00:00" for h in (0, 3, 6, 9, 12, 15, 18)]  # 7 сроков, UTC
    obs = pd.concat([
        _obs(day1, [np.nan, 7, np.nan, np.nan, np.nan, np.nan, np.nan], [20000] * 7, [5] * 7),
        _obs(["2020-04-03T06:00:00"], [np.nan], [3000], [9.0]),        # только видимость, ветер выше порога
        _obs(["2020-04-05T06:00:00"], [45.0], [500], [9.0]),            # туман — не пыль
    ])
    ev = isd.build_events(obs, save=False).set_index("date")
    assert ev.loc["2020-04-01", "dust"] == 1 and ev.loc["2020-04-01", "reliable"]
    assert ev.loc["2020-04-03", "dust"] == 1 and ev.loc["2020-04-03", "criterion"] == "vis"
    assert not ev.loc["2020-04-03", "reliable"]  # один срок — ненадёжная метка
    assert ev.loc["2020-04-05", "dust"] == 0
    assert ev.loc["2020-04-02", "n_terms"] == 0  # пропуск, а не «нет пыли»


def test_local_day_boundary_utc_plus_5():
    # 21:00 UTC 1 апреля = 02:00 местного 2 апреля
    obs = _obs(["2020-04-01T21:00:00"], [31.0], [2000], [12.0])
    ev = isd.build_events(obs, save=False)
    assert ev.date.iloc[0] == pd.Timestamp("2020-04-02")


# ---------------------------------------------------------------- признаки
def _hourly(days=20, wind=5.0, direction=45.0, precip=0.0):
    t = pd.date_range("2021-04-01", periods=24 * days, freq="h")
    return pd.DataFrame({"time_local": t, "wind_speed_10m": wind, "wind_direction_10m": direction,
                         "wind_gusts_10m": wind * 1.5, "precipitation": precip, "soil_moisture_0_to_7cm": 0.1,
                         "relative_humidity_2m": 30.0, "surface_pressure": 1010.0, "temperature_2m": 20.0,
                         "snow_depth": 0.0})


def test_sector_of_boundaries():
    assert sector_of(np.array([0, 11, 12, 349, 45, 180])).tolist() == [0, 0, 1, 0, 2, 8]


def test_daily_features_wind_threshold_and_upwind_index():
    h = _hourly(wind=12.0, direction=45.0)
    sec = np.zeros(16); sec[2] = 0.5  # источник только на северо-востоке
    d = daily_features(h, sec).dropna(subset=["wind_max"])
    row = d.iloc[5]
    assert row.hours_gt10 == 24 and row.hours_gt12 == 0
    assert row.upwind_index == pytest.approx(24 * 0.5 * (12 - 8) ** 3)
    # ветер с другой стороны — индекс нулевой
    d2 = daily_features(_hourly(wind=12.0, direction=225.0), sec).dropna(subset=["wind_max"])
    assert d2.iloc[5].upwind_index == 0


def test_dry_days_and_precip_lags_exclude_target_day():
    h = _hourly(days=12)
    h.loc[h.time_local.dt.day == 3, "precipitation"] = 1.0  # дождь 3 апреля
    d = daily_features(h, None).set_index("date")
    assert d.loc["2021-04-04", "precip_1"] == pytest.approx(24.0)
    assert d.loc["2021-04-04", "dry_days"] == 0
    assert d.loc["2021-04-07", "dry_days"] == 3
    assert d.loc["2021-04-03", "precip_1"] == 0  # день D не входит в «осадки накануне»


def test_persistence_uses_only_past_relative_to_issue():
    feat = pd.DataFrame({"date": pd.date_range("2021-04-01", periods=10)})
    ev = pd.Series([0, 0, 0, 0, 1, 0, 0, 0, 0, 0], index=pd.date_range("2021-04-01", periods=10), dtype=float)
    # h=1: прогноз на 7 апреля выпущен 6-го; известна пыль до 5-го включительно
    f1 = add_persistence(feat, ev, horizon=1).set_index("date")
    assert f1.loc["2021-04-07", "dust_lag"] == 1.0   # D-2 = 5 апреля
    assert f1.loc["2021-04-06", "dust_lag"] == 0.0   # D-2 = 4 апреля


# ---------------------------------------------------------------- метрики
def test_metrics_basic():
    o = np.array([0, 0, 1, 1])
    assert M.brier([0, 0, 1, 1], o) == 0
    assert M.bss([0.1, 0.1, 0.9, 0.9], o, [0.5] * 4) > 0
    c = M.contingency([0.9, 0.1, 0.9, 0.2], [1, 0, 0, 1], 0.5)
    assert (c["TP"], c["FP"], c["FN"]) == (1, 1, 1)
    assert c["POD"] == 0.5 and c["FAR"] == 0.5 and c["CSI"] == pytest.approx(1 / 3)
    assert M.ece([0.2] * 10, [0, 0, 0, 0, 0, 0, 0, 0, 1, 1]) == pytest.approx(0.0)


# ---------------------------------------------------------------- тексты
FC = {"station": "aralsk", "issue_date": "2027-04-12", "day": "2027-04-13", "probability": 0.71, "level": "orange",
      "wind_max": 15.2, "wind_dir_sector": "NE", "dry_days": 7, "source_sector": "NE", "source_km": 120, "degraded": 0}


@pytest.mark.parametrize("lang", ["ru", "kk"])
def test_message_schema(lang):
    m = templates.forecast_message(FC, "teacher", lang)
    assert "71%" in m and "kazhydromet" in m
    assert ("Аральск" in m) if lang == "ru" else ("Арал" in m)
    assert ("вторник" in m) if lang == "ru" else ("сейсенбі" in m)
    assert len(m.splitlines()) >= 5


@pytest.mark.parametrize("lang", ["ru", "kk"])
def test_sms_fits_one_cyrillic_segment(lang):
    for level in ("orange", "red"):
        for day in ("2027-04-12", "2027-04-13", "2027-04-14"):
            s = templates.sms_message(dict(FC, level=level, day=day), lang)
            assert s and len(s) <= 70, s
    assert templates.sms_message(dict(FC, level="yellow"), lang) is None


def test_green_message_is_short():
    m = templates.forecast_message(dict(FC, level="green", probability=0.08), "resident", "ru")
    # первая строка — всё сообщение; вторая — оговорка со ссылкой на Казгидромет (FR-06)
    assert m.count("\n") == 1 and "маловероятен" in m and "kazhydromet" in m.splitlines()[1]
