"""М1 «Глаз», шаг 2: спектральные индексы, разметка, Random Forest, проверка, продукты.

Разметка (раздел 12.4) в этой версии — слабая: вода и растительность берутся из
карты ESA WorldCover 2021, а три класса голого дна («потенциальный источник», песок,
влажный солончак) — из консервативных спектральных правил по литературе
(Kim et al., 2020; Löw et al., 2022). Это стартовая разметка для активного обучения;
перед боевым использованием её заменяют экспертной (QGIS).

Честность метрик:
- блочная пространственная CV 10×10 км (Roberts et al., 2017) меряет СОГЛАСИЕ модели со
  слабой разметкой, а не точность относительно местности: метки — пороговые функции тех же
  признаков (`cv.informative = false`);
- сверка с WorldCover не независима (метки воды/растительности взяты из WorldCover), поэтому
  она считается только на пикселях без слабой метки и рядом публикуется базовая линия
  «мажоритарный класс» (`external_worldcover.independent = false`);
- пороги правил калибруются один раз (перцентили голого пула сцены калибровки) и далее
  фиксируются в models/eye_label_thresholds.json, чтобы новые сцены не перенормировались.
"""
from __future__ import annotations

import json
import logging
import os

import joblib
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.warp import calculate_default_transform, reproject, Resampling
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import cohen_kappa_score, confusion_matrix, f1_score
from sklearn.model_selection import GroupKFold

from aralshield import config as _cfg
from aralshield.config import (EYE_CLASSES, EYE_CRS, EYE_RES_M, FEATURES, MODELS, N_SECTORS, REPORTS,
                               SECTOR_NAMES, SOURCE_DECAY_KM, SOURCE_RADIUS_KM, STATIONS)
from aralshield.eye.composite import EYE_DIR, S2_NAMES, worldcover_to_grid

log = logging.getLogger(__name__)
# Тег карты «Глаза» по умолчанию (контракт K1); константа появляется в config.py у Fixer A
EYE_TAG = getattr(_cfg, "EYE_TAG", "2025v2")
INDEX_NAMES = ["NDVI", "NDWI", "BSI", "SI", "TGSI", "NMDI", "NDMI", "BRIGHT"]
BLOCK_KM = 10
SAMPLES_PER_CLASS = 1500
N_CLASSES = 5
WEB_CRS = "EPSG:3857"            # PNG-оверлеи строятся в Web Mercator (как Leaflet)
MIN_SECTOR_COVERAGE = 0.2        # ниже — нормировка на наблюдённую площадь ненадёжна
LOW_COVERAGE_REPORT = 0.5        # секторы с меньшим покрытием перечисляются в метриках
MIN_BARE_PX = 100                # минимум пикселей голого пула для калибровки порогов
THRESHOLDS_FILE = MODELS / "eye_label_thresholds.json"
# Пороги правил голого дна: имя → (признак, перцентиль голого пула сцены калибровки)
LABEL_QUANTILES = {
    "wet_bright_max": ("BRIGHT", 20), "wet_ndmi_min": ("NDMI", 70),
    "salt_bright_min": ("BRIGHT", 75), "salt_ratio_min": ("RATIO", 60), "salt_si_min": ("SI", 70),
    "sand_tgsi_min": ("TGSI", 75), "sand_ndmi_max": ("NDMI", 40),
}


def indices(stack: np.ndarray) -> np.ndarray:
    """Спектральные индексы раздела 12.3 (+ NDMI и яркость)."""
    b = dict(zip(S2_NAMES, stack))
    eps = 1e-6
    ndvi = (b["B8"] - b["B4"]) / (b["B8"] + b["B4"] + eps)
    ndwi = (b["B3"] - b["B8"]) / (b["B3"] + b["B8"] + eps)
    bsi = ((b["B11"] + b["B4"]) - (b["B8"] + b["B2"])) / ((b["B11"] + b["B4"]) + (b["B8"] + b["B2"]) + eps)
    si = np.sqrt(np.clip(b["B2"] * b["B4"], 0, None))
    tgsi = (b["B4"] - b["B2"]) / (b["B4"] + b["B3"] + b["B2"] + eps)
    nmdi = (b["B8"] - (b["B11"] - b["B12"])) / (b["B8"] + (b["B11"] - b["B12"]) + eps)
    ndmi = (b["B8"] - b["B11"]) / (b["B8"] + b["B11"] + eps)
    bright = (b["B2"] + b["B3"] + b["B4"]) / 3
    return np.stack([ndvi, ndwi, bsi, si, tgsi, nmdi, ndmi, bright]).astype("float32")


