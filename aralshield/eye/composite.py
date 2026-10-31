"""М1 «Глаз», шаг 1: медианный безоблачный композит Sentinel-2 L2A дна Арала.

Снимки берутся из открытого каталога Earth Search (AWS, облачно-оптимизированные
GeoTIFF). Читаются уменьшенные обзоры COG, поэтому полные сцены не скачиваются.
Облака, тени и снег маскируются по слою SCL (раздел 12.2).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject

from aralshield.config import AOI_BBOX, DATA, EYE_CRS, EYE_RES_M

log = logging.getLogger(__name__)
STAC_URL = "https://earth-search.aws.element84.com/v1"
BANDS = ["blue", "green", "red", "rededge1", "rededge2", "rededge3", "nir", "nir08", "swir16", "swir22"]
S2_NAMES = ["B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
BAD_SCL = {0, 1, 3, 8, 9, 10, 11}  # нет данных, дефект, тень облака, облака, перистые, снег
EYE_DIR = DATA / "eye"
EYE_DIR.mkdir(parents=True, exist_ok=True)


def target_grid(res: float = EYE_RES_M):
    tr = Transformer.from_crs("EPSG:4326", EYE_CRS, always_xy=True)
    lon0, lat0, lon1, lat1 = AOI_BBOX
    xs, ys = zip(*[tr.transform(x, y) for x in (lon0, lon1) for y in (lat0, lat1)])
    x0, x1 = np.floor(min(xs) / res) * res, np.ceil(max(xs) / res) * res
    y0, y1 = np.floor(min(ys) / res) * res, np.ceil(max(ys) / res) * res
    width, height = int((x1 - x0) / res), int((y1 - y0) / res)
    return from_origin(x0, y1, res, res), width, height


def search_items(season: str, max_cloud: float = 10, per_tile: int = 3):
    from pystac_client import Client
    cat = Client.open(STAC_URL)
    search = cat.search(collections=["sentinel-2-l2a"], bbox=list(AOI_BBOX), datetime=season,
                        query={"eo:cloud_cover": {"lt": max_cloud}}, max_items=2000)
    items = list(search.items())
    by_tile = defaultdict(list)
    for it in items:
        if it.id.endswith("_L2A") or "L2A" in it.id:
            by_tile[it.properties.get("grid:code", it.id[4:9])].append(it)
    chosen = []
    for tile, its in by_tile.items():
        # Сначала снимки с полным покрытием тайла (края полосы съёмки дают пустые половины),
        # затем с наименьшей облачностью
        its.sort(key=lambda i: (round(i.properties.get("s2:nodata_pixel_percentage", 0) / 10),
                                i.properties.get("eo:cloud_cover", 100)))
        # Не берём дубли одной даты (разные версии обработки)
        seen = set()
        for it in its:
            d = it.datetime.date()
            if d in seen:
                continue
            seen.add(d)
            chosen.append(it)
            if len(seen) >= per_tile:
                break
    log.info("найдено %d сцен, выбрано %d на %d тайлах", len(items), len(chosen), len(by_tile))
    return chosen


def _read_to_grid(href: str, transform, width, height, resampling, scale=1.0, offset=0.0, nodata=0):
    """Читает COG на уровне обзора, близком к целевому разрешению, и переносит в сетку."""
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="4",
                      GDAL_HTTP_RETRY_DELAY="2", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
        with rasterio.open(href) as src:
            res_m = src.res[0]
            if src.crs.is_geographic:  # градусы → метры на широте центра снимка
                lat = (src.bounds.top + src.bounds.bottom) / 2
                res_m = src.res[0] * 111_320 * np.cos(np.deg2rad(lat))
            factor = max(1, int(round(EYE_RES_M / res_m / 2)))  # читаем вдвое точнее цели
            out_h, out_w = src.height // factor, src.width // factor
            categorical = resampling in (Resampling.nearest, Resampling.mode)
            arr = src.read(1, out_shape=(out_h, out_w),
                           resampling=Resampling.nearest if categorical else Resampling.average).astype("float32")
            src_tr = src.transform * src.transform.scale(src.width / out_w, src.height / out_h)
            src_crs = src.crs
    valid = arr != nodata
    arr = np.where(valid, arr * scale + offset, np.nan).astype("float32")
    dst = np.full((height, width), np.nan, dtype="float32")
    reproject(arr, dst, src_transform=src_tr, src_crs=src_crs, dst_transform=transform,
              dst_crs=EYE_CRS, resampling=resampling, src_nodata=np.nan, dst_nodata=np.nan)
    return dst


def _item_stack(it, transform, width, height):
    scl = _read_to_grid(it.assets["scl"].href, transform, width, height, Resampling.nearest)
    bad = np.isnan(scl) | np.isin(np.nan_to_num(scl, nan=0).astype(int), list(BAD_SCL))
    stack = []
    # Earth Search уже вычел смещение BOA (−1000) из значений, если стоит этот флаг;
    # тогда смещение −0,1 из метаданных raster:bands применять повторно нельзя.
    offset_applied = bool(it.properties.get("earthsearch:boa_offset_applied", False))
    for b in BANDS:
        a = it.assets[b]
        rb = (a.extra_fields.get("raster:bands") or [{}])[0]
        offset = 0.0 if offset_applied else rb.get("offset", 0.0)
        arr = _read_to_grid(a.href, transform, width, height, Resampling.average, rb.get("scale", 1e-4), offset)
        arr[bad] = np.nan
        stack.append(arr)
    return np.stack(stack)


def build_composite(season: str = "2025-06-01/2025-09-30", tag: str = "2025",
                    per_tile: int = 4, workers: int = 6) -> Path:
    out = EYE_DIR / f"s2_composite_{tag}.tif"
    if out.exists():
        return out
    transform, width, height = target_grid()
    items = search_items(season, per_tile=per_tile)
    stacks = []

    def work(it):
        try:
            return _item_stack(it, transform, width, height)
        except Exception as e:  # отдельная сцена не должна ломать композит
            log.warning("сцена %s пропущена: %s", it.id, e)
            return None

    with ThreadPoolExecutor(workers) as ex:
        for i, s in enumerate(ex.map(work, items)):
            if s is not None:
                stacks.append(s)
            log.info("сцен обработано: %d/%d", i + 1, len(items))
    comp = np.nanmedian(np.stack(stacks), axis=0).astype("float32")
    profile = dict(driver="GTiff", height=height, width=width, count=len(BANDS), dtype="float32",
                   crs=EYE_CRS, transform=transform, nodata=np.nan, compress="deflate", tiled=True)
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(comp)
        dst.descriptions = tuple(S2_NAMES)
        dst.update_tags(season=season, scenes=len(stacks))
    return out


def worldcover_to_grid() -> np.ndarray:
    """ESA WorldCover 2021 (10 м) в сетке «Глаза» — независимые метки воды и растительности."""
    out = EYE_DIR / "worldcover_grid.npy"
    if out.exists():
        return np.load(out)
    transform, width, height = target_grid()
    grid = np.zeros((height, width), dtype="float32")
    lon0, lat0, lon1, lat1 = AOI_BBOX
    for lat in range(int(np.floor(lat0 / 3) * 3), int(lat1) + 1, 3):
        for lon in range(int(np.floor(lon0 / 3) * 3), int(lon1) + 1, 3):
            href = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
                    f"ESA_WorldCover_10m_2021_v200_N{lat:02d}E{lon:03d}_Map.tif")
            try:
                a = _read_to_grid(href, transform, width, height, Resampling.mode)
            except Exception as e:
                log.warning("WorldCover %s: %s", href, e)
                continue
            m = ~np.isnan(a) & (grid == 0)
            grid[m] = a[m]
    np.save(out, grid)
    return grid
