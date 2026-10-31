"""Модуль «Щит»: геометрия ветра, формула приоритета, сценарии, планы, экспорт ГИС."""
import json
import time
import xml.dom.minidom

import numpy as np
import pandas as pd
import pytest
from pyproj import Transformer

from aralshield.config import EYE_CRS, N_SECTORS, SETTLEMENTS, SOURCE_RADIUS_KM
from aralshield.shield import priority as P

TO_UTM = Transformer.from_crs("EPSG:4326", EYE_CRS, always_xy=True)
TO_LL = Transformer.from_crs(EYE_CRS, "EPSG:4326", always_xy=True)
J0 = 0  # Аральск
AX, AY = TO_UTM.transform(SETTLEMENTS[J0].lon, SETTLEMENTS[J0].lat)


def _cells(offsets_km, S=None, M=None):
    """Ячейки, смещённые от Аральска на (dx, dy) км."""
    off = np.asarray(offsets_km, float)
    x, y = AX + off[:, 0] * 1000, AY + off[:, 1] * 1000
    lon, lat = TO_LL.transform(x, y)
    n = len(off)
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"cell_id": np.arange(100, 100 + n), "x": x, "y": y, "lon": lon, "lat": lat,
                       "S": np.full(n, 0.5) if S is None else S, "M": np.full(n, 0.8) if M is None else M})
    for c in P.SUIT_COMPONENTS:
        df[c] = rng.uniform(size=n)
    return df


def _uniform_rose(v=0.05):
    return np.full(N_SECTORS, v)


def test_cell_due_north_is_sector_N_and_east_is_E():
    cells = _cells([(0, 50), (50, 0), (0, -50), (-50, 0)])
    d, sec = P._geometry(cells)
    assert list(sec[:, J0]) == [0, 4, 8, 12]   # ветер «откуда» = из ячейки на пункт
    assert np.allclose(d[:, J0], 50, atol=0.01)


def test_priority_formula_direction():
    cells = _cells([(0, 50), (0, 50), (0, 120), (0, 50)], S=np.array([0.5, 0.9, 0.5, 0.5]),
                   M=np.array([0.5, 0.5, 0.5, 0.9]))
    d, sec = P._geometry(cells)
    pr = P.priority(cells, {8.0: _uniform_rose()}, d, sec, L=100.0, vt=8.0)
    assert pr[1] > pr[0]          # больше источника → выше
    assert pr[3] > pr[0]          # выше пригодность → выше
    assert pr[0] > pr[2]          # ближе → выше
    # Ветер только с севера: ячейка к северу влияет на Аральск, ячейка к югу — нет
    rose = np.zeros(N_SECTORS)
    rose[0] = 0.1
    c2 = _cells([(0, 50), (0, -50)])
    d2, s2 = P._geometry(c2)
    E = P.exposure(c2, rose, d2, s2, 100.0)
    assert E[0, J0] > 0 and E[1, J0] == 0


def test_distance_cap():
    cells = _cells([(0, 50)])
    d, sec = P._geometry(cells)
    far = d > SOURCE_RADIUS_KM
    assert far.any()
    E = P.exposure(cells, _uniform_rose(), d, sec, 100.0)
    assert np.all(E[far] == 0) and np.all(E[~far] > 0)
    assert np.all(P.exposure(cells, _uniform_rose(), d, sec, 100.0, d_max=None) > 0)


def test_empty_selection_zero_reduction():
    cells = _cells([(0, 50), (30, 30)])
    d, sec = P._geometry(cells)
    r = P.scenario(cells, [], _uniform_rose(), d, sec)
    assert r["cells"] == 0 and r["area_ha"] == 0
    assert r["total_reduction_pct"] == 0 and all(p["reduction_pct"] == 0 for p in r["per_settlement"])


