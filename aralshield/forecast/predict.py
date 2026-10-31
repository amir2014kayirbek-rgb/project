"""Ежедневный выпуск прогноза (задачи J3–J7) и архив прогнозов тестовых лет.

Аварийный режим (раздел 21.2): если модели нет или прогноз погоды недоступен,
система переходит на правило B2 (при наличии погоды), климатологию или постоянную
вероятность и помечает прогноз как упрощённый. Причины сниженной уверенности
пишутся кодами в inputs_json.degraded_reasons (контракт K2):
  no_weather, model_missing — переход на запасной вариант;
  no_station_obs — нет свежих наблюдений станции, модель M2 всё равно используется;
  clim_fallback, const_fallback — какой запасной вариант использован;
  deadline — выпуск не уложился в срок, станция выпущена без загрузок.
"""
from __future__ import annotations

import inspect
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone

import joblib
import numpy as np
import pandas as pd

from aralshield import db
from aralshield.config import (DAILY_DEADLINE_MIN, DEFAULT_LEVELS, FALLBACK_CONST_P, FEATURES, LOCAL_UTC_OFFSET_H,
                               MODELS, SECTOR_NAMES, STATIONS, TEST_YEARS, YEARS)
from aralshield.forecast.dataset import STATION_CODE, load_sector_source, sector_source_tag
from aralshield.forecast.features import DEFAULT_VT, PERSISTENCE_FEATURES, add_persistence, daily_features, sector_of

log = logging.getLogger(__name__)
HORIZONS = (0, 1, 2)
LEGACY_VERSION = "forecast-lgbm-1.0.0"  # артефакты до 1.1.0 не хранили версию

REASON_CODES = ("no_weather", "no_station_obs", "model_missing", "clim_fallback", "const_fallback", "deadline")
FALLBACK_TRIGGERS = {"no_weather", "model_missing"}  # только они отключают M2

DRIVER_LABELS = {
    "wind_max": ("максимальный ветер", "ең күшті жел"), "wind_mean": ("средний ветер", "орташа жел"),
    "gust_max": ("порывы ветра", "жел екпіні"), "hours_gt8": ("часы с ветром > 8 м/с", "8 м/с-тан жоғары жел сағаттары"),
    "hours_gt10": ("часы с ветром > 10 м/с", "10 м/с-тан жоғары жел сағаттары"),
    "hours_gt12": ("часы с ветром > 12 м/с", "12 м/с-тан жоғары жел сағаттары"),
    "hours_gt14": ("часы с ветром > 14 м/с", "14 м/с-тан жоғары жел сағаттары"),
    "dir_sin": ("направление ветра", "жел бағыты"), "dir_cos": ("направление ветра", "жел бағыты"),
    "precip_d": ("осадки в этот день", "осы күнгі жауын-шашын"), "precip_1": ("осадки накануне", "кешегі жауын"),
    "precip_3": ("осадки за 3 дня", "3 күндегі жауын"), "precip_7": ("осадки за неделю", "аптадағы жауын"),
    "precip_14": ("осадки за 2 недели", "2 аптадағы жауын"), "dry_days": ("сухие дни подряд", "қатарынан құрғақ күндер"),
    "soil_moisture": ("влажность почвы", "топырақ ылғалдылығы"), "snow_depth": ("снежный покров", "қар жамылғысы"),
    "rh_min": ("минимальная влажность воздуха", "ауаның ең төмен ылғалдылығы"),
    "dp_24h": ("изменение давления", "қысымның өзгеруі"),
    "dt_max_24h": ("перепад температуры", "температураның өзгеруі"), "temp_max": ("температура", "температура"),
    "upwind_index": ("сухая корка в наветренном секторе", "жел жағындағы құрғақ қабық"),
    "upwind_source_frac": ("доля источника с наветренной стороны", "жел жағындағы шаң көзі үлесі"),
    "source_wind_hours": ("часы ветра со стороны источника", "шаң көзі жағынан соққан жел сағаттары"),
    "doy_sin": ("сезон", "маусым"), "doy_cos": ("сезон", "маусым"), "month": ("месяц", "ай"),
    "dust_lag": ("пыль накануне выпуска", "шығарылым алдындағы шаң"),
    "dust_lag_7d": ("пыльные дни за неделю", "аптадағы шаңды күндер"), "station_code": ("особенности пункта", "елді мекен ерекшелігі"),
}
# Не показываются пользователю как «причина»: не физический фактор (остаются в inputs_json)
HIDDEN_DRIVERS = {"station_code", "doy_cos"}


