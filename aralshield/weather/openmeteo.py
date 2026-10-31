"""Адаптеры к погодным данным через Open-Meteo (без ключа).

* ERA5 / ERA5-Land — исторический ряд для обучения (archive-api).
* Оперативный прогноз на 72 ч + 14 прошедших суток (api).
* Архив оперативных прогнозов (historical-forecast-api) — проверка «реанализ → прогноз».
* CAMS: прогноз/анализ приземной пыли (air-quality-api) — эталон B3.

Все ряды запрашиваются в UTC и переводятся в местное время UTC+5 вручную,
чтобы смена часовых поясов в истории не сдвигала сутки.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import pandas as pd

from aralshield.config import HIST_FORECAST_MODELS, LOCAL_UTC_OFFSET_H, OPER_FORECAST_MODEL, RAW, STATIONS
from aralshield.weather import http

log = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HIST_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

HOURLY_VARS = [
    "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "precipitation",
    "soil_moisture_0_to_7cm", "relative_humidity_2m", "surface_pressure",
    "temperature_2m", "snow_depth",
]


ERA5_LATENCY_DAYS = 7  # ERA5 в Open-Meteo запаздывает на ~5 суток


def _get(url: str, params: dict, deadline: float | None = None) -> dict:
    """Запрос к Open-Meteo по общей политике повторов (weather.http): 3 попытки,
    без повтора на 4xx кроме 429, без паузы после последней попытки."""
    try:
        return http.get(url, params=params, deadline=deadline).json()
    except http.HTTPGiveUp as e:
        raise RuntimeError(f"Open-Meteo недоступен: {e}") from e


def _numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Переменные, которых у модели нет (например, snow_depth у ecmwf_ifs025), приходят как null —
    приводим всё к float, иначе столбец получает тип object и LightGBM отказывается его читать."""
    for c in df.columns:
        if c not in ("time", "time_local"):
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


def _to_frame(js: dict) -> pd.DataFrame:
    h = js["hourly"]
    df = _numeric(pd.DataFrame(h))
    df["time"] = pd.to_datetime(df["time"]) + pd.Timedelta(hours=LOCAL_UTC_OFFSET_H)
    df = df.rename(columns={"time": "time_local"})
    return df


def fetch_era5(lat: float, lon: float, start: date, end: date) -> pd.DataFrame:
    js = _get(ARCHIVE_URL, {
        "latitude": lat, "longitude": lon,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "hourly": ",".join(HOURLY_VARS), "wind_speed_unit": "ms", "timezone": "GMT",
    })
    return _to_frame(js)


def era5_station_history(station_id: str, years: list[int], force: bool = False) -> pd.DataFrame:
    """Почасовой ERA5 для станции, кэшируется в сырой зоне по 5-летним блокам."""
    st = next(s for s in STATIONS if s.id == station_id)
    frames = []
    for y0 in range(min(years), max(years) + 1, 5):
        y1 = min(y0 + 4, max(years))
        path = RAW / "era5" / f"{station_id}_{y0}_{y1}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        end = min(date(y1, 12, 31), date.today())
        # Блок с текущим годом «открыт»: перекачивается, когда файл устарел
        if path.exists() and not force and not http.is_open_cache(path, end, ERA5_LATENCY_DAYS):
            frames.append(pd.read_parquet(path))
            continue
        # Берём на сутки раньше, чтобы после сдвига на UTC+5 первые местные сутки были полными
        df = fetch_era5(st.lat, st.lon, date(y0, 1, 1) - timedelta(days=1), end)
        http.atomic_to_parquet(df, path)
        frames.append(df)
        log.info("ERA5 %s %d–%d: %d ч", station_id, y0, y1, len(df))
        time.sleep(1)
    out = pd.concat(frames, ignore_index=True).drop_duplicates("time_local")
    return out.sort_values("time_local").reset_index(drop=True)


