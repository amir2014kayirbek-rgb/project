"""Хранилище AralShield (раздел 24.1).

Для локального запуска — SQLite; схема повторяет таблицы PostgreSQL/PostGIS из
отчёта, поэтому переход на PostgreSQL сводится к смене подключения.
Таблицы подписчиков отделены от аналитики: аналитические запросы не читают chat_id.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from aralshield.config import DB_PATH, LOCAL_UTC_OFFSET_H, SETTLEMENTS, STATIONS

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS settlements (
  id TEXT PRIMARY KEY, name_ru TEXT, name_kk TEXT, lat REAL, lon REAL, population INTEGER, station TEXT);
CREATE TABLE IF NOT EXISTS stations (
  id TEXT PRIMARY KEY, wmo TEXT, name_ru TEXT, name_kk TEXT, lat REAL, lon REAL);
CREATE TABLE IF NOT EXISTS dust_events (
  station TEXT, date TEXT, dust INTEGER, strong INTEGER, criterion TEXT, ww_codes TEXT,
  vis_min_m REAL, wind_max_obs REAL, reliable INTEGER, PRIMARY KEY (station, date));
CREATE TABLE IF NOT EXISTS forecasts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  station TEXT, issue_date TEXT, horizon INTEGER, day TEXT, probability REAL,
  level TEXT, levels_json TEXT, drivers_json TEXT, source_sector TEXT, source_km REAL,
  wind_max REAL, gust_max REAL, wind_dir_sector TEXT, dry_days REAL, cams_dust REAL,
  model_version TEXT, degraded INTEGER DEFAULT 0, mode TEXT DEFAULT 'live',
  inputs_json TEXT, created_at TEXT,
  UNIQUE (station, issue_date, horizon, mode));
CREATE INDEX IF NOT EXISTS ix_fc_day ON forecasts(station, day);
CREATE TABLE IF NOT EXISTS subscribers (
  id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT, chat_id TEXT, lang TEXT DEFAULT 'kk',
  role TEXT DEFAULT 'resident', consent_at TEXT, created_at TEXT, UNIQUE (channel, chat_id));
CREATE TABLE IF NOT EXISTS subscriptions (
  subscriber_id INTEGER, station TEXT, min_level TEXT DEFAULT 'orange', send_hour INTEGER DEFAULT 6,
  PRIMARY KEY (subscriber_id, station),
  FOREIGN KEY (subscriber_id) REFERENCES subscribers(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT, forecast_id INTEGER, subscriber_id INTEGER, channel TEXT,
  status TEXT, sent_at TEXT, UNIQUE (forecast_id, subscriber_id, channel));
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT, subscriber_hash TEXT, station TEXT, date TEXT, answer TEXT,
  created_at TEXT, UNIQUE (subscriber_hash, station, date));
CREATE TABLE IF NOT EXISTS feedback_requests (
  subscriber_id INTEGER, station TEXT, day TEXT, asked_at TEXT, PRIMARY KEY (subscriber_id, station, day),
  FOREIGN KEY (subscriber_id) REFERENCES subscribers(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS model_versions (
  module TEXT, version TEXT, metrics_json TEXT, status TEXT, created_at TEXT, PRIMARY KEY (module, version));
CREATE TABLE IF NOT EXISTS api_keys (
  key_hash TEXT PRIMARY KEY, owner TEXT, role TEXT, created_at TEXT, revoked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, action TEXT, details TEXT, at TEXT);
"""

# Колонки, добавленные после первой версии схемы (миграция существующей базы в init()).
MIGRATIONS = {
    # station/issue_date/level — для дедупликации рассылки по (пункт, выпуск, подписчик, канал)
    "notifications": {"station": "TEXT", "issue_date": "TEXT", "level": "TEXT"},
}