def local_today() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=LOCAL_UTC_OFFSET_H)).date()


def load_levels() -> dict[int, dict]:
    """Пороги уровней по горизонтам: thresholds_by_horizon.json (если есть), иначе общие thresholds.json."""
    by_h = MODELS / "thresholds_by_horizon.json"
    thr_path = MODELS / "thresholds.json"
    common = json.loads(thr_path.read_text(encoding="utf-8")) if thr_path.exists() else DEFAULT_LEVELS
    out = {h: common for h in HORIZONS}
    if by_h.exists():
        for k, v in json.loads(by_h.read_text(encoding="utf-8")).items():
            out[int(k.lstrip("h"))] = v
    return out


def load_artifacts():
    """(артефакты модели или None, пороги по горизонтам)."""
    path = MODELS / "forecast.joblib"
    arts = None
    if path.exists():
        try:
            arts = joblib.load(path)
        except Exception:  # несовместимая версия библиотек и т. п. — работаем в аварийном режиме
            log.exception("не удалось загрузить %s", path)
    return arts, load_levels()


def load_fallback() -> dict:
    """Параметры B2 и климатологии без joblib (models/fallback.json, пишет forecast.train)."""
    path = MODELS / "fallback.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def level_for(p: float, lv: dict) -> str:
    if p >= lv["red"]:
        return "red"
    if p >= lv["orange"]:
        return "orange"
    if p >= lv["yellow"]:
        return "yellow"
    return "green"


# ---------------------------------------------------------------- объяснения
def drivers_from_shap(sv: pd.Series, x: pd.Series, k: int = 4) -> list[dict]:
    """Главные факторы для пользователя (схема K3). Признаки со значением NaN и
    station_code исключаются — вклад «отсутствующего» признака не является причиной."""
    sv = sv.astype(float).copy()
    missing = {f for f in sv.index if f in x.index and pd.isna(x[f])}
    # sin/cos направления объединяем
    if "dir_sin" in sv and "dir_cos" in sv:
        sv["dir_sin"] = sv["dir_sin"] + sv["dir_cos"]
        sv = sv.drop("dir_cos")
    sv = sv.drop(labels=[c for c in sv.index if c in HIDDEN_DRIVERS or c in missing])
    top = sv.reindex(sv.abs().sort_values(ascending=False).index).head(k)
    return [{"feature": f, "contribution": round(float(v), 3), "label_ru": DRIVER_LABELS.get(f, (f, f))[0],
             "label_kk": DRIVER_LABELS.get(f, (f, f))[1], "direction": "up" if v > 0 else "down"} for f, v in top.items()]


def drivers_batch(sv: pd.DataFrame, X: pd.DataFrame, k: int = 4) -> list[list[dict]]:
    """То же, что drivers_from_shap, но для многих строк сразу (архив тестовых лет)."""
    sv = sv.astype(float).copy()
    nan = X.reindex(columns=sv.columns).isna().to_numpy()
    if "dir_sin" in sv and "dir_cos" in sv:
        sv["dir_sin"] = sv["dir_sin"] + sv["dir_cos"]
    cols = list(sv.columns)
    vals = sv.to_numpy()
    score = np.abs(vals)
    hide = np.array([c in HIDDEN_DRIVERS or c == "dir_cos" for c in cols])
    score[:, hide] = -1.0
    score[nan] = -1.0
    top = np.argsort(-score, axis=1, kind="stable")[:, :k]
    out = []
    for i, idx in enumerate(top):
        out.append([{"feature": cols[j], "contribution": round(float(vals[i, j]), 3),
                     "label_ru": DRIVER_LABELS.get(cols[j], (cols[j], cols[j]))[0],
                     "label_kk": DRIVER_LABELS.get(cols[j], (cols[j], cols[j]))[1],
                     "direction": "up" if vals[i, j] > 0 else "down"} for j in idx if score[i, j] >= 0])
    return out


