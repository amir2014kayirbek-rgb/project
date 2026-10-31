"""REST API AralShield (раздел 24.2) и раздача веб-панели.

Открытые методы — агрегированные прогнозы и точность; методы «по ключу» требуют
заголовок X-API-Key; внутренние методы бота доступны только с localhost (без
ARALSHIELD_INTERNAL_KEY) или с внутренним ключом и скрыты из OpenAPI.
Параметры, из которых строятся пути к файлам (tag, month), проверяются по строгому
шаблону, а итоговый путь — на принадлежность каталогу отчётов/карт.
"""
from __future__ import annotations

import copy
import hmac
import json
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from aralshield import __version__, config, db
from aralshield.api import demo as demo_svc
from aralshield.api import verify
from aralshield.config import (DATA, EYE_CLASSES, KAZHYDROMET_URL, LOCAL_UTC_OFFSET_H, REPORTS, ROLE_GROUP, ROOT,
                               SETTLEMENTS, STATION_BY_ID, STATIONS)
from aralshield.voice import templates

DISCLAIMER = {"ru": "Информационный прогноз, не является официальным предупреждением",
              "kk": "Ақпараттық болжам, ресми ескерту емес"}
WEB = ROOT / "frontend" / "dist"  # сборка React + TypeScript (npm run build)
EYE_DIR = DATA / "eye"
SHIELD_DIR = DATA / "shield"
EYE_TAG = getattr(config, "EYE_TAG", "2025v2")  # контракт K1
TAG_RE = r"^\d{4}(v\d+)?$"            # метка карты «Глаза»: 2025, 2025v2
MONTH_RE = r"^\d{4}(-(0[1-9]|1[0-2]))?$"  # период: 2025 или 2025-06
TZ_LOCAL = timezone(timedelta(hours=LOCAL_UTC_OFFSET_H))
STALE_HOURS = 30          # /v1/health: выпуск старше — «stale»
PRIORITIES_MAX = 7000     # открытый слой рейтинга (контракт K6)
FID_MAX = 2 ** 63 - 1
INTERNAL = dict(include_in_schema=False)  # внутренние методы бота не публикуются в OpenAPI


@asynccontextmanager
async def lifespan(_app):
    db.init()
    yield


app = FastAPI(title="AralShield «Тұз дауылы» API", version=__version__, lifespan=lifespan,
              description="Вероятностный прогноз солёно-пылевых дней для пунктов Кызылординской области. "
                          + DISCLAIMER["ru"] + ".")
app.add_middleware(GZipMiddleware, minimum_size=1000)


def allowed_hosts() -> list[str]:
    """Защита от DNS-rebinding: только локальные имена и ARALSHIELD_ALLOWED_HOSTS (через запятую)."""
    extra = [h.strip() for h in os.environ.get("ARALSHIELD_ALLOWED_HOSTS", "").split(",") if h.strip()]
    return ["localhost", "127.0.0.1", "::1", "[::1]", "testserver", *extra]


app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts())


# ---------------------------------------------------------------- доступ
_hits: dict[str, deque] = {}
_last_sweep = [0.0]
RATE_LIMIT = int(os.environ.get("ARALSHIELD_RATE_LIMIT", "120"))  # запросов в минуту на IP
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    # базовая политика: панель нельзя встроить в чужой фрейм, плагины запрещены
    "Content-Security-Policy": "frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
}


