"""Демо-событие для показа без зависимости от погоды (контракт K8).

Источник истины — replay.event_date в reports/forecast_metrics.json (дату не зашиваем:
после пересчёта «Правды» событие может смениться). Показывается прогноз архива
(mode=backtest), выпущенный накануне (issue_date = event_date − 1, горизонт 1),
для станции с наибольшей вероятностью среди станций replay.series, где в день события была пыль.
Модуль не импортирует FastAPI — его использует и бот (/demo).
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from aralshield import db
from aralshield.config import REPORTS, STATION_BY_ID


def demo_event(metrics: dict | None = None) -> dict | None:
    if metrics is None:
        p = REPORTS / "forecast_metrics.json"
        if not p.exists():
            return None
        try:
            metrics = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return None
    rp = metrics.get("replay") or {}
    ev = rp.get("event_date")
    if not ev:
        return None
    best = None
    for st, ser in (rp.get("series") or {}).items():
        if st not in STATION_BY_ID:
            continue
        for pt in ser.get("h1") or []:
            if pt.get("date") == ev and pt.get("y") == 1 and (best is None or pt.get("p", 0) > best[1]):
                best = (st, float(pt.get("p", 0)))
    if best is None:
        return None
    event = date.fromisoformat(ev)
    return {"event_date": ev, "issue_date": (event - timedelta(days=1)).isoformat(), "station": best[0],
            "horizon": 1, "mode": "backtest", "replay_p": best[1]}


def demo_forecast() -> tuple[dict | None, dict | None]:
    """(описание события, строка forecasts из архива или None, если архив не загружен)."""
    ev = demo_event()
    if not ev:
        return None, None
    r = db.rows("SELECT * FROM forecasts WHERE station=? AND issue_date=? AND horizon=? AND mode=?",
                (ev["station"], ev["issue_date"], ev["horizon"], ev["mode"]))
    return ev, (r[0] if r else None)
