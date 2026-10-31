"""Обучение и честная проверка модуля «Прогноз» (разделы 13.3–13.8, 23.2).

Разбиение по времени: обучение — TRAIN_YEARS, валидация (подбор, калибровка,
пороги) — VALID_YEARS, тест — TEST_YEARS, с зазором GAP_DAYS между частями.
Каждая модель сравнивается с базовыми линиями B0–B3; базовые линии без гиперпараметров
подгоняются на обучении + валидации (последние годы перед тестом).

Важно для интерпретации: признаки погоды целевых суток во всех горизонтах — ERA5
(фактическая погода дня), поэтому h1/h2 на тесте — верхняя граница качества
(perfect prognosis); цена перехода на прогноз оценивается в evaluate_operational.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
import platform
from datetime import date

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import fisher_exact
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from aralshield.config import (FEATURES, GAP_DAYS, HIST_FORECAST_MODELS, MODELS, REPORTS, STATIONS, TEST_YEARS,
                               TRAIN_YEARS, VALID_YEARS, YEARS)
from aralshield.forecast import metrics as M
from aralshield.forecast.features import ALL_FEATURES, MONOTONE_UP, SOURCE_FEATURES

log = logging.getLogger(__name__)
FIG = REPORTS / "figures"
FIG.mkdir(parents=True, exist_ok=True)
MODEL_VERSION = "forecast-lgbm-1.1.0"
HORIZONS = (0, 1, 2)
BASELINES = ("B0", "B1", "B2", "B2-iso")
IDENTICAL_MAX = 0.95  # станция исключается из проверки, если ≥95% часов ветра совпадают с ERA5
WEATHER_INPUTS = {0: "era5_actual_same_day", 1: "era5_actual_upper_bound", 2: "era5_actual_upper_bound"}
HIDDEN_REPLAY = {"station_code", "doy_cos"}


# ---------------------------------------------------------------- разбиение
def split(ds: pd.DataFrame) -> dict[str, pd.DataFrame]:
    d = ds[ds.y.notna()].copy()
    yr = d.date.dt.year
    tr = d[yr.isin(TRAIN_YEARS)]
    va = d[yr.isin(VALID_YEARS)]
    te = d[yr.isin(TEST_YEARS)]
    va = va[va.date >= pd.Timestamp(min(VALID_YEARS), 1, 1) + pd.Timedelta(days=GAP_DAYS)]
    te = te[te.date >= pd.Timestamp(min(TEST_YEARS), 1, 1) + pd.Timedelta(days=GAP_DAYS)]
    return {"train": tr, "valid": va, "test": te}


# ---------------------------------------------------------------- базовые линии
class Climatology:
    """B0: частота пылевых дней станции в данном месяце на обучении (+ валидации)."""

    def fit(self, d):
        self.table = d.groupby(["station", d.date.dt.month])["y"].mean()
        self.overall = d.y.mean()
        return self

    def predict(self, d):
        key = list(zip(d.station, d.date.dt.month))
        return np.array([self.table.get(k, self.overall) for k in key])

    def to_json(self) -> dict:
        return {"table": {f"{s}|{m}": float(v) for (s, m), v in self.table.items()}, "overall": float(self.overall)}


class Persistence:
    """B1: «завтра как сегодня» в вероятностной форме: P(y | событие в день t-1)."""

    def fit(self, d, clim):
        self.table = d.groupby(["station", "dust_lag"])["y"].mean()
        self.clim = clim
        return self

    def predict(self, d):
        base = self.clim.predict(d)
        out = []
        for st, lag, b in zip(d.station, d.dust_lag, base):
            out.append(self.table.get((st, lag), b) if not np.isnan(lag) else b)
        return np.array(out)

    @staticmethod
    def binary(d):
        return np.nan_to_num(d.dust_lag.to_numpy(), nan=0.0)


class WindRule:
    """B2: событие, если максимальный порыв ≥ G и не менее N ≥ 1 суток без осадков.

    N ≥ 1, чтобы правило действительно проверяло сухость; лучший CSI при N = 0
    сохраняется (csi_no_dry), чтобы честно сказать, добавляет ли сухость что-то к ветру."""

    def fit(self, d):
        best, best0 = (-1, None), -1.0
        for g, n in itertools.product(np.arange(8, 26, 1.0), range(0, 8)):
            fire = (d.gust_max >= g) & (d.dry_days >= n)
            c = M.contingency(fire.astype(float), d.y, 0.5)["CSI"]
            if n == 0:
                best0 = max(best0, c)
            elif c > best[0]:
                best = (c, (g, n))
        self.g, self.n = best[1]
        self.csi, self.csi_no_dry = float(best[0]), float(best0)
        fire = self.fire(d)
        self.p_fire = d[fire].groupby("station")["y"].mean()
        self.p_quiet = d[~fire].groupby("station")["y"].mean()
        return self

    def fire(self, d):
        return ((d.gust_max >= self.g) & (d.dry_days >= self.n)).to_numpy()

    def predict(self, d):
        f = self.fire(d)
        pf = d.station.map(self.p_fire).fillna(self.p_fire.mean()).to_numpy()
        pq = d.station.map(self.p_quiet).fillna(self.p_quiet.mean()).to_numpy()
        return np.where(f, pf, pq)

    def to_json(self) -> dict:
        return {"gust_ms": float(self.g), "dry_days": float(self.n),
                "p_fire": {k: float(v) for k, v in self.p_fire.items()},
                "p_quiet": {k: float(v) for k, v in self.p_quiet.items()}}


class WindIsotonic:
    """B2-iso: непрерывная откалиброванная базовая линия — изотоническая P(y | порыв) по станциям."""

    def fit(self, d):
        self.iso, self.base = {}, d.groupby("station")["y"].mean()
        for st, s in d.dropna(subset=["gust_max"]).groupby("station"):
            self.iso[st] = IsotonicRegression(out_of_bounds="clip").fit(s.gust_max, s.y)
        return self

    def predict(self, d):
        out = d.station.map(self.base).fillna(self.base.mean()).to_numpy(float)
        for st, iso in self.iso.items():
            m = ((d.station == st) & d.gust_max.notna()).to_numpy()
            if m.any():
                out[m] = iso.predict(d.gust_max[m])
        return out


# ---------------------------------------------------------------- модели
def _usable(cols, d):
    return [c for c in cols if c in d and d[c].notna().any()]


def fit_logistic(tr, va, feats):
    feats = _usable(feats, tr)
    pipe = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                         LogisticRegression(max_iter=2000, C=0.5, class_weight="balanced"))
    pipe.fit(tr[feats], tr.y)
    iso = IsotonicRegression(out_of_bounds="clip").fit(pipe.predict_proba(va[feats])[:, 1], va.y)
    return {"kind": "logreg", "model": pipe, "calib": iso, "features": feats}


def fit_lgbm(tr, va, feats, seed: int = 42):
    feats = _usable(feats, tr)
    mono = [1 if f in MONOTONE_UP else 0 for f in feats]
    pos = tr.y.mean()
    best = None
    # Подбор на валидационном периоде (тестовые годы в подборе не участвуют)
    grid = itertools.product((7, 15, 31), (50, 150), (1.0, min(5.0, (1 - pos) / max(pos, 1e-3))))
    for leaves, min_child, spw in grid:
        params = dict(objective="binary", learning_rate=0.03, num_leaves=leaves,
                      min_child_samples=min_child, feature_fraction=0.7, bagging_fraction=0.8,
                      bagging_freq=1, lambda_l2=5.0, monotone_constraints=mono,
                      monotone_constraints_method="advanced", scale_pos_weight=spw,
                      seed=seed, verbose=-1, deterministic=True, force_row_wise=True)
        dtr = lgb.Dataset(tr[feats], tr.y, categorical_feature=["station_code"] if "station_code" in feats else "auto")
        dva = lgb.Dataset(va[feats], va.y, reference=dtr)
        booster = lgb.train(params, dtr, num_boost_round=3000, valid_sets=[dva],
                            callbacks=[lgb.early_stopping(150, verbose=False)])
        score = M.pr_auc(booster.predict(va[feats], num_iteration=booster.best_iteration), va.y)
        if best is None or score > best[0]:
            best = (score, booster, params)
    _, booster, params = best
    raw_va = booster.predict(va[feats], num_iteration=booster.best_iteration)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999).fit(raw_va, va.y)
    return {"kind": "lgbm", "model": booster, "calib": iso, "features": feats,
            "params": {k: v for k, v in params.items() if k != "monotone_constraints"},
            "best_iteration": booster.best_iteration, "version": MODEL_VERSION}


def predict_raw(art: dict, d: pd.DataFrame) -> np.ndarray:
    X = d[art["features"]]
    if art["kind"] == "lgbm":
        return art["model"].predict(X, num_iteration=art["best_iteration"])
    return art["model"].predict_proba(X)[:, 1]


def predict(art: dict, d: pd.DataFrame) -> np.ndarray:
    return art["calib"].predict(predict_raw(art, d))


def shap_values(art: dict, d: pd.DataFrame) -> pd.DataFrame:
    """Вклады признаков (TreeSHAP из LightGBM) в лог-шансы."""
    contrib = art["model"].predict(d[art["features"]], num_iteration=art["best_iteration"], pred_contrib=True)
    return pd.DataFrame(contrib[:, :-1], columns=art["features"], index=d.index)


def oof_valid_probs(art: dict, va: pd.DataFrame) -> np.ndarray:
    """Калиброванные вероятности на валидации «вне выборки»: изотоника для каждого года
    валидации подгоняется на остальных её годах (пороги не выбираются на тех же точках,
    на которых подогнана калибровка)."""
    raw = predict_raw(art, va)
    years = va.date.dt.year.to_numpy()
    y = va.y.to_numpy()
    out = art["calib"].predict(raw)
    for yr in np.unique(years):
        m = years == yr
        if (~m).sum() and y[~m].sum():
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999).fit(raw[~m], y[~m])
            out[m] = iso.predict(raw[m])
    return out


# ---------------------------------------------------------------- пороги
def choose_thresholds(p, o) -> dict:
    grid = np.round(np.arange(0.05, 0.96, 0.01), 2)
    tab = pd.DataFrame([M.contingency(p, o, t) for t in grid])
    tab = tab[tab.TP + tab.FP > 0]

    def pick(cond, key, largest=True):
        sub = tab[cond(tab)] if cond else tab
        if sub.empty:
            sub = tab
        row = sub.sort_values(key, ascending=not largest).iloc[0]
        return float(row.threshold)

    t_school = pick(None, "CSI")
    t_health = float(tab[tab.POD >= 0.75].threshold.max()) if (tab.POD >= 0.75).any() else t_school * 0.7
    t_health = min(t_health, t_school)
    # Акимат/ДЧС: только уверенные события — лучший CSI среди порогов с FAR ≤ 0,4
    # и при этом не ниже порога школ; полнота не должна падать ниже 25%.
    ok = tab[(tab.FAR <= 0.4) & (tab.POD >= 0.25) & (tab.threshold >= t_school)]
    t_akimat = float(ok.sort_values("CSI", ascending=False).iloc[0].threshold) if not ok.empty else t_school
    levels = {}
    for group, orange in (("health", t_health), ("school", t_school), ("public", t_school), ("akimat", t_akimat)):
        yellow = round(max(0.05, orange * 0.6), 2)
        strict = tab[(tab.threshold > orange) & (tab.FAR <= 0.25)]
        red = float(strict.threshold.min()) if not strict.empty else orange + 0.2
        red = round(min(0.95, max(red, orange + 0.1)), 2)
        levels[group] = {"yellow": yellow, "orange": round(orange, 2), "red": red}
    return levels


# ---------------------------------------------------------------- H3
def h3_test(p_full, p_abl, o, p_clim, dates) -> dict:
    """H3 (раздел 13.6): помогают ли признаки источника. Парный блочный бутстреп по неделям
    разности M2 − M2-abl; гипотеза подтверждается, только если нижняя граница 95% ДИ ΔPR-AUC > 0."""
    d = M.paired_delta(p_full, p_abl, o, p_clim, dates)
    lo = d["delta_pr_auc_ci"][0]
    d["confirmed"] = bool(np.isfinite(lo) and lo > 0)
    return d


# ---------------------------------------------------------------- B3 CAMS
def evaluate_cams(test: pd.DataFrame, art: dict) -> dict:
    """Сравнение с CAMS на общем периоде: калибровка на 2022-08..2023, проверка 2024–2025."""
    from aralshield.weather.openmeteo import cams_station_history
    rows = []
    for st in STATIONS:
        try:
            c = cams_station_history(st.id, date(2022, 8, 1), date(2025, 12, 31))
        except Exception as e:  # сервис недоступен — честно фиксируем
            log.warning("CAMS недоступен: %s", e)
            return {"available": False, "error": str(e)}
        c["date"] = c.time_local.dt.normalize()
        dmax = c.groupby("date")["dust"].max().rename("cams_dust_max").reset_index()
        dmax["station"] = st.id
        rows.append(dmax)
    cams = pd.concat(rows)
    d = test[test.horizon == 0].merge(cams, on=["station", "date"], how="inner").dropna(subset=["cams_dust_max"])
    fit_part = d[d.date < "2024-01-01"]
    ev_part = d[d.date >= "2024-01-01"]
    if len(fit_part) < 100 or len(ev_part) < 100 or ev_part.y.sum() < 5:
        return {"available": False, "error": "мало пересечения данных"}
    iso = IsotonicRegression(out_of_bounds="clip").fit(fit_part.cams_dust_max, fit_part.y)
    p_cams = iso.predict(ev_part.cams_dust_max)
    p_model = predict(art, ev_part)
    clim = ev_part["p_clim"].to_numpy()
    return {
        "available": True, "period": f"2024-01-01..{ev_part.date.max().date()}", "horizon": 0,
        "cams": M.summary(p_cams, ev_part.y, clim, ev_part.date, with_ci=True),
        "model": M.summary(p_model, ev_part.y, clim, ev_part.date, with_ci=True),
        "like_for_like": False,
        "note": ("CAMS — прогноз/анализ приземной пыли, порог по процентилям заменён изотонической калибровкой. "
                 "Сравнение не равноправное: AralShield здесь получает фактическую погоду дня (ERA5), "
                 "а CAMS — собственный модельный прогноз"),
    }


# ---------------------------------------------------------------- разрыв «реанализ → прогноз»
def identical_fraction(op: pd.DataFrame, ref: pd.DataFrame, col: str = "wind_speed_10m") -> float:
    """Доля часов, где ряд «оперативного прогноза» совпадает с ERA5 (до 0,01): признак того,
    что сервис вместо прогноза отдал реанализ."""
    j = op[["time_local", col]].merge(ref[["time_local", col]], on="time_local", suffixes=("_op", "_re"))
    j = j.dropna()
    if j.empty:
        return float("nan")
    return float((np.round(j[f"{col}_op"].astype(float), 2) == np.round(j[f"{col}_re"].astype(float), 2)).mean())


def excluded_stations(fracs: dict[str, float], limit: float = IDENTICAL_MAX) -> list[str]:
    """Станции, где «прогноз» фактически является ERA5 (или где данных нет)."""
    return [st for st, f in fracs.items() if not np.isfinite(f) or f >= limit]


def _evaluate_operational_model(test: pd.DataFrame, arts: dict, model: str) -> dict:
    from aralshield.forecast.dataset import STATION_CODE, load_sector_source
    from aralshield.forecast.features import add_persistence, daily_features
    from aralshield.weather.openmeteo import era5_station_history, hist_forecast_station

    sectors = load_sector_source(masked=True) or {}
    t0 = test[test.horizon == 0]
    rows, fracs, bias_rows = [], {}, []
    for st in STATIONS:
        try:
            hourly = hist_forecast_station(st.id, date(2022, 1, 1), date(2025, 12, 31), model=model)
            era5 = era5_station_history(st.id, YEARS)
        except Exception as e:
            return {"available": False, "model": model, "error": str(e)}
        fracs[st.id] = round(identical_fraction(hourly, era5), 4)
        if st.id in excluded_stations({st.id: fracs[st.id]}):
            continue
        sec = np.asarray(sectors[st.id]) if st.id in sectors else None
        f = daily_features(hourly, sec)
        # Смещение ветра «прогноз / реанализ» по тем же суткам (раздел 13.3, T03-5)
        fe = daily_features(era5[pd.to_datetime(era5.time_local).dt.year >= 2022], sec)
        b = f[["date", "wind_max", "gust_max", "hours_gt8"]].merge(
            fe[["date", "wind_max", "gust_max", "hours_gt8"]], on="date", suffixes=("_op", "_re")).dropna()
        b["station"] = st.id
        bias_rows.append(b)
        sub = t0[t0.station == st.id].set_index("date")
        f = add_persistence(f, sub["y"], 0)  # метки до дня выпуска
        f["station"], f["station_code"] = st.id, STATION_CODE[st.id]
        f = f.merge(sub[["y", "p_clim"]], left_on="date", right_index=True, how="inner")
        rows.append(f)
    excl = excluded_stations(fracs)
    base = {"model": model, "identical_frac": fracs, "excluded_stations": excl,
            "identical_limit": IDENTICAL_MAX}
    if not rows:
        return {**base, "available": False, "error": "у всех станций ряд совпадает с ERA5 или пуст"}
    op = pd.concat(rows).dropna(subset=["wind_max", "y"])
    if len(op) < 200 or op.y.sum() < 10:
        return {**base, "available": False, "error": "мало данных"}
    re = t0.merge(op[["station", "date"]], on=["station", "date"])
    m = arts[0]["m2"]
    bias = pd.concat(bias_rows)
    bias = bias[bias.date.isin(op.date)]
    ratio = {k: round(float(bias[f"{k}_op"].sum() / bias[f"{k}_re"].sum()), 3) if bias[f"{k}_re"].sum() else None
             for k in ("wind_max", "gust_max", "hours_gt8")}
    return {
        **base, "available": True, "stations": sorted(op.station.unique().tolist()),
        "period": f"{op.date.min().date()}..{op.date.max().date()}", "horizon": 0, "n_days": int(len(op)),
        "reanalysis": M.summary(predict(m, re), re.y, re.p_clim, re.date),
        "operational": M.summary(predict(m, op), op.y, op.p_clim, op.date),
        "rule_B2_operational": M.summary(arts[0]["rule"].predict(op), op.y, op.p_clim, op.date, with_ci=False),
        "bias_ratio_op_to_era5": ratio,
        "missing_share": {k: round(float(op[k].isna().mean()), 3) for k in ("gust_max", "soil_moisture", "snow_depth")},
        "note": f"Open-Meteo Historical Forecast, модель {model} (склейка первых часов прогонов) ≈ горизонт 0–24 ч",
    }


def evaluate_operational(test: pd.DataFrame, arts: dict, models=HIST_FORECAST_MODELS) -> dict:
    """Проверка на архиве оперативных прогнозов погоды (раздел 13.3, 2022–2025).

    Open-Meteo Historical Forecast склеивает первые часы последовательных прогонов,
    поэтому это приближение горизонта 0–24 ч. Та же модель h0 оценивается на тех же
    датах с признаками из ERA5 и из оперативного прогноза — разница и есть цена
    перехода в боевой режим. Станции, где «прогноз» побайтно совпадает с ERA5
    (≥ IDENTICAL_MAX часов ветра), исключаются и перечисляются в excluded_stations.
    Основной результат — первая модель списка (та же, что в боевом режиме)."""
    by_model = {m: _evaluate_operational_model(test, arts, m) for m in models}
    primary = next((by_model[m] for m in models if by_model[m].get("available")), by_model[models[0]])
    return {**primary, "by_model": by_model}


# ---------------------------------------------------------------- H4
def _mh_or(a: np.ndarray, b: np.ndarray, strata: np.ndarray) -> float:
    """Отношение шансов Мантеля–Хензеля по стратам (a, b ∈ {0, 1})."""
    if len(a) == 0:
        return float("nan")
    k = int(strata.max()) + 1
    n = np.bincount(strata, minlength=k).astype(float)
    tp = np.bincount(strata, a * b, k)
    fp = np.bincount(strata, a * (1 - b), k)
    fn = np.bincount(strata, (1 - a) * b, k)
    tn = np.bincount(strata, (1 - a) * (1 - b), k)
    m = n > 0
    num = np.sum(tp[m] * tn[m] / n[m])
    den = np.sum(fp[m] * fn[m] / n[m])
    return float(num / den) if den > 0 else float("nan")


def _block_perm_index(dates: pd.DatetimeIndex, rng, block: int = 7) -> np.ndarray:
    """Перестановка недельных блоков ряда внутри одного календарного месяца разных лет:
    сохраняет сезонность и автокорреляцию внутри недели, разрушает синхронность станций."""
    n = len(dates)
    idx = np.arange(n)
    nb = n // block
    starts = np.arange(nb) * block
    months = dates[starts].month.to_numpy()
    out = idx.copy()
    for mth in np.unique(months):
        bl = starts[months == mth]
        perm = rng.permutation(bl)
        for s_to, s_from in zip(bl, perm):
            out[s_to:s_to + block] = idx[s_from:s_from + block]
    return out


def test_h4(ds: pd.DataFrame, n_perm: int = 999, seed: int = 42) -> dict:
    """H4: пыль в Аральске предшествует пыли в Кызылорде? Наивный тест Фишера дополнен
    ОШ Мантеля–Хензеля со стратами «год–месяц» (убирает общий сезонный ход) и p-значением
    перестановки недельных блоков (учёт автокорреляции)."""
    d0 = ds[ds.horizon == 0]
    ar = d0[d0.station == "aralsk"].set_index("date")["y"]
    kz = d0[d0.station == "kyzylorda"].set_index("date")
    cal = pd.date_range(min(ar.index.min(), kz.index.min()), max(ar.index.max(), kz.index.max()), freq="D")
    A = ar.reindex(cal).to_numpy(float)
    rng = np.random.default_rng(seed)
    perms = [_block_perm_index(cal, rng) for _ in range(n_perm)]
    strata_all = ((cal.year - cal.year.min()) * 12 + cal.month - 1).to_numpy()
    res = {}
    for k in (0, 1, 2):
        B = kz["y"].reindex(cal).shift(-k).to_numpy(float)
        ds_ = kz["dir_sin"].reindex(cal).shift(-k).to_numpy(float)
        dc_ = kz["dir_cos"].reindex(cal).shift(-k).to_numpy(float)
        out = {}
        for name in ("all", "nw_w_wind"):
            def stat(Bv, S, C, name=name):
                ok = np.isfinite(A) & np.isfinite(Bv)
                if name == "nw_w_wind":
                    ang = np.rad2deg(np.arctan2(S, C)) % 360
                    ok &= np.isfinite(ang) & (ang >= 247.5) & (ang <= 337.5)
                return ok, _mh_or(A[ok], Bv[ok], strata_all[ok])
            ok, mh = stat(B, ds_, dc_)
            a, b = A[ok], B[ok]
            tab = pd.crosstab(a, b).reindex(index=[0.0, 1.0], columns=[0.0, 1.0], fill_value=0)
            odds, p = fisher_exact(tab.to_numpy())
            null = np.array([stat(B[pi], ds_[pi], dc_[pi])[1] for pi in perms])
            null = null[np.isfinite(null)]
            p_perm = float((1 + np.sum(null >= mh)) / (1 + len(null))) if np.isfinite(mh) and len(null) else float("nan")
            out[name] = {"n": int(ok.sum()), "odds_ratio": float(odds), "p_value": float(p),
                         "mh_or": mh, "p_perm": p_perm,
                         "P(kyzylorda)": float(b.mean()) if len(b) else float("nan"),
                         "P(kyzylorda|aralsk)": float(b[a == 1].mean()) if (a == 1).any() else float("nan"),
                         "table": tab.to_numpy().tolist()}
        out["significant"] = bool(np.isfinite(out["all"]["mh_or"]) and out["all"]["mh_or"] > 1
                                  and out["all"]["p_perm"] < 0.05)
        res[f"lag_{k}"] = out
    sig = [k for k in (0, 1, 2) if res[f"lag_{k}"]["significant"]]
    nsig = [k for k in (0, 1, 2) if k not in sig]
    res["method"] = ("ОШ Мантеля–Хензеля, страты год–месяц; p — перестановка недельных блоков "
                     f"внутри календарного месяца ({n_perm} перестановок), односторонняя (ОШ > 1)")
    if sig:
        ru = (f"Совместная встречаемость пыли в Аральске и Кызылорде сверх сезонного хода значима "
              f"на сдвиг{'ах' if len(sig) > 1 else 'е'} {' и '.join(map(str, sig))} сут")
        kk = (f"Арал мен Қызылордадағы шаңның маусымдық жүрістен тыс бірге байқалуы "
              f"{' және '.join(map(str, sig))} тәуліктік ығысуда маңызды")
        if nsig:
            ru += f"; на сдвиг{'ах' if len(nsig) > 1 else 'е'} {' и '.join(map(str, nsig))} сут — не значима"
            kk += f"; {' және '.join(map(str, nsig))} тәуліктік ығысуда маңызды емес"
        ru += ". Перенос пыли из Аральска этим не доказан: возможна общая синоптическая причина."
        kk += ". Бұл шаңның Аралдан тасымалданатынын дәлелдемейді: ортақ синоптикалық себеп болуы мүмкін."
    else:
        ru = "H4 не подтверждается: с учётом сезона (страты год–месяц) связь не значима ни на одном сдвиге."
        kk = "H4 расталмады: маусымды ескергенде (жыл–ай страталары) байланыс ешбір ығысуда маңызды емес."
    res["conclusion_ru"], res["conclusion_kk"] = ru, kk
    res["conclusion"] = ru
    return res


# ---------------------------------------------------------------- реплей
def _replay_drivers(sv: pd.Series, x: pd.Series, k: int = 5) -> list[dict]:
    """Факторы события для реплея: без признаков со значением NaN и без station_code."""
    keep = [f for f in sv.index if f not in HIDDEN_REPLAY and not pd.isna(x.get(f))]
    s = sv[keep].sort_values(ascending=False)
    return [{"feature": f, "contribution": round(float(v), 3)} for f, v in s.head(k).items()]


def replay(test: pd.DataFrame, arts: dict) -> dict:
    """Реплей крупнейшего регионального события тестовых лет (модель его не видела)."""
    t0 = test[test.horizon == 0]
    per_day = t0.groupby("date").agg(n_dust=("y", "sum"), n_strong=("strong", "sum"))
    per_day = per_day.sort_values(["n_dust", "n_strong"], ascending=False)
    if per_day.empty:
        return {}
    day = per_day.index[0]
    out = {"event_date": str(day.date()), "stations_with_dust": int(per_day.iloc[0].n_dust), "series": {},
           "weather_inputs": "era5_actual_upper_bound"}
    window = pd.date_range(day - pd.Timedelta(days=5), day + pd.Timedelta(days=3))
    for st in STATIONS:
        ser = {}
        for h in HORIZONS:
            sub = test[(test.horizon == h) & (test.station == st.id) & (test.date.isin(window))].sort_values("date")
            if sub.empty:
                continue
            p = predict(arts[h]["m2"], sub)
            ser[f"h{h}"] = [{"date": str(d.date()), "p": round(float(x), 3), "y": int(y)}
                            for d, x, y in zip(sub.date, p, sub.y)]
        sub = test[(test.horizon == 1) & (test.station == st.id) & (test.date == day)]
        if not sub.empty:
            art = arts[1]["m2"]
            ser["drivers_h1"] = _replay_drivers(shap_values(art, sub).iloc[0], sub[art["features"]].iloc[0])
        out["series"][st.id] = ser
    return out


# ---------------------------------------------------------------- графики
def plots(results: dict, test_preds: dict, importance: pd.Series):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from sklearn.metrics import precision_recall_curve

    names = [n for n in ("B0", "B1", "B2", "B2-iso", "M1", "M2", "M2-abl") if n in results["h0"]["models"]]
    fig, ax = plt.subplots(figsize=(8, 4))
    w = 0.8 / len(names)
    for i, n in enumerate(names):
        vals = [results[f"h{h}"]["models"][n]["pr_auc"] for h in HORIZONS]
        ax.bar(np.arange(3) + i * w, vals, w, label=n)
    ax.set_xticks(np.arange(3) + w * (len(names) - 1) / 2,
                  ["сегодня (h0)", "завтра (h1)*", "послезавтра (h2)*"])
    ax.set_ylabel("PR-AUC (тест)")
    ax.set_title("Модели против базовых линий на непросмотренных годах\n"
                 "* h1/h2 — верхняя граница: погода дня из ERA5, а не из прогноза", fontsize=9)
    ax.legend(ncol=4, fontsize=8)
    fig.tight_layout(); fig.savefig(FIG / "pr_auc_models.png", dpi=130); plt.close(fig)

    o, preds = test_preds["y"], test_preds["p"]
    fig, axs = plt.subplots(1, 2, figsize=(10, 4))
    for n in ("B0", "B2", "B2-iso", "M1", "M2"):
        if n in preds:
            pr, rc, _ = precision_recall_curve(o, preds[n])
            axs[0].plot(rc, pr, label=n)
    axs[0].set_xlabel("полнота (POD)"); axs[0].set_ylabel("точность")
    axs[0].set_title("Кривая точность–полнота, h1 (верхняя граница)")
    axs[0].legend()
    rel = M.reliability(preds["M2"], o)
    axs[1].plot([0, 1], [0, 1], "k--", lw=1)
    axs[1].plot(rel.p_mean, rel.o_freq, "o-", label="M2 LightGBM")
    axs[1].set_xlabel("прогнозная вероятность"); axs[1].set_ylabel("наблюдаемая частота")
    axs[1].set_title("Диаграмма надёжности, h1"); axs[1].legend()
    fig.tight_layout(); fig.savefig(FIG / "pr_reliability_h1.png", dpi=130); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 5))
    imp = importance.sort_values().tail(15)
    ax.barh(imp.index, imp.values, color="#c0703a")
    ax.set_title("Глобальная важность признаков (среднее |SHAP|), h1")
    fig.tight_layout(); fig.savefig(FIG / "shap_importance_h1.png", dpi=130); plt.close(fig)


# ---------------------------------------------------------------- происхождение артефакта
def provenance() -> dict:
    """Воспроизводимость (23.6, NFR-08): хэш набора данных и версии библиотек."""
    import scipy
    import sklearn
    path = FEATURES / "dataset.parquet"
    sha = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return {"dataset_sha256": sha, "python": platform.python_version(), "lightgbm": lgb.__version__,
            "sklearn": sklearn.__version__, "scipy": scipy.__version__, "pandas": pd.__version__,
            "numpy": np.__version__}


def _data_notes(ds: pd.DataFrame) -> dict:
    lab = ds[ds.y.notna()]
    last = lab.date.max()
    notes = {"last_label": str(last.date()) if pd.notna(last) else None}
    end = pd.Timestamp(max(TEST_YEARS), 12, 31)
    if pd.notna(last) and last < end:
        notes["partial_year"] = int(max(TEST_YEARS))
        notes["partial_year_note_ru"] = (f"{max(TEST_YEARS)} — неполный год: архив NOAA ISD заканчивается "
                                         f"{last.date()}, метки после этой даты отсутствуют")
    for name in ("ww_unreliable_years", "inhomogeneous_years"):
        p = FEATURES / f"{name}.json"
        if p.exists():
            notes[name] = json.loads(p.read_text(encoding="utf-8"))
    return notes


# ---------------------------------------------------------------- главный сценарий
def run() -> dict:
    ds = pd.read_parquet(FEATURES / "dataset.parquet")
    ds["date"] = pd.to_datetime(ds["date"])
    feats_full = [f for f in ALL_FEATURES]
    feats_abl = [f for f in ALL_FEATURES if f not in SOURCE_FEATURES]
    results, arts, test_frames = {}, {}, []
    levels_by_h = {}
    test_preds_h1 = None
    importance_h1 = None

    for h in HORIZONS:
        parts = split(ds[ds.horizon == h])
        tr, va, te = parts["train"], parts["valid"], parts["test"]
        trva = pd.concat([tr, va])
        log.info("h%d: train %d (%d соб.), valid %d, test %d", h, len(tr), tr.y.sum(), len(va), len(te))
        # Базовые линии без подбора — на обучении + валидации (последние годы перед тестом)
        clim = Climatology().fit(trva)
        pers = Persistence().fit(trva, clim)
        rule = WindRule().fit(trva)
        wiso = WindIsotonic().fit(trva)
        m1 = fit_logistic(tr, va, feats_full)
        m2 = fit_lgbm(tr, va, feats_full)
        m2a = fit_lgbm(tr, va, feats_abl)
        arts[h] = {"m2": m2, "m2_abl": m2a, "m1": m1, "clim": clim, "pers": pers, "rule": rule}

        te = te.copy()
        te["p_clim"] = clim.predict(te)
        p_sample = Climatology().fit(te).predict(te)  # «выборочная» климатология теста (справочно)
        preds = {"B0": te.p_clim.to_numpy(), "B1": pers.predict(te), "B2": rule.predict(te),
                 "B2-iso": wiso.predict(te),
                 "M1": predict(m1, te), "M2": predict(m2, te), "M2-abl": predict(m2a, te)}
        res = {"models": {}, "split": {k: [int(v.date.dt.year.min()), int(v.date.dt.year.max()), int(len(v)), int(v.y.sum())]
                                      for k, v in parts.items()},
               "weather_inputs": WEATHER_INPUTS[h],
               "reference_climatology": "train+valid",
               "rule_B2": {"gust_ms": rule.g, "dry_days": rule.n, "csi": rule.csi, "csi_no_dry": rule.csi_no_dry,
                           "dryness_adds": bool(rule.csi > rule.csi_no_dry)}}
        for n, p in preds.items():
            res["models"][n] = M.summary(p, te.y, te.p_clim, te.date, with_ci=n in ("M2", "M2-abl", "B2", "B2-iso"))
            res["models"][n]["bss_sample_clim"] = M.bss(p, te.y, p_sample)
        res["H3"] = h3_test(preds["M2"], preds["M2-abl"], te.y, te.p_clim, te.date)
        # Бинарные версии правил для метеорологических метрик
        res["binary"] = {"B1_persistence": M.contingency(Persistence.binary(te), te.y, 0.5),
                         "B2_rule": M.contingency(rule.fire(te).astype(float), te.y, 0.5)}
        # Качество по станциям (ворота: ни на одной станции M2 не хуже B2 более чем на 0,02)
        res["per_station"] = {}
        for st in STATIONS:
            m = (te.station == st.id).to_numpy()
            if m.sum() and te.y[m].sum():
                res["per_station"][st.id] = {
                    "events": int(te.y[m].sum()),
                    "M2_pr_auc": M.pr_auc(preds["M2"][m], te.y[m]),
                    "B2_pr_auc": M.pr_auc(preds["B2"][m], te.y[m]),
                    "M2_bss": M.bss(preds["M2"][m], te.y[m], te.p_clim[m]),
                }
        # SHAP
        sv = shap_values(m2, te)
        imp = sv.abs().mean().sort_values(ascending=False)
        res["shap_top"] = {k: round(float(v), 4) for k, v in imp.head(10).items()}
        res["reliability_M2"] = M.reliability(preds["M2"], te.y).round(3).to_dict(orient="records")
        # Пороги уровней — для каждого горизонта свои, по калибровке «вне выборки» на валидации
        levels = choose_thresholds(oof_valid_probs(m2, va), va.y)
        levels_by_h[h] = levels
        res["levels"] = levels
        res["levels_source"] = "valid, изотоника вне выборки по годам (leave-one-year-out)"
        res["at_levels"] = {g: M.contingency(preds["M2"], te.y, lv["orange"]) for g, lv in levels.items()}
        if h == 1:
            test_preds_h1 = {"y": te.y.to_numpy(), "p": preds}
            importance_h1 = imp
        results[f"h{h}"] = res
        te["horizon"] = h
        test_frames.append(te)

    test_all = pd.concat(test_frames)
    # Сравнение с CAMS (B3)
    try:
        results["B3_cams"] = evaluate_cams(test_all, arts[0]["m2"])
    except Exception as e:
        log.exception("CAMS")
        results["B3_cams"] = {"available": False, "error": str(e)}
    try:
        results["operational"] = evaluate_operational(test_all, arts)
    except Exception as e:
        log.exception("оперативный архив")
        results["operational"] = {"available": False, "error": str(e)}
    results["H4"] = test_h4(ds)
    results["replay"] = replay(test_all, arts)
    results["data_notes"] = _data_notes(ds)
    results["gates"] = quality_gates(results)
    results["model_version"] = MODEL_VERSION
    results["created"] = pd.Timestamp.now().isoformat(timespec="seconds")
    results["provenance"] = provenance()
    for h in HORIZONS:
        arts[h]["m2"]["provenance"] = results["provenance"]

    plots(results, test_preds_h1, importance_h1)
    joblib.dump({h: {k: v for k, v in a.items()} for h, a in arts.items()}, MODELS / "forecast.joblib")
    (MODELS / "thresholds.json").write_text(json.dumps(levels_by_h[1], ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    (MODELS / "thresholds_by_horizon.json").write_text(
        json.dumps({f"h{h}": lv for h, lv in levels_by_h.items()}, ensure_ascii=False, indent=2), encoding="utf-8")
    # Запасной режим без joblib (раздел 21.2): правило B2 и климатология в JSON
    (MODELS / "fallback.json").write_text(json.dumps(
        {f"h{h}": {"rule": arts[h]["rule"].to_json(), "clim": arts[h]["clim"].to_json()} for h in HORIZONS},
        ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORTS / "forecast_metrics.json").write_text(json.dumps(results, ensure_ascii=False, indent=2, default=_js),
                                                   encoding="utf-8")
    write_report(results)
    _register_model(results)
    return results


def _register_model(r: dict) -> None:
    """Запись в реестр model_versions (раздел 24.1)."""
    try:
        from aralshield import db
        db.init()
        brief = {f"h{h}": {k: r[f"h{h}"]["models"]["M2"].get(k) for k in ("pr_auc", "bss", "ece")} for h in HORIZONS}
        brief["provenance"] = r.get("provenance")
        db.execute("INSERT OR REPLACE INTO model_versions (module, version, metrics_json, status, created_at) "
                   "VALUES (?,?,?,?,?)", ("forecast", r["model_version"], json.dumps(brief, default=_js),
                                          "active", r["created"]))
    except Exception:
        log.exception("реестр model_versions не обновлён")


def _js(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, tuple):
        return list(o)
    return str(o)


def quality_gates(r: dict) -> dict:
    gates = {}
    b3 = r.get("B3_cams", {})
    for h in HORIZONS:
        mm = r[f"h{h}"]["models"]
        bases = [n for n in BASELINES if n in mm]
        best_base = max(bases, key=lambda n: mm[n]["pr_auc"])
        m2_lo = mm["M2"]["pr_auc_ci"][0]
        base_hi = mm[best_base].get("pr_auc_ci", (mm[best_base]["pr_auc"],) * 2)[1]
        per = r[f"h{h}"]["per_station"]
        top3 = list(r[f"h{h}"]["shap_top"])[:3]
        h3 = r[f"h{h}"].get("H3", {})
        beats = bool(mm["M2"]["pr_auc"] > mm[best_base]["pr_auc"])
        g = {
            "pr_auc_beats_best_baseline": beats,
            "pr_auc_ci_separated": bool(m2_lo > base_hi),
            "best_baseline": best_base,
            "bss_positive": bool(mm["M2"]["bss"] > 0),
            "ece_le_0_05": bool(mm["M2"]["ece"] <= 0.05),
            "ece_quantile": mm["M2"].get("ece_q"),
            "per_station_not_worse_than_B2": bool(all(v["M2_pr_auc"] >= v["B2_pr_auc"] - 0.02 for v in per.values())),
            "wind_in_top3_shap": bool(any(f.startswith(("wind", "gust", "hours_gt", "upwind")) for f in top3)),
            # H3 — по нижней границе парного бутстреп-интервала ΔPR-AUC (M2 − M2-abl) > 0
            "H3_source_features_help": bool(h3.get("confirmed", False)),
            "H3_delta_pr_auc": h3.get("delta_pr_auc"),
            "H3_delta_pr_auc_ci": h3.get("delta_pr_auc_ci"),
            "H3_delta_bss_ci": h3.get("delta_bss_ci"),
            "weather_inputs": r[f"h{h}"].get("weather_inputs"),
        }
        # B3 (CAMS) — только h0 и только на своём периоде 2024–2025
        if h == 0 and b3.get("available"):
            g["pr_auc_beats_B3_cams"] = bool(b3["model"]["pr_auc"] > b3["cams"]["pr_auc"])
            g["pr_auc_beats_best_baseline"] = bool(beats and g["pr_auc_beats_B3_cams"])
        gates[f"h{h}"] = g
    return gates


def _f(x, fmt=".3f"):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else format(x, fmt)


def _ci(ci):
    return f"[{_f(ci[0])}…{_f(ci[1])}]" if ci else ""


def write_report(r: dict):
    notes = r.get("data_notes", {})
    L = ["# Отчёт модуля «Прогноз»", "",
         f"Версия модели: `{r['model_version']}`, создан {r['created']}.", "",
         "Метки — наблюдения метеостанций (коды ВМО и видимость, NOAA ISD); признаки — ERA5 (Open-Meteo).",
         "Тест — годы, которые модель не видела. Интервалы — 95% блочный бутстреп по неделям.",
         "BSS считается относительно климатологии станция × месяц по обучению + валидации; "
         "в скобках — относительно выборочной климатологии самого теста.", "",
         "**Важно:** погода целевых суток во всех горизонтах берётся из реанализа ERA5 (фактическая погода дня). "
         "Поэтому числа h1/h2 — **верхняя граница (фактическая погода дня)**, а не качество прогноза на 1–2 суток; "
         "цена перехода на оперативный прогноз — в разделе «Проверка на архиве оперативных прогнозов».", ""]
    if notes.get("partial_year_note_ru"):
        L += [f"Данные: {notes['partial_year_note_ru']}.", ""]
    for h in HORIZONS:
        x = r[f"h{h}"]
        title = ["сегодня", "завтра", "послезавтра"][h]
        if x.get("weather_inputs") == "era5_actual_upper_bound":
            title += "; верхняя граница (фактическая погода дня)"
        L += [f"## Горизонт h{h} ({title})", "",
              f"Разбиение (годы, строк, событий): {x['split']}", "",
              "| Модель | PR-AUC | ROC-AUC | Brier | BSS (выбор. клим.) | ECE | ECE квант. |",
              "|---|---|---|---|---|---|---|"]
        for n, m in x["models"].items():
            ci = f" {_ci(m['pr_auc_ci'])}" if "pr_auc_ci" in m else ""
            L.append(f"| {n} | {m['pr_auc']:.3f}{ci} | {m['roc_auc']:.3f} | {m['brier']:.4f} | "
                     f"{m['bss']:.3f} ({_f(m.get('bss_sample_clim'))}) | {m['ece']:.3f} | {_f(m.get('ece_q'))} |")
        rb = x["rule_B2"]
        L += ["", f"Правило B2: порыв ≥ {rb['gust_ms']} м/с и ≥ {rb['dry_days']} сухих суток "
                  f"(CSI {_f(rb.get('csi'))}; без условия сухости — {_f(rb.get('csi_no_dry'))}"
                  + ("" if rb.get("dryness_adds", True) else "; **сухость ничего не добавляет к ветру**") + "). "
                  "B2-iso — откалиброванная непрерывная линия P(событие | порыв) по станциям.", "",
              "Топ признаков (среднее |SHAP|): " + ", ".join(x["shap_top"]), ""]
        h3 = x.get("H3", {})
        if h3:
            verdict = "H3 подтверждена" if h3.get("confirmed") else "H3 не подтверждена"
            L += [f"{verdict}: ΔPR-AUC (M2 − M2 без признаков источника) = {_f(h3.get('delta_pr_auc'))} "
                  f"{_ci(h3.get('delta_pr_auc_ci'))}, ΔBSS = {_f(h3.get('delta_bss'))} {_ci(h3.get('delta_bss_ci'))} "
                  "(парный блочный бутстреп; гипотеза принимается, только если нижняя граница ΔPR-AUC > 0).", ""]
        L += ["Ворота качества: " + json.dumps(r["gates"][f"h{h}"], ensure_ascii=False, default=_js), ""]
        if "levels" in x:
            L += [f"Пороги уровней по группам для h{h} (валидация, калибровка вне выборки): "
                  + json.dumps(x["levels"], ensure_ascii=False), ""]
            for g, c in x["at_levels"].items():
                L.append(f"- {g}: порог {c['threshold']:.2f} → POD {_f(c['POD'], '.2f')}, FAR {_f(c['FAR'], '.2f')}, "
                         f"CSI {_f(c['CSI'], '.2f')}")
            L.append("")
    b3 = r["B3_cams"]
    L += ["## Сравнение с CAMS (B3)", ""]
    if b3.get("available"):
        L += [f"Период {b3['period']}, горизонт 0: AralShield PR-AUC {b3['model']['pr_auc']:.3f}, "
              f"BSS {b3['model']['bss']:.3f}; CAMS PR-AUC {b3['cams']['pr_auc']:.3f}, BSS {b3['cams']['bss']:.3f}.", "",
              "Оговорка: сравнение не равноправное — AralShield здесь получает фактическую погоду дня (ERA5), "
              "а CAMS — собственный модельный прогноз пыли (изотонически откалиброван на 2022-08…2023). "
              "Равноправное сравнение — с признаками из оперативного прогноза (см. ниже).", ""]
    else:
        L += [f"Недоступно: {b3.get('error')}", ""]
    op = r.get("operational", {})
    L += ["## Проверка на архиве оперативных прогнозов погоды", ""]
    for name, o in (op.get("by_model") or {"": op}).items():
        head = f"Модель {o.get('model', name)}: "
        fr = o.get("identical_frac") or {}
        if fr:
            L.append(head + "доля часов ветра, совпадающих с ERA5: "
                     + ", ".join(f"{k} {_f(v, '.2f')}" for k, v in fr.items())
                     + (f"; исключены (≥{IDENTICAL_MAX:.0%} совпадений, это реанализ, а не прогноз): "
                        + ", ".join(o["excluded_stations"]) if o.get("excluded_stations") else "; исключённых нет") + ".")
        if o.get("available"):
            b = o.get("bias_ratio_op_to_era5", {})
            L += [f"Станции {', '.join(o['stations'])}, период {o['period']} ({o['n_days']} сут), горизонт 0. "
                  f"Признаки из ERA5: PR-AUC {o['reanalysis']['pr_auc']:.3f}, BSS {o['reanalysis']['bss']:.3f}. "
                  f"Признаки из оперативного прогноза: PR-AUC {o['operational']['pr_auc']:.3f} "
                  f"{_ci(o['operational'].get('pr_auc_ci'))}, BSS {o['operational']['bss']:.3f}. "
                  f"Правило B2 на оперативных данных: PR-AUC {o['rule_B2_operational']['pr_auc']:.3f}. "
                  f"Отношение «прогноз / ERA5»: максимальный ветер {_f(b.get('wind_max'), '.2f')}, "
                  f"порывы {_f(b.get('gust_max'), '.2f')}, часы > 8 м/с {_f(b.get('hours_gt8'), '.2f')}. "
                  f"Доля пропусков: {json.dumps(o.get('missing_share', {}), ensure_ascii=False)}.",
                  o["note"] + ".", ""]
        else:
            L += [head + f"недоступно: {o.get('error')}", ""]
    h4 = r["H4"]
    L += ["## Гипотеза H4 (Аральск → Кызылорда)", "", h4.get("conclusion_ru", h4.get("conclusion", "")), "",
          f"Метод: {h4.get('method', 'точный тест Фишера')}.", ""]
    for k in ("lag_0", "lag_1", "lag_2"):
        a = h4[k]["all"]
        L.append(f"- сдвиг {k[-1]} сут: ОШ М–Х = {_f(a.get('mh_or'), '.2f')}, p (перестановки) = {_f(a.get('p_perm'), '.3f')}; "
                 f"наивно OR = {a['odds_ratio']:.2f}, p (Фишер) = {a['p_value']:.2g}; "
                 f"P(Кызылорда) = {a['P(kyzylorda)']:.3f}, P(Кызылорда | Аральск) = {a['P(kyzylorda|aralsk)']:.3f}"
                 + (" — значимо" if h4[k].get("significant") else " — не значимо"))
    rp = r.get("replay", {})
    if rp:
        L += ["", "## Реплей события из тестовых лет", "",
              f"Дата: {rp['event_date']}, пыль на {rp['stations_with_dust']} станциях (признаки — ERA5, верхняя граница). "
              "Подробности — reports/forecast_metrics.json."]
    excl = op.get("excluded_stations") or []
    bias = op.get("bias_ratio_op_to_era5") or {}
    L += ["", "![PR-AUC](figures/pr_auc_models.png)", "", "![PR и надёжность](figures/pr_reliability_h1.png)", "",
          "![SHAP](figures/shap_importance_h1.png)", "",
          "## Ограничения", "",
          "- h1/h2 оценены с фактической погодой целевого дня (ERA5): это верхняя граница; реальное качество "
          "прогноза на 1–2 суток ниже и не измерено.",
          "- Обучение на реанализе завышает точность боевого режима; проверка на архиве оперативных прогнозов "
          + (f"выполнена только на станциях с настоящим прогнозом ({', '.join(op.get('stations', []))}); "
             if op.get("available") else "недоступна; ")
          + (f"исключены {', '.join(excl)} (ряд совпадает с ERA5)." if excl else "исключённых станций нет."),
          "- Оперативный ветер систематически слабее ERA5"
          + (f" (максимальный ветер ×{_f(bias.get('wind_max'), '.2f')}, порывы ×{_f(bias.get('gust_max'), '.2f')})"
             if bias else "") + ": модель монотонна по ветру, поэтому в боевом режиме возможен сдвиг к недопредупреждению; "
          "квантильная коррекция не выполнялась.",
          "- Карта источников «Глаза» статична (композит 2025 г.) и применяется ко всем годам 2000–2025, включая тест: "
          "формально это информация из будущего (нарушение правила 13.3 «карты только из прошлых месяцев»); "
          "H3 поэтому проверяет направленный вес источника, а не его динамику.",
          "- Метки по видимости зависят от практики наблюдателя и перехода на автоматические станции; "
          "станционные годы без передачи кода погоды считаются без метки"
          + (f" ({json.dumps(notes.get('ww_unreliable_years'), ensure_ascii=False)})" if notes.get("ww_unreliable_years") else "")
          + "."]
    if notes.get("partial_year_note_ru"):
        L.append(f"- {notes['partial_year_note_ru']}.")
    (REPORTS / "forecast_report.md").write_text("\n".join(L), encoding="utf-8")