def _drivers(art: dict, row: pd.DataFrame, k: int = 4) -> list[dict]:
    if art["kind"] != "lgbm":
        return []
    from aralshield.forecast.train import shap_values
    return drivers_from_shap(shap_values(art, row).iloc[0], row.iloc[0], k)


# ---------------------------------------------------------------- атрибуция источника
def daily_sources(hourly: pd.DataFrame | None, sec_frac, sec_dist) -> dict:
    """Сектор с наибольшим вкладом в наветренный индекс по суткам (атрибуция, раздел 14.1):
    {дата: (сектор, км)}."""
    if sec_frac is None or hourly is None or hourly.empty:
        return {}
    t = pd.to_datetime(hourly.time_local).dt.normalize().to_numpy()
    v, d = hourly.wind_speed_10m.to_numpy(float), hourly.wind_direction_10m.to_numpy(float)
    m = np.isfinite(v) & np.isfinite(d)
    s = sector_of(d[m])
    w = np.asarray(sec_frac, dtype=float)[s] * np.clip(v[m] - DEFAULT_VT, 0, None) ** 3
    tab = pd.DataFrame({"date": t[m], "s": s, "w": w}).groupby(["date", "s"])["w"].sum().unstack(fill_value=0.0)
    out = {}
    for day, row in tab.iterrows():
        if not np.isfinite(row.max()) or row.max() <= 0:
            continue
        si = int(row.idxmax())
        out[pd.Timestamp(day)] = (SECTOR_NAMES[si], sec_dist[si] if sec_dist else None)
    return out


def _source(hourly: pd.DataFrame, day: pd.Timestamp, sec_frac, sec_dist):
    """Сектор-источник одних суток (обёртка над daily_sources)."""
    return daily_sources(hourly, sec_frac, sec_dist).get(pd.Timestamp(day), (None, None))


def _row_meta(r: pd.Series) -> dict:
    ang = np.rad2deg(np.arctan2(r.get("dir_sin", np.nan), r.get("dir_cos", np.nan)))
    return {"wind_max": None if pd.isna(r.get("wind_max")) else round(float(r["wind_max"]), 1),
            "gust_max": None if pd.isna(r.get("gust_max")) else round(float(r["gust_max"]), 1),
            "dry_days": None if pd.isna(r.get("dry_days")) else float(r["dry_days"]),
            "wind_dir_sector": None if np.isnan(ang) else SECTOR_NAMES[int(sector_of(np.array([ang % 360]))[0])]}


# ---------------------------------------------------------------- аварийный режим
def _rule_params(arts, fb: dict, h: int) -> dict | None:
    if arts and h in arts and "rule" in arts[h]:
        r = arts[h]["rule"]
        return {"gust_ms": float(r.g), "dry_days": float(r.n), "p_fire": r.p_fire.to_dict(),
                "p_quiet": r.p_quiet.to_dict()}
    return (fb.get(f"h{h}") or {}).get("rule")


def _clim_prob(arts, fb: dict, h: int, station: str, day: date) -> float | None:
    if arts and h in arts and "clim" in arts[h]:
        return float(arts[h]["clim"].predict(pd.DataFrame({"station": [station], "date": [pd.Timestamp(day)]}))[0])
    c = (fb.get(f"h{h}") or {}).get("clim")
    if c:
        return float(c["table"].get(f"{station}|{pd.Timestamp(day).month}", c["overall"]))
    return None