def test_reduction_monotone_in_cover_and_dedup():
    cells = _cells([(0, 50), (30, 30), (-20, 10)])
    d, sec = P._geometry(cells)
    vals = [P.scenario(cells, [100, 101], _uniform_rose(), d, sec, cover=c)["total_reduction_pct"]
            for c in (0.1, 0.3, 0.6, 1.0)]
    assert all(b > a for a, b in zip(vals, vals[1:]))
    r = P.scenario(cells, [100, 100, 100, 999], _uniform_rose(), d, sec)
    assert r["cells"] == 1 and r["cell_ids"] == [100] and r["unknown_ids"] == [999]


def test_plan_area_exact_and_not_below_request():
    rng = np.random.default_rng(3)
    off = rng.uniform(-100, 100, size=(60, 2))
    cells = _cells(off, S=rng.uniform(0, 1, 60), M=rng.uniform(0, 1, 60))
    cells["priority"] = rng.uniform(size=60)
    d, sec = P._geometry(cells)
    for plan in ("rating", "random", "nearest"):
        for area in (1000.0, 400.0, 1.0, 1250.0):
            ids, last = P.plan_cells(cells, plan, area, d, seed=1)
            r = P.scenario(cells, ids, _uniform_rose(), d, sec, last_fraction=last)
            assert r["area_ha"] >= area - 1e-6
            assert r["area_ha"] == pytest.approx(area)
    ids, last = P.plan_cells(cells, "rating", 1000.0, d)
    assert len(ids) == 3 and last == pytest.approx(0.5)
    assert ids == cells.sort_values("priority", ascending=False).cell_id.head(3).tolist()
    # random: только S выше медианы и M > 0.5
    ok = set(cells[(cells.S > cells.S.median()) & (cells.M > 0.5)].cell_id)
    for s in range(5):
        assert set(P.plan_cells(cells, "random", 1200.0, d, seed=s)[0]) <= ok
    # nearest: пригодные, по возрастанию расстояния до ближайшего пункта
    ids, _ = P.plan_cells(cells, "nearest", 2000.0, d)
    pos = [cells.index[cells.cell_id == i][0] for i in ids]
    assert all(cells.M.iloc[p] > 0.5 for p in pos)
    assert list(d[pos].min(axis=1)) == sorted(d[pos].min(axis=1))


def test_partial_last_cell_scales_effect():
    cells = _cells([(0, 50)])
    d, sec = P._geometry(cells)
    full = P.scenario(cells, [100], _uniform_rose(), d, sec)
    half = P.scenario(cells, [100], _uniform_rose(), d, sec, last_fraction=0.5)
    assert half["area_ha"] == P.CELL_HA / 2
    assert half["total_reduction_pct"] == pytest.approx(full["total_reduction_pct"] / 2)


def test_random_distribution_keys():
    rng = np.random.default_rng(4)
    cells = _cells(rng.uniform(-100, 100, size=(40, 2)), S=rng.uniform(0, 1, 40), M=rng.uniform(0.4, 1, 40))
    d, sec = P._geometry(cells)
    r = P.random_plan_distribution(cells, 800.0, _uniform_rose(), d, sec, n_draws=10)
    assert r["p5"] <= r["mean"] <= r["p95"] and r["n_draws"] == 10


def test_protected_population_definition():
    N = np.array([s.population for s in SETTLEMENTS], float)
    E = np.zeros((3, len(N)))
    E[0, 0] = 1.0                       # весь вклад — одному пункту
    E[1, 0], E[1, 1] = 0.5, 0.5         # двум поровну
    out = P.protected_population(E)
    assert out[0] == N[0]
    assert out[1] == N[0] + N[1]
    assert out[2] == 0                  # нет вклада — никого не защищает


def test_rbo_bounds():
    a = list(range(50))
    assert P.rbo(a, a) == pytest.approx(1.0)
    assert P.rbo(a, list(range(100, 150))) == pytest.approx(0.0)
    assert 0 < P.rbo(a, a[::-1]) < 1