def _base_labels(stack: np.ndarray, idx: np.ndarray, wc: np.ndarray):
    """Вода/растительность по WorldCover + согласию спектра и маска «голого пула»."""
    b = dict(zip(S2_NAMES, stack))
    I = dict(zip(INDEX_NAMES, idx))
    lab = np.zeros(wc.shape, dtype="uint8")
    valid = np.all(np.isfinite(stack), axis=0)
    lab[(wc == 80) & (I["NDWI"] > 0.0) & valid] = 5
    veg_wc = np.isin(wc, [10, 20, 30, 90])
    lab[veg_wc & (I["NDVI"] > 0.18) & valid] = 4
    bare = np.isin(wc, [60, 30, 20]) & (I["NDVI"] < 0.12) & (I["NDWI"] < -0.05) & valid & (lab == 0)
    feats = {"BRIGHT": I["BRIGHT"], "NDMI": I["NDMI"], "SI": I["SI"], "TGSI": I["TGSI"],
             "RATIO": b["B2"] / (b["B4"] + 1e-6)}  # «белизна» корки: B2 ≈ B4
    return lab, bare, feats


def calibrate_label_thresholds(stack: np.ndarray, idx: np.ndarray, wc: np.ndarray) -> dict[str, float] | None:
    """Пороги правил = перцентили голого пула этой сцены. None, если голого пула почти нет."""
    _, bare, feats = _base_labels(stack, idx, wc)
    if bare.sum() < MIN_BARE_PX:
        return None
    return {k: float(np.nanpercentile(feats[f][bare], q)) for k, (f, q) in LABEL_QUANTILES.items()}


def weak_labels(stack: np.ndarray, idx: np.ndarray, wc: np.ndarray,
                thresholds: dict[str, float] | None = None) -> np.ndarray:
    """Высокоуверенные метки 1..5, 0 — без метки.

    thresholds — зафиксированные пороги (см. calibrate_label_thresholds). Если не заданы,
    калибруются по этой же сцене (поведение старой версии, только для калибровки).
    При пустом голом пуле классы 1–3 не размечаются (без NaN-порогов и исключений)."""
    lab, bare, F = _base_labels(stack, idx, wc)
    if thresholds is None:
        thresholds = calibrate_label_thresholds(stack, idx, wc)
    if thresholds is None or not bare.any():
        log.warning("голый пул пуст или мал — классы голого дна не размечены")
        return lab
    t = thresholds
    with np.errstate(invalid="ignore"):
        # Влажный солончак: тёмная поверхность с поглощением воды в КВИК
        wet = bare & (F["BRIGHT"] < t["wet_bright_max"]) & (F["NDMI"] > t["wet_ndmi_min"])
        # Сухая солевая корка: яркая, спектрально «белая» (B2 ≈ B4), засолённая
        salt = (bare & ~wet & (F["BRIGHT"] > t["salt_bright_min"]) & (F["RATIO"] > t["salt_ratio_min"])
                & (F["SI"] > t["salt_si_min"]))
        # Песок: желтовато-красный (TGSI высокий), сухой, средней яркости
        sand = bare & ~wet & ~salt & (F["TGSI"] > t["sand_tgsi_min"]) & (F["NDMI"] < t["sand_ndmi_max"])
    lab[wet] = 3
    lab[salt] = 1
    lab[sand] = 2
    return lab


