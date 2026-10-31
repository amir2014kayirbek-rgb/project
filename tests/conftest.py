"""Тесты работают с временным каталогом данных, а не с рабочей базой проекта.

Переменные окружения задаются до импорта пакета (config читает их при импорте);
.env проекта не читается (ARALSHIELD_SKIP_DOTENV), токены бота и шлюза SMS убираются,
временный каталог удаляется после сеанса.
"""
import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix="aralshield_test_")
os.environ["ARALSHIELD_DATA"] = _TMP
os.environ["ARALSHIELD_INTERNAL_KEY"] = "test-internal-key"
os.environ["ARALSHIELD_SKIP_DOTENV"] = "1"
os.environ["ARALSHIELD_HASH_PEPPER"] = "test-pepper"
for _k in ("TELEGRAM_BOT_TOKEN", "SMS_GATEWAY_URL", "SMS_GATEWAY_KEY", "ARALSHIELD_ALLOWED_HOSTS"):
    os.environ.pop(_k, None)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)


# ---------------------------------------------------------------- помощники тестов фиксера C
import types  # noqa: E402
from datetime import date, timedelta  # noqa: E402

import pytest  # noqa: E402


def _reset_db() -> None:
    from aralshield import db
    db.init()
    with db.connect() as con:
        for t in ("notifications", "feedback_requests", "feedback", "subscriptions", "subscribers", "forecasts",
                  "dust_events", "api_keys", "audit_log"):
            con.execute(f"DELETE FROM {t}")


def _issue(station="aralsk", issue_date=None, levels=("green", "orange", "yellow"), probs=(0.1, 0.71, 0.4),
           mode="live", **extra):
    from aralshield import db
    issue_date = issue_date or db.local_today()
    items = [{"station": station, "issue_date": issue_date.isoformat(), "horizon": h,
              "day": (issue_date + timedelta(days=h)).isoformat(), "probability": p, "level": lv,
              "levels_json": {"public": lv, "school": lv, "health": lv, "akimat": lv},
              "drivers_json": [], "wind_max": 15, "wind_dir_sector": "NE", "dry_days": 7,
              "model_version": "test", "mode": mode, **extra}
             for h, (lv, p) in enumerate(zip(levels, probs))]
    db.save_forecasts(items)
    return items


def _subscribe(chat_id, station="aralsk", channel="telegram", role="resident", min_level="orange", lang="ru",
               send_hour=6):
    from aralshield import db
    sid = db.upsert_subscriber(channel, chat_id, lang, role)
    db.set_subscription(sid, station, min_level, send_hour)
    return sid


@pytest.fixture
def cdb():
    """Чистая временная база и помощники: cdb.issue(...), cdb.subscribe(...)."""
    _reset_db()
    return types.SimpleNamespace(issue=_issue, subscribe=_subscribe, reset=_reset_db)
