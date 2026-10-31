"""М0 «Правда»: загрузка синоптических наблюдений NOAA ISD и каталог пылевых дней.

Пылевой день (раздел 10.2, приложение Б): в течение местных суток
  * код текущей погоды ВМО ww ∈ {06–09, 30–35, 98}, или
  * видимость < 5 км без осадков, тумана и метели (ww не в {10–12, 20–29, 36–99})
    при ветре строго выше порога 6 м/с.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from aralshield.config import CLEAN, LOCAL_UTC_OFFSET_H, RAW, STATIONS, YEARS
from aralshield.weather import http

log = logging.getLogger(__name__)

ISD_URL = "https://noaa-global-hourly-pds.s3.amazonaws.com/{year}/{isd}.csv"

DUST_WW = {6, 7, 8, 9, 30, 31, 32, 33, 34, 35, 98}
STRONG_WW = {33, 34, 35, 98}
VIS_THRESHOLD_M = 5000
# Порог ветра для критерия видимости: «выше порога» (раздел 10.2) — строго больше 6 м/с.
# Значение 6 м/с — типичная пороговая скорость подъёма пыли с сухой поверхности; сверяется с Казгидрометом.
VIS_WIND_MIN_MS = 6.0
# Коды, при которых плохая видимость — не пыль: дымка/туман (10–12), осадки или туман
# в последний час (20–29), позёмок/метель (36–39), туман (40–49), осадки и грозы (50–99).
VIS_EXCLUDE_WW = set(range(10, 13)) | set(range(20, 30)) | set(range(36, 100))
MIN_TERMS_PER_DAY = 6  # не менее 6 из 8 синоптических сроков (раздел 22.3)

# Порядок предпочтения записей одного срока: SYNOP > SYNOP+METAR > SYNOP судна/AWS > METAR
SYNOP_TYPES = ("FM-12", "SY-MT")
REPORT_PRIORITY = {"FM-12": 0, "SY-MT": 1, "FM-16": 2, "FM-15": 3}

# Станционный год считается надёжным для кодового критерия, если код текущей погоды ww
# (MW1 или группа 7wwW1W2) передан хотя бы в WW_REPORT_MIN_RATE синоптических сроков.
# Порог подобран по распределению ww_report_rate в completeness.csv (раздел 10.3): у Аральска,
# Жосалы и Кызылорды доля во все годы 2000–2025 ≥ 0,096, у Казалинска в 2000–2014 и 2016–2022 —
# 0,04–0,075 (код почти не передавался, «нет пыли» там — отсутствие наблюдения, а не чистый воздух).
WW_REPORT_MIN_RATE = 0.08

NOT_FOUND_TTL_DAYS = 7     # повторить запрос года, которого «нет» (404), через неделю
ISD_LATENCY_DAYS = 60      # NOAA выкладывает год полностью не раньше чем через ~2 месяца


def _paths(isd: str, year: int):
    base = RAW / "isd" / f"{isd}_{year}"
    return base.with_suffix(".csv"), base.with_suffix(".404")


def _fresh_marker(path) -> bool:
    """Отметка «нет данных» (404) ещё действует? Пустой .csv — отметка старого формата."""
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age < timedelta(days=NOT_FOUND_TTL_DAYS)


def fetch_station_year(isd: str, year: int, deadline: float | None = None) -> pd.DataFrame | None:
    """Скачивает год наблюдений в память, ничего не записывая (боевой режим).
    None — NOAA ещё не выложил год (404)."""
    r = http.get(ISD_URL.format(year=year, isd=isd), allow_404=True, deadline=deadline)
    if r.status_code == 404:
        return None
    return pd.read_csv(io.BytesIO(r.content), dtype=str, low_memory=False)


def read_cached_year(isd: str, year: int) -> pd.DataFrame | None:
    """Уже скачанный год из сырой зоны (без сети)."""
    path, _ = _paths(isd, year)
    if not path.exists() or path.stat().st_size == 0:
        return None
    return pd.read_csv(path, dtype=str, low_memory=False)


def download_station_year(isd: str, year: int, force: bool = False) -> pd.DataFrame | None:
    """Скачивает год наблюдений станции в сырую зону (идемпотентно).

    * 404 записывается отдельной отметкой `.404` со сроком годности NOT_FOUND_TTL_DAYS;
    * файл «открытого» года (скачан до того, как NOAA мог выложить его целиком) обновляется по возрасту;
    * запись атомарная (tmp + os.replace); если сеть недоступна, остаётся прежний файл.
    """
    path, marker = _paths(isd, year)
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_empty = path.exists() and path.stat().st_size == 0
    has_data = path.exists() and not legacy_empty
    not_found = _fresh_marker(marker) or (legacy_empty and _fresh_marker(path))
    stale_open = has_data and http.is_open_cache(path, date(year, 12, 31), ISD_LATENCY_DAYS)
    if force or stale_open or (not has_data and not not_found):
        url = ISD_URL.format(year=year, isd=isd)
        try:
            r = http.get(url, allow_404=True)
        except http.HTTPGiveUp as e:
            if not has_data:
                raise RuntimeError(f"не удалось скачать {url}") from e
            log.warning("ISD %s %d: сеть недоступна (%s), используется прежний файл", isd, year, e)
        else:
            if r.status_code == 404:
                log.warning("нет данных %s за %s", isd, year)
                http.atomic_write_bytes(marker, datetime.now().isoformat().encode())
                if legacy_empty:
                    path.unlink()
                return read_cached_year(isd, year) if has_data else None
            http.atomic_write_bytes(path, r.content)
            if marker.exists():
                marker.unlink()
    return read_cached_year(isd, year)


def _num(part: str, scale: float = 1.0, missing: tuple[str, ...] = ()) -> float:
    if part is None or part == "" or part in missing:
        return np.nan
    try:
        return float(part) / scale
    except ValueError:
        return np.nan


_SYNOP_7 = re.compile(r"^7(\d\d)(\d|/)(\d|/)$")
_IRIXHVV = re.compile(r"^[0-4/][1-7/][0-9/][0-9/]{2}$")
_SECTION_END = ("333", "444", "555", "222")


def _synop_info(rem: str) -> tuple[float, float]:
    """(ww, ix) из раздела 1 сводки SYNOP в поле REM.

    Два формата REM в архиве NOAA:
      * старый: `SYN082AAXX YYGGi IIiii iRixhVV Nddff …` (AAXX может стоять отдельно);
      * новый:  `SYN058<IIiii> iRixhVV Nddff …`.
    После iRixhVV идут Nddff и, при ff = 99, группа 00fff; группа 7wwW1W2 читается только
    при ix ∈ {1, 4} (таблица 4677). При ix = 7 (автоматическая станция, таблица 4680, где
    30–35 — туман) код не используется для кодового критерия. Разбор останавливается на 333/555.
    """
    if not isinstance(rem, str) or "SYN" not in rem:
        return np.nan, np.nan
    tok = rem.split("SYN", 1)[1].replace("=", " ").replace(";", " ").split()
    if not tok:
        return np.nan, np.nan
    aaxx = next((k for k, t in enumerate(tok[:2]) if "AAXX" in t), None)
    if aaxx is not None:
        i = aaxx + 3              # AAXX YYGGi IIiii
    elif len(tok[0]) == 8 and tok[0].isdigit():
        i = 1                     # <длина><IIiii>
    elif len(tok) > 1 and tok[0].isdigit() and len(tok[0]) == 3 and len(tok[1]) == 5:
        i = 2                     # <длина> <IIiii>
    else:
        return np.nan, np.nan     # BUFR, NIL и прочее
    if len(tok) <= i + 1 or not _IRIXHVV.match(tok[i]):
        return np.nan, np.nan
    ix_c = tok[i][1]
    ix = float(ix_c) if ix_c.isdigit() else np.nan
    nddff = tok[i + 1]
    j = i + 2
    if nddff[3:5] == "99" and j < len(tok) and tok[j].startswith("00") and len(tok[j]) == 5:
        j += 1                    # 00fff — скорость ≥ 99 единиц
    if ix_c not in ("1", "4"):
        return np.nan, ix
    for g in tok[j:]:
        if g in _SECTION_END or (g.startswith("222") and len(g) == 5):
            break
        m = _SYNOP_7.match(g)
        if m:
            return float(m.group(1)), ix
    return np.nan, ix


def _ww_from_synop(rem: str) -> float:
    """Достаёт ww из группы 7wwW1W2 раздела 1 сводки (если MW1 пуст)."""
    return _synop_info(rem)[0]


def parse_isd(df: pd.DataFrame, station_id: str) -> pd.DataFrame:
    """Разбирает поля ISD в очищенные наблюдения с флагами качества."""
    out = pd.DataFrame()
    out["time_utc"] = pd.to_datetime(df["DATE"], utc=True)
    wnd = df["WND"].fillna("").str.split(",", expand=True).reindex(columns=range(5))
    out["wind_dir"] = [_num(v, missing=("999",)) for v in wnd[0]]
    out["wind_speed"] = [_num(v, 10, missing=("9999",)) for v in wnd[3]]
    vis = df["VIS"].fillna("").str.split(",", expand=True).reindex(columns=range(4))
    out["visibility_m"] = [_num(v, missing=("999999",)) for v in vis[0]]
    mw = df["MW1"].fillna("") if "MW1" in df else pd.Series([""] * len(df))
    ww = [_num(v.split(",")[0]) if v else np.nan for v in mw]
    rem = df["REM"] if "REM" in df else pd.Series([None] * len(df))
    syn = [_synop_info(r) for r in rem]
    out["ww"] = [w if not np.isnan(w) else s[0] for w, s in zip(ww, syn)]
    out["ix"] = [s[1] for s in syn]
    out["ww_source"] = np.select([~np.isnan(ww), out["ww"].notna().to_numpy()], ["MW1", "SYN"], default="")
    # Порыв ветра (OC1) при наличии
    if "OC1" in df:
        out["gust"] = [_num(v.split(",")[0], 10, missing=("9999",)) if isinstance(v, str) else np.nan
                       for v in df["OC1"]]
    else:
        out["gust"] = np.nan
    out["station"] = station_id
    out["report_type"] = (df["REPORT_TYPE"].fillna("").str.strip() if "REPORT_TYPE" in df
                          else pd.Series([""] * len(df))).to_numpy()

    # Контроль диапазонов (раздел 22.3)
    out.loc[(out.wind_speed < 0) | (out.wind_speed > 60), "wind_speed"] = np.nan
    out.loc[(out.visibility_m < 0) | (out.visibility_m > 100_000), "visibility_m"] = np.nan
    # Только синоптические сроки (кратные 3 ч), одна запись на срок
    out = out[out.time_utc.dt.minute == 0]
    out = out[out.time_utc.dt.hour % 3 == 0]
    # Дубли срока: детерминированно оставляем SYNOP (FM-12 > SY-MT > FM-16 > FM-15), раздел 22.3
    out = out.assign(_prio=out.report_type.map(REPORT_PRIORITY).fillna(9))
    out = out.sort_values(["time_utc", "_prio"], kind="stable")
    dup = out.duplicated(["station", "time_utc"], keep=False)
    if dup.any():
        g = out[dup].groupby("time_utc")
        n_conf = int((g["report_type"].nunique() > 1).sum())
        n_vis = int((g["visibility_m"].nunique() > 1).sum())
        log.info("%s: %d сроков с дублями (%d с разными типами сводок, %d с расхождением видимости); "
                 "оставлена запись с приоритетом FM-12", station_id, int(g.ngroups), n_conf, n_vis)
    out = out.drop_duplicates(["station", "time_utc"], keep="first").drop(columns="_prio")
    return out.reset_index(drop=True)


def build_observations(years: list[int] = YEARS) -> pd.DataFrame:
    frames = []
    for st in STATIONS:
        for y in years:
            raw = download_station_year(st.isd, y)
            if raw is None or raw.empty:
                continue
            frames.append(parse_isd(raw, st.id))
            log.info("%s %d: %d сроков", st.id, y, len(frames[-1]))
    obs = pd.concat(frames, ignore_index=True)
    http.atomic_to_parquet(obs, CLEAN / "observations.parquet")
    return obs


def _valid_synop_term(o: pd.DataFrame) -> pd.Series:
    """Срок засчитывается в полноту, только если это синоптическая сводка (FM-12/SY-MT)
    с видимостью или с индикатором погоды (ix / ww), раздел 22.3."""
    synop = o["report_type"].isin(SYNOP_TYPES) if "report_type" in o else pd.Series(True, index=o.index)
    ix = o["ix"] if "ix" in o else pd.Series(np.nan, index=o.index)
    return synop & (o["visibility_m"].notna() | ix.notna() | o["ww"].notna())


def build_events(obs: pd.DataFrame, save: bool = True) -> pd.DataFrame:
    """Каталог пылевых дней по станциям (местные сутки, UTC+5)."""
    o = obs.copy()
    o["date"] = (o.time_utc + pd.Timedelta(hours=LOCAL_UTC_OFFSET_H)).dt.tz_localize(None).dt.normalize()
    ww = o["ww"]
    o["by_code"] = ww.isin(DUST_WW)
    o["strong"] = ww.isin(STRONG_WW)
    vis_ok_ww = ww.isna() | ~ww.isin(VIS_EXCLUDE_WW)
    o["by_vis"] = (o.visibility_m < VIS_THRESHOLD_M) & vis_ok_ww & (o.wind_speed > VIS_WIND_MIN_MS)
    # Согласованность: код пыльной бури при видимости > 10 км — на ручную проверку
    o["suspicious"] = ww.isin({30, 31, 32, 33, 34, 35}) & (o.visibility_m > 10_000)
    o["valid_term"] = _valid_synop_term(o)
    o["ww_term"] = o["valid_term"] & ww.notna()

    g = o.groupby(["station", "date"])
    ev = pd.DataFrame({
        "n_terms": g["valid_term"].sum(),
        "n_ww": g["ww_term"].sum(),
        "by_code": g["by_code"].any(),
        "by_vis": g["by_vis"].any(),
        "strong": g["strong"].any(),
        "suspicious": g["suspicious"].any(),
        "wind_max_obs": g["wind_speed"].max(),
        "vis_min_m": g["visibility_m"].min(),
        "ww_codes": g["ww"].apply(lambda s: ",".join(str(int(x)) for x in sorted(set(s.dropna())) if int(x) in DUST_WW)),
    }).reset_index()
    ev["dust"] = (ev.by_code | ev.by_vis).astype(int)
    ev["criterion"] = np.select([ev.by_code & ev.by_vis, ev.by_code, ev.by_vis],
                                ["code+vis", "code", "vis"], default="")
    ev["reliable"] = ev.n_terms >= MIN_TERMS_PER_DAY

    # Полный календарь до конца последнего года (но не дальше сегодняшнего дня):
    # дни без наблюдений — явные пропуски, а не «нет пыли»
    full = []
    for st, sub in ev.groupby("station"):
        last = min(pd.Timestamp(sub.date.max().year, 12, 31), pd.Timestamp(date.today()))
        idx = pd.date_range(sub.date.min(), max(last, sub.date.max()), freq="D")
        f = sub.set_index("date").reindex(idx)
        f["station"] = st
        f["n_terms"] = f["n_terms"].fillna(0).astype(int)
        f["n_ww"] = f["n_ww"].fillna(0).astype(int)
        f["reliable"] = f["reliable"].astype("boolean").fillna(False).astype(bool)
        full.append(f.rename_axis("date").reset_index())
    ev = pd.concat(full, ignore_index=True)
    if save:
        http.atomic_to_parquet(ev, CLEAN / "dust_events.parquet")
    return ev


def completeness_report(ev: pd.DataFrame) -> pd.DataFrame:
    """Полнота рядов и число пылевых дней по станциям и годам (раздел 22.4).

    ww_report_rate — доля синоптических сроков с переданным кодом погоды ww;
    ww_reliable — год пригоден для кодового критерия (ww_report_rate ≥ WW_REPORT_MIN_RATE);
    last_obs — последний день с наблюдениями (неполный год виден явно)."""
    e = ev.copy()
    e["year"] = e.date.dt.year
    if "n_ww" not in e:
        e["n_ww"] = np.nan
    obs_date = e.date.where(e.n_terms > 0)
    rep = e.assign(obs_date=obs_date).groupby(["station", "year"]).agg(
        days=("date", "size"),
        reliable_days=("reliable", "sum"),
        dust_days=("dust", lambda s: int(np.nansum(s))),
        strong_days=("strong", lambda s: int(np.nansum(s.astype(float)))),
        n_terms=("n_terms", "sum"),
        n_ww=("n_ww", "sum"),
        last_obs=("obs_date", "max"),
    ).reset_index()
    rep["completeness"] = (rep.reliable_days / rep.days).round(3)
    rep["ww_report_rate"] = (rep.n_ww / rep.n_terms.where(rep.n_terms > 0)).round(3)
    rep["ww_reliable"] = rep.ww_report_rate >= WW_REPORT_MIN_RATE
    rep["last_obs"] = rep.last_obs.dt.date
    return rep


def ww_unreliable_years(ev: pd.DataFrame) -> dict[str, list[int]]:
    """Станционные годы, где код погоды почти не передавался: метка там неизвестна,
    а не «нет пыли» (раздел 10.3)."""
    rep = completeness_report(ev)
    bad = rep[~rep.ww_reliable & (rep.n_terms > 0)]
    return {st: sorted(int(y) for y in s.year) for st, s in bad.groupby("station")}


def run(years: list[int] = YEARS) -> pd.DataFrame:
    obs = build_observations(years)
    ev = build_events(obs)
    rep = completeness_report(ev)
    rep.to_csv(CLEAN / "completeness.csv", index=False)
    return ev