def _secure(resp):
    for k, v in SECURITY_HEADERS.items():
        resp.headers.setdefault(k, v)
    return resp


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.url.path.startswith("/v1/"):
        ip = request.client.host if request.client else "?"
        now = time.monotonic()
        if now - _last_sweep[0] > 60:  # пустые и старые ключи удаляются, словарь не растёт бесконечно
            for k in [k for k, q in _hits.items() if not q or now - q[-1] > 60]:
                del _hits[k]
            _last_sweep[0] = now
        q = _hits.setdefault(ip, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            retry = max(1, int(60 - (now - q[0])) + 1)
            return _secure(JSONResponse({"detail": "слишком много запросов"}, status_code=429,
                                        headers={"Retry-After": str(retry)}))
        q.append(now)
    return _secure(await call_next(request))


def require_key(x_api_key: str | None = Header(default=None)) -> dict:
    who = db.check_api_key(x_api_key)
    if not who:
        raise HTTPException(401, "нужен действующий ключ API (заголовок X-API-Key)")
    return who


def require_internal(request: Request, x_internal_key: str | None = Header(default=None)) -> None:
    expected = os.environ.get("ARALSHIELD_INTERNAL_KEY")
    if expected:
        if x_internal_key and hmac.compare_digest(x_internal_key.encode(), expected.encode()):
            return
    elif request.client and request.client.host in ("127.0.0.1", "::1", "localhost"):
        return
    raise HTTPException(403, "внутренний метод")


# ---------------------------------------------------------------- справочники
@app.get("/v1/settlements")
def settlements():
    cov = {s.id for s in STATIONS}
    return [{"id": s.id, "name_ru": s.name_ru, "name_kk": s.name_kk, "lat": s.lat, "lon": s.lon,
             "population": s.population, "forecast_station": s.station, "has_station": s.id in cov}
            for s in SETTLEMENTS]


@app.get("/v1/stations")
def stations():
    return [{"id": s.id, "wmo": s.wmo, "name_ru": s.name_ru, "name_kk": s.name_kk, "lat": s.lat, "lon": s.lon,
             "district_ru": s.district_ru, "district_kk": s.district_kk} for s in STATIONS]


SETTLEMENT_BY_ID = {s.id: s for s in SETTLEMENTS}


def _station_for(settlement: str) -> str:
    if not settlement or not settlement.strip():
        raise HTTPException(422, "пустой параметр settlement")
    if settlement in STATION_BY_ID:
        return settlement
    s = SETTLEMENT_BY_ID.get(settlement)
    if not s or not s.station:
        raise HTTPException(404, "пункт не найден или не покрыт прогнозом")
    return s.station


def _settlement_obj(settlement: str) -> dict:
    """Запрошенный пункт (а не станция-заместитель), контракт K5."""
    s = SETTLEMENT_BY_ID.get(settlement) or STATION_BY_ID[settlement]
    return {"id": s.id, "name_kk": s.name_kk, "name_ru": s.name_ru, "lat": s.lat, "lon": s.lon}


def _place(settlement: str | None, station: str, lang: str) -> str:
    """Заголовок сообщения: «Байконур (по станции Жосалы)», если пункт без своей станции."""
    st_name = templates.place_name(station, lang)
    if not settlement or settlement == station or settlement not in SETTLEMENT_BY_ID:
        return st_name
    s = SETTLEMENT_BY_ID[settlement]
    return (f"{s.name_kk} ({st_name} станциясы бойынша)" if lang == "kk" else f"{s.name_ru} (по станции {st_name})")


# ---------------------------------------------------------------- свежесть выпуска (K5)
def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _inputs(r: dict) -> dict:
    try:
        v = json.loads(r.get("inputs_json") or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def _issued_at(r: dict) -> datetime:
    """Фактическое время выпуска: inputs_json.issued_at (A11), иначе created_at.
    Для архива (backtest) — номинальные 06:00 дня выпуска: created_at там — время пересборки архива."""
    if r.get("mode") != "backtest":
        t = _parse_ts(_inputs(r).get("issued_at")) or _parse_ts(r.get("created_at"))
        if t:
            return t.astimezone(TZ_LOCAL)
    return datetime.fromisoformat(f"{r['issue_date']}T06:00:00").replace(tzinfo=TZ_LOCAL)


REASON_CODES = ("no_weather", "no_station_obs", "model_missing", "clim_fallback", "const_fallback", "deadline")


def _degraded_reasons(r: dict) -> list[str]:
    """Коды причин сниженной уверенности (K2). Для старых выпусков без кодов — по текстовым заметкам."""
    inp = _inputs(r)
    rs = inp.get("degraded_reasons")
    if isinstance(rs, list):
        return [str(x) for x in rs]
    notes = " ".join(str(n) for n in (inp.get("notes") or []))
    out = []
    if "погоды" in notes:
        out.append("no_weather")
    if "наблюдений" in notes:
        out.append("no_station_obs")
    if r.get("degraded") and not out and "fallback" in (r.get("model_version") or ""):
        out.append("model_missing")
    return out


def _drivers_up(r: dict) -> list[str]:
    out = []
    for d in json.loads(r.get("drivers_json") or "[]"):
        c = d.get("contribution")
        if d.get("direction") == "up" and d.get("feature") != "station_code" and c is not None and c == c:
            out.append(d["feature"])
    return out


def _fc_out(r: dict, today: date) -> dict:
    out = {k: r[k] for k in ("id", "day", "horizon", "probability", "level", "source_sector", "source_km",
                             "wind_max", "gust_max", "wind_dir_sector", "dry_days", "cams_dust")}
    out["levels_by_group"] = json.loads(r["levels_json"] or "{}")
    out["drivers"] = _drivers_up(r)
    out["degraded"] = bool(r["degraded"])
    out["degraded_reasons"] = _degraded_reasons(r)
    out["past"] = date.fromisoformat(r["day"]) < today
    return out


def _freshness(r: dict, today: date) -> dict:
    t = _issued_at(r)
    return {"issue_date": r["issue_date"], "issued_at": t.isoformat(timespec="seconds"),
            "age_hours": round((datetime.now(timezone.utc) - t).total_seconds() / 3600, 1),
            "stale": date.fromisoformat(r["issue_date"]) < today}


# ---------------------------------------------------------------- прогнозы
@app.get("/v1/forecasts")
def forecasts(settlement: str | None = None, mode: Literal["live", "backtest"] = "live",
              issue_date: date | None = None):
    """Прогноз на 3 суток. Без settlement — по всем станциям.
    Каждый пункт несёт issue_date, issued_at, stale, age_hours; горизонты — past и degraded_reasons."""
    stations_ = [_station_for(settlement)] if settlement is not None else [s.id for s in STATIONS]
    today = db.local_today()
    res = []
    for st in stations_:
        if issue_date:
            rs = db.rows("SELECT * FROM forecasts WHERE station=? AND mode=? AND issue_date=? ORDER BY horizon",
                         (st, mode, issue_date.isoformat()))
        else:
            rs = db.latest_forecasts(st, mode)
        if not rs:
            continue
        res.append({"settlement": _settlement_obj(settlement if settlement is not None else st),
                    "forecast_station": st, "mode": mode, **_freshness(rs[0], today),
                    "model_version": rs[0]["model_version"],
                    "horizons": [_fc_out(r, today) for r in rs], "official_warning_url": KAZHYDROMET_URL,
                    "disclaimer": DISCLAIMER["ru"], "disclaimer_kk": DISCLAIMER["kk"]})
    if settlement is not None and not res:
        if issue_date:
            raise HTTPException(404, "прогноза за эту дату выпуска нет")
        raise HTTPException(404, "прогноз ещё не выпущен — запустите `python -m aralshield.cli daily`")
    return res[0] if settlement is not None else res


@app.get("/v1/forecasts/{fid}/explanation")
def explanation(fid: int = PathParam(ge=1, le=FID_MAX)):
    r = db.rows("SELECT * FROM forecasts WHERE id=?", (fid,))
    if not r:
        raise HTTPException(404, "прогноз не найден")
    r = r[0]
    t = _issued_at(r)
    return {"id": fid, "station": r["station"], "day": r["day"], "probability": r["probability"],
            "drivers": [d for d in json.loads(r["drivers_json"] or "[]") if d.get("feature") != "station_code"],
            "source_sector": r["source_sector"], "source_km": r["source_km"], "inputs": _inputs(r),
            "model_version": r["model_version"], "degraded": bool(r["degraded"]),
            "degraded_reasons": _degraded_reasons(r), "mode": r["mode"], "issue_date": r["issue_date"],
            "issued_at": t.isoformat(timespec="seconds")}


PREVIEW_404 = {"ru": "нет прогноза", "kk": "болжам жоқ"}


@app.get("/v1/messages/preview", response_class=PlainTextResponse)
def message_preview(settlement: str, horizon: int = Query(1, ge=0, le=2), role: str = "resident",
                    lang: Literal["ru", "kk"] = "ru", channel: Literal["telegram", "sms"] = "telegram",
                    mode: Literal["live", "backtest"] = "live", issue_date: date | None = None):
    st = _station_for(settlement)
    q = "SELECT * FROM forecasts WHERE station=? AND mode=? AND horizon=?"
    p: list = [st, mode, horizon]
    if issue_date:
        q += " AND issue_date=?"
        p.append(issue_date.isoformat())
    r = db.rows(q + " ORDER BY issue_date DESC LIMIT 1", p)
    if not r:
        raise HTTPException(404, PREVIEW_404[lang])
    fc = dict(r[0])
    fc["level"] = json.loads(fc["levels_json"] or "{}").get(ROLE_GROUP.get(role, "public"), fc["level"])
    if channel == "sms":
        return templates.sms_message(fc, lang, role=role) or templates.T[lang]["sms_none"]
    text = templates.forecast_message(fc, role, lang, place=_place(settlement, st, lang))
    if role == "teacher" and fc["level"] in ("orange", "red"):
        text += "\n\n" + templates.T[lang]["parents_head"] + "\n" + templates.parents_message(fc, lang)
    return text


@app.get("/v1/summary", response_class=PlainTextResponse)
def summary(lang: Literal["ru", "kk"] = "ru"):
    """Ежедневная сводка для акимата и ДЧС."""
    return templates.summary_message(db.latest_forecasts(), lang, verify.orange_hits(30))


@app.get("/v1/demo")
def demo():
    """Демо-событие (K8): из replay.event_date; прогноз архива, выпущенный накануне, горизонт 1."""
    ev, row = demo_svc.demo_forecast()
    if not ev:
        raise HTTPException(404, "демо-событие недоступно: нет отчёта прогноза (cli train)")
    return {**ev, "available": row is not None, "forecast_id": row["id"] if row else None,
            "settlement": _settlement_obj(ev["station"]),
            "label_ru": templates.demo_header(date.fromisoformat(ev["event_date"]), "ru"),
            "label_kk": templates.demo_header(date.fromisoformat(ev["event_date"]), "kk")}


# ---------------------------------------------------------------- события и точность
@app.get("/v1/events")
def events(station: str, date_from: date = Query(alias="from"), date_to: date = Query(alias="to"),
           who: dict = Depends(require_key)):
    if station not in STATION_BY_ID:
        raise HTTPException(404, "станция не найдена")
    if date_from > date_to:
        raise HTTPException(400, "дата «from» позже даты «to»")
    if (date_to - date_from).days > 3700:
        raise HTTPException(400, "не более 10 лет за запрос")
    db.audit(who["owner"], "events", f"{station} {date_from}..{date_to}")
    return db.rows("""SELECT date, dust, strong, criterion, ww_codes, vis_min_m, wind_max_obs, reliable
                      FROM dust_events WHERE station=? AND date BETWEEN ? AND ? ORDER BY date""",
                   (station, date_from.isoformat(), date_to.isoformat()))


@app.get("/v1/events/calendar")
def events_calendar(station: str, year: int = Query(ge=1990, le=2100),
                    mode: Literal["live", "backtest"] = "backtest"):
    """Открытый календарь «прогноз против факта» за год (агрегаты, без ключа)."""
    if station not in STATION_BY_ID:
        raise HTTPException(404, "станция не найдена")
    lo, hi = f"{year}-01-01", f"{year}-12-31"
    ev = db.rows("SELECT date, dust, strong, reliable FROM dust_events WHERE station=? AND date BETWEEN ? AND ?",
                 (station, lo, hi))
    fc = db.rows("""SELECT day, probability, level FROM forecasts WHERE station=? AND horizon=1
                    AND mode=? AND day BETWEEN ? AND ? ORDER BY day""", (station, mode, lo, hi))
    return {"events": ev, "forecasts_h1": fc, "mode": mode}


@app.get("/v1/accuracy")
def accuracy(settlement: str | None = None, period: str = Query("30d", pattern=r"^[1-9]\d{0,3}d$"),
             horizon: int = Query(1, ge=0, le=2), mode: Literal["auto", "live", "backtest"] = "auto"):
    """Статистика точности по проверенным прогнозам (FR-07). mode=auto: боевые выпуски,
    если за окно есть проверенные, иначе архив тестовых лет (поле mode и тексты это называют)."""
    st = _station_for(settlement) if settlement is not None else None
    out = verify.accuracy(st, int(period[:-1]), horizon, mode)
    out["settlement"] = settlement
    return out


# ---------------------------------------------------------------- отчёты моделей
@lru_cache(maxsize=8)
def _json_file(path: str, mtime: float):
    return json.loads(open(path, encoding="utf-8").read())


def _safe_file(base: Path, name: str) -> Path | None:
    """Файл name строго внутри base (без подкаталогов и выходов наверх) или None."""
    try:
        p = (base / name).resolve()
        if p.parent != base.resolve() or not p.is_file():
            return None
    except OSError:
        return None
    return p


def _report(name: str):
    p = _safe_file(REPORTS, name)
    if not p:
        raise HTTPException(404, "отчёт ещё не построен")
    return _json_file(str(p), p.stat().st_mtime)


def _tags(base: Path, pattern: str, requested: str | None, prefix: str | None = None) -> list[str]:
    """Кандидаты меток: запрошенная; иначе EYE_TAG, затем существующие (новые первыми)."""
    import re
    if requested:
        return [requested]
    found = []
    for p in sorted(base.glob(pattern.replace("{tag}", "*")), key=lambda x: -x.stat().st_mtime):
        tag = p.name[len(pattern.split("{tag}")[0]):-len(pattern.split("{tag}")[1])]
        if re.match(TAG_RE, tag):
            found.append(tag)
    out = [EYE_TAG] + [t for t in found if t != EYE_TAG]
    return [t for t in out if not prefix or t.startswith(prefix)]


@app.get("/v1/reports/forecast")
def report_forecast():
    return _report("forecast_metrics.json")


@app.get("/v1/reports/eye")
def report_eye(tag: str | None = Query(None, pattern=TAG_RE)):
    for t in _tags(REPORTS, "eye_metrics_{tag}.json", tag):
        p = _safe_file(REPORTS, f"eye_metrics_{t}.json")
        if p:
            return _json_file(str(p), p.stat().st_mtime)
    raise HTTPException(404, "отчёт «Глаза» ещё не построен")


@app.get("/v1/reports/shield")
def report_shield():
    return _report("shield_metrics.json")


# ---------------------------------------------------------------- карты источников
def _overlay_payload(tags: list[str]) -> dict | None:
    for tag in tags:
        meta = _safe_file(EYE_DIR, f"overlays_{tag}.json")
        if meta:
            bounds = json.loads(meta.read_text(encoding="utf-8"))
            return {"period": tag, "tag": tag,
                    "classes": {str(k): {"ru": v[0], "kk": v[1], "color": v[2]} for k, v in EYE_CLASSES.items()},
                    "layers": {name: {"url": f"/layers/{name}_{tag}.png", "bounds": b} for name, b in bounds.items()}}
    return None


@app.get("/v1/sources/maps")
def source_maps(month: str | None = Query(None, pattern=MONTH_RE), who: dict = Depends(require_key)):
    """Слои «Глаза» за период (PNG + границы). Сейчас доступен сезонный композит; помесячные
    карты (YYYY-MM) отдаются, только если построены, иначе 404 — месяц не подменяется годом."""
    if month and "-" in month:
        res = _overlay_payload([month])
        if not res:
            raise HTTPException(404, "помесячные карты не построены; доступен сезонный композит (month=YYYY)")
        return res
    res = _overlay_payload(_tags(EYE_DIR, "overlays_{tag}.json", None, prefix=month))
    if not res:
        raise HTTPException(404, "карта за этот период не построена")
    return res


@app.get("/v1/sources/preview")
def source_preview(tag: str | None = Query(None, pattern=TAG_RE)):
    """Открытый обзор: классы и границы (без выгрузки растров)."""
    res = _overlay_payload(_tags(EYE_DIR, "overlays_{tag}.json", tag))
    if not res:
        raise HTTPException(404, "карта не построена")
    return res


@app.get("/layers/{name}")
def layer(name: str):
    allowed = {p.name for p in EYE_DIR.glob("*.png")}
    if name not in allowed:
        raise HTTPException(404, "слой не найден")
    return FileResponse(EYE_DIR / name, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


# ---------------------------------------------------------------- приоритеты посадок
@lru_cache(maxsize=1)
def _shield_state(mtime: float):
    from aralshield.shield import priority as P
    cells = pd.read_parquet(SHIELD_DIR / "priority_cells.parquet")
    tr_cells = cells.copy()
    from pyproj import Transformer
    from aralshield.config import EYE_CRS
    t = Transformer.from_crs("EPSG:4326", EYE_CRS, always_xy=True)
    tr_cells["x"], tr_cells["y"] = t.transform(cells.lon.to_numpy(), cells.lat.to_numpy())
    rose = P.wind_rose()
    d, sec = P._geometry(tr_cells)
    return tr_cells, rose, d, sec


def _shield_mtime() -> float:
    p = SHIELD_DIR / "priority_cells.parquet"
    if not p.exists():
        raise HTTPException(404, "рейтинг посадок ещё не рассчитан (python -m aralshield.cli shield)")
    return p.stat().st_mtime


def shield_state():
    return _shield_state(_shield_mtime())


@lru_cache(maxsize=16)
def _priorities_bytes(mtime: float, classes: tuple[str, ...], min_suit: float, limit: int) -> bytes:
    """Готовый сериализованный GeoJSON: повторные запросы не строят слой заново (T06-2)."""
    from aralshield.shield.priority import to_geojson
    cells = _shield_state(mtime)[0]
    sub = cells[(cells.M >= min_suit) & cells["class"].isin(classes)].sort_values("priority", ascending=False)
    fc = to_geojson(sub.head(limit))
    fc["total_matching"] = int(len(sub))
    fc["returned"] = int(min(limit, len(sub)))
    return json.dumps(fc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@app.get("/v1/priorities")
def priorities(min_suitability: float = Query(0.0, ge=0, le=1), classes: str = "надёжный высокий,высокий",
               limit: int = Query(2000, ge=1, le=PRIORITIES_MAX)):
    """Рейтинг ячеек (GeoJSON). Открыт для просмотра; выгрузка файлов — по ключу.
    total_matching — сколько ячеек подходит под фильтр (показано не более limit)."""
    want = tuple(sorted({c.strip() for c in classes.split(",") if c.strip()}))[:8]
    body = _priorities_bytes(_shield_mtime(), want, round(min_suitability, 3), limit)
    return Response(body, media_type="application/json")


@app.get("/v1/priorities/export")
def priorities_export(fmt: Literal["geojson", "kml", "shp"] = "geojson", who: dict = Depends(require_key)):
    db.audit(who["owner"], "export_priorities", fmt)
    if fmt == "shp":
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for ext in ("shp", "shx", "dbf", "prj", "cpg"):  # .cpg — кодировка DBF (UTF-8)
                f = SHIELD_DIR / f"priority_top.{ext}"
                if f.exists():
                    z.write(f, f.name)
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": "attachment; filename=aralshield_priority_top.zip"})
    f = SHIELD_DIR / f"priority_top.{fmt}"
    if not f.exists():
        raise HTTPException(404, "файл не построен")
    return FileResponse(f, filename=f"aralshield_priority_top.{fmt}")


class ScenarioIn(BaseModel):
    cell_ids: list[int] = Field(default_factory=list, max_length=5000)
    plan: Literal["custom", "rating", "random", "nearest"] = "custom"
    area_ha: float = Field(1000, gt=0, le=200_000)
    L: float = Field(100, ge=20, le=400)
    cover: float = Field(0.6, gt=0, le=1)


@lru_cache(maxsize=32)
def _plan_result(mtime: float, plan: str, area_ha: float, L: float, cover: float) -> dict:
    """Сценарий по плану (rating / random / nearest) — кэшируется: random считает 50 розыгрышей."""
    from aralshield.shield import priority as P
    cells, rose, d, sec = _shield_state(mtime)
    vt = rose[P.BASE["vt"]]
    ids, last = P.plan_cells(cells, plan, area_ha, d, seed=0)
    res = P.scenario(cells, ids, vt, d, sec, L=L, cover=cover, last_fraction=last)
    if plan == "random":
        res["distribution"] = P.random_plan_distribution(cells, area_ha, vt, d, sec, L=L, cover=cover)
    return res


@app.post("/v1/scenarios")
def scenarios(body: ScenarioIn):
    """Сценарий «что если»: снижение пылевой нагрузки по пунктам.
    plan: custom (выбранные ячейки), rating (по рейтингу), random (случайные пригодные; distribution —
    mean/p5/p95 по 50 розыгрышам), nearest (ближайшие к пунктам — заместитель текущей практики).
    area_ha — точная площадь (последняя ячейка засаживается частично)."""
    from aralshield.shield import priority as P
    mtime = _shield_mtime()
    if body.plan == "custom":
        ids = list(dict.fromkeys(int(i) for i in body.cell_ids))
        if not ids:
            raise HTTPException(400, "выберите ячейки на карте")
        cells, rose, d, sec = _shield_state(mtime)
        known = set(cells.cell_id.astype(int).tolist())
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise HTTPException(422, f"ячейки не найдены в рейтинге: {len(unknown)} (например, {unknown[0]})")
        res = P.scenario(cells, ids, rose[P.BASE["vt"]], d, sec, L=body.L, cover=body.cover)
    else:
        res = copy.deepcopy(_plan_result(mtime, body.plan, round(body.area_ha, 3), body.L, body.cover))
    res["cell_ids"] = [int(i) for i in dict.fromkeys(res.get("cell_ids") or [])]
    res.pop("unknown_ids", None)
    res["plan"] = body.plan
    res["requested_area_ha"] = body.area_ha if body.plan != "custom" else None
    return res


# ---------------------------------------------------------------- подписки и обратная связь (бот)
ROLES = Literal["resident", "parent", "asthma", "teacher", "doctor", "farmer", "akimat", "dchs", "forestry"]


class SubscriptionIn(BaseModel):
    # e-mail-рассылки нет (W5): канал email отклоняется (422), а не уходит в Telegram
    channel: Literal["telegram", "sms"] = "telegram"
    chat_id: str = Field(min_length=1, max_length=64)
    station: str = Field(min_length=1, max_length=64, description="станция или населённый пункт")
    # None — оставить сохранённое значение (для нового подписчика — значение по умолчанию)
    lang: Literal["ru", "kk"] | None = None
    role: ROLES | None = None
    min_level: Literal["yellow", "orange", "red"] | None = None
    send_hour: int | None = Field(None, ge=0, le=23)


class ForecastImport(BaseModel):
    items: list[dict] = Field(min_length=1, max_length=200)


@app.post("/v1/forecasts/import", dependencies=[Depends(require_internal)], **INTERNAL)
def forecasts_import(body: ForecastImport):
    """Приём готового живого выпуска, посчитанного вне сервера (GitHub Actions).
    Нужен бесплатным хостингам, чьи общие IP-адреса Open-Meteo ограничивает (HTTP 429)."""
    for it in body.items:
        p = it.get("probability")
        if (it.get("station") not in STATION_BY_ID or it.get("mode", "live") != "live"
                or it.get("horizon") not in (0, 1, 2) or not isinstance(p, (int, float)) or not 0 <= p <= 1):
            raise HTTPException(422, "некорректная запись прогноза")
        try:
            date.fromisoformat(it["issue_date"]), date.fromisoformat(it["day"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(422, "некорректная дата прогноза")
    db.save_forecasts(body.items)
    return {"ok": True, "saved": len(body.items)}


@app.post("/v1/subscriptions", dependencies=[Depends(require_internal)], **INTERNAL)
def subscribe(body: SubscriptionIn):
    st = _station_for(body.station)
    sid = db.upsert_subscriber(body.channel, body.chat_id, body.lang, body.role)
    db.set_subscription(sid, st, body.min_level, body.send_hour)
    return {"ok": True, "station": st}


@app.delete("/v1/subscriptions", dependencies=[Depends(require_internal)], **INTERNAL)
def unsubscribe(channel: str, chat_id: str, station: str | None = None):
    """Без station — полная отписка с удалением данных; со station — только этот пункт."""
    if station:
        sub = db.get_subscriber(channel, chat_id)
        return {"deleted": bool(sub) and db.delete_subscription(sub["id"], _station_for(station))}
    return {"deleted": db.delete_subscriber(channel, chat_id)}


class FeedbackIn(BaseModel):
    channel: Literal["telegram", "sms"] = "telegram"
    chat_id: str = Field(min_length=1, max_length=64)
    station: str
    date: date
    answer: Literal["yes", "no", "unknown"]


@app.post("/v1/feedback", dependencies=[Depends(require_internal)], **INTERNAL)
def feedback(body: FeedbackIn):
    if body.station not in STATION_BY_ID:
        raise HTTPException(404, "станция не найдена")
    lo, hi = db.feedback_window()
    if not lo <= body.date <= hi:
        raise HTTPException(422, "дата ответа вне допустимого окна (последние 7 дней)")
    if not db.was_notified(body.channel, body.chat_id, body.station, body.date.isoformat()):
        raise HTTPException(422, "по этому пункту и дню оповещения не было")
    db.save_feedback(body.channel, body.chat_id, body.station, body.date.isoformat(), body.answer)
    return {"ok": True}


@app.get("/v1/feedback/stats")
def feedback_stats():
    return db.rows("""SELECT station, date, answer, COUNT(*) AS n FROM feedback
                      GROUP BY station, date, answer ORDER BY date DESC LIMIT 500""")


# ---------------------------------------------------------------- состояние
@app.get("/v1/health")
def health(strict: bool = False):
    """status: ok | stale (выпуск старше 30 ч или его нет) | degraded (упрощённый режим).
    strict=1 — HTTP 503 при любом статусе, кроме ok (для внешнего монитора)."""
    today = db.local_today()
    latest = db.latest_forecasts()
    by_st: dict[str, list[dict]] = {}
    for r in latest:
        by_st.setdefault(r["station"], []).append(r)
    ages = {st: (datetime.now(timezone.utc) - _issued_at(rs[0])).total_seconds() / 3600 for st, rs in by_st.items()}
    stale_st = sorted([st for st, a in ages.items() if a > STALE_HOURS] + [s.id for s in STATIONS if s.id not in by_st])
    degraded = sum(int(r["degraded"]) for r in latest if r["station"] not in stale_st)
    stale = bool(stale_st)
    status = "stale" if stale else ("degraded" if degraded else "ok")
    last = max(latest, key=lambda r: (r["issue_date"], r["created_at"] or "")) if latest else None
    out = {"status": status, "stale": stale, "version": __version__,
           "last_issue_date": last["issue_date"] if last else None,
           "last_created_at": last["created_at"] if last else None,
           "last_issued_at": _issued_at(last).isoformat(timespec="seconds") if last else None,
           "age_hours": round(min(ages.values()), 1) if ages else None, "stale_threshold_hours": STALE_HOURS,
           "stale_stations": stale_st, "forecast_fresh": bool(latest) and not stale,
           "issued_today": bool(last) and last["issue_date"] == today.isoformat(),
           "degraded_forecasts": degraded,
           "models": {"forecast": (ROOT / "models" / "forecast.joblib").exists(),
                      "eye": (ROOT / "models" / "eye_rf.joblib").exists(),
                      "shield": (SHIELD_DIR / "priority_cells.parquet").exists()}}
    if strict and status != "ok":
        return JSONResponse(out, status_code=503)
    return out


# ---------------------------------------------------------------- веб-панель
if (WEB / "assets").exists():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")


@app.get("/", include_in_schema=False)
def index():
    if not (WEB / "index.html").exists():
        return PlainTextResponse("Веб-панель не собрана: cd frontend && npm install && npm run build", status_code=503)
    # index.html не кэшируем: после пересборки панели браузер должен получить новые имена ресурсов
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/figures/{name}", include_in_schema=False)
def figure(name: str):
    allowed = {p.name for p in (REPORTS / "figures").glob("*.png")}
    if name not in allowed:
        raise HTTPException(404)
    return FileResponse(REPORTS / "figures" / name)
