"""Сборка обучающего набора: метки «Правды» + признаки ERA5 + секторы «Глаза»."""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from aralshield.config import CLEAN, FEATURES, STATIONS, TRAIN_YEARS, YEARS
from aralshield.forecast.features import add_persistence, daily_features
from aralshield.truth.isd import ww_unreliable_years
from aralshield.weather.openmeteo import era5_station_history

log = logging.getLogger(__name__)

STATION_CODE = {s.id: i for i, s in enumerate(STATIONS)}
SECTOR_FILE = FEATURES / "sector_source.json"


# Сектор, покрытый снимками меньше чем наполовину, не даёт значения F_s(θ): иначе модель учится
# «нет источника» там, где просто нет композита (Кызылорда и Жосалы за краем AOI «Глаза»).
MIN_SECTOR_COVERAGE = 0.5


def load_sector_source(masked: bool = False) -> dict[str, list[float]] | None:
    """F_s(θ) по станциям из модуля «Глаз» (если он уже отработал).
    masked=True: секторы с coverage < MIN_SECTOR_COVERAGE заменяются на NaN (неизвестно)."""
    if not SECTOR_FILE.exists():
        return None
    js = json.loads(SECTOR_FILE.read_text(encoding="utf-8"))
    st = js["stations"]
    cov = js.get("coverage")
    if masked and cov:
        st = {k: [float(f) if (k in cov and cov[k][i] is not None and cov[k][i] >= MIN_SECTOR_COVERAGE) else float("nan")
                  for i, f in enumerate(v)] for k, v in st.items()}
    return st


def sector_source_tag() -> str | None:
    """Тег композита «Глаза», из которого построены F_s(θ) (например, 2025v2)."""
    if SECTOR_FILE.exists():
        tag = json.loads(SECTOR_FILE.read_text(encoding="utf-8")).get("tag")
        return None if tag is None else str(tag)
    return None


def load_events() -> pd.DataFrame:
    ev = pd.read_parquet(CLEAN / "dust_events.parquet")
    ev["date"] = pd.to_datetime(ev["date"])
    return ev


def inhomogeneous_years(ev: pd.DataFrame, frac: float = 0.25, min_train_years: int = 3) -> dict[str, list[int]]:
    """Проверка однородности рядов (раздел 10.3): годы, где станция сообщила пылевых дней
    меньше frac от своей медианы, считаются сменой практики наблюдений, а не «чистым воздухом».
    Медиана считается только по годам обучения (TRAIN_YEARS), чтобы отбор не опирался на метки
    теста; если у станции меньше min_train_years годов обучения, проверка не выполняется."""
    e = ev[ev.reliable].copy()
    e["year"] = e.date.dt.year
    counts = e.groupby(["station", "year"]).dust.sum()
    out = {}
    for st, s in counts.groupby(level=0):
        years = s.index.get_level_values(1)
        train = s[years.isin(TRAIN_YEARS)]
        if len(train) < min_train_years:
            log.warning("%s: %d лет обучения — проверка однородности пропущена", st, len(train))
            continue
        med = train.median()
        bad = s[s < frac * med].index.get_level_values(1).tolist()
        if bad:
            out[st] = [int(y) for y in bad]
    return out


def build(horizons=(0, 1, 2), years=YEARS) -> pd.DataFrame:
    ev = load_events()
    # 1) Годы без передачи кода погоды ww — метка неизвестна (раздел 10.3, completeness.csv)
    if "n_ww" in ev:
        no_ww = ww_unreliable_years(ev)
    else:
        log.warning("в каталоге нет n_ww (старая версия truth) — фильтр по передаче ww пропущен")
        no_ww = {}
    (FEATURES / "ww_unreliable_years.json").write_text(json.dumps(no_ww), encoding="utf-8")
    log.info("станционные годы без передачи ww (метка неизвестна): %s", no_ww)
    for st, ys in no_ww.items():
        ev.loc[(ev.station == st) & ev.date.dt.year.isin(ys), "reliable"] = False
    # 2) Неоднородные годы по медиане лет обучения
    bad_years = inhomogeneous_years(ev)
    (FEATURES / "inhomogeneous_years.json").write_text(json.dumps(bad_years), encoding="utf-8")
    log.info("исключены неоднородные станционные годы: %s", bad_years)
    for st, ys in bad_years.items():
        m = (ev.station == st) & ev.date.dt.year.isin(ys)
        ev.loc[m, "reliable"] = False
    sectors = load_sector_source(masked=True)
    rows = []
    for st in STATIONS:
        hourly = era5_station_history(st.id, years)
        sec = np.asarray(sectors[st.id]) if sectors and st.id in sectors else None
        day = daily_features(hourly, sec)
        e = ev[ev.station == st.id].set_index("date")
        label = e["dust"].where(e["reliable"])  # ненадёжные дни — без метки
        for h in horizons:
            f = add_persistence(day, label, h)
            f["station"] = st.id
            f["station_code"] = STATION_CODE[st.id]
            f["horizon"] = h
            f["y"] = label.reindex(pd.to_datetime(f["date"])).to_numpy()
            f["strong"] = e["strong"].reindex(pd.to_datetime(f["date"])).astype(float).to_numpy()
            rows.append(f)
    ds = pd.concat(rows, ignore_index=True)
    ds = ds[ds["date"].dt.year.isin(years)]
    ds.to_parquet(FEATURES / "dataset.parquet", index=False)
    log.info("набор: %d строк, событий %d", len(ds), int(np.nansum(ds.y)))
    return ds
