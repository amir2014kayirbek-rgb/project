"""Модуль «Глаз»: секторные признаки F_s(θ), покрытие, слабая разметка, сверка с WorldCover, оверлеи."""
import json
import os
import time
import warnings
from collections import namedtuple

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from sklearn.ensemble import RandomForestClassifier

from aralshield.config import EYE_CRS, N_SECTORS
from aralshield.eye import classify as C
from aralshield.eye.composite import S2_NAMES

St = namedtuple("St", "id lon lat")
STATION = St("test", 61.67, 46.80)
PX_M = 2000.0
HALF = 155  # пикселей по 2 км от станции → 310 км > R = 300 км


def _grid():
    sx, sy = Transformer.from_crs("EPSG:4326", EYE_CRS, always_xy=True).transform(STATION.lon, STATION.lat)
    tr = from_origin(sx - HALF * PX_M, sy + HALF * PX_M, PX_M, PX_M)
    n = 2 * HALF
    xs = (np.arange(n) + 0.5) * PX_M - HALF * PX_M  # смещение центра пикселя от станции, м
    ys = HALF * PX_M - (np.arange(n) + 0.5) * PX_M
    DX, DY = np.meshgrid(xs, ys)
    return tr, DX / 1000, DY / 1000


def _stats(p, tr):
    return C.sector_stats(p, tr, stations=[STATION])


def test_full_disk_gives_one_everywhere():
    tr, DX, _ = _grid()
    st = _stats(np.ones(DX.shape, "float32"), tr)
    assert np.allclose(st["F"]["test"], 1.0, atol=1e-6)
    assert np.allclose(st["coverage"]["test"], 1.0, atol=0.03)


def test_bearing_convention_north_is_sector_0_east_is_sector_4():
    tr, DX, DY = _grid()
    north = ((np.abs(DX) < 1.5) & (DY > 10))  # у самой станции азимут пикселя ±45°.astype("float32")
    f = np.array(_stats(north, tr)["F"]["test"])
    assert f[0] > 0 and np.all(f[1:] == 0)
    east = ((np.abs(DY) < 1.5) & (DX > 10)).astype("float32")
    f = np.array(_stats(east, tr)["F"]["test"])
    assert f[4] > 0 and np.count_nonzero(f) == 1


def test_gaps_are_not_counted_as_no_source():
    """Шахматные пропуски: F_s = 1 во всех секторах, покрытие ≈ 0.5 (раньше F_s ≈ 0.5)."""
    tr, DX, _ = _grid()
    p = np.ones(DX.shape, "float32")
    rr, cc = np.indices(p.shape)
    p[(rr + cc) % 2 == 1] = np.nan
    st = _stats(p, tr)
    assert np.allclose(st["F"]["test"], 1.0, atol=1e-6)
    assert np.allclose(st["coverage"]["test"], 0.5, atol=0.03)


def test_half_disk_missing():
    """Западная половина без данных: восточные секторы F=1, покрытие≈1; западные — покрытие≈0."""
    tr, DX, _ = _grid()
    p = np.where(DX > 0, 1.0, np.nan).astype("float32")
    st = _stats(p, tr)
    F, cov = np.array(st["F"]["test"]), np.array(st["coverage"]["test"])
    east, west = [2, 3, 4, 5, 6], [10, 11, 12, 13, 14]
    assert np.allclose(F[east], 1.0) and np.all(cov[east] > 0.95)
    assert np.all(cov[west] < 0.01) and np.all(F[west] == 0)  # данных нет → нижняя оценка 0, но coverage≈0
    assert 0.4 < cov.mean() < 0.6
    assert st["distance_km"]["test"][12] is None


def test_low_coverage_uses_lower_bound():
    """При покрытии < MIN_SECTOR_COVERAGE доля не экстраполируется на весь сектор."""
    tr, DX, DY = _grid()
    d = np.hypot(DX, DY)
    p = np.where((d > 250) & (d < 300), 1.0, np.nan).astype("float32")  # тонкое дальнее кольцо
    st = _stats(p, tr)
    cov = np.array(st["coverage"]["test"])
    assert np.all(cov < C.MIN_SECTOR_COVERAGE)
    assert np.allclose(st["F"]["test"], cov, atol=2e-4)  # нижняя оценка = наблюдённая доля (p=1)


def _scene(h=40, w=40, bare=True):
    """Синтетическая сцена: 10 каналов, WorldCover = голый грунт (60) или только вода (80)."""
    rng = np.random.default_rng(0)
    base = np.array([0.20, 0.22, 0.25, 0.27, 0.28, 0.29, 0.30, 0.30, 0.35, 0.30], "float32")
    stack = base[:, None, None] + rng.normal(0, 0.01, (10, h, w)).astype("float32")
    wc = np.full((h, w), 60 if bare else 80, dtype="float32")
    return stack, C.indices(stack), wc


def test_weak_labels_empty_bare_pool_no_crash():
    stack, idx, wc = _scene(bare=False)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        lab = C.weak_labels(stack, idx, wc)
    assert not np.isin(lab, [1, 2, 3]).any()
    assert C.calibrate_label_thresholds(stack, idx, wc) is None