def load_or_calibrate_thresholds(stack, idx, wc, tag: str, recalibrate: bool = False) -> dict:
    """Пороги считаются один раз и хранятся в models/; новые сцены их не перенормируют."""
    if THRESHOLDS_FILE.exists() and not recalibrate:
        saved = json.loads(THRESHOLDS_FILE.read_text(encoding="utf-8"))
        return {**saved, "reused": True}
    thr = calibrate_label_thresholds(stack, idx, wc)
    if thr is None:
        raise RuntimeError("на сцене нет голого дна для калибровки порогов слабой разметки")
    saved = {"calibrated_on": tag, "thresholds": thr,
             "quantiles": {k: list(v) for k, v in LABEL_QUANTILES.items()},
             "method": "перцентили голого пула сцены калибровки; далее пороги фиксированы"}
    _write_json(THRESHOLDS_FILE, saved)
    return {**saved, "reused": False}


def _features(stack, idx):
    return np.concatenate([stack, idx]).reshape(stack.shape[0] + idx.shape[0], -1).T


def proba_full(rf, X: np.ndarray, n_classes: int = N_CLASSES) -> np.ndarray:
    """Вероятности по всем классам 1..n: столбцы берутся по rf.classes_, отсутствующие = 0."""
    out = np.zeros((len(X), n_classes), dtype="float32")
    if len(X):
        out[:, np.asarray(rf.classes_, dtype=int) - 1] = rf.predict_proba(X)
    return out


def _block_bootstrap(y, y_pred, groups, rng, labels, n_boot: int = 300) -> dict:
    """Бутстреп по пространственным блокам: CI для OA, κ, macro-F1 и F1 по классам."""
    members = [np.flatnonzero(groups == g) for g in np.unique(groups)]
    oa, kap, mf1, f1c = [], [], [], []
    for _ in range(n_boot):
        m = np.concatenate([members[i] for i in rng.integers(0, len(members), len(members))])
        yt, yp = y[m], y_pred[m]
        oa.append((yt == yp).mean())
        kap.append(cohen_kappa_score(yt, yp) if len(np.unique(np.r_[yt, yp])) > 1 else np.nan)
        f = f1_score(yt, yp, labels=labels, average=None, zero_division=0)
        f1c.append(f)
        mf1.append(f.mean())
    ci = lambda a: [float(np.nanpercentile(a, 2.5)), float(np.nanpercentile(a, 97.5))]  # noqa: E731
    f1c = np.array(f1c)
    return {"oa_ci95": ci(oa), "kappa_ci95": ci(kap), "macro_f1_ci95": ci(mf1),
            "f1_ci95": {EYE_CLASSES[c][0]: ci(f1c[:, i]) for i, c in enumerate(labels)}, "n_boot": n_boot}