def test_classify_requires_min_suitability():
    pr = np.arange(100, dtype=float)
    stab = np.ones(100)
    M = np.full(100, 0.9)
    M[-1] = 0.1
    cls = P.classify_priority(pr, stab, M)
    assert cls[-1] == "средний" and cls[-2] == "надёжный высокий"


def test_suitability_weight_sets_sum_to_one():
    for w in P.SUIT_WEIGHT_SETS.values():
        assert sum(w) == pytest.approx(1.0)
    cells = _cells([(0, 10)] * 5)
    for c in P.SUIT_COMPONENTS:
        cells[c] = 1.0
    assert np.allclose(P.suitability(cells, "equal"), 1.0)


def _signed_area(ring):
    a = np.asarray(ring, float)
    x, y = a[:, 0], a[:, 1]
    return 0.5 * np.sum(x[:-1] * y[1:] - x[1:] * y[:-1])


def _export_cells(n=20):
    rng = np.random.default_rng(5)
    cells = _cells(rng.uniform(-100, 100, size=(n, 2)))
    cells["priority"] = rng.uniform(size=n)
    cells["stability"] = rng.uniform(size=n)
    cells["class"] = np.where(np.arange(n) % 2 == 0, "высокий", "надёжный высокий")
    cells["protects"] = "aralsk"
    cells["protected_population"] = 30000
    return cells


def test_export_orientation_cpg_fields(tmp_path):
    shapefile = pytest.importorskip("shapefile")
    cells = _export_cells()
    P.export(cells, tmp_path)
    assert (tmp_path / "priority_top.cpg").read_text() == "UTF-8"
    r = shapefile.Reader(str(tmp_path / "priority_top"))
    areas = [_signed_area(s.points) for s in r.shapes()]
    assert len(areas) == 20 and all(a < 0 for a in areas)          # ESRI: по часовой
    names = [f[0] for f in r.fields[1:]]
    for need in ("area_ha", "prot_pop", "stab", "source"):
        assert need in names
    assert all(f[2] <= 19 for f in r.fields[1:] if f[1] in ("N", "F"))
    assert r.record(0)["class"] in ("высокий", "надёжный высокий")
    gj = json.loads((tmp_path / "priority_top.geojson").read_text(encoding="utf-8"))
    assert all(_signed_area(f["geometry"]["coordinates"][0]) > 0 for f in gj["features"])  # RFC 7946
    xml.dom.minidom.parse(str(tmp_path / "priority_top.kml"))


def test_geojson_true_utm_square_and_speed():
    cells = _export_cells(6000)
    ring = np.asarray(P.to_geojson(cells.head(1))["features"][0]["geometry"]["coordinates"][0])
    x, y = TO_UTM.transform(ring[:, 0], ring[:, 1])
    assert np.allclose(np.diff(np.asarray(x))[[0, 2]], [2000, -2000], atol=2)   # стороны квадрата UTM
    assert np.allclose(np.diff(np.asarray(y))[[1, 3]], [2000, -2000], atol=2)
    assert all(len(str(v).split(".")[-1]) <= 5 for v in ring.ravel())
    no_xy = cells.drop(columns=["x", "y"])          # API может прислать ячейки без x, y
    a = P.to_geojson(no_xy.head(3))["features"][0]["geometry"]["coordinates"][0]
    assert np.allclose(a, P.to_geojson(cells.head(3))["features"][0]["geometry"]["coordinates"][0], atol=1e-5)
    P.to_geojson(cells)
    t = time.perf_counter()
    gj = P.to_geojson(cells)
    dt = time.perf_counter() - t
    assert len(gj["features"]) == 6000
    assert set(gj["features"][0]["properties"]) == {"cell_id", "priority", "class", "suitability", "source",
                                                    "stability", "protects", "protected_population", "area_ha"}
    assert dt < 1.0, f"to_geojson 6000 ячеек: {dt:.3f} с"
