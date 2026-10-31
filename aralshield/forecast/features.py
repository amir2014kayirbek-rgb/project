"""Признаки модуля «Прогноз» (раздел 13.2).

Признаки целевых суток D строятся из почасового ряда погоды: в обучении это
реанализ ERA5, в работе — оперативный прогноз. Признак персистентности берётся
только из наблюдений, известных к 06:00 дня выпуска t (D = t + h).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aralshield.config import N_SECTORS

WIND_THRESHOLDS = (8, 10, 12, 14)
DEFAULT_VT = 8.0  # пороговая скорость для наветренного индекса, м/с
DRY_DAYS_CAP = 30

WIND_FEATURES = ["wind_max", "wind_mean", "gust_max",
                 "hours_gt8", "hours_gt10", "hours_gt12", "hours_gt14"]
SOURCE_FEATURES = ["upwind_index", "upwind_source_frac", "source_wind_hours"]
MOISTURE_FEATURES = ["precip_d", "precip_1", "precip_3", "precip_7", "precip_14",
                     "dry_days", "soil_moisture", "snow_depth", "rh_min"]
SYNOPTIC_FEATURES = ["dp_24h", "dt_max_24h", "temp_max"]
DIRECTION_FEATURES = ["dir_sin", "dir_cos"]
SEASON_FEATURES = ["doy_sin", "doy_cos", "month"]
PERSISTENCE_FEATURES = ["dust_lag", "dust_lag_7d"]

# Суточные столбцы, зависящие от полноты часового ряда ветра
WIND_DAY_COLS = WIND_FEATURES + ["u_sin", "u_cos", "upwind_index", "source_wind_hours",
                                  "dir_sin", "dir_cos", "upwind_source_frac"]

ALL_FEATURES = (WIND_FEATURES + DIRECTION_FEATURES + MOISTURE_FEATURES + SYNOPTIC_FEATURES
                + SOURCE_FEATURES + SEASON_FEATURES + PERSISTENCE_FEATURES + ["station_code"])
# Монотонность: вероятность не убывает с ростом ветра (раздел 13.4)
MONOTONE_UP = set(WIND_FEATURES + ["upwind_index"])


def sector_of(direction_deg: np.ndarray) -> np.ndarray:
    """Номер сектора 0..15 по метеорологическому направлению (откуда дует ветер)."""
    width = 360 / N_SECTORS
    return (np.floor(((np.asarray(direction_deg) + width / 2) % 360) / width)).astype(int)


def daily_features(hourly: pd.DataFrame, sector_source: np.ndarray | None,
                   vt: float = DEFAULT_VT) -> pd.DataFrame:
    """Суточные признаки по местным суткам.

    hourly: столбцы time_local + переменные Open-Meteo.
    sector_source: 16 значений F_s(θ) — взвешенная доля «потенциального источника»
        в секторе, ОТКУДА дует ветер; None, если карта «Глаза» недоступна.
    """
    h = hourly.copy()
    h["date"] = pd.to_datetime(h["time_local"]).dt.normalize()
    v = h["wind_speed_10m"].astype(float)
    d = h["wind_direction_10m"].astype(float)
    h["excess3"] = np.clip(v - vt, 0, None) ** 3
    sec = sector_of(d.fillna(0).to_numpy())
    if sector_source is not None:
        f = np.asarray(sector_source, dtype=float)[sec]
        f[d.isna().to_numpy()] = np.nan
    else:
        f = np.full(len(h), np.nan)
    h["F"] = f
    h["U"] = (h["F"] * h["excess3"]).where((h["excess3"] > 0) | (sector_source is None), 0.0)
    # Сильный ветер из сектора без снимков (F = NaN при известном направлении): вклад неизвестен
    h["uncovered"] = (h["F"].isna() & d.notna() & (h["excess3"] > 0)).astype(float) if sector_source is not None else 0.0
    med = np.nanmedian(sector_source) if sector_source is not None and np.isfinite(sector_source).any() else np.nan
    h["source_wind"] = ((h["F"] > med) & (v > vt)).astype(float)
    # Компоненты ветра для «среднего направления сильного ветра»
    rad = np.deg2rad(d)
    w = np.where(v > vt, v, 0.0)
    h["u_sin"] = np.sin(rad) * w
    h["u_cos"] = np.cos(rad) * w
    for t in WIND_THRESHOLDS:
        h[f"gt{t}"] = (v > t).astype(float)

    g = h.groupby("date")
    day = pd.DataFrame({
        "wind_max": g["wind_speed_10m"].max(),
        "wind_mean": g["wind_speed_10m"].mean(),
        "gust_max": g["wind_gusts_10m"].max(),
        **{f"hours_gt{t}": g[f"gt{t}"].sum() for t in WIND_THRESHOLDS},
        "u_sin": g["u_sin"].sum(), "u_cos": g["u_cos"].sum(),
        "precip_d": g["precipitation"].sum(min_count=1),
        "soil_moisture": g["soil_moisture_0_to_7cm"].mean(),
        "snow_depth": g["snow_depth"].max(),
        "rh_min": g["relative_humidity_2m"].min(),
        "pressure_mean": g["surface_pressure"].mean(),
        "temp_max": g["temperature_2m"].max(),
        "upwind_index": g["U"].sum(min_count=1),
        "source_wind_hours": g["source_wind"].sum(),
        "n_hours": g.size(),
        "n_wind": g["wind_speed_10m"].count(),
        "n_uncovered": g["uncovered"].sum(),
    })
    # Признаки источника неизвестны, если хоть один час сильного ветра шёл из непокрытого сектора
    day.loc[day.n_uncovered > 0, ["upwind_index", "source_wind_hours"]] = np.nan
    # Направление сильного ветра; если сильного ветра не было — направление максимума
    idxmax = h.loc[h["wind_speed_10m"].notna()].groupby("date")["wind_speed_10m"].idxmax()
    dir_at_max = h.loc[idxmax, ["date", "wind_direction_10m"]].set_index("date")["wind_direction_10m"]
    strong = np.hypot(day.u_sin, day.u_cos) > 0
    ang = np.where(strong, np.rad2deg(np.arctan2(day.u_sin, day.u_cos)), dir_at_max.reindex(day.index))
    day["dir_sin"] = np.sin(np.deg2rad(ang))
    day["dir_cos"] = np.cos(np.deg2rad(ang))
    if sector_source is not None:
        day["upwind_source_frac"] = np.asarray(sector_source)[sector_of(np.nan_to_num(ang) % 360)]
    else:
        day["upwind_source_frac"] = np.nan
    # Неполные сутки (края ряда): ветровые признаки только по полным 24 ч,
    # при < 18 ч сутки выбрасываются целиком
    day.loc[day.n_wind < 24, WIND_DAY_COLS] = np.nan
    day.loc[day.n_hours < 18, :] = np.nan

    p = day["precip_d"]
    # Осадки за предыдущие сутки (не включая D)
    for n in (1, 3, 7, 14):
        day[f"precip_{n}"] = p.shift(1).rolling(n, min_periods=max(1, n // 2)).sum()
    wet = (p >= 0.5).astype(float).where(p.notna())
    # Число сухих суток подряд перед D (до 30). Пока ряд не видел ни одного дождливого дня,
    # длина засухи неизвестна (NaN), если только нижняя граница уже не достигла потолка 30.
    dry = []
    run, lower = np.nan, 0  # run — известная длина засухи; lower — её нижняя граница с начала ряда
    for val in wet.shift(1).to_numpy():
        if not np.isnan(val) and val > 0:
            run, lower = 0.0, 0
        elif not np.isnan(run):
            run += 1  # сутки без данных об осадках продолжают засуху (как раньше)
        elif not np.isnan(val):
            lower += 1
            if lower >= DRY_DAYS_CAP:
                run = float(DRY_DAYS_CAP)
        dry.append(min(run, DRY_DAYS_CAP) if not np.isnan(run) else np.nan)
    day["dry_days"] = dry
    day["dp_24h"] = day["pressure_mean"] - day["pressure_mean"].shift(1)
    day["dt_max_24h"] = day["temp_max"] - day["temp_max"].shift(1)
    doy = day.index.dayofyear.to_numpy()
    day["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    day["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    day["month"] = day.index.month
    return day.drop(columns=["u_sin", "u_cos", "pressure_mean", "n_hours", "n_wind", "n_uncovered"]).rename_axis("date").reset_index()


def add_persistence(feat: pd.DataFrame, events: pd.Series, horizon: int) -> pd.DataFrame:
    """Персистентность, известная в момент выпуска: событие в день t-1 (= D-h-1)
    и число пылевых дней за 7 суток до t."""
    ev = events.astype(float)
    lag = horizon + 1
    out = feat.copy()
    dates = pd.to_datetime(out["date"])
    out["dust_lag"] = ev.reindex(dates - pd.Timedelta(days=lag)).to_numpy()
    roll7 = ev.rolling(7, min_periods=4).sum()
    out["dust_lag_7d"] = roll7.reindex(dates - pd.Timedelta(days=lag)).to_numpy()
    return out
