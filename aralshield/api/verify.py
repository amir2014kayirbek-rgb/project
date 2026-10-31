"""Проверка прогнозов «прогноз против факта» (FR-07) — общий сервис для API, сводки и бота.

Модуль не импортирует FastAPI: бот берёт его без сборки веб-приложения.
Режимы не смешиваются: live — боевые выпуски, окно заканчивается сегодня;
backtest — архив тестовых лет, окно заканчивается последним проверенным днём,
и это прямо сказано в тексте («архив тестовых лет 2020–2025, …»).
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np

from aralshield import db
from aralshield.config import TEST_YEARS
from aralshield.voice.lang import date_text, kk_poss, plural_ru

ARCHIVE = f"{TEST_YEARS[0]}–{TEST_YEARS[-1]}"
_JOIN = """FROM forecasts f JOIN dust_events e ON e.station=f.station AND e.date=f.day
           WHERE f.horizon=? AND f.mode=? AND e.reliable=1 AND e.dust IS NOT NULL"""


def _where(station: str | None, horizon: int, mode: str) -> tuple[str, list]:
    q, p = _JOIN, [horizon, mode]
    if station:
        q += " AND f.station=?"
        p.append(station)
    return q, p


def last_verified_day(station: str | None, horizon: int, mode: str) -> date | None:
    q, p = _where(station, horizon, mode)
    d = db.rows(f"SELECT MAX(f.day) AS d {q}", p)[0]["d"]
    return date.fromisoformat(d) if d else None


def verification(station: str | None, horizon: int, mode: str, start: date, end: date) -> list[dict]:
    """Проверенные пары (прогноз, факт) за [start, end]; фильтры — в SQL, порядок детерминирован."""
    q, p = _where(station, horizon, mode)
    return db.rows(f"""SELECT f.station, f.day, f.probability, f.level, e.dust {q}
                       AND f.day BETWEEN ? AND ? ORDER BY f.station, f.day""", p + [start.isoformat(), end.isoformat()])


def _round(x: float, k: int):
    return None if x is None or not math.isfinite(x) else round(float(x), k)


def _brier(p: np.ndarray, o: np.ndarray) -> float | None:
    return float(np.mean((p - o) ** 2)) if len(o) else None


def _pr_auc(p: np.ndarray, o: np.ndarray) -> float | None:
    if len(o) == 0 or o.sum() == 0 or o.sum() == len(o):
        return None  # не определена без обоих классов
    from aralshield.forecast import metrics as M
    return float(M.pr_auc(p, o))


def texts(alerts: int, confirmed: int, events: int, caught: int, mode: str, days: int,
          start: date | None, end: date | None) -> dict:
    """Точность простыми словами (без POD/FAR), ru и kk."""
    if mode == "backtest" and start and end:
        pre_ru = f"Архив тестовых лет {ARCHIVE}, {date_text(start, 'ru', False)} {start.year} — {date_text(end, 'ru', False)} {end.year}: "
        pre_kk = f"{ARCHIVE} тест жылдарының мұрағаты, {start.year} ж. {date_text(start, 'kk', False)} — {end.year} ж. {date_text(end, 'kk', False)}: "
    else:
        pre_ru = f"За {days} {plural_ru(days, 'день', 'дня', 'дней')}: "
        pre_kk = f"Соңғы {days} күнде: "
    if alerts:
        ru = (f"{pre_ru}из {alerts} {plural_ru(alerts, 'оповещения', 'оповещений', 'оповещений')} оранжевого и красного "
              f"уровня {plural_ru(confirmed, 'подтвердилось', 'подтвердились', 'подтвердилось')} {confirmed}.")
        kk = (f"{pre_kk}қызғылт сары және қызыл деңгейлі {alerts} ескертудің {confirmed}-{kk_poss(confirmed)} расталды.")
    else:
        ru = f"{pre_ru}оповещений оранжевого и красного уровня не было."
        kk = f"{pre_kk}қызғылт сары және қызыл деңгейлі ескерту болған жоқ."
    if events:
        ru += (f" Пыльных дней: {events}, из них предупредили заранее: {caught}.")
        kk += (f" Шаңды күндер: {events}, оның {caught}-{kk_poss(caught)} алдын ала ескертілді.")
    return {"text_ru": ru, "text_kk": kk}


def accuracy(station: str | None = None, days: int = 30, horizon: int = 1, mode: str = "auto",
             end: date | None = None) -> dict:
    """Статистика точности за days суток. mode=auto: live, если за окно есть проверенные
    боевые прогнозы, иначе архив backtest (это отражается в полях mode и text_*)."""
    if days < 1:
        raise ValueError("period must be ≥ 1 day")
    today = db.local_today()
    if mode == "auto":
        lv = verification(station, horizon, "live", today - timedelta(days=days - 1), today)
        mode = "live" if lv else "backtest"
    if mode == "live":
        end = end or today
    else:
        end = end or last_verified_day(station, horizon, mode)
    out = {"period": f"{days}d", "mode": mode, "as_of": today.isoformat(), "horizon": horizon, "station": station,
           "from": None, "to": None, "n": 0, "events": 0, "orange_alerts": 0, "orange_confirmed": 0,
           "events_warned": 0, "POD": None, "FAR": None, "brier": None, "pr_auc": None}
    if end is None:
        out.update(texts(0, 0, 0, 0, mode, days, None, None))
        out["note"] = "недостаточно проверенных прогнозов за период"
        out["source"] = _source(mode)
        return out
    start = end - timedelta(days=days - 1)
    rs = verification(station, horizon, mode, start, end)
    out.update({"from": start.isoformat(), "to": end.isoformat(), "n": len(rs), "source": _source(mode)})
    o = np.array([int(r["dust"]) for r in rs], dtype=int)
    p = np.array([float(r["probability"]) for r in rs], dtype=float)
    alert = np.array([r["level"] in ("orange", "red") for r in rs], dtype=bool)
    tp = int((alert & (o == 1)).sum())
    out.update({"events": int(o.sum()), "orange_alerts": int(alert.sum()), "orange_confirmed": tp, "events_warned": tp,
                "POD": _round(tp / o.sum(), 3) if o.sum() else None,
                "FAR": _round(1 - tp / alert.sum(), 3) if alert.sum() else None,
                "brier": _round(_brier(p, o), 4), "pr_auc": _round(_pr_auc(p, o), 3)})
    out.update(texts(int(alert.sum()), tp, int(o.sum()), tp, mode, days, start, end))
    if not rs:
        out["note"] = "недостаточно проверенных прогнозов за период"
    elif not o.sum():
        out["note"] = "за период не было пыльных дней: PR-AUC не определена"
    return out


def _source(mode: str) -> str:
    if mode == "backtest":
        return f"архив тестовых лет {ARCHIVE}: прогнозы модели на годах, которых она не видела, против наблюдений NOAA ISD"
    return "боевые выпуски, проверенные наблюдениями станций"


def orange_hits(days: int = 30) -> dict | None:
    """Строка точности для сводки: оранжевый и красный уровни, все пункты, горизонт 1."""
    a = accuracy(None, days, 1, "auto")
    if not a["orange_alerts"]:
        return None
    return {"hit": a["orange_confirmed"], "n": a["orange_alerts"], "mode": a["mode"], "days": days,
            "from": a["from"], "to": a["to"]}