def _fallback(arts, fb: dict, h: int, row: pd.DataFrame, station: str, day: date,
              have_weather: bool) -> tuple[float, str, list[str]]:
    """Запасная вероятность: B2 (если есть погода) → климатология → постоянная.
    Возвращает (p, версия, доп. коды причин)."""
    rule = _rule_params(arts, fb, h) if have_weather and not row.empty else None
    if rule:
        g, dd = row.iloc[0].get("gust_max", np.nan), row.iloc[0].get("dry_days", np.nan)
        if pd.notna(g) and (pd.notna(dd) or rule["dry_days"] <= 0):
            fire = g >= rule["gust_ms"] and (rule["dry_days"] <= 0 or dd >= rule["dry_days"])
            tab = rule["p_fire"] if fire else rule["p_quiet"]
            p = tab.get(station, float(np.mean(list(tab.values()))) if tab else np.nan)
            if pd.notna(p):
                return float(p), "rule-B2-fallback", []
        log.warning("%s h%d: нет входов для правила B2 (порыв/сухие дни) — климатология", station, h)
    try:
        p = _clim_prob(arts, fb, h, station, day)
    except (KeyError, ValueError, AttributeError) as e:
        log.warning("%s h%d: климатология недоступна: %s", station, h, e)
        p = None
    if p is not None and np.isfinite(p):
        return p, "clim-fallback", ["clim_fallback"]
    return FALLBACK_CONST_P, "const-fallback", ["const_fallback"]


def _fallback_prob(arts, h, row: pd.DataFrame, station: str, day: date) -> float:
    """Совместимость: только вероятность запасного режима."""
    return _fallback(arts, load_fallback(), h, row, station, day, have_weather=not row.empty)[0]


# ---------------------------------------------------------------- персистентность
def _recent_events(station_id: str, issue: date | None = None, deadline: float | None = None) -> pd.Series:
    """Свежие наблюдения станции для персистентности: текущий и прошлый год.
    Сеть читается только в память; при сбое берётся уже скачанный файл сырой зоны.
    Сырая зона не перезаписывается (раздел 22.1)."""
    from aralshield.truth import isd
    from aralshield.weather import http
    issue = issue or local_today()
    st = next(s for s in STATIONS if s.id == station_id)
    frames = []
    for y in (issue.year - 1, issue.year):
        cached = isd.read_cached_year(st.isd, y)
        path, _ = isd._paths(st.isd, y)
        raw = None
        if cached is not None and not http.is_open_cache(path, date(y, 12, 31), isd.ISD_LATENCY_DAYS, 0):
            raw = cached  # закрытый год уже в сырой зоне
        else:
            try:
                raw = isd.fetch_station_year(st.isd, y, deadline=deadline)
            except Exception as e:  # сеть/NOAA недоступны — не повод терять прежний файл
                log.warning("свежие наблюдения %s %d недоступны: %s", station_id, y, e)
            if raw is None or raw.empty:
                raw = cached
        if raw is not None and not raw.empty:
            frames.append(isd.parse_isd(raw, station_id))
    if not frames:
        return pd.Series(dtype=float)
    ev = isd.build_events(pd.concat(frames, ignore_index=True), save=False)
    if "n_ww" in ev:
        for yy in isd.ww_unreliable_years(ev).get(station_id, []):
            ev.loc[ev.date.dt.year == yy, "reliable"] = False
    ev = ev.set_index("date")
    return ev["dust"].where(ev["reliable"]).astype(float)


# ---------------------------------------------------------------- ежедневный выпуск
def _save(items: list[dict], replace_mode: str | None = None) -> None:
    """db.save_forecasts с атомарной заменой режима (контракт K4), если она уже поддерживается."""
    if replace_mode and "replace_mode" in inspect.signature(db.save_forecasts).parameters:
        db.save_forecasts(items, replace_mode=replace_mode)
        return
    if replace_mode:
        log.warning("db.save_forecasts без replace_mode — удаление и запись в разных транзакциях")
        with db.connect() as con:
            con.execute("DELETE FROM forecasts WHERE mode=?", (replace_mode,))
    db.save_forecasts(items)


