"""Telegram-бот AralShield (раздел 25.1) на aiogram 3.

Запуск: задать TELEGRAM_BOT_TOKEN (выдаёт @BotFather) и выполнить
    python -m aralshield.cli bot

Собираются только: идентификатор чата, пункт, роль, язык, порог, время рассылки (раздел 15.5).
Геолокация и данные о здоровье не запрашиваются. /stop удаляет все данные подписчика
(подписки, историю рассылки, ответы обратной связи).
Бот работает только в личных чатах: подписка хранится по идентификатору пользователя,
а в группе кнопки мастера могли бы нажимать другие участники.
Работа с базой и pandas выполняется в потоке (asyncio.to_thread), чтобы не блокировать
обработку сообщений других пользователей.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import date

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup)

from aralshield import db
from aralshield.config import KAZHYDROMET_URL, ROLE_GROUP, STATIONS, STATION_BY_ID
from aralshield.voice import templates

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

SEND_HOURS = (6, 7, 19)  # 19:00 — вечером накануне (чабаны, раздел 25.1)

UI = {
    "ru": {
        "choose_lang": "Тілді таңдаңыз / Выберите язык:",
        "choose_place": "Выберите населённый пункт:",
        "choose_role": ("Кто вы? Роль нужна только для подходящих советов; диагнозы и данные о здоровье "
                        "мы не спрашиваем и не храним:"),
        "choose_level": "О каких событиях сообщать?",
        "choose_hour": "Когда присылать сообщение?",
        "roles": {"resident": "Житель", "parent": "Родитель", "asthma": "Чувствительная группа (ранние оповещения)",
                  "teacher": "Учитель / школа", "doctor": "Врач / поликлиника", "farmer": "Фермер / чабан",
                  "akimat": "Акимат / ДЧС"},
        "levels": {"yellow": "🟡 Жёлтый и выше (все)", "orange": "🟠 Оранжевый и выше (рекомендуется)",
                   "red": "🔴 Только красный"},
        "hours": {6: "Утром, 06:00", 7: "Утром, 07:00", 19: "Вечером накануне, 19:00"},
        "done": ("Готово! Пункт: {place}. Сообщение придёт в {hour:02d}:00, если риск не ниже выбранного.\n\n"
                 "Уровни: 🟢 маловероятен · 🟡 возможен · 🟠 вероятен · 🔴 очень вероятен.\n"
                 "Мы сообщаем вероятность, а не приговор, и публикуем свою точность (/accuracy).\n"
                 "AralShield — информационный сервис; официальные предупреждения — Казгидромет: {url}"),
        "settings": "Сейчас: {place} · {role} · {level} · {hour:02d}:00 · русский.\nИзменить:",
        "menu": ["Болжам / Прогноз", "Дәлдік / Точность", "Баптаулар / Настройки", "Көмек / Помощь",
                 "Тоқтату / Отписаться"],
        "no_sub": "Вы ещё не подписаны. Нажмите /start.",
        "no_fc": "Прогноз ещё не выпущен. Попробуйте позже.",
        "stale": "⚠️ Это прогноз от {date}: свежий выпуск ещё не вышел.",
        "stopped": "Вы отписались, ваши данные удалены. Вернуться — /start.",
        "expired": "Это меню устарело, нажмите /start.",
        "help": ("AralShield оценивает вероятность пылевого дня на 3 суток для пунктов Приаралья по данным "
                 "метеостанций, спутников и прогнозов погоды.\n\n🟢 маловероятен — ничего не требуется\n"
                 "🟡 возможен — держите лекарства при себе, если у вас астма\n"
                 "🟠 вероятен — закройте окна, перенесите занятия на улице, укройте скот\n"
                 "🔴 очень вероятен — сократите время на улице, следите за предупреждениями Казгидромета\n\n"
                 "Советы не заменяют рекомендаций вашего врача. Официальные предупреждения: {url}\n"
                 "/forecast — прогноз · /accuracy — точность · /settings — настройки · /demo — пример · "
                 "/stop — отписка"),
        "fb_thanks": "Спасибо! Ответ учтён в проверке прогноза.",
        "acc_none": "Пока недостаточно проверенных прогнозов.",
        "parents": "Текст для пересылки родителям:",
        "demo_none": "Демо-событие пока недоступно (нет архива прогнозов).",
        "demo_sms": "SMS:",
    },
    "kk": {
        "choose_lang": "Тілді таңдаңыз / Выберите язык:",
        "choose_place": "Елді мекенді таңдаңыз:",
        "choose_role": ("Сіз кімсіз? Рөл тек лайықты кеңес беру үшін керек; диагноз бен денсаулық туралы "
                        "деректерді сұрамаймыз және сақтамаймыз:"),
        "choose_level": "Қандай оқиғалар туралы хабарлау керек?",
        "choose_hour": "Хабарламаны қашан жіберейік?",
        "roles": {"resident": "Тұрғын", "parent": "Ата-ана", "asthma": "Сезімтал топ (ерте ескерту)",
                  "teacher": "Мұғалім / мектеп", "doctor": "Дәрігер / емхана", "farmer": "Фермер / шопан",
                  "akimat": "Әкімдік / ТЖД"},
        "levels": {"yellow": "🟡 Сары және одан жоғары (барлығы)",
                   "orange": "🟠 Қызғылт сары және одан жоғары (ұсынылады)", "red": "🔴 Тек қызыл"},
        "hours": {6: "Таңертең, 06:00", 7: "Таңертең, 07:00", 19: "Алдыңғы күні кешке, 19:00"},
        "done": ("Дайын! Елді мекен: {place}. Қауіп таңдалған деңгейден төмен болмаса, хабарлама {hour:02d}:00-де "
                 "келеді.\n\nДеңгейлер: 🟢 екіталай · 🟡 мүмкін · 🟠 ықтимал · 🔴 өте ықтимал.\n"
                 "Біз үкім емес, ықтималдықты айтамыз және өз дәлдігімізді жариялаймыз (/accuracy).\n"
                 "AralShield — ақпараттық қызмет; ресми ескертулер — Қазгидромет: {url}"),
        "settings": "Қазір: {place} · {role} · {level} · {hour:02d}:00 · қазақша.\nӨзгерту:",
        "menu": ["Болжам / Прогноз", "Дәлдік / Точность", "Баптаулар / Настройки", "Көмек / Помощь",
                 "Тоқтату / Отписаться"],
        "no_sub": "Сіз әлі жазылмағансыз. /start басыңыз.",
        "no_fc": "Болжам әлі шыққан жоқ. Кейінірек көріңіз.",
        "stale": "⚠️ Бұл {date} шыққан болжам: жаңа болжам әлі шықпады.",
        "stopped": "Жазылымнан шықтыңыз, деректеріңіз жойылды. Қайту — /start.",
        "expired": "Бұл мәзір ескірген, /start басыңыз.",
        "help": ("AralShield метеостанциялар, спутниктер және ауа райы болжамдары бойынша Арал өңіріндегі елді "
                 "мекендер үшін 3 тәулікке шаңды күн ықтималдығын бағалайды.\n\n🟢 екіталай — ештеңе қажет емес\n"
                 "🟡 мүмкін — демікпеңіз болса, дәріңізді жаныңызда ұстаңыз\n"
                 "🟠 ықтимал — терезелерді жабыңыз, сабақтарды ғимарат ішіне ауыстырыңыз, малды қорада ұстаңыз\n"
                 "🔴 өте ықтимал — далада болу уақытын азайтыңыз, Қазгидромет ескертулерін қадағалаңыз\n\n"
                 "Кеңестер дәрігеріңіздің нұсқауын алмастырмайды. Ресми ескертулер: {url}\n"
                 "/forecast — болжам · /accuracy — дәлдік · /settings — баптаулар · /demo — мысал · "
                 "/stop — жазылымнан шығу"),
        "fb_thanks": "Рақмет! Жауабыңыз болжамды тексеруде ескерілді.",
        "acc_none": "Тексерілген болжамдар әзірге жеткіліксіз.",
        "parents": "Ата-аналарға жіберуге арналған мәтін:",
        "demo_none": "Демо-оқиға әзірге қолжетімсіз (болжам мұрағаты жоқ).",
        "demo_sms": "SMS:",
    },
}


def _get_sub(user_id: int) -> dict | None:
    return db.get_subscriber("telegram", str(user_id))


async def _sub(user_id: int) -> dict | None:
    return await asyncio.to_thread(_get_sub, user_id)


async def _lang(user_id: int) -> str:
    s = await _sub(user_id)
    return s["lang"] if s else "kk"


def _menu(lang: str) -> ReplyKeyboardMarkup:
    m = UI[lang]["menu"]
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=m[0]), KeyboardButton(text=m[1])],
                                         [KeyboardButton(text=m[2]), KeyboardButton(text=m[3])],
                                         [KeyboardButton(text=m[4])]], resize_keyboard=True)


def _kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in r]
                                                 for r in rows])


def _share_kb(lang: str, text: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=templates.T[lang]["share"],
                                                                       url=templates.share_url(text))]])


# ---------------------------------------------------------------- мастер подписки
# Черновик живёт в памяти (данные пишутся в базу только в конце); принимаются нажатия
# только из последнего сообщения мастера и не позже DRAFT_TTL секунд.
_draft: dict[int, dict] = {}
DRAFT_TTL = 1800
DRAFT_MAX = 10_000


def _new_draft(user_id: int, msg_id: int | None) -> None:
    if len(_draft) >= DRAFT_MAX:  # старые черновики вытесняются
        for k in sorted(_draft, key=lambda k: _draft[k]["ts"])[: DRAFT_MAX // 10]:
            _draft.pop(k, None)
    _draft[user_id] = {"msg_id": msg_id, "ts": time.monotonic()}


def _draft_for(cb: CallbackQuery) -> dict | None:
    d = _draft.get(cb.from_user.id)
    if not d or time.monotonic() - d["ts"] > DRAFT_TTL:
        _draft.pop(cb.from_user.id, None)
        return None
    if not isinstance(cb.message, Message) or cb.message.message_id != d.get("msg_id"):
        return None  # кнопка из старого сообщения мастера
    d["ts"] = time.monotonic()
    return d


async def _stale(cb: CallbackQuery, lang: str = "kk") -> None:
    await cb.answer(UI[lang]["expired"], show_alert=False)


@router.message(CommandStart())
async def start(msg: Message):
    m = await msg.answer(UI["kk"]["choose_lang"], reply_markup=_kb([[("Қазақша", "lang:kk"), ("Русский", "lang:ru")]]))
    _new_draft(msg.from_user.id, m.message_id)


@router.message(Command("settings"))
@router.message(F.text == "Баптаулар / Настройки")
async def settings(msg: Message):
    s = await _sub(msg.from_user.id)
    if s and s.get("station"):
        lang = s["lang"]
        txt = UI[lang]["settings"].format(place=templates.place_name(s["station"], lang),
                                          role=UI[lang]["roles"].get(s["role"], s["role"]),
                                          level=UI[lang]["levels"].get(s["min_level"], s["min_level"]),
                                          hour=s["send_hour"] if s["send_hour"] is not None else 6)
        await msg.answer(txt)
    await start(msg)


@router.callback_query(F.data.startswith("lang:"))
async def on_lang(cb: CallbackQuery):
    d = _draft_for(cb)
    lang = cb.data.split(":", 1)[1]
    if d is None or lang not in UI:
        return await _stale(cb)
    d["lang"] = lang
    rows = [[(s.name_kk if lang == "kk" else s.name_ru, f"st:{s.id}")] for s in STATIONS]
    await cb.message.edit_text(UI[lang]["choose_place"], reply_markup=_kb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("st:"))
async def on_station(cb: CallbackQuery):
    d = _draft_for(cb)
    st = cb.data.split(":", 1)[1]
    if d is None or "lang" not in d or st not in STATION_BY_ID:
        return await _stale(cb, (d or {}).get("lang", "kk"))
    lang = d["lang"]
    d["station"] = st
    rows = [[(name, f"role:{key}")] for key, name in UI[lang]["roles"].items()]
    await cb.message.edit_text(UI[lang]["choose_role"], reply_markup=_kb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("role:"))
async def on_role(cb: CallbackQuery):
    d = _draft_for(cb)
    role = cb.data.split(":", 1)[1]
    if d is None or "station" not in d or role not in UI[d.get("lang", "kk")]["roles"]:
        return await _stale(cb, (d or {}).get("lang", "kk"))
    lang = d["lang"]
    d["role"] = role
    rows = [[(name, f"lvl:{key}")] for key, name in UI[lang]["levels"].items()]
    await cb.message.edit_text(UI[lang]["choose_level"], reply_markup=_kb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("lvl:"))
async def on_level(cb: CallbackQuery):
    d = _draft_for(cb)
    level = cb.data.split(":", 1)[1]
    # роль, пункт и язык обязательны: устаревшая кнопка не должна молча записать «жителя»
    if d is None or not {"lang", "station", "role"} <= d.keys() or level not in ("yellow", "orange", "red"):
        return await _stale(cb, (d or {}).get("lang", "kk"))
    lang = d["lang"]
    d["level"] = level
    rows = [[(name, f"hr:{h}")] for h, name in UI[lang]["hours"].items()]
    await cb.message.edit_text(UI[lang]["choose_hour"], reply_markup=_kb(rows))
    await cb.answer()


def _save(user_id: int, d: dict, hour: int) -> None:
    sid = db.upsert_subscriber("telegram", str(user_id), d["lang"], d["role"])
    db.set_subscription(sid, d["station"], d["level"], hour, exclusive=True)


@router.callback_query(F.data.startswith("hr:"))
async def on_hour(cb: CallbackQuery):
    d = _draft_for(cb)
    try:
        hour = int(cb.data.split(":", 1)[1])
    except ValueError:
        hour = -1
    if d is None or not {"lang", "station", "role", "level"} <= d.keys() or hour not in SEND_HOURS:
        return await _stale(cb, (d or {}).get("lang", "kk"))
    _draft.pop(cb.from_user.id, None)
    lang = d["lang"]
    await asyncio.to_thread(_save, cb.from_user.id, d, hour)
    place = templates.place_name(d["station"], lang)
    await cb.message.edit_text(UI[lang]["done"].format(place=place, hour=hour, url=KAZHYDROMET_URL),
                               disable_web_page_preview=True)
    await cb.message.answer("👇", reply_markup=_menu(lang))
    await cb.answer()


# ---------------------------------------------------------------- прогноз
def forecast_view(sub: dict, today: date | None = None) -> list[tuple[str, str | None]]:
    """Сообщения для /forecast: [(текст, текст для кнопки «Переслать» или None)].
    Дни считаются от сегодняшней даты; прошедшие дни не показываются; старый выпуск помечается."""
    today = today or db.local_today()
    lang, role = sub["lang"], sub["role"]
    fcs = db.latest_forecasts(sub["station"])
    if not fcs:
        return [(UI[lang]["no_fc"], None)]
    group = ROLE_GROUP.get(role, "public")
    t = templates.T[lang]
    issue = date.fromisoformat(fcs[0]["issue_date"])
    lines, worst = [], None
    for f in fcs:
        day = date.fromisoformat(f["day"])
        if day < today:
            continue
        lv = json.loads(f["levels_json"] or "{}").get(group, f["level"])
        f = dict(f, level=lv)
        when = templates.when_text(day, lang, today)
        word = templates._head_word(f, role, lang) if lv != "green" else f"{t['dust_day']} {t['level_word'][lv]}"
        lines.append(f"{templates.LEVEL_ICON[lv]} {when[:1].upper() + when[1:]}: {word} — "
                     f"{templates.pct(f['probability'], lang)}")
        if worst is None or templates.RANK[lv] > templates.RANK[worst["level"]]:
            worst = f
    if not lines:
        return [(UI[lang]["no_fc"], None)]
    head = templates.place_name(sub["station"], lang)
    if issue < today:
        head += "\n" + UI[lang]["stale"].format(date=templates.date_text(issue, lang, weekday=False))
    out = [(f"{head}\n" + "\n".join(lines) + f"\n{t['disclaimer']}", None)]
    if worst and worst["level"] != "green":
        out.append((templates.forecast_message(worst, role, lang, today=today), None))
        if role == "teacher" and worst["level"] in ("orange", "red"):
            p = templates.parents_message(worst, lang, today=today)
            out.append((f"{UI[lang]['parents']}\n{p}", p))
    return out


@router.message(Command("forecast"))
@router.message(F.text == "Болжам / Прогноз")
async def forecast(msg: Message):
    sub = await _sub(msg.from_user.id)
    if not sub or not sub.get("station"):
        await msg.answer(UI["kk"]["no_sub"] + "\n" + UI["ru"]["no_sub"])
        return
    for text, share in await asyncio.to_thread(forecast_view, sub):
        await msg.answer(text, disable_web_page_preview=True,
                         reply_markup=_share_kb(sub["lang"], share) if share else None)


def demo_view(lang: str, role: str = "resident") -> list[str]:
    """/demo: архивное событие из replay (K8) — не зависит от сегодняшней погоды и сети."""
    from aralshield.api.demo import demo_forecast
    ev, row = demo_forecast()
    if not ev or not row:
        return [UI[lang]["demo_none"]]
    issue = date.fromisoformat(row["issue_date"])
    fc = dict(row, level=json.loads(row["levels_json"] or "{}").get(ROLE_GROUP.get(role, "public"), row["level"]))
    head = templates.demo_header(date.fromisoformat(ev["event_date"]), lang)
    out = [f"{head}\n\n{templates.forecast_message(fc, role, lang, today=issue)}"]
    sms = templates.sms_message(dict(row), lang, today=issue, role=role)
    if sms:
        out.append(f"{head}\n{UI[lang]['demo_sms']} {sms}")
    if role == "teacher" and fc["level"] in ("orange", "red"):
        out.append(f"{UI[lang]['parents']}\n{templates.parents_message(fc, lang, today=issue)}")
    return out


@router.message(Command("demo"))
async def demo(msg: Message):
    sub = await _sub(msg.from_user.id)
    lang, role = (sub["lang"], sub["role"]) if sub else ("kk", "resident")
    for text in await asyncio.to_thread(demo_view, lang, role):
        await msg.answer(text, disable_web_page_preview=True)


def accuracy_text(lang: str, station: str | None) -> str | None:
    """Точность за 30 дней простыми словами (без POD/FAR); при отсутствии боевых проверок —
    архив тестовых лет, и это сказано в тексте."""
    from aralshield.api.verify import accuracy as acc_fn
    a = acc_fn(station, 30, 1, "auto")
    if not a["n"]:
        return None
    return a["text_kk"] if lang == "kk" else a["text_ru"]


@router.message(Command("accuracy"))
@router.message(F.text == "Дәлдік / Точность")
async def accuracy(msg: Message):
    sub = await _sub(msg.from_user.id)
    lang = sub["lang"] if sub else "kk"
    try:
        txt = await asyncio.to_thread(accuracy_text, lang, sub["station"] if sub and sub.get("station") else None)
    except Exception:
        log.exception("/accuracy: ошибка расчёта точности")
        txt = None
    await msg.answer(txt or UI[lang]["acc_none"])


@router.message(Command("help"))
@router.message(F.text == "Көмек / Помощь")
async def help_(msg: Message):
    lang = await _lang(msg.from_user.id)
    await msg.answer(UI[lang]["help"].format(url=KAZHYDROMET_URL), disable_web_page_preview=True)


@router.message(Command("stop"))
@router.message(F.text == "Тоқтату / Отписаться")
async def stop(msg: Message):
    lang = await _lang(msg.from_user.id)
    deleted = await asyncio.to_thread(db.delete_subscriber, "telegram", str(msg.from_user.id))
    _draft.pop(msg.from_user.id, None)
    await msg.answer(UI[lang]["stopped"] if deleted else UI[lang]["no_sub"])


def feedback_ok(user_id: int, station: str, day: str) -> bool:
    """Ответ принимается только за последние 7 дней и только от оповещённого подписчика."""
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return False
    lo, hi = db.feedback_window()
    return lo <= d <= hi and db.was_notified("telegram", str(user_id), station, day)


@router.callback_query(F.data.startswith("fb:"))
async def on_feedback(cb: CallbackQuery):
    parts = cb.data.split(":")
    if (len(parts) != 4 or parts[1] not in STATION_BY_ID or parts[3] not in ("yes", "no", "unknown")
            or not await asyncio.to_thread(feedback_ok, cb.from_user.id, parts[1], parts[2])):
        await cb.answer()
        return
    await asyncio.to_thread(db.save_feedback, "telegram", str(cb.from_user.id), parts[1], parts[2], parts[3])
    lang = await _lang(cb.from_user.id)
    if isinstance(cb.message, Message):
        await cb.message.edit_text(UI[lang]["fb_thanks"])
    await cb.answer()


@router.callback_query()
async def on_unknown_callback(cb: CallbackQuery):
    """Любая другая кнопка: отвечаем, чтобы у пользователя не «крутилось» ожидание."""
    await _stale(cb, await _lang(cb.from_user.id))


async def _run(token: str):
    bot = Bot(token)
    dp = Dispatcher()
    dp.include_router(router)
    from aiogram.types import BotCommand
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in (
        ("start", "Жазылу / Подписка"), ("forecast", "Болжам / Прогноз"), ("accuracy", "Дәлдік / Точность"),
        ("settings", "Баптаулар / Настройки"), ("demo", "Демо / Пример"), ("help", "Көмек / Помощь"),
        ("stop", "Тоқтату / Отписаться"))])
    await dp.start_polling(bot)


def main():
    db.init()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Задайте TELEGRAM_BOT_TOKEN (получить у @BotFather) и запустите снова.")
    asyncio.run(_run(token))
