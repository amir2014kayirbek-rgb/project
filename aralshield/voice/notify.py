"""Рассылка (задача J8): Telegram, SMS-шлюз, вопросы обратной связи.

Telegram — через Bot API (токен в TELEGRAM_BOT_TOKEN). SMS — адаптер к HTTP API
провайдера (SMS_GATEWAY_URL). Без токена/шлюза или с --dry-run сообщения складываются
в data/outbox и записываются со статусом dry_run: такая «репетиция» не мешает
настоящей рассылке. Дедупликация — по (подписчик, канал, пункт, дата выпуска):
повторный выпуск того же дня не рассылается, если уровень не вырос; неудачные
отправки (failed) повторяются при следующем запуске. Устаревший выпуск
(issue_date ≠ сегодня по Кызылорде) не рассылается вовсе.
Вся работа с базой — одно подключение и одна транзакция на запуск.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, timedelta

import requests

from aralshield import db
from aralshield.config import DATA, ROLE_GROUP
from aralshield.voice import templates

log = logging.getLogger(__name__)
RANK = {"green": 0, "yellow": 1, "orange": 2, "red": 3}
OUTBOX = DATA / "outbox"
CHANNELS = ("telegram", "sms")
SMS_MIN_LEVEL = "orange"  # SMS-шаблоны есть только для оранжевого и красного уровней

_TOKEN_RE = re.compile(r"bot\d+:[\w-]+")


class TokenMaskFilter(logging.Filter):
    """Маскирует токен бота (bot<id>:<secret>) в любых записях журнала."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if _TOKEN_RE.search(msg):
            record.msg, record.args = _TOKEN_RE.sub("bot***", msg), None
        return True


def mask_phone(phone: str) -> str:
    """+77001234567 → +7700***4567 (в outbox телефоны не хранятся открыто)."""
    p = str(phone)
    return p[:5] + "***" + p[-4:] if len(p) > 9 else "***"


def _level_for_role(fc: dict, role: str) -> str:
    levels = json.loads(fc["levels_json"] or "{}")
    return levels.get(ROLE_GROUP.get(role, "public"), fc["level"])


def _tg_send(token: str, chat_id: str, text: str, buttons: list[list[dict]] | None = None) -> bool:
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data=payload, timeout=20)
    except requests.RequestException as e:
        # только тип ошибки: текст исключения содержит URL с токеном (T06-3)
        log.warning("Telegram: ошибка сети (%s)", type(e).__name__)
        return False
    if not r.ok:
        log.warning("Telegram: ответ %s", r.status_code)
    return r.ok


def _sms_send(phone: str, text: str) -> bool:
    url, key = os.environ.get("SMS_GATEWAY_URL"), os.environ.get("SMS_GATEWAY_KEY")
    try:
        r = requests.post(url, json={"to": phone, "text": text}, headers={"Authorization": f"Bearer {key}"}, timeout=20)
    except requests.RequestException as e:
        log.warning("SMS: ошибка сети (%s)", type(e).__name__)
        return False
    if not r.ok:
        log.warning("SMS: ответ %s", r.status_code)
    return r.ok


class _Outbox:
    """Файлы «сухой рассылки»: один дескриптор на файл за весь запуск."""

    def __init__(self):
        self._fh: dict[str, object] = {}

    def write(self, name: str, text: str) -> None:
        if name not in self._fh:
            OUTBOX.mkdir(parents=True, exist_ok=True)
            self._fh[name] = open(OUTBOX / name, "a", encoding="utf-8")
        self._fh[name].write(text)

    def close(self) -> None:
        for fh in self._fh.values():
            fh.close()


_RANK_SQL = "CASE {c} WHEN 'red' THEN 3 WHEN 'orange' THEN 2 WHEN 'yellow' THEN 1 ELSE 0 END"


