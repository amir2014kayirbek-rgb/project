"""Рассылка (C2, C3, C4, C14, C15, C21, C23, C29): идемпотентность, репетиция, свежесть, производительность."""
from __future__ import annotations

import logging
import time
from datetime import timedelta

import pytest
import requests

from aralshield import db
from aralshield.voice import notify


@pytest.fixture
def tg(monkeypatch):
    """Настоящий путь отправки с подменённым HTTP: список отправленных (chat_id, text, buttons)."""
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:FAKE-TOKEN")
    state = {"ok": True}

    def fake(token, chat_id, text, buttons=None):
        if state["ok"]:
            sent.append((chat_id, text, buttons))
        return state["ok"]

    monkeypatch.setattr(notify, "_tg_send", fake)
    return sent, state


def _status():
    return [r["status"] for r in db.rows("SELECT status FROM notifications ORDER BY id")]


def test_reissue_keeps_id_and_does_not_resend(cdb, tg):
    sent, _ = tg
    cdb.subscribe("1")
    cdb.issue()
    ids1 = [r["id"] for r in db.rows("SELECT id FROM forecasts ORDER BY horizon")]
    assert notify.run()["sent"] == 1
    cdb.issue(probs=(0.12, 0.73, 0.41))  # повторный выпуск того же дня (ручной перезапуск daily)
    ids2 = [r["id"] for r in db.rows("SELECT id FROM forecasts ORDER BY horizon")]
    assert ids1 == ids2
    assert notify.run()["sent"] == 0 and len(sent) == 1
    cdb.issue(levels=("green", "red", "yellow"), probs=(0.1, 0.9, 0.4))  # уровень вырос — сообщаем снова
    assert notify.run()["sent"] == 1 and len(sent) == 2


def test_dry_run_does_not_block_real_send(cdb, tg):
    sent, _ = tg
    cdb.subscribe("1")
    cdb.subscribe("+77001234567", channel="sms")
    cdb.issue()
    s = notify.run(dry_run=True)
    assert s["sent"] == 0 and s["dry_run_written"] == 2 and set(_status()) == {"dry_run"}
    sms_out = (notify.OUTBOX / f"sms_{db.local_today()}.txt").read_text(encoding="utf-8")
    assert "+7700***4567" in sms_out and "1234567" not in sms_out  # телефон замаскирован
    s = notify.run()
    assert s["sent"] == 1 and len(sent) == 1  # Telegram ушёл по-настоящему; SMS без шлюза — снова в outbox


def test_failed_send_is_retried(cdb, tg):
    _, state = tg
    cdb.subscribe("1")
    cdb.issue()
    state["ok"] = False
    assert notify.run()["failed"] == 1
    state["ok"] = True
    assert notify.run()["sent"] == 1 and _status() == ["sent"]


def test_sms_below_orange_is_skipped_not_failed(cdb, tg):
    cdb.subscribe("+77001112233", channel="sms", min_level="yellow")
    cdb.issue(levels=("green", "yellow", "yellow"), probs=(0.1, 0.3, 0.3))
    s = notify.run()
    assert s["failed"] == 0 and s["skipped"] == 1


def test_stale_issue_is_not_sent(cdb, tg):
    sent, _ = tg
    cdb.subscribe("1")
    cdb.issue(issue_date=db.local_today() - timedelta(days=5), levels=("red", "red", "red"), probs=(0.9, 0.9, 0.9))
    s = notify.run()
    assert s["sent"] == 0 and s["stale_stations"] == ["aralsk"] and sent == []


def test_unknown_channel_is_skipped(cdb, tg):
    sent, _ = tg
    cdb.subscribe("director@school.kz", channel="email")
    cdb.issue()
    s = notify.run()
    assert s["unknown_channel"] == 1 and sent == []


def test_teacher_gets_separate_parents_message_with_share_button(cdb, tg):
    sent, _ = tg
    cdb.subscribe("7", role="teacher")
    cdb.issue()
    notify.run()
    assert len(sent) == 2
    assert "родител" in sent[1][1] and sent[1][2][0][0]["url"].startswith("https://t.me/share/url")


def test_send_hour_filter_and_evening_skips_today(cdb, tg):
    sent, _ = tg
    cdb.subscribe("morning", send_hour=6)
    cdb.subscribe("evening", send_hour=19)
    cdb.issue(levels=("red", "green", "green"), probs=(0.9, 0.1, 0.1))  # опасен только сегодняшний день
    s = notify.run(hour=19)
    assert s["subscribers"] == 1 and s["sent"] == 0
    assert notify.run(hour=6)["sent"] == 1 and sent[0][0] == "morning"


def test_feedback_asked_once(cdb, tg):
    cdb.subscribe("1")
    cdb.issue()
    notify.run()  # оповещение о завтрашнем дне
    after = db.local_today() + timedelta(days=2)  # «вчера» = день, о котором предупреждали
    asked = [notify.run(today=after)["feedback_asked"] for _ in range(3)]
    assert asked == [1, 0, 0]


def test_token_is_not_logged(monkeypatch, caplog):
    def boom(*a, **k):
        raise requests.ConnectionError("Max retries exceeded with url: /bot123456:SECRET-TOKEN/sendMessage")
    monkeypatch.setattr(notify.requests, "post", boom)
    with caplog.at_level(logging.WARNING):
        assert notify._tg_send("123456:SECRET-TOKEN", "1", "x") is False
    assert "SECRET" not in caplog.text
    rec = logging.LogRecord("x", logging.WARNING, "", 0, "url /bot123456:SECRET-TOKEN/send", None, None)
    notify.TokenMaskFilter().filter(rec)
    assert "SECRET" not in rec.getMessage() and "bot***" in rec.getMessage()


def test_notify_5000_subscribers_dry_run_is_fast(cdb):
    stations = ("aralsk", "kazalinsk", "zhosaly", "kyzylorda")
    for st in stations:
        cdb.issue(station=st)
    with db.connect() as con:
        con.executemany("INSERT INTO subscribers (channel, chat_id, lang, role) VALUES ('telegram', ?, 'ru', 'resident')",
                        [(str(10_000 + i),) for i in range(5000)])
        ids = [r["id"] for r in con.execute("SELECT id FROM subscribers")]
        con.executemany("INSERT INTO subscriptions VALUES (?,?,?,?)",
                        [(i, stations[i % 4], "orange", 6) for i in ids])
    t0 = time.perf_counter()
    s = notify.run(dry_run=True)
    dt = time.perf_counter() - t0
    assert s["dry_run_written"] == 5000 and dt < 15, dt