def issue_daily(issue: date | None = None, save: bool = True) -> list[dict]:
    """Боевой выпуск на сегодня (только local_today(): прошлые даты требуют архива прогнозов)."""
    from aralshield.eye.classify import ensure_sector_distance
    from aralshield.forecast.train import predict as model_predict
    from aralshield.weather.openmeteo import fetch_cams_dust, fetch_operational_forecast, save_operational_forecast

    today = local_today()
    if issue is not None and issue != today:
        raise ValueError(f"боевой выпуск возможен только на сегодня ({today}), запрошено {issue}")
    issue = today
    issued_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    deadline = time.monotonic() + DAILY_DEADLINE_MIN * 60
    if save:
        db.init()
    arts, levels = load_artifacts()
    fb = load_fallback()
    sectors = load_sector_source(masked=True) or {}
    tag = sector_source_tag()
    try:
        dists = ensure_sector_distance() or {}
    except Exception:
        log.exception("расстояния до секторов-источников недоступны")
        dists = {}
    out = []
    for st in STATIONS:
        sec = np.asarray(sectors[st.id]) if st.id in sectors else None
        st_reasons, notes = [], []
        hourly, feat, fetched, model = None, None, None, None
        late = time.monotonic() > deadline
        if late:
            log.error("%s: истёк срок выпуска (%d мин) — упрощённый режим", st.id, DAILY_DEADLINE_MIN)
            st_reasons += ["deadline", "no_weather"]
            notes.append("выпуск не уложился в срок — данные не загружались")
        else:
            try:
                hourly = fetch_operational_forecast(st.lat, st.lon, deadline=deadline)
                feat = daily_features(hourly, sec)
                fetched, model = hourly.attrs.get("fetched_at"), hourly.attrs.get("model")
                if save:
                    try:
                        save_operational_forecast(hourly, st.id, issue)
                    except OSError as e:
                        log.warning("архив оперативного прогноза %s не записан: %s", st.id, e)
            except Exception as e:
                log.error("прогноз погоды недоступен для %s: %s", st.id, e)
                hourly, feat = None, None
                notes.append("нет оперативного прогноза погоды")
        events = pd.Series(dtype=float) if late else _recent_events(st.id, issue, deadline)
        cams = {}
        if not late:
            try:
                c = fetch_cams_dust(st.lat, st.lon, forecast_days=4)
                cams = c.groupby(c.time_local.dt.normalize())["dust"].max().to_dict()
            except Exception as e:
                notes.append(f"CAMS недоступен: {type(e).__name__}")
        srcs = daily_sources(hourly, sec, dists.get(st.id))
        for h in HORIZONS:
            day = issue + timedelta(days=h)
            ts = pd.Timestamp(day)
            reasons = list(st_reasons)
            if feat is not None:
                f = add_persistence(feat, events, h)
                row = f[f.date == ts].copy()
            else:
                row = pd.DataFrame()
            weather_cols = [c for c in row.columns if c not in ("date", *PERSISTENCE_FEATURES)]
            have_weather = not row.empty and not row[weather_cols].isna().all(axis=1).any()
            if not have_weather and "no_weather" not in reasons:
                reasons.append("no_weather")
            if not row.empty:
                row["station_code"] = STATION_CODE[st.id]
                row["station"] = st.id
                if row[PERSISTENCE_FEATURES].isna().all(axis=1).any():
                    reasons.append("no_station_obs")
                    notes_h = "нет свежих наблюдений станции — признак персистентности пропущен"
                    if notes_h not in notes:
                        notes.append(notes_h)
            if not (arts and h in arts and "m2" in arts[h]):
                reasons.append("model_missing")
            if not FALLBACK_TRIGGERS & set(reasons):
                art = arts[h]["m2"]
                p = float(model_predict(art, row)[0])
                drivers = _drivers(art, row)
                version = art.get("version", LEGACY_VERSION)
            else:
                p, version, extra = _fallback(arts, fb, h, row, st.id, day, have_weather)
                reasons += extra
                drivers = []
            reasons = [r for r in REASON_CODES if r in reasons]  # порядок и без повторов
            meta = _row_meta(row.iloc[0]) if not row.empty else {}
            src, km = srcs.get(ts, (None, None))
            lv = levels.get(h, DEFAULT_LEVELS)
            lv_all = {g: level_for(p, x) for g, x in lv.items()}
            missing = ([c for c in row.columns if c not in ("date", "station") and pd.isna(row.iloc[0][c])]
                       if not row.empty else [])
            out.append({
                "station": st.id, "issue_date": issue.isoformat(), "horizon": h, "day": day.isoformat(),
                "probability": round(p, 4), "level": lv_all.get("public", level_for(p, DEFAULT_LEVELS["public"])),
                "levels_json": lv_all, "drivers_json": drivers, "source_sector": src, "source_km": km,
                "cams_dust": round(float(cams[ts]), 1) if ts in cams else None,
                "model_version": version, "degraded": int(bool(reasons)), "mode": "live",
                "inputs_json": {"weather": f"Open-Meteo оперативный прогноз ({model})" if model else None,
                                "weather_model": model, "fetched_at": fetched, "issued_at": issued_at,
                                "notes": notes, "degraded_reasons": reasons,
                                "missing_features": missing, "station_code": STATION_CODE[st.id],
                                "sector_map": f"eye-rf {tag}" if tag else None},
                **meta,
            })
    if save:
        db.save_forecasts(out)
    return out