def fetch_operational_forecast(lat: float, lon: float, past_days: int = 31, model: str = OPER_FORECAST_MODEL,
                               deadline: float | None = None) -> pd.DataFrame:
    """Свежий оперативный прогноз на 3 суток плюс прошедшие дни для признаков влаги.
    past_days ≥ 31: счётчик сухих суток (до 30) и осадки за 14 суток видят полную историю."""
    js = _get(FORECAST_URL, {
        "latitude": lat, "longitude": lon, "hourly": ",".join(HOURLY_VARS),
        "wind_speed_unit": "ms", "timezone": "GMT", "forecast_days": 4, "past_days": past_days,
        "models": model,
    }, deadline=deadline)
    df = _to_frame(js)
    df.attrs["fetched_at"] = pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")
    df.attrs["model"] = model
    return df


def save_operational_forecast(df: pd.DataFrame, station_id: str, issue: date) -> None:
    """Собственный архив оперативных прогнозов (J3): raw/oper_forecast/<станция>_<выпуск>.parquet."""
    path = RAW / "oper_forecast" / f"{station_id}_{issue.isoformat()}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out["fetched_at"] = df.attrs.get("fetched_at")
    out["model"] = df.attrs.get("model")
    http.atomic_to_parquet(out, path)


def fetch_historical_forecast(lat: float, lon: float, start: date, end: date,
                              model: str = HIST_FORECAST_MODELS[0]) -> pd.DataFrame:
    """Архив оперативных прогнозов (склейка первых часов прогонов) явно заданной модели.
    Без models= сервис для части точек отдаёт ряд, совпадающий с ERA5 (проверено: ecmwf_ifs)."""
    js = _get(HIST_FORECAST_URL, {
        "latitude": lat, "longitude": lon,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "hourly": ",".join(HOURLY_VARS), "wind_speed_unit": "ms", "timezone": "GMT",
        "models": model,
    })
    return _to_frame(js)


def fetch_cams_dust(lat: float, lon: float, start: date | None = None, end: date | None = None,
                    forecast_days: int | None = None) -> pd.DataFrame:
    """Приземная концентрация пыли CAMS, мкг/м³."""
    params = {"latitude": lat, "longitude": lon, "hourly": "dust", "timezone": "GMT"}
    if start is not None:
        params.update(start_date=start.isoformat(), end_date=end.isoformat())
    else:
        params.update(forecast_days=forecast_days or 4, past_days=1)
    return _to_frame(_get(AIR_URL, params))


def cams_station_history(station_id: str, start: date, end: date, force: bool = False) -> pd.DataFrame:
    st = next(s for s in STATIONS if s.id == station_id)
    path = RAW / "cams" / f"{station_id}_{start}_{end}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force and not http.is_open_cache(path, end, 2):
        return pd.read_parquet(path)
    df = fetch_cams_dust(st.lat, st.lon, start, end)
    http.atomic_to_parquet(df, path)
    return df


def hist_forecast_station(station_id: str, start: date, end: date, model: str = HIST_FORECAST_MODELS[0],
                          force: bool = False) -> pd.DataFrame:
    """Кэш архива оперативных прогнозов; имя файла включает модель."""
    st = next(s for s in STATIONS if s.id == station_id)
    path = RAW / "hist_forecast" / f"{station_id}_{model}_{start}_{end}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force and not http.is_open_cache(path, end, 2):
        return _numeric(pd.read_parquet(path))
    df = fetch_historical_forecast(st.lat, st.lon, start, end, model=model)
    http.atomic_to_parquet(df, path)
    return df


def wind_climatology(lat: float, lon: float, years: tuple[int, int] = (2015, 2024)) -> pd.DataFrame:
    """Почасовой ветер ERA5 для розы ветров «Щита» (кэшируется)."""
    path = RAW / "era5" / f"wind_{lat:.2f}_{lon:.2f}_{years[0]}_{years[1]}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return pd.read_parquet(path)
    js = _get(ARCHIVE_URL, {
        "latitude": lat, "longitude": lon,
        "start_date": f"{years[0]}-01-01", "end_date": f"{years[1]}-12-31",
        "hourly": "wind_speed_10m,wind_direction_10m", "wind_speed_unit": "ms", "timezone": "GMT",
    })
    df = _to_frame(js)
    http.atomic_to_parquet(df, path)
    return df
