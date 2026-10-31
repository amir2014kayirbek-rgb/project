"""«Правда»: разбор SYNOP обоих форматов REM, дубли сроков, критерий видимости, загрузка ISD."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import requests

from aralshield.truth import isd
from aralshield.weather import http


# ---------------------------------------------------------------- SYNOP
def test_aaxx_wind_group_is_not_read_as_ww():
    # Nddff = 73204 (N=7, 320°, 4 м/с) — не группа 7wwW1W2
    assert np.isnan(isd._ww_from_synop("SYN082AAXX 08061 35746 11596 73204"))
    # ix = 2: погода не передавалась
    assert np.isnan(isd._ww_from_synop("SYN076AAXX 03001 35849 42998 70704"))


def test_aaxx_weather_group_with_ix_1():
    rem = "SYN070AAXX  01061 35849 41/// 83502 11022 21031 40257 52010 74742 333 21065;"
    assert isd._ww_from_synop(rem) == 47.0
    ww, ix = isd._synop_info(rem)
    assert ix == 1.0


def test_aaxx_as_separate_token_and_new_layout():
    assert isd._ww_from_synop("SYN070    AAXX  13211 35746 41496 82302 11102 21102 40325 58007 77171 8602/;") == 71.0
    assert isd._ww_from_synop("SYN10435746 31596 83415 10088 20006 73133 8637/=") == 31.0
    assert np.isnan(isd._ww_from_synop("SYN05835746 429// 00403 10076 20040 30212 40278 52008 555 10007="))
    assert np.isnan(isd._ww_from_synop("SYN004BUFR"))


def test_00fff_group_skipped_and_ix7_ignored():
    # ff = 99 → следующая группа 00fff (00105 м/с не должна путаться ни с чем), затем 7-группа
    assert isd._ww_from_synop("SYN07035746 41596 83299 00105 10088 73133=") == 31.0
    # ix = 7: таблица 4680 (30–35 — туман), для кодового критерия не используется
    ww, ix = isd._synop_info("SYN07035746 47596 83415 10088 73133=")
    assert np.isnan(ww) and ix == 7.0


def _isd_frame(rows):
    return pd.DataFrame(rows, columns=["DATE", "WND", "VIS", "MW1", "REM", "REPORT_TYPE"])


def test_duplicate_term_prefers_fm12_over_fm15():
    rows = [["2015-05-01T00:00:00", "020,1,N,0040,1", "999999,9,9,9", "", "MET090METAR UAOO", "FM-15"],
            ["2015-05-01T00:00:00", "040,1,N,0030,1", "020000,1,9,9", "", "SYN06438062 425// 82202=", "FM-12"]]
    for order in (rows, rows[::-1]):
        o = isd.parse_isd(_isd_frame(order * 1), "kyzylorda")
        assert len(o) == 1
        assert o.report_type.iloc[0] == "FM-12" and o.visibility_m.iloc[0] == 20000 and o.wind_speed.iloc[0] == 3.0


# ---------------------------------------------------------------- критерий видимости
def _obs(times, ww, vis, wind, rtype="FM-12"):
    return pd.DataFrame({"time_utc": pd.to_datetime(times, utc=True), "ww": ww, "visibility_m": vis,
                         "wind_speed": wind, "station": "aralsk", "report_type": rtype, "ix": np.nan})


@pytest.mark.parametrize("ww", [38.0, 36.0, 10.0, 11.0, 28.0, 25.0, 45.0])
def test_visibility_rule_excludes_snow_mist_fog_precip(ww):
    ev = isd.build_events(_obs(["2020-02-03T06:00:00"], [ww], [3000], [11.0]), save=False)
    assert ev.dust.iloc[0] == 0


def test_visibility_rule_wind_strictly_above_threshold():
    ev = isd.build_events(_obs(["2020-04-03T06:00:00", "2020-04-04T06:00:00"], [np.nan, np.nan],
                               [3000, 3000], [6.0, 6.5]), save=False).set_index("date")
    assert ev.loc["2020-04-03", "dust"] == 0 and ev.loc["2020-04-04", "dust"] == 1


def test_n_terms_counts_only_synoptic_reports():
    times = [f"2020-04-01T{h:02d}:00:00" for h in (0, 3, 6, 9, 12, 15)]
    obs = pd.concat([_obs(times[:5], [np.nan] * 5, [20000] * 5, [3.0] * 5),
                     _obs(times[5:], [np.nan], [20000], [3.0], rtype="FM-15")])  # METAR-only срок
    ev = isd.build_events(obs, save=False)
    assert ev.n_terms.iloc[0] == 5 and not ev.reliable.iloc[0]


def test_completeness_has_ww_report_rate():
    times = [f"2020-04-01T{h:02d}:00:00" for h in (0, 3, 6, 9, 12, 15, 18)]
    ev = isd.build_events(_obs(times, [np.nan] * 6 + [5.0], [20000] * 7, [3.0] * 7), save=False)
    rep = isd.completeness_report(ev)
    assert rep.ww_report_rate.iloc[0] == pytest.approx(1 / 7, abs=1e-3)
    assert not rep.ww_reliable.iloc[0] or isd.WW_REPORT_MIN_RATE <= 1 / 7


# ---------------------------------------------------------------- загрузка ISD
class _Resp:
    def __init__(self, code, content=b"", headers=None):
        self.status_code, self.content, self.headers = code, content, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def test_404_writes_marker_not_empty_csv(monkeypatch, tmp_path):
    monkeypatch.setattr(isd, "RAW", tmp_path)
    monkeypatch.setattr(http.requests, "get", lambda *a, **k: _Resp(404))
    assert isd.download_station_year("X", 2026) is None
    assert (tmp_path / "isd" / "X_2026.404").exists()
    assert not (tmp_path / "isd" / "X_2026.csv").exists()
    # отметка свежая — повторного запроса нет
    monkeypatch.setattr(http.requests, "get", lambda *a, **k: pytest.fail("лишний запрос"))
    assert isd.download_station_year("X", 2026) is None


def test_forced_refresh_keeps_cache_when_network_down(monkeypatch, tmp_path):
    monkeypatch.setattr(isd, "RAW", tmp_path)
    (tmp_path / "isd").mkdir()
    (tmp_path / "isd" / "X_2020.csv").write_text("DATE,WND\n2020-01-01T00:00:00,\"0,1,N,0010,1\"\n", encoding="utf-8")
    monkeypatch.setattr(http.time, "sleep", lambda s: None)

    def down(*a, **k):
        raise requests.ConnectionError("нет сети")
    monkeypatch.setattr(http.requests, "get", down)
    df = isd.download_station_year("X", 2020, force=True)
    assert df is not None and len(df) == 1


def test_fetch_station_year_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(isd, "RAW", tmp_path)
    monkeypatch.setattr(http.requests, "get", lambda *a, **k: _Resp(200, b"DATE,WND\n2026-01-01T00:00:00,x\n"))
    assert len(isd.fetch_station_year("X", 2026)) == 1
    assert not any(tmp_path.rglob("*"))


# ---------------------------------------------------------------- политика повторов
def test_http_retry_policy(monkeypatch):
    sleeps, calls = [], []
    monkeypatch.setattr(http.time, "sleep", sleeps.append)

    def down(*a, **k):
        calls.append(k.get("timeout"))
        raise requests.ConnectionError("x")
    monkeypatch.setattr(http.requests, "get", down)
    with pytest.raises(http.HTTPGiveUp):
        http.get("http://e")
    assert len(calls) == 3 and calls[0] == (10, 60) and sleeps == [2, 4]  # без паузы после последней

    sleeps.clear(); calls.clear()
    monkeypatch.setattr(http.requests, "get", lambda *a, **k: calls.append(1) or _Resp(400))
    with pytest.raises(http.HTTPGiveUp):
        http.get("http://e")
    assert len(calls) == 1 and sleeps == []  # 4xx не повторяется

    seq = iter([_Resp(429, headers={"Retry-After": "7"}), _Resp(200, b"{}")])
    monkeypatch.setattr(http.requests, "get", lambda *a, **k: next(seq))
    assert http.get("http://e").status_code == 200 and sleeps == [7.0]