def train_and_map(tag: str = EYE_TAG, seed: int = 42, recalibrate: bool = False) -> dict:
    with rasterio.open(EYE_DIR / f"s2_composite_{tag}.tif") as src:
        stack = src.read().astype("float32")
        profile = src.profile
        transform = src.transform
    idx = indices(stack)
    wc = worldcover_to_grid()
    thr = load_or_calibrate_thresholds(stack, idx, wc, tag, recalibrate=recalibrate)
    lab = weak_labels(stack, idx, wc, thr["thresholds"])
    H, W = lab.shape
    X_all = _features(stack, idx)
    valid = np.all(np.isfinite(X_all), axis=1)

    # Выборка, сбалансированная по классам
    rng = np.random.default_rng(seed)
    flat = lab.ravel()
    picks = []
    for c in range(1, N_CLASSES + 1):
        cand = np.flatnonzero((flat == c) & valid)
        if len(cand):
            picks.append(rng.choice(cand, size=min(SAMPLES_PER_CLASS, len(cand)), replace=False))
        else:
            log.warning("класс %s не получил слабых меток", EYE_CLASSES[c][0])
    sel = np.concatenate(picks)
    X, y = X_all[sel], flat[sel]
    rows, cols = np.divmod(sel, W)
    block_px = int(BLOCK_KM * 1000 / EYE_RES_M)
    groups = (rows // block_px) * 10_000 + (cols // block_px)

    # Блочная пространственная кросс-валидация (5 фолдов)
    y_pred = np.zeros_like(y)
    for tr_i, te_i in GroupKFold(n_splits=5).split(X, y, groups):
        rf = RandomForestClassifier(n_estimators=300, min_samples_leaf=2, n_jobs=-1, random_state=seed,
                                    class_weight="balanced")
        rf.fit(X[tr_i], y[tr_i])
        y_pred[te_i] = rf.predict(X[te_i])
    labels = list(range(1, N_CLASSES + 1))
    cm = confusion_matrix(y, y_pred, labels=labels)
    f1 = f1_score(y, y_pred, labels=labels, average=None, zero_division=0)
    iou = [cm[i, i] / max(1, cm[i, :].sum() + cm[:, i].sum() - cm[i, i]) for i in range(N_CLASSES)]
    oa = float((y == y_pred).mean())
    boot = _block_bootstrap(y, y_pred, groups, rng, labels)
    cv = {"overall_accuracy": oa, "oa_ci95": boot["oa_ci95"],
          "kappa": float(cohen_kappa_score(y, y_pred)), "kappa_ci95": boot["kappa_ci95"],
          "macro_f1_ci95": boot["macro_f1_ci95"],
          "f1": {EYE_CLASSES[c][0]: float(f) for c, f in zip(labels, f1)}, "f1_ci95": boot["f1_ci95"],
          "iou": {EYE_CLASSES[c][0]: float(v) for c, v in zip(labels, iou)},
          "confusion_matrix": cm.tolist(), "n_samples": int(len(y)),
          "class_counts": {EYE_CLASSES[c][0]: int((y == c).sum()) for c in labels},
          "validation": f"блочная пространственная CV, блоки {BLOCK_KM}×{BLOCK_KM} км, 5 фолдов; "
                        f"CI — бутстреп по блокам ({boot['n_boot']} повторов)",
          # Метки — пороговые функции тех же признаков, поэтому CV почти всегда ≈1 и о точности не говорит
          "metric_kind": "agreement_with_weak_labels",
          "informative": False,
          "meaning_ru": ("согласие модели со слабой разметкой на отложенных блоках; метки получены правилами "
                         "по тем же каналам и индексам, поэтому это НЕ точность относительно местности; "
                         "гипотезу H1 (OA ≥ 80%) этим проверить нельзя — нужна экспертная разметка"),
          "meaning_kk": ("модельдің әлсіз белгілермен шеттетілген блоктардағы келісімі; белгілер сол арналар мен "
                         "индекстер бойынша ережелермен алынған, сондықтан бұл жергілікті жердегі дәлдік ЕМЕС; "
                         "H1 гипотезасын (OA ≥ 80%) бұлай тексеруге болмайды — сарапшылық белгілеу қажет")}
    cv["meaning"] = cv["meaning_ru"]

    # Итоговая модель на всей выборке и карта
    rf = RandomForestClassifier(n_estimators=400, min_samples_leaf=2, n_jobs=-1, random_state=seed,
                                class_weight="balanced").fit(X, y)
    proba = np.full((H * W, N_CLASSES), np.nan, dtype="float32")
    vi = np.flatnonzero(valid)
    for chunk in np.array_split(vi, max(1, len(vi) // 200_000)):
        proba[chunk] = proba_full(rf, X_all[chunk])
    cls = np.where(valid, np.nanargmax(np.nan_to_num(proba, nan=-1), axis=1) + 1, 0).reshape(H, W).astype("uint8")
    p_source = proba[:, 0].reshape(H, W)  # столбец класса 1 (по rf.classes_, см. proba_full)
    uncertainty = (1 - np.nanmax(np.nan_to_num(proba, nan=-1), axis=1)).reshape(H, W)
    uncertainty[~valid.reshape(H, W)] = np.nan
    importance = dict(sorted(zip(S2_NAMES + INDEX_NAMES, rf.feature_importances_.round(4).tolist()),
                             key=lambda kv: -kv[1]))
    joblib.dump(rf, MODELS / "eye_rf.joblib")

    prof = profile.copy()
    prof.update(count=1, dtype="uint8", nodata=0)
    with rasterio.open(EYE_DIR / f"classes_{tag}.tif", "w", **prof) as dst:
        dst.write(cls, 1)
    prof.update(count=2, dtype="float32", nodata=np.nan)
    with rasterio.open(EYE_DIR / f"source_prob_{tag}.tif", "w", **prof) as dst:
        dst.write(np.stack([p_source, uncertainty]))
    # Сохраняем индексы для маски пригодности «Щита»
    np.save(EYE_DIR / f"indices_{tag}.npy", idx)

    area = {EYE_CLASSES[c][0]: round(float((cls == c).sum() * (EYE_RES_M / 1000) ** 2), 1) for c in labels}
    trained = np.zeros(H * W, dtype=bool)
    trained[sel] = True
    external = worldcover_agreement(cls, wc, trained.reshape(H, W), lab=lab, block_px=block_px)
    result = {"tag": tag, "cv": cv, "external_worldcover": external,
              "feature_importance": importance, "area_km2": area,
              "area_note_ru": ("площади классов — по карте модели, обученной на сбалансированной выборке "
                               "(по 1500 пикселей на класс + class_weight='balanced'), т. е. с равным априором "
                               "классов; поэтому редкие классы (влажный солончак) на карте завышены, "
                               "площади — индикативные, не учётные"),
              "area_note_kk": ("класс аудандары теңестірілген іріктемеде (әр класқа 1500 пиксель + "
                               "class_weight='balanced') оқытылған модель картасы бойынша, яғни кластардың "
                               "априорлары тең; сондықтан сирек кластар (ылғалды сортаң) асыра көрсетілген, "
                               "аудандар индикативті"),
              "weak_label_counts": {EYE_CLASSES[c][0]: int((lab == c).sum()) for c in labels},
              "label_thresholds": thr,
              "labels": "слабая разметка: WorldCover 2021 (вода, растительность) + спектральные правила (голое дно)",
              "overlays": {"png_crs": WEB_CRS,
                           "note": "PNG построены в EPSG:3857, границы — углы в градусах WGS84 (Leaflet imageOverlay)"}}
    st = sector_stats(p_source, transform)
    result["sector_source"] = st["F"]
    result["sector_coverage"] = st["coverage"]
    result["sector_coverage_mean"] = {k: round(float(np.mean(v)), 3) for k, v in st["coverage"].items()}
    result["sector_coverage_low"] = [
        {"station": sid, "sector": SECTOR_NAMES[i], "coverage": c}
        for sid, cov in st["coverage"].items() for i, c in enumerate(cov) if c < LOW_COVERAGE_REPORT]
    result["sector_normalisation"] = _SECTOR_NOTE
    _write_json(FEATURES / "sector_source.json",
                {"tag": tag, "sectors": N_SECTORS, "radius_km": SOURCE_RADIUS_KM, "decay_km": SOURCE_DECAY_KM,
                 "normalisation": _SECTOR_NOTE, "min_sector_coverage": MIN_SECTOR_COVERAGE,
                 "stations": st["F"], "coverage": st["coverage"]})
    # Расстояния пишем сразу и с тегом, чтобы они не расходились с F_s
    _write_json(FEATURES / "sector_distance.json", {"tag": tag, "stations": st["distance_km"]})
    render_pngs(stack, cls, p_source, transform, tag)
    _write_json(REPORTS / f"eye_metrics_{tag}.json", result)
    return result


def _agreement(model: np.ndarray, ref: np.ndarray) -> dict:
    """Согласие укрупнённых классов + базовая линия «всё — мажоритарный класс эталона»."""
    n = int(len(ref))
    out = {"n_pixels": n, "overall_agreement": None, "kappa": None, "recall": {},
           "baseline_majority": None, "baseline_majority_class": None, "gain_over_baseline": None}
    if n == 0:
        return out
    vals, cnt = np.unique(ref, return_counts=True)
    maj = vals[np.argmax(cnt)]
    oa = float((model == ref).mean())
    base = float(cnt.max() / n)
    out.update(overall_agreement=oa, baseline_majority=base, baseline_majority_class=str(maj),
               gain_over_baseline=oa - base,
               kappa=float(cohen_kappa_score(ref, model)) if len(np.unique(np.r_[ref, model])) > 1 else None)
    for k in ("water", "veg", "bare"):
        r = ref == k
        out["recall"][k] = float((model[r] == k).mean()) if r.any() else None
    return out


def worldcover_agreement(cls: np.ndarray, wc: np.ndarray, trained: np.ndarray,
                         lab: np.ndarray | None = None, block_px: int | None = None) -> dict:
    """Согласие с ESA WorldCover 2021 по укрупнённым классам: вода (80), голый грунт (60),
    древесно-кустарниковая растительность (10, 20); «травы» (30) не используются.

    Это НЕ независимая проверка: слабые метки воды и растительности взяты из WorldCover.
    Поэтому основная оценка — только по пикселям без слабой метки (lab == 0) и вне обучающей
    выборки, рядом — базовая линия «мажоритарный класс». Дополнительно (block_px) — только
    блоки, в которых нет ни одного размеченного пикселя. Для сравнения приводится и старая,
    циркулярная оценка по всем пикселям."""
    base = ~trained & (cls > 0) & np.isin(wc, [10, 20, 60, 80])
    model_all = np.select([cls == 5, cls == 4], ["water", "veg"], default="bare")
    ref_all = np.select([wc == 80, np.isin(wc, [10, 20])], ["water", "veg"], default="bare")
    m = base & (lab == 0) if lab is not None else base
    out = {"independent": False, "reference": "ESA WorldCover 2021",
           "evaluated_on": "пиксели без слабой метки и вне обучающей выборки" if lab is not None
           else "пиксели вне обучающей выборки (слабые метки не исключены — циркулярно)"}
    out.update(_agreement(model_all[m], ref_all[m]))
    if lab is not None and block_px:
        H, W = cls.shape
        rr, cc = np.indices((H, W))
        block = (rr // block_px) * 10_000 + (cc // block_px)
        labelled_blocks = np.unique(block[lab > 0])
        m2 = m & ~np.isin(block, labelled_blocks)
        out["unlabelled_blocks"] = _agreement(model_all[m2], ref_all[m2])
        out["unlabelled_blocks"]["note"] = f"только блоки {BLOCK_KM}×{BLOCK_KM} км без слабых меток"
    circ = _agreement(model_all[base], ref_all[base])
    out["including_weak_labelled_pixels"] = {k: circ[k] for k in ("n_pixels", "overall_agreement", "kappa",
                                                                  "baseline_majority")}
    out["note_ru"] = ("согласие с WorldCover, не независимая проверка: метки воды и растительности взяты "
                      "из WorldCover; WorldCover 2021 к тому же устарел для береговой линии 2025 г.")
    out["note_kk"] = ("WorldCover-пен келісім, тәуелсіз тексеру емес: су мен өсімдік белгілері WorldCover-ден "
                      "алынған; оның үстіне WorldCover 2021 жағалау сызығының 2025 жылғы күйін көрсетпейді")
    out["note"] = out["note_ru"]
    return out


_SECTOR_NOTE = ("F_s = Σ w·p / Σ w по валидным пикселям сектора, w = exp(−d/L)·площадь, d ≤ R; "
                "coverage = Σ w(валидные) / Σ w(весь сектор). При coverage < "
                f"{MIN_SECTOR_COVERAGE} берётся нижняя оценка Σ w·p / Σ w(весь сектор)")


def sector_stats(p_source: np.ndarray, transform, stations=None) -> dict[str, dict[str, list]]:
    """F_s(θ) в 16 секторах вокруг станции в радиусе R с весом exp(-d/L) (раздел 12.7).

    Пробелы композита и края AOI (NaN) НЕ считаются «нет источника»: F_s нормируется на
    взвешенную площадь валидных пикселей сектора, а доля этой площади возвращается как
    coverage (0..1). При coverage < MIN_SECTOR_COVERAGE экстраполяция ненадёжна — берётся
    нижняя оценка (наблюдённый источник / полная площадь сектора). Сектор 0 = север,
    азимут — от станции на пиксель (= метеонаправление «откуда дует»).
    Возвращает {"F", "coverage", "distance_km"} — словари {station_id: [16 значений]}."""
    stations = STATIONS if stations is None else stations
    H, W = p_source.shape
    xs = transform.c + (np.arange(W) + 0.5) * transform.a
    ys = transform.f + (np.arange(H) + 0.5) * transform.e
    XX, YY = np.meshgrid(xs, ys)
    tr = Transformer.from_crs("EPSG:4326", EYE_CRS, always_xy=True)
    px_area = abs(transform.a * transform.e) / 1e6  # км² (по сетке, а не по константе)
    L, R = SOURCE_DECAY_KM, SOURCE_RADIUS_KM
    # Площадь сектора с весом: (2π/16) ∫0^R r e^{-r/L} dr
    denom = (2 * np.pi / N_SECTORS) * (L ** 2 * (1 - np.exp(-R / L) * (1 + R / L)))
    valid = np.isfinite(p_source)
    p = np.where(valid, p_source, 0.0)
    F, cov, dist = {}, {}, {}
    for st in stations:
        sx, sy = tr.transform(st.lon, st.lat)
        dx, dy = (XX - sx) / 1000, (YY - sy) / 1000
        d = np.hypot(dx, dy)
        bearing = (np.rad2deg(np.arctan2(dx, dy)) + 360) % 360  # направление от станции на пиксель
        sec = (np.floor(((bearing + 360 / N_SECTORS / 2) % 360) / (360 / N_SECTORS))).astype(int).ravel()
        w = (np.where(d <= R, np.exp(-d / L), 0) * px_area).ravel()
        w_valid = np.bincount(sec, weights=w * valid.ravel(), minlength=N_SECTORS)
        w_src = np.bincount(sec, weights=w * p.ravel(), minlength=N_SECTORS)
        wd = np.bincount(sec, weights=w * p.ravel() * d.ravel(), minlength=N_SECTORS)
        c = np.clip(w_valid / denom, 0, 1)
        f = np.where(c >= MIN_SECTOR_COVERAGE, w_src / np.maximum(w_valid, 1e-12), w_src / denom)
        F[st.id] = [round(float(v), 5) for v in np.clip(f, 0, 1)]
        cov[st.id] = [round(float(v), 4) for v in c]
        dist[st.id] = [round(float(a / b), 1) if b > 0 else None for a, b in zip(wd, w_src)]
    return {"F": F, "coverage": cov, "distance_km": dist}


def sector_source(p_source: np.ndarray, transform, with_distance: bool = False):
    """Совместимая обёртка: F_s по станциям (и, по желанию, расстояния до источника, км)."""
    st = sector_stats(p_source, transform)
    return (st["F"], st["distance_km"]) if with_distance else st["F"]


def ensure_sector_distance(tag: str = EYE_TAG) -> dict[str, list[float | None]]:
    """Расстояния до источника по секторам (для фразы «около 120 км»), кэш в зоне признаков.

    Кэш хранит тег карты и пересчитывается, если тег другой или source_prob_<tag>.tif новее."""
    path = FEATURES / "sector_distance.json"
    src_path = EYE_DIR / f"source_prob_{tag}.tif"
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = None
        fresh = (isinstance(cached, dict) and cached.get("tag") == tag and isinstance(cached.get("stations"), dict)
                 and (not src_path.exists() or path.stat().st_mtime >= src_path.stat().st_mtime))
        if fresh:
            return cached["stations"]
    if not src_path.exists():
        log.warning("нет %s — расстояния до источника недоступны", src_path.name)
        return {}
    with rasterio.open(src_path) as src:
        dist = sector_stats(src.read(1), src.transform)["distance_km"]
    _write_json(path, {"tag": tag, "stations": dist})
    return dist


def _write_json(path, obj):
    """Атомарная запись JSON (tmp + os.replace)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _to_web_png(arr, transform, path, colorize, resampling=Resampling.nearest):
    """PNG на сетке EPSG:3857 (Leaflet растягивает imageOverlay линейно в Меркаторе, поэтому
    строки PNG должны быть равномерны по Меркатору, а не по широте). Возвращает углы
    [[south, west], [north, east]] в градусах WGS84."""
    from PIL import Image
    H, W = arr.shape
    dst_tr, w, h = calculate_default_transform(EYE_CRS, WEB_CRS, W, H,
                                               *rasterio.transform.array_bounds(H, W, transform))
    dst = np.zeros((h, w), dtype=arr.dtype) if arr.dtype == np.uint8 else np.full((h, w), np.nan, dtype="float32")
    reproject(arr, dst, src_transform=transform, src_crs=EYE_CRS, dst_transform=dst_tr, dst_crs=WEB_CRS,
              resampling=resampling)
    rgba = colorize(dst)
    Image.fromarray(rgba, "RGBA").save(path)
    west, north = dst_tr.c, dst_tr.f
    east, south = west + w * dst_tr.a, north + h * dst_tr.e
    lons, lats = Transformer.from_crs(WEB_CRS, "EPSG:4326", always_xy=True).transform([west, east], [south, north])
    return [[float(lats[0]), float(lons[0])], [float(lats[1]), float(lons[1])]]


_to_wgs84_png = _to_web_png  # старое имя (совместимость)


def render_pngs(stack, cls, p_source, transform, tag):
    """Слои для веб-панели (Leaflet imageOverlay): классы, вероятность источника, RGB."""
    from PIL import Image
    bounds = {}
    pal = np.zeros((6, 4), dtype=np.uint8)
    for c, (_, _, hexcol) in EYE_CLASSES.items():
        pal[c] = [int(hexcol[i:i + 2], 16) for i in (1, 3, 5)] + [210]
    bounds["classes"] = _to_web_png(cls, transform, EYE_DIR / f"classes_{tag}.png", lambda a: pal[a])

    def heat(a):
        v = np.clip(np.nan_to_num(a, nan=0), 0, 1)
        rgba = np.zeros(a.shape + (4,), dtype=np.uint8)
        rgba[..., 0] = 200 + 55 * v
        rgba[..., 1] = 180 * (1 - v)
        rgba[..., 2] = 60 * (1 - v)
        rgba[..., 3] = np.where(v > 0.2, 60 + 180 * v, 0)
        return rgba
    bounds["source_prob"] = _to_web_png(p_source, transform, EYE_DIR / f"source_prob_{tag}.png", heat,
                                        Resampling.bilinear)
    rgb = np.stack([stack[2], stack[1], stack[0]])  # B4, B3, B2
    # Истинные цвета: растягиваем 2–98 перцентили каждого канала
    rgb8 = []
    for band in rgb:
        lo, hi = np.nanpercentile(band, [2, 98])
        rgb8.append(np.clip((np.nan_to_num(band, nan=lo) - lo) / (hi - lo + 1e-6) * 255, 0, 255))
    alpha = np.where(np.all(np.isfinite(rgb), axis=0), 255, 0)
    packed = np.stack(rgb8 + [alpha]).astype(np.uint8)
    chans = []
    for i in range(4):
        b = _to_web_png(packed[i], transform, EYE_DIR / f"_tmp{i}.png", lambda a: np.dstack([a, a, a, a]))
        chans.append(np.array(Image.open(EYE_DIR / f"_tmp{i}.png"))[..., 0])
        (EYE_DIR / f"_tmp{i}.png").unlink()
    Image.fromarray(np.dstack(chans), "RGBA").save(EYE_DIR / f"rgb_{tag}.png")
    bounds["rgb"] = b
    _write_json(EYE_DIR / f"overlays_{tag}.json", bounds)
