"""Интеграционные тесты API на временной базе."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aralshield import db
from aralshield.api.main import app


@pytest.fixture(scope="module")
def client():
    db.init()
    db.save_forecasts([
        {"station": "aralsk", "issue_date": "2027-04-12", "horizon": h, "day": f"2027-04-1{2 + h}",
         "probability": p, "level": lv, "levels_json": {"public": lv, "school": lv, "health": lv, "akimat": lv},
         "drivers_json": [{"feature": "gust_max", "contribution": 0.8, "label_ru": "порывы", "label_kk": "екпін",
                           "direction": "up"}],
         "source_sector": "NE", "source_km": 120, "wind_max": 15, "wind_dir_sector": "NE", "dry_days": 7,
         "model_version": "test", "mode": "live"}
        for h, (p, lv) in enumerate([(0.18, "green"), (0.71, "orange"), (0.42, "yellow")])
    ])
    with TestClient(app, headers={"X-Internal-Key": "test-internal-key"}) as c:
        yield c


def test_forecast_shape(client):
    r = client.get("/v1/forecasts", params={"settlement": "aralsk"})
    assert r.status_code == 200
    j = r.json()
    assert [h["level"] for h in j["horizons"]] == ["green", "orange", "yellow"]
    assert j["horizons"][1]["drivers"] == ["gust_max"]
    assert "kazhydromet" in j["official_warning_url"]


def test_settlement_maps_to_station(client):
    j = client.get("/v1/forecasts", params={"settlement": "saksaulsky"}).json()
    assert j["settlement"]["id"] == "saksaulsky" and j["forecast_station"] == "aralsk"  # K5: эхо пункта
    assert client.get("/v1/forecasts", params={"settlement": "nowhere"}).status_code == 404


def test_preview_and_sms(client):
    t = client.get("/v1/messages/preview", params={"settlement": "aralsk", "horizon": 1, "lang": "kk"}).text
    assert "71%" in t and "Арал" in t
    s = client.get("/v1/messages/preview", params={"settlement": "aralsk", "horizon": 1, "channel": "sms"}).text
    assert len(s) <= 70


def test_protected_endpoints_need_key(client):
    assert client.get("/v1/events", params={"station": "aralsk", "from": "2020-01-01", "to": "2020-02-01"}).status_code == 401
    key = db.create_api_key("test")
    r = client.get("/v1/events", params={"station": "aralsk", "from": "2020-01-01", "to": "2020-02-01"},
                   headers={"X-API-Key": key})
    assert r.status_code == 200


def test_subscription_roundtrip_and_stop(client):
    body = {"chat_id": "42", "lang": "ru", "role": "teacher", "station": "aralsk", "min_level": "orange"}
    assert client.post("/v1/subscriptions", json=body).json()["ok"] is True
    sub = db.get_subscriber("telegram", "42")
    assert sub["station"] == "aralsk" and sub["role"] == "teacher"
    assert client.delete("/v1/subscriptions", params={"channel": "telegram", "chat_id": "42"}).json()["deleted"]
    assert db.get_subscriber("telegram", "42") is None


def test_internal_methods_reject_outsiders(client):
    body = {"chat_id": "7", "station": "aralsk"}
    assert client.post("/v1/subscriptions", json=body, headers={"X-Internal-Key": "wrong"}).status_code == 403


def test_input_validation(client):
    bad = {"chat_id": "1", "lang": "en", "station": "aralsk"}
    assert client.post("/v1/subscriptions", json=bad).status_code == 422
    assert client.get("/v1/accuracy", params={"period": "drop table"}).status_code == 422


def test_health(client):
    j = client.get("/v1/health").json()
    assert j["last_issue_date"] == "2027-04-12"


# ---------------------------------------------------------------- фиксер C: безопасность и контракт
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from aralshield.api import main as api_main  # noqa: E402


@pytest.mark.parametrize("url", ["/v1/reports/eye?tag=x/../../frontend/package",
                                 "/v1/reports/eye?tag=x%5C..%5C..%5Cfrontend%5Ctsconfig",
                                 "/v1/sources/preview?tag=/../../frontend/package-lock",
                                 "/v1/reports/eye?tag=2025/../../x"])
def test_path_traversal_rejected(client, url):
    assert client.get(url).status_code == 422


def test_maps_month_validated_and_404_without_path(client):
    key = db.create_api_key("maps-test")
    h = {"X-API-Key": key}
    assert client.get("/v1/sources/maps", params={"month": "x/../../frontend/package"}, headers=h).status_code == 422
    r = client.get("/v1/sources/maps", params={"month": "2031-06"}, headers=h)
    assert r.status_code == 404 and "/" not in r.json()["detail"].split("(")[0]
    r = client.get("/v1/reports/eye", params={"tag": "1999"})
    assert r.status_code == 404 and "eye_metrics" not in r.json()["detail"]


def test_forecast_freshness_fields_and_settlement_echo(client):
    j = client.get("/v1/forecasts", params={"settlement": "saksaulsky"}).json()
    assert j["settlement"]["name_ru"] == "Саксаульский" and j["forecast_station"] == "aralsk"
    assert j["issue_date"] == "2027-04-12" and j["issued_at"].endswith("+05:00")
    assert j["stale"] is False and isinstance(j["age_hours"], float)
    hz = j["horizons"][0]
    assert hz["past"] is False and hz["degraded_reasons"] == []
    assert client.get("/v1/forecasts", params={"settlement": ""}).status_code == 422


def test_preview_header_and_kk_placeholder(client):
    t = client.get("/v1/messages/preview", params={"settlement": "saksaulsky", "horizon": 1}).text
    assert t.startswith("🟠 Саксаульский (по станции Аральск)")
    s = client.get("/v1/messages/preview", params={"settlement": "aralsk", "horizon": 0, "channel": "sms",
                                                     "lang": "kk"}).text
    assert "SMS тек" in s
    r = client.get("/v1/messages/preview", params={"settlement": "kyzylorda", "lang": "kk"})
    assert r.status_code == 404 and r.json()["detail"] == "болжам жоқ"


def test_contract_validation(client):
    assert client.post("/v1/subscriptions", json={"channel": "email", "chat_id": "a@b.kz",
                                                   "station": "aralsk"}).status_code == 422
    r = client.post("/v1/subscriptions", json={"chat_id": "s1", "station": "baikonur"})
    assert r.json()["station"] == "zhosaly"
    assert client.get("/v1/accuracy", params={"period": "0d"}).status_code == 422
    assert client.get("/v1/forecasts/99999999999999999999999/explanation").status_code == 422
    assert client.get("/v1/priorities", params={"limit": 7001}).status_code == 422
    key = db.create_api_key("events-test")
    r = client.get("/v1/events", params={"station": "aralsk", "from": "2020-02-01", "to": "2020-01-01"},
                   headers={"X-API-Key": key})
    assert r.status_code == 400
    client.get("/v1/events", params={"station": "aralsk", "from": "2020-01-01", "to": "2020-02-01"},
               headers={"X-API-Key": key})
    assert db.rows("SELECT 1 FROM audit_log WHERE action='events'")


def test_feedback_validation(client):
    body = {"chat_id": "fb1", "station": "aralsk", "date": "2999-01-01", "answer": "yes"}
    assert client.post("/v1/feedback", json=body).status_code == 422
    body["date"] = db.local_today().isoformat()
    assert client.post("/v1/feedback", json=body).status_code == 422  # оповещения не было


def test_security_headers_hidden_internal_routes_and_host(client):
    r = client.get("/v1/stations")
    assert r.headers["X-Frame-Options"] == "DENY" and "frame-ancestors" in r.headers["Content-Security-Policy"]
    paths = client.get("/openapi.json").json()["paths"]
    assert "/v1/subscriptions" not in paths and "/v1/feedback" not in paths
    assert client.get("/v1/stations", headers={"Host": "rebind.attacker.example"}).status_code == 400


def test_rate_limit_has_retry_after(client, monkeypatch):
    monkeypatch.setattr(api_main, "RATE_LIMIT", 1)
    api_main._hits.clear()
    client.get("/v1/stations")
    r = client.get("/v1/stations")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1 and r.headers["X-Frame-Options"] == "DENY"
    api_main._hits.clear()


def test_scenarios_plans_and_unknown_ids(client, monkeypatch):
    """Мини-рейтинг из 30 ячеек: планы через plan_cells / scenario(last_fraction) Фиксера B."""
    from pyproj import Transformer

    from aralshield.config import EYE_CRS
    from aralshield.shield import priority as P
    rng = np.random.default_rng(1)
    lon, lat = 60.5 + rng.random(30), 45.5 + rng.random(30)
    x, y = Transformer.from_crs("EPSG:4326", EYE_CRS, always_xy=True).transform(lon, lat)
    cells = pd.DataFrame({"cell_id": np.arange(100, 130), "lon": lon, "lat": lat, "x": x, "y": y,
                          "S": rng.random(30), "M": 0.6 + 0.4 * rng.random(30), "priority": rng.random(30)})
    d, sec = P._geometry(cells)
    state = (cells, {8.0: np.full(16, 1 / 16)}, d, sec)
    monkeypatch.setattr(api_main, "_shield_mtime", lambda: -1.0)
    monkeypatch.setattr(api_main, "_shield_state", lambda mtime: state)
    api_main._plan_result.cache_clear()
    r = client.post("/v1/scenarios", json={"plan": "custom", "cell_ids": [100, 100, 101]}).json()
    assert r["cell_ids"] == [100, 101] and r["cells"] == 2
    assert client.post("/v1/scenarios", json={"plan": "custom", "cell_ids": [100, 999999]}).status_code == 422
    for plan in ("rating", "nearest", "random"):
        r = client.post("/v1/scenarios", json={"plan": plan, "area_ha": 1000}).json()
        assert r["plan"] == plan and abs(r["area_ha"] - 1000) < 1e-6
        assert len(set(r["cell_ids"])) == len(r["cell_ids"])
    assert {"mean", "p5", "p95"} <= set(r["distribution"])
    api_main._plan_result.cache_clear()


def test_accuracy_backtest_is_labelled_and_deterministic(client, cdb):
    from datetime import date, timedelta
    d0 = date(2024, 5, 1)
    items, ev = [], []
    for i in range(20):
        day = d0 + timedelta(days=i)
        lv = "orange" if i % 4 == 0 else "green"
        items.append({"station": "aralsk", "issue_date": (day - timedelta(days=1)).isoformat(), "horizon": 1,
                      "day": day.isoformat(), "probability": 0.6 if lv == "orange" else 0.1, "level": lv,
                      "levels_json": {"public": lv}, "mode": "backtest"})
        ev.append(("aralsk", day.isoformat(), int(i % 5 == 0), 0, "", "", None, None, 1))
    db.save_forecasts(items)
    with db.connect() as con:
        con.executemany("INSERT INTO dust_events VALUES (?,?,?,?,?,?,?,?,?)", ev)
    j = client.get("/v1/accuracy", params={"settlement": "aralsk", "period": "30d"}).json()
    assert j["mode"] == "backtest" and j["to"] == "2024-05-20" and j["n"] == 20
    assert j["orange_alerts"] == 5 and j["orange_confirmed"] == 1 and j["events"] == 4 and "Архив тестовых лет" in j["text_ru"]
    assert j["pr_auc"] is not None and j["as_of"] == db.local_today().isoformat()
    j = client.get("/v1/accuracy", params={"settlement": "aralsk", "period": "30d", "mode": "live"}).json()
    assert j["n"] == 0 and j["pr_auc"] is None and j["brier"] is None and j["orange_alerts"] == 0


def test_health_stale_and_strict(client, cdb):
    from datetime import timedelta
    for s in ("aralsk", "kazalinsk", "zhosaly", "kyzylorda"):
        cdb.issue(station=s)
    j = client.get("/v1/health").json()
    assert j["status"] == "ok" and j["stale"] is False
    cdb.issue(station="kyzylorda", issue_date=db.local_today() + timedelta(days=1),
              created_at="2020-01-01T00:00:00+00:00")
    j = client.get("/v1/health").json()
    assert j["status"] == "stale" and j["stale_stations"] == ["kyzylorda"]
    assert client.get("/v1/health", params={"strict": 1}).status_code == 503


def test_stale_forecast_flags(client, cdb):
    from datetime import timedelta
    cdb.issue(issue_date=db.local_today() - timedelta(days=1))
    j = client.get("/v1/forecasts", params={"settlement": "aralsk"}).json()
    assert j["stale"] is True and [h["past"] for h in j["horizons"]] == [True, False, False]
    assert "(выпуск" in client.get("/v1/summary").text


def test_forecast_import_requires_key_and_validates(client):
    item = {"station": "kazalinsk", "issue_date": "2027-05-01", "horizon": 0, "day": "2027-05-01",
            "probability": 0.4, "level": "yellow", "levels_json": {"public": "yellow"}, "mode": "live",
            "model_version": "test"}
    assert client.post("/v1/forecasts/import", json={"items": [item]}, headers={"X-Internal-Key": "wrong"}).status_code == 403
    assert client.post("/v1/forecasts/import", json={"items": [dict(item, probability=7)]}).status_code == 422
    assert client.post("/v1/forecasts/import", json={"items": [dict(item, station="x")]}).status_code == 422
    r = client.post("/v1/forecasts/import", json={"items": [item]})
    assert r.status_code == 200 and r.json()["saved"] == 1
    j = client.get("/v1/forecasts", params={"settlement": "kazalinsk"}).json()
    assert j["issue_date"] == "2027-05-01" and j["horizons"][0]["probability"] == 0.4
