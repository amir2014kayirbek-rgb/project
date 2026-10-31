"""М3 «Щит»: индекс приоритета посадок саксаула, сценарии «что если», устойчивость.

E_ij = S_i · P_ij · exp(−d_ij / L) · N_j · [d_ij ≤ R],   Priority_i = M_i · Σ_j E_ij   (раздел 14.2)
ΔE_j = Σ_{i ∈ посадки} (S_i − S_i′) · P_ij · exp(−d_ij / L) · N_j · [d_ij ≤ R]       (раздел 14.3)

R = SOURCE_RADIUS_KM (300 км): дальше ячейка не влияет на пункт, иначе крупный далёкий
город (Кызылорда, 250–650 км) управлял бы всем рейтингом; вариант без ограничения
приводится в анализе чувствительности. Доступность от дорог в M_i не учтена — открытого
слоя дорог нет (ограничение W9).
"""
from __future__ import annotations

import json
import logging
import os
import warnings
from itertools import product
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from scipy.stats import kendalltau

from aralshield import config as _cfg
from aralshield.config import (DATA, EYE_CRS, N_SECTORS, REPORTS, SECTOR_NAMES, SETTLEMENTS, SOURCE_RADIUS_KM)
from aralshield.eye.classify import INDEX_NAMES
from aralshield.eye.composite import EYE_DIR

log = logging.getLogger(__name__)
EYE_TAG = getattr(_cfg, "EYE_TAG", "2025v2")  # контракт K1
SHIELD_DIR = DATA / "shield"
SHIELD_DIR.mkdir(parents=True, exist_ok=True)
CELL_KM = 2           # ячейка рейтинга (карта «Глаза» 250 м агрегируется)
CELL_HA = CELL_KM ** 2 * 100
# Веса пригодности (Kim et al., 2020): NDVI, NMDI (влага) — больше лучше; SI (соль), TGSI (крупность) — меньше лучше
SUIT_WEIGHT_SETS = {
    "base":   (0.35, 0.25, 0.25, 0.15),
    "equal":  (0.25, 0.25, 0.25, 0.25),
    "veg+":   (0.45, 0.20, 0.20, 0.15),
    "moist+": (0.25, 0.40, 0.20, 0.15),
    "salt+":  (0.25, 0.20, 0.40, 0.15),
}
SUIT_COMPONENTS = ("g_ndvi", "g_nmdi", "g_si", "g_tgsi")
BASE = {"L": 100.0, "vt": 8.0, "suit_power": 1.0, "weights": "base"}
SENSITIVITY = {"L": [50, 100, 150, 200], "vt": [6.0, 8.0, 10.0], "suit_power": [0.5, 1.0, 2.0],
               "weights": list(SUIT_WEIGHT_SETS)}
TOP_N = 200
RBO_P = 0.98          # глубина внимания RBO ≈ 1/(1−p) = 50 ячеек
COVER_FACTOR = 0.6    # ожидаемое снижение S_i после посадки (проективное покрытие)
M_MIN_TOP = 0.4       # ниже — ячейка не попадает в «высокий»/«надёжный высокий»
PROTECT_SHARE = 0.8   # «защищаемое население»: пункты, получающие 80% вклада ячейки
RANDOM_DRAWS = 50


def wind_rose(vt_list=(6.0, 8.0, 10.0)) -> dict[float, np.ndarray]:
    """Доля часов с ветром выше порога по 16 секторам «откуда дует» (ERA5, 2015–2024),
    по трём точкам на дне — среднее."""
    from aralshield.forecast.features import sector_of
    from aralshield.weather.openmeteo import wind_climatology
    pts = [(45.6, 60.2), (46.3, 60.8), (45.0, 59.6)]
    frames = [wind_climatology(lat, lon) for lat, lon in pts]
    out = {}
    for vt in vt_list:
        acc = np.zeros(N_SECTORS)
        for f in frames:
            v = f["wind_speed_10m"].to_numpy()
            d = f["wind_direction_10m"].to_numpy()
            m = np.isfinite(v) & np.isfinite(d) & (v > vt)
            acc += np.bincount(sector_of(d[m]), minlength=N_SECTORS) / len(v)
        out[vt] = acc / len(frames)
    return out