# Индексы создаются после миграции: часть из них опирается на новые колонки.
INDEXES = """
CREATE INDEX IF NOT EXISTS ix_fc_mode_issue ON forecasts(mode, issue_date);
CREATE INDEX IF NOT EXISTS ix_fc_mode_station_issue ON forecasts(mode, station, issue_date);
CREATE INDEX IF NOT EXISTS ix_notif_status ON notifications(channel, status, forecast_id);
CREATE INDEX IF NOT EXISTS ix_notif_dedup ON notifications(subscriber_id, channel, station, issue_date);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_today() -> date:
    """Сегодняшняя дата по времени Кызылорды (UTC+5), а не по часовому поясу сервера."""
    return (datetime.now(timezone.utc) + timedelta(hours=LOCAL_UTC_OFFSET_H)).date()


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    # journal_mode=WAL хранится в самом файле и задаётся один раз в init();
    # synchronous=NORMAL в режиме WAL безопасен и убирает fsync на каждую фиксацию
    con.execute("PRAGMA synchronous = NORMAL")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _ensure_columns(con: sqlite3.Connection) -> None:
    for table, cols in MIGRATIONS.items():
        have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        for col, typ in cols.items():
            if col not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def init() -> None:
    with connect() as con:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(SCHEMA)
        _ensure_columns(con)
        con.executescript(INDEXES)
        con.executemany("INSERT OR REPLACE INTO stations VALUES (?,?,?,?,?,?)",
                        [(s.id, s.wmo, s.name_ru, s.name_kk, s.lat, s.lon) for s in STATIONS])
        con.executemany("INSERT OR REPLACE INTO settlements VALUES (?,?,?,?,?,?,?)",
                        [(s.id, s.name_ru, s.name_kk, s.lat, s.lon, s.population, s.station) for s in SETTLEMENTS])


def rows(sql: str, params=()) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def execute(sql: str, params=()) -> int:
    with connect() as con:
        cur = con.execute(sql, params)
        return cur.lastrowid


# ---------------------------------------------------------------- события
def load_events(ev) -> int:
    """Загружает каталог «Правды» (DataFrame) в базу."""
    recs = []
    for r in ev.itertuples():
        if r.n_terms == 0:
            continue
        recs.append((r.station, str(r.date.date()), int(r.dust) if r.dust == r.dust else None,
                     int(bool(r.strong)) if r.strong == r.strong else 0, r.criterion or "",
                     r.ww_codes or "", None if r.vis_min_m != r.vis_min_m else float(r.vis_min_m),
                     None if r.wind_max_obs != r.wind_max_obs else float(r.wind_max_obs), int(bool(r.reliable))))
    with connect() as con:
        con.executemany("INSERT OR REPLACE INTO dust_events VALUES (?,?,?,?,?,?,?,?,?)", recs)
    return len(recs)


# ---------------------------------------------------------------- прогнозы
FC_KEY = ["station", "issue_date", "horizon", "mode"]
FC_COLS = ["station", "issue_date", "horizon", "day", "probability", "level", "levels_json", "drivers_json",
           "source_sector", "source_km", "wind_max", "gust_max", "wind_dir_sector", "dry_days", "cams_dust",
           "model_version", "degraded", "mode", "inputs_json", "created_at"]
_UPSERT = (f"INSERT INTO forecasts ({','.join(FC_COLS)}) VALUES ({','.join('?' * len(FC_COLS))}) "
           f"ON CONFLICT({','.join(FC_KEY)}) DO UPDATE SET "
           + ", ".join(f"{c}=excluded.{c}" for c in FC_COLS if c not in FC_KEY))


def save_forecasts(items: list[dict], replace_mode: str | None = None) -> None:
    """Сохраняет выпуск прогноза (контракт K4).

    Повторный выпуск того же (пункт, дата выпуска, горизонт, режим) обновляет строку
    на месте и сохраняет её id: иначе рассылка приняла бы его за новый прогноз
    и оповестила подписчиков повторно. При replace_mode в той же транзакции
    удаляются строки этого режима, которых нет в новом наборе (пересборка архива).
    """
    recs = []
    for it in items:
        rec = {c: it.get(c) for c in FC_COLS}
        rec["mode"] = rec["mode"] or "live"
        rec["degraded"] = int(bool(rec["degraded"]))
        for k in ("levels_json", "drivers_json", "inputs_json"):
            if rec[k] is not None and not isinstance(rec[k], str):
                rec[k] = json.dumps(rec[k], ensure_ascii=False)
        rec["created_at"] = rec["created_at"] or now()
        recs.append([rec[c] for c in FC_COLS])
    with connect() as con:
        con.executemany(_UPSERT, recs)
        if replace_mode:
            con.execute("CREATE TEMP TABLE IF NOT EXISTS _fc_keep (station TEXT, issue_date TEXT, horizon INTEGER)")
            con.execute("DELETE FROM _fc_keep")
            con.executemany("INSERT INTO _fc_keep VALUES (?,?,?)",
                            [(r[0], r[1], r[2]) for r, it in zip(recs, items) if (it.get("mode") or "live") == replace_mode])
            con.execute("""DELETE FROM forecasts WHERE mode=? AND NOT EXISTS (
                             SELECT 1 FROM _fc_keep k WHERE k.station=forecasts.station
                             AND k.issue_date=forecasts.issue_date AND k.horizon=forecasts.horizon)""", (replace_mode,))


def latest_forecasts(station: str | None = None, mode: str = "live") -> list[dict]:
    """Последний выпуск по каждому пункту отдельно (а не общий MAX по всем пунктам):
    если выпуск одной станции не удался, её прежний прогноз не пропадает молча —
    его свежесть проверяют вызывающие (issue_date против local_today())."""
    q = """SELECT f.* FROM forecasts f WHERE f.mode=? AND f.issue_date=(
             SELECT MAX(g.issue_date) FROM forecasts g WHERE g.mode=f.mode AND g.station=f.station)"""
    params: list = [mode]
    if station:
        q += " AND f.station=?"
        params.append(station)
    return rows(q + " ORDER BY f.station, f.horizon", params)


# ---------------------------------------------------------------- подписки
def upsert_subscriber(channel: str, chat_id: str, lang: str | None = None, role: str | None = None) -> int:
    """Создаёт подписчика или обновляет только переданные поля (None — оставить как было)."""
    with connect() as con:
        con.execute("""INSERT INTO subscribers (channel, chat_id, lang, role, consent_at, created_at)
                       VALUES (?,?,COALESCE(?, 'kk'),COALESCE(?, 'resident'),?,?) ON CONFLICT(channel, chat_id)
                       DO UPDATE SET lang=COALESCE(?, lang), role=COALESCE(?, role)""",
                    (channel, str(chat_id), lang, role, now(), now(), lang, role))
        return con.execute("SELECT id FROM subscribers WHERE channel=? AND chat_id=?",
                           (channel, str(chat_id))).fetchone()["id"]


def set_subscription(subscriber_id: int, station: str, min_level: str | None = None, send_hour: int | None = None,
                     exclusive: bool = False) -> None:
    """Подписка на один пункт: upsert по (подписчик, пункт); непереданные поля сохраняются.
    exclusive=True — бот, где у подписчика ровно один пункт: остальные удаляются."""
    with connect() as con:
        if exclusive:
            con.execute("DELETE FROM subscriptions WHERE subscriber_id=? AND station<>?", (subscriber_id, station))
        con.execute("""INSERT INTO subscriptions (subscriber_id, station, min_level, send_hour)
                       VALUES (?,?,COALESCE(?, 'orange'),COALESCE(?, 6))
                       ON CONFLICT(subscriber_id, station)
                       DO UPDATE SET min_level=COALESCE(?, min_level), send_hour=COALESCE(?, send_hour)""",
                    (subscriber_id, station, min_level, send_hour, min_level, send_hour))


def delete_subscription(subscriber_id: int, station: str) -> bool:
    with connect() as con:
        return con.execute("DELETE FROM subscriptions WHERE subscriber_id=? AND station=?",
                           (subscriber_id, station)).rowcount > 0


def delete_subscriber(channel: str, chat_id: str) -> bool:
    """Отписка и удаление всех данных подписчика (/stop): подписки, история рассылки,
    запросы и ответы обратной связи. True — если было что удалять."""
    hashes = (subscriber_hash(channel, chat_id), _legacy_hash(channel, chat_id))
    with connect() as con:
        r = con.execute("SELECT id FROM subscribers WHERE channel=? AND chat_id=?", (channel, str(chat_id))).fetchone()
        n = con.execute("DELETE FROM feedback WHERE subscriber_hash IN (?,?)", hashes).rowcount
        if r:
            con.execute("DELETE FROM notifications WHERE subscriber_id=?", (r["id"],))
            con.execute("DELETE FROM feedback_requests WHERE subscriber_id=?", (r["id"],))
            n += con.execute("DELETE FROM subscribers WHERE id=?", (r["id"],)).rowcount
        return n > 0


def get_subscriber(channel: str, chat_id: str) -> dict | None:
    r = rows("""SELECT s.*, sub.station, sub.min_level, sub.send_hour FROM subscribers s
                LEFT JOIN subscriptions sub ON sub.subscriber_id = s.id
                WHERE s.channel=? AND s.chat_id=? ORDER BY sub.station""", (channel, str(chat_id)))
    return r[0] if r else None


def get_subscriptions(channel: str, chat_id: str) -> list[dict]:
    return rows("""SELECT sub.* FROM subscriptions sub JOIN subscribers s ON s.id = sub.subscriber_id
                   WHERE s.channel=? AND s.chat_id=? ORDER BY sub.station""", (channel, str(chat_id)))


# ---------------------------------------------------------------- обезличивание и обратная связь
_PEPPER: bytes | None = None


def _pepper() -> bytes:
    """Секрет для HMAC (K9): ARALSHIELD_HASH_PEPPER или случайное значение этой установки,
    хранящееся рядом с базой (файл aralshield.sqlite.pepper не попадает в git)."""
    global _PEPPER
    env = os.environ.get("ARALSHIELD_HASH_PEPPER")
    if env:
        return env.encode()
    if _PEPPER is None:
        p = DB_PATH.with_name(DB_PATH.name + ".pepper")
        try:
            _PEPPER = p.read_bytes().strip()
        except OSError:
            _PEPPER = b""
        if not _PEPPER:
            p.parent.mkdir(parents=True, exist_ok=True)
            _PEPPER = secrets.token_hex(32).encode()
            p.write_bytes(_PEPPER)
            log.warning("ARALSHIELD_HASH_PEPPER не задан: создан локальный секрет %s (не публикуйте его)", p.name)
    return _PEPPER


def subscriber_hash(channel: str, chat_id: str) -> str:
    """Обезличенный идентификатор для таблицы обратной связи и журнала рассылки:
    HMAC-SHA256 с секретом, перебором chat_id не обращается."""
    return hmac.new(_pepper(), f"{channel}:{chat_id}".encode(), hashlib.sha256).hexdigest()[:32]


def _legacy_hash(channel: str, chat_id: str) -> str:
    """Хэш первой версии (без секрета) — только чтобы удалить старые записи при /stop."""
    return hashlib.sha256(f"{channel}:{chat_id}".encode()).hexdigest()[:16]


def save_feedback(channel: str, chat_id: str, station: str, day: str, answer: str) -> None:
    execute("""INSERT OR REPLACE INTO feedback (subscriber_hash, station, date, answer, created_at)
               VALUES (?,?,?,?,?)""", (subscriber_hash(channel, chat_id), station, day, answer, now()))


def was_notified(channel: str, chat_id: str, station: str, day: str) -> bool:
    """Был ли подписчик действительно оповещён о пыли в этом пункте на этот день."""
    return bool(rows("""SELECT 1 FROM notifications n JOIN subscribers s ON s.id = n.subscriber_id
                        JOIN forecasts f ON f.id = n.forecast_id
                        WHERE s.channel=? AND s.chat_id=? AND f.station=? AND f.day=? AND n.status='sent'
                        LIMIT 1""", (channel, str(chat_id), station, day)))


def feedback_window(today: date | None = None) -> tuple[date, date]:
    """Допустимые даты ответа «Было пыльно?»: не позже сегодня и не раньше недели назад."""
    today = today or local_today()
    return today - timedelta(days=7), today


# ---------------------------------------------------------------- ключи API
def _h(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_api_key(owner: str, role: str = "partner") -> str:
    key = "as_" + secrets.token_urlsafe(24)
    execute("INSERT INTO api_keys (key_hash, owner, role, created_at) VALUES (?,?,?,?)", (_h(key), owner, role, now()))
    audit("cli", "apikey_create", owner)
    return key


def check_api_key(key: str | None) -> dict | None:
    if not key:
        return None
    r = rows("SELECT owner, role FROM api_keys WHERE key_hash=? AND revoked=0", (_h(key),))
    return r[0] if r else None


def list_api_keys() -> list[dict]:
    """Ключи без секретов: id — первые 12 знаков хэша."""
    return rows("""SELECT substr(key_hash, 1, 12) AS id, owner, role, created_at, revoked FROM api_keys
                   ORDER BY created_at""")


def revoke_api_key(ident: str) -> int:
    """Отзывает ключи по владельцу или по id из list_api_keys() (не короче 8 знаков)."""
    prefix = ident.lower() if len(ident) >= 8 and all(c in "0123456789abcdef" for c in ident.lower()) else None
    with connect() as con:
        n = con.execute("UPDATE api_keys SET revoked=1 WHERE revoked=0 AND (owner=? OR "
                        "(? IS NOT NULL AND substr(key_hash, 1, length(?))=?))",
                        (ident, prefix, prefix, prefix)).rowcount
    if n:
        audit("cli", "apikey_revoke", ident)
    return n


def audit(actor: str, action: str, details: str = "") -> None:
    execute("INSERT INTO audit_log (actor, action, details, at) VALUES (?,?,?,?)", (actor, action, details, now()))
