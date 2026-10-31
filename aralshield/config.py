"""Общие настройки AralShield: пути, станции, населённые пункты, пороги."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("ARALSHIELD_DATA", ROOT / "data"))
RAW = DATA / "raw"          # сырая зона: ровно как пришло от источника
CLEAN = DATA / "clean"      # очищенная зона
FEATURES = DATA / "features"  # зона признаков
MODELS = ROOT / "models"
REPORTS = ROOT / "reports"
DB_PATH = DATA / "aralshield.sqlite"

for _p in (RAW, CLEAN, FEATURES, MODELS, REPORTS):
    _p.mkdir(parents=True, exist_ok=True)

TZ = "Asia/Qyzylorda"  # UTC+5
LOCAL_UTC_OFFSET_H = 5

# Годы исторического набора и честное разбиение по времени (раздел 13.3).
YEARS = list(range(2000, 2026))
TRAIN_YEARS = list(range(2000, 2016))
VALID_YEARS = list(range(2016, 2020))
TEST_YEARS = list(range(2020, 2026))
GAP_DAYS = 7  # зазор между частями выборки

KAZHYDROMET_URL = "https://www.kazhydromet.kz"

# Погодная модель Open-Meteo для боевого прогноза и для проверки «реанализ → прогноз»
# (одна и та же, чтобы проверка была сопоставимой). В архиве historical-forecast модель
# ecmwf_ifs совпадает с ERA5 побайтно, поэтому берётся ecmwf_ifs025 (ветер с 2024-02,
# порывы с 2025-02) и, как вторая проверка, gfs_seamless (с 2022, без влажности почвы).
OPER_FORECAST_MODEL = os.environ.get("ARALSHIELD_OPER_MODEL", "ecmwf_ifs025")
HIST_FORECAST_MODELS = (OPER_FORECAST_MODEL, "gfs_seamless")

# Аварийный режим (раздел 21.2): постоянная вероятность, если нет ни модели, ни правила B2,
# ни климатологии, и предельное время ежедневного выпуска, после которого оставшиеся
# станции выпускаются в упрощённом режиме с причиной deadline.
FALLBACK_CONST_P = 0.1
DAILY_DEADLINE_MIN = 15


@dataclass(frozen=True)
class Station:
    id: str           # короткий идентификатор пункта
    wmo: str          # индекс ВМО
    isd: str          # файл NOAA ISD (USAF + WBAN)
    name_ru: str
    name_kk: str
    lat: float
    lon: float
    district_ru: str
    district_kk: str
    population: int   # оценка населения пункта, требует сверки со статистикой


# Метеостанции с прогнозом первой фазы (раздел 13.1).
STATIONS: list[Station] = [
    Station("aralsk", "35746", "35746099999", "Аральск", "Арал", 46.783, 61.667,
            "Аральский район", "Арал ауданы", 30000),
    Station("kazalinsk", "35849", "35849099999", "Казалинск", "Қазалы", 45.767, 62.117,
            "Казалинский район", "Қазалы ауданы", 7000),
    Station("zhosaly", "35953", "35953099999", "Жосалы", "Жосалы", 45.500, 64.083,
            "Кармакшинский район", "Қармақшы ауданы", 18000),
    Station("kyzylorda", "38062", "38062099999", "Кызылорда", "Қызылорда", 44.706, 65.591,
            "г. Кызылорда", "Қызылорда қ.", 330000),
]
STATION_BY_ID = {s.id: s for s in STATIONS}


@dataclass(frozen=True)
class Settlement:
    id: str
    name_ru: str
    name_kk: str
    lat: float
    lon: float
    population: int
    station: str | None  # ближайшая станция с прогнозом


# Пункты, которые защищает «Щит» (раздел 14.2). Население — округлённые оценки
# для демонстрации; перед использованием заменить данными статистики/WorldPop.
SETTLEMENTS: list[Settlement] = [
    Settlement("aralsk", "Аральск", "Арал", 46.797, 61.667, 30000, "aralsk"),
    Settlement("saksaulsky", "Саксаульский", "Сексеуіл", 47.083, 61.167, 13000, "aralsk"),
    Settlement("kamystybas", "Камыстыбас", "Қамыстыбас", 46.200, 61.950, 4000, "aralsk"),
    Settlement("zhalanash", "Жаланаш", "Жалаңаш", 46.150, 61.250, 2000, "aralsk"),
    Settlement("akespe", "Акеспе", "Ақеспе", 46.800, 60.530, 1000, "aralsk"),
    Settlement("karateren", "Каратерен", "Қаратерең", 46.050, 61.050, 2500, "aralsk"),
    Settlement("kazalinsk", "Казалинск", "Қазалы", 45.763, 62.107, 7000, "kazalinsk"),
    Settlement("aiteke_bi", "Айтеке би", "Әйтеке би", 45.850, 62.150, 30000, "kazalinsk"),
    Settlement("bekarys", "Бекарыстан би", "Бекарыстан би", 45.600, 61.950, 2000, "kazalinsk"),
    Settlement("zhosaly", "Жосалы", "Жосалы", 45.488, 64.078, 18000, "zhosaly"),
    Settlement("baikonur", "Байконур", "Байқоңыр", 45.617, 63.317, 70000, "zhosaly"),
    Settlement("kyzylorda", "Кызылорда", "Қызылорда", 44.848, 65.482, 330000, "kyzylorda"),
]

# Область интереса модуля «Глаз»: казахстанская часть высохшего дна и побережье.
AOI_BBOX = (58.0, 44.3, 62.3, 47.1)  # lon_min, lat_min, lon_max, lat_max
EYE_CRS = "EPSG:32641"               # UTM 41N
EYE_RES_M = 250                       # разрешение карты классов, м

# Тег композита «Глаза» по умолчанию для eye/shield/API (контракт K1); переопределяется ARALSHIELD_EYE_TAG.
EYE_TAG = os.environ.get("ARALSHIELD_EYE_TAG", "2025v2")

EYE_CLASSES = {
    1: ("Потенциальный источник", "Шаң көзі болуы мүмкін", "#d9534f"),
    2: ("Песок", "Құм", "#e8c37a"),
    3: ("Влажный солончак", "Ылғалды сортаң", "#7aa6c2"),
    4: ("Растительность", "Өсімдік", "#4f9a4a"),
    5: ("Вода", "Су", "#1f5fa8"),
}

N_SECTORS = 16
SECTOR_NAMES = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
SOURCE_RADIUS_KM = 300
SOURCE_DECAY_KM = 100

# Уровни риска (раздел 15.2): нижние границы вероятности по группам пользователей.
# Подбираются на валидации в forecast.train и перезаписываются в models/thresholds.json.
DEFAULT_LEVELS = {
    "health":  {"yellow": 0.15, "orange": 0.30, "red": 0.55},
    "school":  {"yellow": 0.25, "orange": 0.45, "red": 0.65},
    "akimat":  {"yellow": 0.35, "orange": 0.55, "red": 0.75},
    "public":  {"yellow": 0.25, "orange": 0.45, "red": 0.65},
}
ROLE_GROUP = {"parent": "health", "asthma": "health", "doctor": "health",
              "teacher": "school", "akimat": "akimat", "dchs": "akimat",
              "farmer": "public", "resident": "public", "forestry": "public"}