# ---------------------------------------------------------------- архив тестовых лет
def backtest_to_db() -> int:
    """Прогнозы модели на тестовых годах (модель их не видела) → архив «прогноз против факта».

    Признаки — ERA5 (фактическая погода дня, perfect-prog), разбиение то же, что в обучении
    (train.split, с зазором GAP_DAYS). Для каждой строки пишутся факторы (схема K3) и сектор-источник."""
    from aralshield.forecast.train import shap_values, split
    from aralshield.forecast.train import predict as model_predict
    from aralshield.weather.openmeteo import era5_station_history

    arts, levels = load_artifacts()
    if not arts:
        raise RuntimeError("нет models/forecast.joblib — сначала выполните обучение (cli train)")
    ds = pd.read_parquet(FEATURES / "dataset.parquet")
    ds["date"] = pd.to_datetime(ds["date"])
    sectors = load_sector_source(masked=True) or {}
    tag = sector_source_tag()
    try:
        from aralshield.eye.classify import ensure_sector_distance
        dists = ensure_sector_distance() or {}
    except Exception:
        log.exception("расстояния до секторов-источников недоступны")
        dists = {}
    srcs = {}
    for st in STATIONS:
        sec = np.asarray(sectors[st.id]) if st.id in sectors else None
        if sec is None:
            continue
        try:
            hourly = era5_station_history(st.id, YEARS)
        except Exception as e:
            log.warning("ERA5 %s недоступен для атрибуции источника: %s", st.id, e)
            continue
        t = pd.to_datetime(hourly.time_local)
        srcs[st.id] = daily_sources(hourly[t.dt.year.isin(TEST_YEARS)], sec, dists.get(st.id))
    inputs = {"weather": "ERA5 (perfect-prog)", "degraded_reasons": [],
              "sector_map": f"eye-rf {tag}" if tag else None,
              "note": "архив тестовых лет: погода дня из реанализа, а не из прогноза"}
    items = []
    for h in HORIZONS:
        art = arts[h]["m2"]
        d = split(ds[ds.horizon == h])["test"].dropna(subset=["wind_max"])
        if d.empty:
            continue
        p = model_predict(art, d)
        drv = (drivers_batch(shap_values(art, d), d[art["features"]]) if art["kind"] == "lgbm"
               else [[] for _ in range(len(d))])
        version = art.get("version", LEGACY_VERSION)
        lv = levels.get(h, DEFAULT_LEVELS)
        for (_, r), pi, drivers in zip(d.iterrows(), p, drv):
            lv_all = {g: level_for(float(pi), x) for g, x in lv.items()}
            src, km = srcs.get(r.station, {}).get(pd.Timestamp(r.date), (None, None))
            items.append({"station": r.station, "issue_date": (r.date - pd.Timedelta(days=h)).date().isoformat(),
                          "horizon": h, "day": r.date.date().isoformat(), "probability": round(float(pi), 4),
                          "level": lv_all["public"], "levels_json": lv_all, "mode": "backtest",
                          "drivers_json": drivers, "source_sector": src, "source_km": km,
                          "model_version": version, "degraded": 0, "inputs_json": inputs,
                          **_row_meta(r)})
    db.init()
    _save(items, replace_mode="backtest")
    return len(items)