def test_fixed_thresholds_are_not_renormalised():
    """Пороги калибровки, применённые к сцене, где всё темнее порога, не создают «источник»."""
    stack, idx, wc = _scene()
    thr = C.calibrate_label_thresholds(stack, idx, wc)
    assert set(thr) == set(C.LABEL_QUANTILES)
    lab_scene = C.weak_labels(stack, idx, wc, thr)
    assert (lab_scene == 1).any()
    dark = stack * 0.5
    lab_dark = C.weak_labels(dark, C.indices(dark), wc, thr)
    assert not (lab_dark == 1).any()           # фиксированные пороги: тёмная сцена не «солевая»
    assert (C.weak_labels(dark, C.indices(dark), wc) == 1).any()  # перцентили сцены дали бы класс всегда


def test_proba_full_with_missing_classes():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(300, 4))
    y = np.repeat([2, 4, 5], 100)
    X[y == 4] += 3
    X[y == 5] -= 3
    rf = RandomForestClassifier(n_estimators=10, random_state=0).fit(X, y)
    P = C.proba_full(rf, X)
    assert P.shape == (300, 5)
    assert np.allclose(P[:, [0, 2]], 0)          # классов 1 и 3 нет → нулевые столбцы
    assert np.allclose(P.sum(axis=1), 1)
    assert np.allclose(P[:, 1], rf.predict_proba(X)[:, 0])  # класс 2 — первый в rf.classes_


def test_worldcover_agreement_excludes_weak_labels_and_reports_baseline():
    h, w = 20, 20
    wc = np.full((h, w), 60)
    wc[:5] = 80                     # вода по WorldCover
    lab = np.zeros((h, w), "uint8")
    lab[:5] = 5                     # слабые метки воды взяты из WorldCover
    cls = np.full((h, w), 1, "uint8")
    cls[:5] = 5                     # модель повторяет метки
    trained = np.zeros((h, w), bool)
    out = C.worldcover_agreement(cls, wc, trained, lab=lab, block_px=5)
    assert out["independent"] is False and "не независимая" in out["note_ru"] and out["note_kk"]
    assert out["n_pixels"] == 15 * 20                 # размеченные пиксели исключены
    assert out["baseline_majority"] == 1.0
    assert out["gain_over_baseline"] == 0.0
    assert out["including_weak_labelled_pixels"]["n_pixels"] == 400
    assert out["unlabelled_blocks"]["n_pixels"] == 15 * 20


def test_ensure_sector_distance_tagged_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "FEATURES", tmp_path)
    monkeypatch.setattr(C, "EYE_DIR", tmp_path)
    sx, sy = Transformer.from_crs("EPSG:4326", EYE_CRS, always_xy=True).transform(61.67, 46.80)
    tr = from_origin(sx - 20_000, sy + 20_000, 2000, 2000)
    prof = dict(driver="GTiff", width=20, height=20, count=1, dtype="float32", crs=EYE_CRS, transform=tr)
    with rasterio.open(tmp_path / "source_prob_t1.tif", "w", **prof) as dst:
        dst.write(np.ones((1, 20, 20), "float32"))
    cache = tmp_path / "sector_distance.json"
    cache.write_text(json.dumps({"aralsk": [1.0] * N_SECTORS}), encoding="utf-8")  # старый формат без тега
    d1 = C.ensure_sector_distance("t1")
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert saved["tag"] == "t1" and d1 == saved["stations"] and d1["aralsk"] != [1.0] * N_SECTORS
    saved["stations"]["aralsk"] = [7.0] * N_SECTORS
    cache.write_text(json.dumps(saved), encoding="utf-8")
    assert C.ensure_sector_distance("t1")["aralsk"] == [7.0] * N_SECTORS   # тот же тег → кэш
    assert C.ensure_sector_distance("other") == {}                          # нет растра другого тега
    later = time.time() + 5
    os.utime(tmp_path / "source_prob_t1.tif", (later, later))               # растр новее кэша
    assert C.ensure_sector_distance("t1")["aralsk"] != [7.0] * N_SECTORS


def test_png_overlay_is_mercator_aligned(tmp_path):
    """Горизонтальная полоса UTM попадает в строку PNG, соответствующую её широте в Меркаторе."""
    from PIL import Image
    sx, sy = Transformer.from_crs("EPSG:4326", EYE_CRS, always_xy=True).transform(61.0, 45.7)
    n, res = 150, 2000.0
    tr = from_origin(sx - n / 2 * res, sy + n / 2 * res, res, res)
    arr = np.zeros((n, n), "uint8")
    row = 40
    arr[row - 1:row + 2] = 1  # полоса 3 пикселя, чтобы пережить ресемплинг
    (s, w), (nn, e) = C._to_web_png(arr, tr, tmp_path / "x.png", lambda a: np.dstack([a * 255] * 4).astype("uint8"))
    assert s < nn and w < e
    img = np.array(Image.open(tmp_path / "x.png"))[..., 0]
    col = img.shape[1] // 2
    hit = np.flatnonzero(img[:, col] > 0).mean()
    # истинная широта полосы в центре
    x_c = tr.c + (n / 2) * res
    y_c = tr.f + (row + 0.5) * tr.e
    _, lat_true = Transformer.from_crs(EYE_CRS, "EPSG:4326", always_xy=True).transform(x_c, y_c)
    merc = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    y_n, y_s, y_t = (merc.transform(0, v)[1] for v in (nn, s, lat_true))
    expect_row = (y_n - y_t) / (y_n - y_s) * img.shape[0] - 0.5
    assert abs(hit - expect_row) <= 1.5
    assert S2_NAMES[0] == "B2"  # порядок каналов, на который опирается indices()
