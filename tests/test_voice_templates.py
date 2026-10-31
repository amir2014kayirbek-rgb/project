"""Тексты (C4, C5, C7, C8, C22, C24, C25, C28, C30): длина, свежесть, источник, падежи, ссылка."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from aralshield.voice import templates as T
from aralshield.voice.lang import kk_abl, kk_poss, plural_ru

TODAY = date(2027, 4, 12)  # понедельник
FULL = {"station": "kyzylorda", "issue_date": TODAY.isoformat(), "probability": 0.71, "wind_max": 25,
        "wind_dir_sector": "NNE", "dry_days": 21, "source_sector": "NNE", "source_km": 120, "degraded": 1}


@pytest.mark.parametrize("lang", ["ru", "kk"])
def test_all_messages_fit_limits(lang):
    for role in T.ROLE_ACTIONS:
        for level in T.LEVELS:
            for public in ("green", level):
                for off in (0, 1, 2, 5, 200):  # относительные дни и абсолютная дата
                    fc = dict(FULL, level=level, day=(TODAY + timedelta(days=off)).isoformat(),
                              levels_json={"public": public})
                    m = T.forecast_message(fc, role, lang, today=TODAY)
                    assert len(m) <= 400, (role, level, off, len(m))
                    assert T.SHORT_URL in m  # FR-06: ссылка в каждом сообщении, включая зелёное
                    for deg in (0, 1):
                        s = T.sms_message(dict(fc, degraded=deg), lang, today=TODAY)
                        assert s is None or len(s) <= 70, s


def test_day_words_are_relative_to_today_not_issue():
    fc = dict(FULL, level="orange", issue_date="2027-04-10", day="2027-04-12", degraded=0)
    m = T.forecast_message(fc, "resident", "ru", today=TODAY)
    assert m.startswith("🟠 Кызылорда, сегодня (понедельник)")
    later = T.forecast_message(fc, "resident", "ru", today=date(2027, 4, 20))
    assert "12 апреля (понедельник)" in later and "сегодня" not in later
    assert "12 сәуір (дүйсенбі)" in T.forecast_message(fc, "resident", "kk", today=date(2027, 4, 20))


def test_backtest_rows_are_relative_to_issue_date():
    fc = dict(FULL, level="red", mode="backtest", issue_date="2025-04-03", day="2025-04-04", degraded=0)
    assert "завтра (пятница)" in T.forecast_message(fc, "resident", "ru")


def test_summary_without_source_and_all_green():
    fcs = [dict(FULL, level="orange", day=TODAY.isoformat(), horizon=0, source_sector=None)]
    s = T.summary_message(fcs, "ru", today=TODAY)
    assert "источник" not in s and "Наибольший риск — Кызылорда, сегодня (понедельник), 71%." in s
    assert T.SHORT_URL in s
    green = [dict(f, level="green", probability=0.05) for f in fcs]
    assert "Пылевых дней не ожидается." in T.summary_message(green, "ru", today=TODAY)
    assert "Шаңды күн күтілмейді." in T.summary_message(green, "kk", today=TODAY)
    past = T.summary_message(fcs, "ru", today=TODAY + timedelta(days=5))
    assert "Актуальный прогноз ещё не выпущен." in past  # прошедшие дни не выдаются за сегодня


def test_summary_accuracy_line_names_period():
    fcs = [dict(FULL, level="orange", day=TODAY.isoformat(), horizon=0)]
    s = T.summary_message(fcs, "ru", {"hit": 7, "n": 10, "mode": "live", "days": 30}, today=TODAY)
    assert "за 30 дней: 7 из 10" in s
    k = T.summary_message(fcs, "kk", {"hit": 7, "n": 10, "mode": "live", "days": 30}, today=TODAY)
    assert "10-нан 7" in k
    a = T.summary_message(fcs, "ru", {"hit": 5, "n": 7, "mode": "backtest", "from": "2025-07-26", "to": "2025-08-24"},
                          today=TODAY)
    assert "архив тестовых лет" in a and "26 июля 2025" in a


def test_health_group_wording_below_public_orange():
    fc = dict(FULL, level="orange", probability=0.12, day=TODAY.isoformat(), levels_json={"public": "green"}, degraded=0)
    assert "возможен, повышенный риск для вас — 12%" in T.forecast_message(fc, "asthma", "ru", today=TODAY)
    assert "вероятен" not in T.forecast_message(fc, "parent", "ru", today=TODAY).splitlines()[0]
    fc_pub = dict(fc, levels_json={"public": "orange"}, probability=0.6)
    assert "пылевой день вероятен — 60%" in T.forecast_message(fc_pub, "asthma", "ru", today=TODAY)


def test_texts_corrections():
    red_doc = dict(FULL, level="red", day=TODAY.isoformat(), degraded=0)
    assert "усильте" in T.forecast_message(red_doc, "doctor", "ru", today=TODAY)
    assert "усилите" not in str(T.T)
    assert T.pct(0.999, "ru") == ">95%" and T.pct(0.999, "kk") == "95%-дан жоғары"
    assert "после 21 сухого дня".capitalize() in T.forecast_message(dict(red_doc, wind_dir_sector="N"), "resident",
                                                                    "ru", today=TODAY)
    assert "(неточно)" in T.sms_message(dict(red_doc, degraded=1), "ru", today=TODAY)


@pytest.mark.parametrize("n,abl", [(1, "ден"), (4, "тен"), (5, "тен"), (7, "ден"), (8, "ден"), (10, "нан"),
                                   (20, "дан"), (21, "ден"), (6, "дан"), (40, "тан"), (100, "ден")])
def test_kk_ablative(n, abl):
    assert kk_abl(n) == abl


def test_kk_possessive_and_ru_plural():
    assert [kk_poss(n) for n in (1, 2, 5, 6, 7, 10, 20)] == ["і", "сі", "і", "сы", "сі", "ы", "сы"]
    forms = ("день", "дня", "дней")
    assert [plural_ru(n, *forms) for n in (1, 4, 5, 7, 8, 10, 11, 20, 21, 22)] == \
        ["день", "дня", "дней", "дней", "дней", "дней", "дней", "дней", "день", "дня"]