def run(dry_run: bool = False, hour: int | None = None, today: date | None = None) -> dict:
    """Рассылка по последнему выпуску. hour — только подписки с этим send_hour
    (вечерний запуск, hour ≥ 12, сообщает о завтрашних и последующих днях)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_sim = dry_run or not token
    sms_sim = dry_run or not os.environ.get("SMS_GATEWAY_URL")
    today = today or db.local_today()
    stats = {"subscribers": 0, "sent": 0, "skipped": 0, "failed": 0, "dry_run_written": 0, "feedback_asked": 0,
             "dry_run": tg_sim, "sms_gateway": not sms_sim, "stale_stations": [], "unknown_channel": 0}
    out = _Outbox()
    records = []
    with db.connect() as con:
        q = """SELECT s.id, s.channel, s.chat_id, s.lang, s.role, sub.station, sub.min_level, sub.send_hour
               FROM subscribers s JOIN subscriptions sub ON sub.subscriber_id = s.id"""
        subs = [dict(r) for r in con.execute(q + (" WHERE sub.send_hour=?" if hour is not None else ""),
                                             (hour,) if hour is not None else ()).fetchall()]
        stats["subscribers"] = len(subs)
        latest: dict[str, list[dict]] = {}
        for r in con.execute("""SELECT f.* FROM forecasts f WHERE f.mode='live' AND f.issue_date=(
                                  SELECT MAX(g.issue_date) FROM forecasts g WHERE g.mode='live' AND g.station=f.station)
                                ORDER BY f.station, f.horizon"""):
            latest.setdefault(r["station"], []).append(dict(r))
        first_day = today + timedelta(days=1) if hour is not None and hour >= 12 else today
        current: dict[str, list[dict]] = {}
        for st, fcs in latest.items():
            if fcs[0]["issue_date"] != today.isoformat():
                log.warning("рассылка: прогноз %s устарел (выпуск %s, сегодня %s) — не отправляется",
                            st, fcs[0]["issue_date"], today)
                stats["stale_stations"].append(st)
                continue
            current[st] = [f for f in fcs if date.fromisoformat(f["day"]) >= first_day]
        # уже доставленное: максимальный уровень по (подписчик, канал, пункт, выпуск), только status='sent'
        sent_level = {}
        for r in con.execute(f"""SELECT n.subscriber_id, n.channel, COALESCE(n.station, f.station) AS st,
                                   MAX({_RANK_SQL.format(c="COALESCE(n.level, f.level)")}) AS rk
                                 FROM notifications n LEFT JOIN forecasts f ON f.id = n.forecast_id
                                 WHERE n.status='sent' AND COALESCE(n.issue_date, f.issue_date)=?
                                 GROUP BY n.subscriber_id, n.channel, st""", (today.isoformat(),)):
            sent_level[(r["subscriber_id"], r["channel"], r["st"])] = r["rk"]
        warned = set()
        for s in subs:
            if s["channel"] not in CHANNELS:
                stats["unknown_channel"] += 1
                if s["channel"] not in warned:
                    log.warning("рассылка: канал %r не поддерживается — подписчики пропущены", s["channel"])
                    warned.add(s["channel"])
                continue
            fcs = current.get(s["station"], [])
            min_rank = RANK[s["min_level"]]
            if s["channel"] == "sms":
                min_rank = max(min_rank, RANK[SMS_MIN_LEVEL])
            # самый опасный горизонт, прошедший порог подписчика (при равенстве — ближайший)
            hits = [(RANK[_level_for_role(f, s["role"])], f) for f in fcs]
            hits = [(rk, f) for rk, f in hits if rk >= min_rank]
            if not hits:
                stats["skipped"] += 1
                continue
            rk, fc = max(hits, key=lambda x: (x[0], -x[1]["horizon"]))
            prev = sent_level.get((s["id"], s["channel"], s["station"]))
            if prev is not None and rk <= prev:
                stats["skipped"] += 1
                continue
            fc = dict(fc, level=_level_for_role(fc, s["role"]))
            if s["channel"] == "sms":
                text = templates.sms_message(fc, s["lang"], today=today, role=s["role"])
                if sms_sim:
                    out.write(f"sms_{today}.txt", f"{mask_phone(s['chat_id'])}\t{text}\n")
                    status = "dry_run"
                else:
                    status = "sent" if _sms_send(s["chat_id"], text) else "failed"
            else:
                text = templates.forecast_message(fc, s["role"], s["lang"], today=today)
                parents = None
                if s["role"] == "teacher" and fc["level"] in ("orange", "red"):
                    p = templates.parents_message(fc, s["lang"], today=today)
                    parents = (templates.T[s["lang"]]["parents_head"] + "\n" + p,
                               [[{"text": templates.T[s["lang"]]["share"], "url": templates.share_url(p)}]])
                if tg_sim:
                    tag = db.subscriber_hash("telegram", s["chat_id"])[:12]
                    out.write(f"telegram_{today}.txt", f"--- {tag}\n{text}\n" + (f"{parents[0]}\n" if parents else ""))
                    status = "dry_run"
                else:
                    ok = _tg_send(token, s["chat_id"], text)
                    if ok and parents:
                        _tg_send(token, s["chat_id"], parents[0], parents[1])
                    status = "sent" if ok else "failed"
            records.append((fc["id"], s["id"], s["channel"], status, db.now(), s["station"], fc["issue_date"],
                            fc["level"]))
            stats[{"sent": "sent", "failed": "failed", "dry_run": "dry_run_written"}[status]] += 1
        # доставленное не перезаписывается репетицией или ошибкой
        con.executemany("""INSERT INTO notifications (forecast_id, subscriber_id, channel, status, sent_at,
                                                       station, issue_date, level) VALUES (?,?,?,?,?,?,?,?)
                           ON CONFLICT(forecast_id, subscriber_id, channel) DO UPDATE SET
                             status=excluded.status, sent_at=excluded.sent_at, station=excluded.station,
                             issue_date=excluded.issue_date, level=excluded.level
                           WHERE notifications.status <> 'sent' OR excluded.status = 'sent'""", records)
        stats["feedback_asked"] = ask_feedback(token, dry_run, today=today, con=con, outbox=out)
    out.close()
    return stats


def ask_feedback(token: str | None, dry_run: bool, today: date | None = None, con=None, outbox=None) -> int:
    """На следующий день после оповещения спрашиваем «Было пыльно?» (FR-08) — один раз."""
    if con is None:
        with db.connect() as c:
            return ask_feedback(token, dry_run, today, c, outbox)
    today = today or db.local_today()
    yesterday = (today - timedelta(days=1)).isoformat()
    rows = con.execute("""SELECT DISTINCT s.id, s.chat_id, s.lang, f.station, f.day FROM notifications n
                          JOIN forecasts f ON f.id = n.forecast_id JOIN subscribers s ON s.id = n.subscriber_id
                          WHERE n.channel='telegram' AND n.status='sent' AND f.day=?
                          AND NOT EXISTS (SELECT 1 FROM feedback_requests q WHERE q.subscriber_id=s.id
                                          AND q.station=f.station AND q.day=f.day)""", (yesterday,)).fetchall()
    n = 0
    for r in rows:
        t = templates.T[r["lang"]]
        buttons = [[{"text": t["fb_yes"], "callback_data": f"fb:{r['station']}:{r['day']}:yes"},
                    {"text": t["fb_no"], "callback_data": f"fb:{r['station']}:{r['day']}:no"},
                    {"text": t["fb_unknown"], "callback_data": f"fb:{r['station']}:{r['day']}:unknown"}]]
        if token and not dry_run:
            if not _tg_send(token, r["chat_id"], templates.feedback_question(r["station"], r["lang"]), buttons):
                continue  # повторим при следующем запуске
            con.execute("INSERT OR IGNORE INTO feedback_requests VALUES (?,?,?,?)",
                        (r["id"], r["station"], r["day"], db.now()))
        n += 1
    return n
