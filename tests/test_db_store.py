"""Хранилище (C2/K4, C4, C11, C12, C33, C36): upsert без смены id, последний выпуск по пункту,
подписки по пунктам, удаление данных при /stop, HMAC, ключи API, индексы."""
from __future__ import annotations

import hashlib
from datetime import timedelta

from aralshield import db


def test_save_forecasts_upsert_preserves_id_and_replace_mode(cdb):
    cdb.issue(mode="backtest", issue_date=db.local_today() - timedelta(days=400))
    old = db.rows("SELECT id FROM forecasts WHERE mode='backtest' ORDER BY horizon")
    items = cdb.issue(mode="backtest", issue_date=db.local_today() - timedelta(days=400), probs=(0.2, 0.8, 0.5))
    assert db.rows("SELECT id FROM forecasts WHERE mode='backtest' ORDER BY horizon") == old
    # пересборка архива: строки, которых нет в новом наборе, удаляются в той же транзакции
    db.save_forecasts(items[:1], replace_mode="backtest")
    left = db.rows("SELECT id, probability FROM forecasts WHERE mode='backtest'")
    assert left == [{"id": old[0]["id"], "probability": 0.2}]


def test_latest_forecasts_is_per_station(cdb):
    today = db.local_today()
    cdb.issue(station="kazalinsk", issue_date=today - timedelta(days=5))
    cdb.issue(station="aralsk", issue_date=today)
    got = {(r["station"], r["issue_date"]) for r in db.latest_forecasts()}
    assert got == {("kazalinsk", (today - timedelta(days=5)).isoformat()), ("aralsk", today.isoformat())}
    assert len(db.latest_forecasts("kazalinsk")) == 3


def test_set_subscription_is_upsert_per_station(cdb):
    sid = db.upsert_subscriber("telegram", "a", "ru", "teacher")
    db.set_subscription(sid, "aralsk", "yellow", 7)
    db.set_subscription(sid, "kazalinsk")  # второй пункт не удаляет первый
    db.set_subscription(sid, "aralsk", send_hour=19)  # непереданные поля сохраняются
    subs = {r["station"]: (r["min_level"], r["send_hour"]) for r in db.get_subscriptions("telegram", "a")}
    assert subs == {"aralsk": ("yellow", 19), "kazalinsk": ("orange", 6)}
    db.upsert_subscriber("telegram", "a")  # без lang/role — прежние значения
    assert db.get_subscriber("telegram", "a")["role"] == "teacher"
    db.set_subscription(sid, "zhosaly", exclusive=True)  # бот: ровно один пункт
    assert [r["station"] for r in db.get_subscriptions("telegram", "a")] == ["zhosaly"]


def test_stop_deletes_feedback_and_notifications(cdb):
    sid = cdb.subscribe("42")
    fid = cdb.issue()[0] and db.rows("SELECT id FROM forecasts LIMIT 1")[0]["id"]
    db.execute("INSERT INTO notifications (forecast_id, subscriber_id, channel, status) VALUES (?,?,?,?)",
               (fid, sid, "telegram", "sent"))
    db.save_feedback("telegram", "42", "aralsk", db.local_today().isoformat(), "yes")
    legacy = hashlib.sha256(b"telegram:42").hexdigest()[:16]
    db.execute("INSERT INTO feedback (subscriber_hash, station, date, answer) VALUES (?,?,?,?)",
               (legacy, "aralsk", "2020-01-01", "no"))
    assert db.delete_subscriber("telegram", "42") is True
    assert db.rows("SELECT * FROM feedback") == [] and db.rows("SELECT * FROM notifications") == []
    assert db.delete_subscriber("telegram", "42") is False  # нечего удалять → бот ответит no_sub


def test_subscriber_hash_is_keyed(cdb):
    h = db.subscriber_hash("telegram", "5123456789")
    assert h != hashlib.sha256(b"telegram:5123456789").hexdigest()[: len(h)]
    assert len(h) == 32


def test_api_key_list_and_revoke(cdb):
    key = db.create_api_key("Лесхоз")
    assert db.check_api_key(key)["owner"] == "Лесхоз"
    ks = db.list_api_keys()
    assert len(ks) == 1 and key not in str(ks)
    assert db.revoke_api_key(ks[0]["id"]) == 1 and db.check_api_key(key) is None


def test_indexes_exist(cdb):
    names = {r["name"] for r in db.rows("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"ix_fc_mode_issue", "ix_notif_status"} <= names