def _pct_rank(a: np.ndarray) -> np.ndarray:
    """Процентильный ранг 0..1 по переданным (валидным) ячейкам; NaN → NaN."""
    return pd.Series(a).rank(pct=True).to_numpy()


def suitability(cells: pd.DataFrame, weights=SUIT_WEIGHT_SETS["base"]) -> np.ndarray:
    """M_i = clip((Σ w·g − 0.2) / 0.6, 0, 1), g — ранги «хорошести» компонентов (0..1)."""
    if isinstance(weights, str):
        weights = SUIT_WEIGHT_SETS[weights]
    m = sum(w * cells[c].to_numpy() for w, c in zip(weights, SUIT_COMPONENTS))
    return np.clip((m - 0.2) / 0.6, 0, 1)


def build_cells(tag: str = EYE_TAG) -> pd.DataFrame:
    """Ячейки 2×2 км: S_i (доля источника), M_i (пригодность), координаты."""
    with rasterio.open(EYE_DIR / f"source_prob_{tag}.tif") as src:
        p_source = src.read(1)
        transform = src.transform
    with rasterio.open(EYE_DIR / f"classes_{tag}.tif") as src:
        cls = src.read(1)
    idx = dict(zip(INDEX_NAMES, np.load(EYE_DIR / f"indices_{tag}.npy")))
    k = int(CELL_KM * 1000 / transform.a)
    H, W = (p_source.shape[0] // k) * k, (p_source.shape[1] // k) * k

    def agg(a, fn=np.nanmean):
        with warnings.catch_warnings():  # «Mean of empty slice» для полностью пустых ячеек
            warnings.simplefilter("ignore", RuntimeWarning)
            return fn(a[:H, :W].reshape(H // k, k, W // k, k), axis=(1, 3)).ravel()

    S = agg(p_source)
    water = agg((cls == 5).astype(float))
    valid_frac = agg((cls > 0).astype(float))
    ndvi, tgsi, si, nmdi = (agg(idx[n]) for n in ("NDVI", "TGSI", "SI", "NMDI"))
    rows_i, cols_i = np.indices((H // k, W // k))
    x = (transform.c + (cols_i + 0.5) * k * transform.a).ravel()
    y = (transform.f + (rows_i + 0.5) * k * transform.e).ravel()
    keep = (valid_frac > 0.8) & (water < 0.5) & np.isfinite(S)
    tr = Transformer.from_crs(EYE_CRS, "EPSG:4326", always_xy=True)
    lon, lat = tr.transform(x[keep], y[keep])
    cells = pd.DataFrame({"cell_id": np.flatnonzero(keep), "x": x[keep], "y": y[keep], "lon": lon, "lat": lat,
                          "S": S[keep], "water": water[keep], "valid": valid_frac[keep]})
    # Пригодность по подходу Kim et al. (2020): больше влаги и растительности, меньше засоления
    # и крупности — лучше приживаемость. Ранги 0..1 только по валидным ячейкам (вода и пустые
    # ячейки прямоугольника не сдвигают перцентили); пропуск = худшее значение компонента.
    cells["g_ndvi"] = np.nan_to_num(_pct_rank(ndvi[keep]), nan=0.0)
    cells["g_nmdi"] = np.nan_to_num(_pct_rank(nmdi[keep]), nan=0.0)
    cells["g_si"] = np.nan_to_num(1 - _pct_rank(si[keep]), nan=0.0)
    cells["g_tgsi"] = np.nan_to_num(1 - _pct_rank(tgsi[keep]), nan=0.0)
    cells["M"] = suitability(cells)
    cells.attrs["cell_km"] = CELL_KM
    return cells


def _geometry(cells: pd.DataFrame):
    tr = Transformer.from_crs("EPSG:4326", EYE_CRS, always_xy=True)
    sx, sy = tr.transform([s.lon for s in SETTLEMENTS], [s.lat for s in SETTLEMENTS])
    dx = (np.asarray(sx)[None, :] - cells.x.to_numpy()[:, None]) / 1000  # от ячейки к пункту
    dy = (np.asarray(sy)[None, :] - cells.y.to_numpy()[:, None]) / 1000
    d = np.hypot(dx, dy)
    # Ветер должен дуть ОТ ячейки К пункту: направление «откуда» = азимут от пункта на ячейку
    bearing_from_settlement = (np.rad2deg(np.arctan2(-dx, -dy)) + 360) % 360
    from aralshield.forecast.features import sector_of
    sec = sector_of(bearing_from_settlement)
    return d, sec


def _population() -> np.ndarray:
    return np.array([s.population for s in SETTLEMENTS], dtype=float)


def _kernel(d: np.ndarray, L: float, d_max: float | None = SOURCE_RADIUS_KM) -> np.ndarray:
    """exp(−d/L)·N_j с обрезкой d ≤ d_max (None — без обрезки)."""
    K = np.exp(-d / L) * _population()[None, :]
    if d_max is not None:
        K = np.where(d <= d_max, K, 0.0)
    return K


def exposure(cells, rose, d, sec, L, S=None, d_max: float | None = SOURCE_RADIUS_KM) -> np.ndarray:
    """Матрица E_ij (ячейки × пункты)."""
    S = cells.S.to_numpy() if S is None else S
    return S[:, None] * rose[sec] * _kernel(d, L, d_max)


def _m(cells, weights) -> np.ndarray:
    if weights in (None, "base") or not set(SUIT_COMPONENTS) <= set(cells.columns):
        return cells.M.to_numpy()
    return suitability(cells, weights)


def priority(cells, rose_by_vt, d, sec, L=100.0, vt=8.0, suit_power=1.0, weights="base",
             d_max: float | None = SOURCE_RADIUS_KM) -> np.ndarray:
    E = exposure(cells, rose_by_vt[vt], d, sec, L, d_max=d_max)
    return (_m(cells, weights) ** suit_power) * E.sum(axis=1)


def rbo(a, b, p: float = RBO_P) -> float:
    """Rank-biased overlap (Webber et al., 2010), экстраполированный вариант для списков
    одинаковой длины k: 1 — одинаковые списки, 0 — непересекающиеся."""
    a, b = list(a), list(b)
    k = min(len(a), len(b))
    if k == 0:
        return float("nan")
    sa, sb, x, acc = set(), set(), 0, 0.0
    for d in range(1, k + 1):
        ia, ib = a[d - 1], b[d - 1]
        if ia == ib:
            x += 1
        else:
            x += (ia in sb) + (ib in sa)
        sa.add(ia)
        sb.add(ib)
        acc += x / d * p ** d
    return float((x / k) * p ** k + (1 - p) / p * acc)


def _compare(base: np.ndarray, pr: np.ndarray, base_order: np.ndarray, top_n: int) -> dict:
    """τ на объединении топ-N (определение гейта), τ по всем ячейкам, совпадение топ-N, RBO."""
    order = np.argsort(-pr, kind="mergesort")
    base_top, top = base_order[:top_n], order[:top_n]
    union = np.union1d(base_top, top)
    return {"tau_top": float(kendalltau(base[union], pr[union]).statistic),
            "tau_all": float(kendalltau(base, pr).statistic),
            "overlap": len(np.intersect1d(base_top, top)) / top_n,
            "rbo": rbo(base_top, top)}


def sensitivity(cells, rose_by_vt, d, sec, top_n: int = TOP_N) -> tuple[np.ndarray, dict]:
    """Перебор L × порог выдувания × степень M × веса пригодности (раздел 14.4).

    Устойчивость ячейки = доля всех сценариев сетки, где она в топ-N. Сравнения с базовым
    рейтингом (τ, совпадение, RBO) считаются БЕЗ самого базового сценария (он даёт 1)."""
    S = cells.S.to_numpy()
    kern = {L: _kernel(d, L) for L in SENSITIVITY["L"]}
    esum = {(L, vt): (S[:, None] * rose_by_vt[vt][sec] * kern[L]).sum(axis=1)
            for L in SENSITIVITY["L"] for vt in SENSITIVITY["vt"]}
    Ms = {w: _m(cells, w) for w in SENSITIVITY["weights"]}
    base = (Ms[BASE["weights"]] ** BASE["suit_power"]) * esum[(BASE["L"], BASE["vt"])]
    base_order = np.argsort(-base, kind="mergesort")
    hits = np.zeros(len(cells))
    rows = []
    combos = list(product(SENSITIVITY["L"], SENSITIVITY["vt"], SENSITIVITY["suit_power"], SENSITIVITY["weights"]))
    for L, vt, sp, w in combos:
        pr = (Ms[w] ** sp) * esum[(L, vt)]
        hits[np.argsort(-pr, kind="mergesort")[:top_n]] += 1
        if (L, vt, sp, w) == (BASE["L"], BASE["vt"], BASE["suit_power"], BASE["weights"]):
            continue
        rows.append({"L": L, "vt": vt, "suit_power": sp, "weights": w, **_compare(base, pr, base_order, top_n)})
    stab = hits / len(combos)
    df = pd.DataFrame(rows)

    def by(col):
        return {str(k): {m: round(float(v), 4) for m, v in g[["tau_top", "overlap", "rbo"]].mean().items()}
                for k, g in df.groupby(col)}
    # Отдельная строка: базовые параметры без ограничения дальности d ≤ R
    nocap = priority(cells, rose_by_vt, d, sec, **BASE, d_max=None)
    summary = {"scenarios": len(combos), "compared_scenarios": len(rows), "base_excluded": True, "top_n": top_n,
               "grid": {k: list(v) for k, v in SENSITIVITY.items()},
               "suit_weight_sets": {k: list(v) for k, v in SUIT_WEIGHT_SETS.items()},
               # Определение гейта не меняется: τ Кендалла на объединении топ-N базового и сценария
               "kendall_tau_top_mean": float(df.tau_top.mean()), "kendall_tau_top_min": float(df.tau_top.min()),
               "kendall_tau_all_mean": float(df.tau_all.mean()), "kendall_tau_all_min": float(df.tau_all.min()),
               "top_overlap_mean": float(df.overlap.mean()), "top_overlap_min": float(df.overlap.min()),
               "rbo_mean": float(df.rbo.mean()), "rbo_min": float(df.rbo.min()), "rbo_p": RBO_P,
               "by_parameter": {c: by(c) for c in ("L", "vt", "suit_power", "weights")},
               "no_distance_cap": {**_compare(base, nocap, base_order, top_n),
                                   "note": f"базовые параметры без обрезки d ≤ {SOURCE_RADIUS_KM} км"},
               "robust_cells": int((stab >= 0.8).sum()),
               "robust_definition": "ячейка в топ-N не менее чем в 80% сценариев сетки (включая базовый)"}
    return stab, summary


def classify_priority(pr: np.ndarray, stab: np.ndarray, M: np.ndarray | None = None,
                      m_min: float = M_MIN_TOP) -> np.ndarray:
    """Классы раздела 14.6. «Высокий» требует q ≥ 0.9 и M ≥ m_min, иначе — «средний»."""
    q = pd.Series(pr).rank(pct=True).to_numpy()
    ok = np.ones(len(pr), bool) if M is None else (np.asarray(M) >= m_min)
    high = (q >= 0.9) & ok
    cls = np.where(high, "высокий", np.where(q >= 0.7, "средний", "низкий")).astype(object)
    cls[high & (stab >= 0.8)] = "надёжный высокий"
    return cls


def protected_population(E: np.ndarray, share: float = PROTECT_SHARE) -> np.ndarray:
    """Население пунктов, которые вместе получают долю share вклада ячейки (по убыванию E_ij).
    Ячейка без вклада (S = 0 или все пункты дальше R) защищает 0 человек."""
    N = _population()
    tot = E.sum(axis=1)
    order = np.argsort(-E, axis=1, kind="mergesort")
    cum = np.cumsum(np.take_along_axis(E, order, axis=1), axis=1) / np.where(tot > 0, tot, 1)[:, None]
    k = (cum < share - 1e-12).sum(axis=1) + 1
    take = np.arange(E.shape[1])[None, :] < k[:, None]
    out = (N[order] * take).sum(axis=1)
    out[tot <= 0] = 0
    return out.astype(int)


def scenario(cells: pd.DataFrame, selected: list[int], rose, d, sec, L=100.0,
             cover: float = COVER_FACTOR, last_fraction: float = 1.0,
             d_max: float | None = SOURCE_RADIUS_KM) -> dict:
    """«Что если»: посадки в выбранных ячейках снижают S_i на cover·M_i (приживаемость ~ пригодность).
    Последняя ячейка списка засаживается на долю last_fraction (точная площадь сценария).
    Повторы и неизвестные id не учитываются; использованные id возвращаются в cell_ids."""
    ids = list(dict.fromkeys(int(i) for i in selected))
    id2pos = dict(zip(cells.cell_id.astype(int).tolist(), range(len(cells))))
    used = [i for i in ids if i in id2pos]
    pos = np.array([id2pos[i] for i in used], dtype=int)
    frac = np.ones(len(pos))
    if len(pos):
        frac[-1] = float(np.clip(last_fraction, 0.0, 1.0))
    S = cells.S.to_numpy()
    S_new = S.copy()
    S_new[pos] = S[pos] * (1 - cover * cells.M.to_numpy()[pos] * frac)
    E0 = exposure(cells, rose, d, sec, L, d_max=d_max).sum(axis=0)
    E1 = exposure(cells, rose, d, sec, L, S_new, d_max=d_max).sum(axis=0)
    per = []
    for j, s in enumerate(SETTLEMENTS):
        per.append({"settlement": s.id, "name_ru": s.name_ru, "name_kk": s.name_kk, "population": s.population,
                    "load_before": float(E0[j]), "load_after": float(E1[j]),
                    "reduction_pct": float(100 * (E0[j] - E1[j]) / E0[j]) if E0[j] > 0 else 0.0})
    area = float(frac.sum() * CELL_HA)  # га
    total0, total1 = E0.sum(), E1.sum()
    protected = sum(p["population"] * p["reduction_pct"] / 100 for p in per)
    return {"cells": len(pos), "area_ha": round(area, 6),
            "total_reduction_pct": float(100 * (total0 - total1) / total0) if total0 > 0 else 0.0,
            "people_weighted_reduction": float(protected), "per_settlement": per,
            "cell_ids": used, "unknown_ids": [i for i in ids if i not in id2pos]}


def plan_cells(cells: pd.DataFrame, plan: str, area_ha: float, d: np.ndarray | None = None,
               seed: int = 0) -> tuple[list[int], float]:
    """Ячейки плана посадок на площадь area_ha → (cell_ids по порядку, доля последней ячейки).

    rating  — по убыванию приоритета;
    random  — случайно среди ячеек с S выше медианы и M > 0.5 (честный «случайный» план);
    nearest — пригодные (M > 0.5) ячейки, ближайшие к пунктам (заместитель текущей практики:
              реального плана лесхоза в открытых данных нет, W9)."""
    if area_ha <= 0:
        return [], 1.0
    n_exact = area_ha / CELL_HA
    n = max(1, int(np.ceil(n_exact - 1e-9)))
    last = float(n_exact - (n - 1))
    if plan == "rating":
        ids = cells.sort_values("priority", ascending=False, kind="mergesort").cell_id.to_numpy()
    elif plan == "random":
        pool = cells[(cells.S > cells.S.median()) & (cells.M > 0.5)].cell_id.to_numpy()
        ids = np.random.default_rng(seed).permutation(pool)
    elif plan == "nearest":
        if d is None:
            raise ValueError("для плана nearest нужна матрица расстояний d")
        m = (cells.M > 0.5).to_numpy()
        dmin = d[m].min(axis=1)
        ids = cells.cell_id.to_numpy()[m][np.argsort(dmin, kind="mergesort")]
    else:
        raise ValueError(f"неизвестный план: {plan}")
    ids = [int(i) for i in ids[:n]]
    if len(ids) < n:  # пул исчерпан — площадь меньше запрошенной
        last = 1.0
    return ids, last


def random_plan_distribution(cells, area_ha, rose, d, sec, L=100.0, cover=COVER_FACTOR,
                             n_draws: int = RANDOM_DRAWS, seed: int = 0) -> dict:
    """Распределение people_weighted_reduction для случайных планов (mean, p5, p95)."""
    vals = []
    for k in range(n_draws):
        ids, last = plan_cells(cells, "random", area_ha, d, seed=seed + k)
        vals.append(scenario(cells, ids, rose, d, sec, L=L, cover=cover, last_fraction=last)
                    ["people_weighted_reduction"])
    v = np.asarray(vals)
    return {"mean": float(v.mean()), "p5": float(np.percentile(v, 5)), "p95": float(np.percentile(v, 95)),
            "n_draws": n_draws, "pool": "S > медианы и M > 0.5",
            "pool_cells": int(((cells.S > cells.S.median()) & (cells.M > 0.5)).sum())}


def settlement_contributions(cells, E: np.ndarray, E_nocap: np.ndarray, d: np.ndarray,
                             top_idx: np.ndarray) -> list[dict]:
    """Таблица вкладов пунктов: какие пункты определяют рейтинг."""
    tot, tot_nc, top = E.sum(), E_nocap.sum(), E[top_idx].sum()
    main = E.argmax(axis=1)
    has = E.sum(axis=1) > 0
    out = []
    for j, s in enumerate(SETTLEMENTS):
        out.append({"settlement": s.id, "name_ru": s.name_ru, "name_kk": s.name_kk, "population": s.population,
                    "share_total_E": round(float(E[:, j].sum() / tot), 4) if tot > 0 else 0.0,
                    "share_total_E_no_cap": round(float(E_nocap[:, j].sum() / tot_nc), 4) if tot_nc > 0 else 0.0,
                    "share_top_E": round(float(E[top_idx, j].sum() / top), 4) if top > 0 else 0.0,
                    "cells_main_target": int(((main == j) & has).sum()),
                    "cells_within_radius": int((d[:, j] <= SOURCE_RADIUS_KM).sum()),
                    "d_min_km": round(float(d[:, j].min()), 1), "d_median_km": round(float(np.median(d[:, j])), 1)})
    return out


def run(tag: str = EYE_TAG) -> dict:
    cells = build_cells(tag)
    rose = wind_rose()
    d, sec = _geometry(cells)
    pr = priority(cells, rose, d, sec, **BASE)
    stab, sens = sensitivity(cells, rose, d, sec)
    cells["priority"] = pr / pr.max() if pr.max() > 0 else pr
    cells["stability"] = stab
    cells["class"] = classify_priority(pr, stab, cells.M.to_numpy())
    E = exposure(cells, rose[BASE["vt"]], d, sec, BASE["L"])
    has = E.sum(axis=1) > 0
    cells["protects"] = np.where(has, np.array([s.id for s in SETTLEMENTS], dtype=object)[E.argmax(axis=1)], "")
    cells["protected_population"] = protected_population(E)
    cells.drop(columns=["x", "y"]).to_parquet(SHIELD_DIR / "priority_cells.parquet", index=False)

    # Сравнение планов на ровно 1000 га (последняя ячейка засаживается частично)
    area = 1000.0
    vt_rose = rose[BASE["vt"]]
    ids_r, last_r = plan_cells(cells, "rating", area, d)
    ids_n, last_n = plan_cells(cells, "nearest", area, d)
    by_rating = scenario(cells, ids_r, vt_rose, d, sec, last_fraction=last_r)
    nearest = scenario(cells, ids_n, vt_rose, d, sec, last_fraction=last_n)
    rnd = random_plan_distribution(cells, area, vt_rose, d, sec)
    cmp = {"area_ha": by_rating["area_ha"], "cell_ha": CELL_HA,
           "by_rating": by_rating, "nearest_suitable": nearest, "random_suitable": rnd,
           "random_suitable_people_weighted_mean": rnd["mean"],  # старый ключ (совместимость)
           "note": ("nearest — пригодные ячейки, ближайшие к пунктам: заместитель «текущего плана», "
                    "реального плана лесхоза нет в открытых данных")}
    top_idx = np.argsort(-pr, kind="mergesort")[:TOP_N]
    E_nocap = exposure(cells, vt_rose, d, sec, BASE["L"], d_max=None)
    result = {"tag": tag, "cells": len(cells), "cell_km": CELL_KM, "base_params": BASE,
              "distance_cap_km": SOURCE_RADIUS_KM, "m_min_top": M_MIN_TOP,
              "suitable_share_m_gt_0_5": round(float((cells.M > 0.5).mean()), 4),
              "sensitivity": sens,
              "wind_rose_8ms": dict(zip(SECTOR_NAMES, np.round(rose[8.0], 4).tolist())),
              "class_counts": cells["class"].value_counts().to_dict(), "comparison_1000ha": cmp,
              "per_settlement": settlement_contributions(cells, E, E_nocap, d, top_idx),
              "definitions": {
                  "E_ij": f"S_i·P_ij·exp(−d_ij/L)·N_j при d_ij ≤ {SOURCE_RADIUS_KM} км, иначе 0",
                  "M_i": ("clip((0.35·r(NDVI) + 0.25·r(NMDI) + 0.25·(1−r(SI)) + 0.15·(1−r(TGSI)) − 0.2)/0.6, 0, 1), "
                          "r — процентильный ранг по валидным ячейкам; доступность от дорог не учтена (нет слоя, W9). "
                          "M — относительный ранг, а не агрономический порог: доля M > 0.5 заметно выше ≈15% "
                          "пригодной площади по Kim et al. (2020)"),
                  "class": (f"высокий: верхние 10% приоритета и M ≥ {M_MIN_TOP}; надёжный высокий: плюс устойчивость "
                            "≥ 0.8; средний: 70–90% (или верхние 10% при M < порога); низкий: остальное"),
                  "protected_population": (f"население пунктов, которые вместе получают {int(PROTECT_SHARE * 100)}% "
                                           "вклада ячейки (по убыванию E_ij); 0, если вклада нет"),
                  "protects": "пункт с наибольшим E_ij; пусто, если вклада нет",
                  "scenario_area": "точная площадь: последняя ячейка засаживается частично",
              },
              # Гейт раздела 14.4: определение не меняется (τ на объединении топ-N, без базового сценария)
              "gate_rank_stability_ge_0_7": bool(sens["kendall_tau_top_mean"] >= 0.7)}
    export(cells)
    _write_json(REPORTS / "shield_metrics.json", result)
    return result


def _write_json(path, obj):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    os.replace(tmp, path)


def _cell_rings(cells: pd.DataFrame) -> np.ndarray:
    """Настоящие углы квадратов UTM (CELL_KM × CELL_KM) в WGS84: массив (n, 5, 2), кольцо
    SW→SE→NE→NW→SW — против часовой стрелки (RFC 7946); 5 знаков после запятой."""
    if {"x", "y"} <= set(cells.columns):
        x, y = cells.x.to_numpy(float), cells.y.to_numpy(float)
    else:
        x, y = Transformer.from_crs("EPSG:4326", EYE_CRS, always_xy=True).transform(
            cells.lon.to_numpy(float), cells.lat.to_numpy(float))
        x, y = np.asarray(x), np.asarray(y)
    h = CELL_KM * 500.0
    cx = x[:, None] + np.array([-h, h, h, -h, -h])[None, :]
    cy = y[:, None] + np.array([-h, -h, h, h, -h])[None, :]
    lon, lat = Transformer.from_crs(EYE_CRS, "EPSG:4326", always_xy=True).transform(cx.ravel(), cy.ravel())
    return np.round(np.stack([np.asarray(lon), np.asarray(lat)], axis=-1).reshape(len(x), 5, 2), 5)


def to_geojson(cells: pd.DataFrame) -> dict:
    """GeoJSON ячеек (векторизовано; схема свойств прежняя)."""
    if len(cells) == 0:
        return {"type": "FeatureCollection", "features": []}
    rings = _cell_rings(cells).tolist()
    cols = zip(cells.cell_id.astype(int).tolist(), np.round(cells.priority.to_numpy(float), 4).tolist(),
               cells["class"].tolist(), np.round(cells.M.to_numpy(float), 3).tolist(),
               np.round(cells.S.to_numpy(float), 3).tolist(), np.round(cells.stability.to_numpy(float), 2).tolist(),
               cells.protects.tolist(), cells.protected_population.astype(int).tolist())
    feats = [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
              "properties": {"cell_id": cid, "priority": p, "class": c, "suitability": m, "source": s,
                             "stability": st, "protects": pt, "protected_population": pp, "area_ha": CELL_HA}}
             for ring, (cid, p, c, m, s, st, pt, pp) in zip(rings, cols)]
    return {"type": "FeatureCollection", "features": feats}


def export(cells: pd.DataFrame, out_dir=None):
    """GeoJSON, KML и Shapefile для ГИС лесного хозяйства (раздел 14.6)."""
    out_dir = SHIELD_DIR if out_dir is None else out_dir
    top = cells[cells["class"].isin(["надёжный высокий", "высокий"])]
    gj = to_geojson(top)
    (out_dir / "priority_top.geojson").write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    kml = ['<?xml version="1.0" encoding="UTF-8"?>', '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
           "<name>AralShield — приоритеты посадок саксаула</name>"]
    for f in gj["features"]:
        p = f["properties"]
        coords = " ".join(f"{x:.5f},{y:.5f},0" for x, y in f["geometry"]["coordinates"][0])
        ext = "".join(f'<Data name="{k}"><value>{escape(str(p[k]))}</value></Data>'
                      for k in ("cell_id", "priority", "class", "suitability", "source", "stability", "protects",
                                "protected_population", "area_ha"))
        name = f"Ячейка {p['cell_id']} ({p['class']})"
        desc = (f"приоритет {p['priority']}, пригодность {p['suitability']}, площадь {p['area_ha']} га, "
                f"защищает: {p['protects']} ({p['protected_population']} чел.), устойчивость {p['stability']}")
        kml.append(f"<Placemark><name>{escape(name)}</name><description>{escape(desc)}</description>"
                   f"<ExtendedData>{ext}</ExtendedData>"
                   f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{coords}</coordinates>"
                   f"</LinearRing></outerBoundaryIs></Polygon></Placemark>")
    kml.append("</Document></kml>")
    (out_dir / "priority_top.kml").write_text("\n".join(kml), encoding="utf-8")
    try:
        import shapefile  # pyshp
    except ImportError:
        log.warning("pyshp не установлен — Shapefile не создан")
        return
    # Ширины числовых полей ≤ 19 (dBase); строки — в байтах UTF-8
    fields = (("cell_id", "N", 10, 0), ("priority", "F", 12, 4), ("class", "C", 50, 0), ("suitab", "F", 8, 3),
              ("protects", "C", 32, 0), ("area_ha", "N", 8, 0), ("prot_pop", "N", 10, 0), ("stab", "F", 6, 2),
              ("source", "F", 8, 3))
    with shapefile.Writer(str(out_dir / "priority_top"), shapeType=shapefile.POLYGON, encoding="utf-8") as w:
        for name, typ, size, dec in fields:
            w.field(name, typ, size=size, decimal=dec)
        for f in gj["features"]:
            p = f["properties"]
            # ESRI: внешнее кольцо — ПО часовой стрелке (GeoJSON/KML остаются против часовой)
            w.poly([f["geometry"]["coordinates"][0][::-1]])
            w.record(p["cell_id"], p["priority"], p["class"], p["suitability"], p["protects"], p["area_ha"],
                     p["protected_population"], p["stability"], p["source"])
    (out_dir / "priority_top.prj").write_text(
        'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
        'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]', encoding="utf-8")
    (out_dir / "priority_top.cpg").write_text("UTF-8", encoding="ascii")
