"""Бот (C4, C6, C7, C12, C13, C18, C31): устаревший прогноз, демо, обратная связь, мастер подписки.
Без токена и сети: проверяются функции представления и обработчики с поддельным callback."""
from __future__ import annotations

import asyncio
import types
from datetime import datetime, timedelta

from aiogram.types import Chat, Message

from aralshield import db
from aralshield.api import demo as demo_mod
from aralshield.voice import bot


def test_forecast_view_marks_stale_and_hides_past_days(cdb):
    cdb.issue(issue_date=db.local_today() - timedelta(days=2))
    out = bot.forecast_view({"lang": "ru", "role": "resident", "station": "aralsk"})
    text = out[0][0]
    assert "⚠️ Это прогноз от" in text
    assert text.count("\n🟢") + text.count("\n🟡") + text.count("\n🟠") == 1  # остался только сегодняшний день
    assert "kazhydromet.kz" in text and "Сегодня" in text


def test_forecast_view_teacher_gets_parents_text_with_share(cdb):
    cdb.issue()
    out = bot.forecast_view({"lang": "kk", "role": "teacher", "station": "aralsk"})
    assert len(out) == 3 and out[2][1] and "шаңды ауа райы күтіледі" in out[2][1]


def test_demo_view_uses_replay_event_offline(cdb, monkeypatch):
    ev = {"event_date": "2025-04-04", "issue_date": "2025-04-03", "station": "aralsk", "horizon": 1,
          "mode": "backtest", "replay_p": 0.7}
    monkeypatch.setattr(demo_mod, "demo_event", lambda metrics=None: ev)
    from datetime import date
    cdb.issue(issue_date=date(2025, 4, 3), mode="backtest", levels=("green", "red", "green"), probs=(0.1, 0.9, 0.1))
    out = bot.demo_view("ru", "resident")
    assert out[0].startswith("ДЕМО: архивное событие 4 апреля 2025")
    assert "завтра (пятница)" in out[0] and "SMS:" in out[1]
    monkeypatch.setattr(demo_mod, "demo_event", lambda metrics=None: None)
    assert bot.demo_view("kk") == [bot.UI["kk"]["demo_none"]]


def test_demo_event_rule_picks_dusty_station_with_highest_p():
    m = {"replay": {"event_date": "2025-04-04", "series": {
        "aralsk": {"h1": [{"date": "2025-04-04", "p": 0.7, "y": 1}]},
        "kazalinsk": {"h1": [{"date": "2025-04-04", "p": 0.9, "y": 0}]},
        "zhosaly": {"h1": [{"date": "2025-04-04", "p": 0.8, "y": 1}]}}}}
    ev = demo_mod.demo_event(m)
    assert ev["station"] == "zhosaly" and ev["issue_date"] == "2025-04-03" and ev["mode"] == "backtest"


def test_feedback_requires_notification_and_recent_date(cdb):
    sid = cdb.subscribe("55")
    cdb.issue()
    today = db.local_today()
    assert not bot.feedback_ok(55, "aralsk", today.isoformat())  # оповещения не было
    fid = db.rows("SELECT id FROM forecasts WHERE horizon=0")[0]["id"]
    db.execute("INSERT INTO notifications (forecast_id, subscriber_id, channel, status) VALUES (?,?,?,?)",
               (fid, sid, "telegram", "sent"))
    assert bot.feedback_ok(55, "aralsk", today.isoformat())
    assert not bot.feedback_ok(55, "aralsk", "not-a-date")
    assert not bot.feedback_ok(55, "aralsk", "2099-12-31")
    assert not bot.feedback_ok(999, "aralsk", today.isoformat())


def _cb(user_id: int, data: str, msg_id: int):
    msg = Message.model_construct(message_id=msg_id, date=datetime.now(), chat=Chat(id=user_id, type="private"))
    answers = []

    async def answer(text=None, **kw):
        answers.append(text)
    return types.SimpleNamespace(from_user=types.SimpleNamespace(id=user_id), data=data, message=msg,
                                 answer=answer), answers


def test_stale_wizard_button_does_not_commit_default_role(cdb):
    bot._draft.clear()
    bot._new_draft(77, msg_id=10)
    bot._draft[77].update(lang="ru", station="kazalinsk")  # роль ещё не выбрана
    cb, answers = _cb(77, "lvl:red", 10)
    asyncio.run(bot.on_level(cb))
    assert answers == [bot.UI["ru"]["expired"]] and db.get_subscriber("telegram", "77") is None
    cb, answers = _cb(77, "role:teacher", 9)  # кнопка из старого сообщения мастера
    asyncio.run(bot.on_role(cb))
    assert answers == [bot.UI["kk"]["expired"]] and "role" not in bot._draft[77]
