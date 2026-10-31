"""М4 «Голос»: тексты сообщений на казахском и русском (раздел 15.3, приложение В).

Схема: что — где — когда — насколько уверены — что делать — откуда пыль — оговорка.
Ограничения 25.1: Telegram — до 400 знаков, SMS — до 70 знаков кириллицей,
не более трёх действий, каждое начинается с глагола; ссылка на Казгидромет — в каждом сообщении (FR-06).
«Сегодня/завтра» считаются от сегодняшней даты по Кызылорде, а не от даты выпуска.
Казахские тексты перед запуском вычитывает носитель языка (раздел 25.4).
"""
from __future__ import annotations

import json
from datetime import date

from aralshield.config import KAZHYDROMET_URL, ROLE_GROUP, STATION_BY_ID, TEST_YEARS
from aralshield.voice.lang import WEEKDAYS, date_text, kk_abl, plural_ru

LEVELS = ("green", "yellow", "orange", "red")
RANK = {lv: i for i, lv in enumerate(LEVELS)}
LEVEL_ICON = {"green": "🟢", "yellow": "🟡", "orange": "🟠", "red": "🔴"}
SHORT_URL = KAZHYDROMET_URL.split("://", 1)[-1].removeprefix("www.")  # kazhydromet.kz — Telegram делает ссылкой
ARCHIVE_YEARS = f"{TEST_YEARS[0]}–{TEST_YEARS[-1]}"

T = {
    "ru": {
        "level_word": {"green": "маловероятен", "yellow": "возможен", "orange": "вероятен",
                       "red": "очень вероятен"},
        "level_name": {"green": "Зелёный", "yellow": "Жёлтый", "orange": "Оранжевый", "red": "Красный"},
        "dust_day": "пылевой день",
        "strong": "высокая вероятность сильного пылевого события",
        # группы с пониженным порогом (13.8): уровень выше общего, но «вероятен» при 12% звучит как ложь
        "elevated": {"default": "возможен, повышенный риск для вас", "child": "возможен, повышенный риск для детей",
                     "doctor": "возможен, повышенный риск для пациентов"},
        "rel_day": ["сегодня", "завтра", "послезавтра"],
        "wind": "{dir} ветер до {v:.0f} м/с",
        "after_dry": "После {n} {days} — ",
        "source": "Вероятный источник — {sector} высохшего дна.",
        "todo": "Что делать: ",
        "disclaimer": f"Инфопрогноз, не официальное предупреждение: {SHORT_URL}",
        "degraded": "⚠️ Данные неполные, уверенность ниже.",
        "stale": "⚠️ Выпуск от {date}: свежий прогноз ещё не вышел.",
        "actions": {
            "public": {"yellow": ["держите лекарства при себе, если у вас астма"],
                       "orange": ["закройте окна", "не оставляйте детей надолго на улице",
                                  "держите лекарства при себе, если у вас астма"],
                       "red": ["сократите время на улице", "закройте окна", "наденьте маску при выходе"]},
            "health": {"yellow": ["держите ингалятор под рукой"],
                       "orange": ["держите ингалятор под рукой", "закройте окна", "отложите прогулки"],
                       "red": ["оставайтесь в помещении", "следуйте плану действий от вашего врача",
                               "закройте окна"]},
            "parent": {"yellow": ["следите за самочувствием ребёнка"],
                       "orange": ["не отпускайте детей надолго на улицу", "закройте окна",
                                  "держите лекарства ребёнка под рукой"],
                       "red": ["оставьте детей в помещении", "закройте окна",
                               "при ухудшении самочувствия обратитесь к врачу"]},
            "school": {"yellow": ["приготовьтесь перенести занятия на улице в зал"],
                       "orange": ["проведите уроки физкультуры в зале", "закройте окна",
                                  "предупредите родителей"],
                       "red": ["отмените занятия на улице", "закройте окна", "предупредите родителей"]},
            "doctor": {"yellow": ["напомните пациентам с астмой и ХОБЛ о плане действий"],
                       "orange": ["подготовьтесь к росту обращений",
                                  "напомните пациентам с астмой и ХОБЛ о плане действий"],
                       "red": ["усильте готовность поликлиники к росту обращений",
                               "напомните пациентам о плане действий"]},
            "farmer": {"yellow": ["следите за прогнозом"],
                       "orange": ["держите скот в укрытии", "укройте всходы по возможности"],
                       "red": ["держите скот в укрытии", "не выгоняйте отару", "оставайтесь в помещении"]},
            "akimat": {"yellow": ["отслеживайте обновления прогноза"],
                       "orange": ["предупредите школы и поликлиники района", "проверьте каналы оповещения"],
                       "red": ["приведите службы в готовность", "оповестите население"]},
        },
        "parents_head": "Текст для родителей (перешлите в чат класса):",
        "parents": "{Day} ожидается пыльная погода, занятия на улице переносятся в помещение.",
        "share": "Переслать родителям",
        "green_line": "{place}, {when}: пылевой день маловероятен — {p}.",
        "sms": {"orange": "{When} вероятна пыльная буря. Держите скот в укрытии. AralShield",
                "red": "{When} сильная пыльная буря. Уведите скот в укрытие. AralShield"},
        "sms_degraded": {"orange": "{When} вероятна пыльная буря (неточно). Держите скот в укрытии.",
                         "red": "{When} сильная пыльная буря (неточно). Уведите скот в укрытие."},
        # SMS для всех ролей, кроме чабанов/фермеров (им — про скот)
        "sms_general": {"orange": "{When} вероятна пыльная буря. Закройте окна. AralShield",
                        "red": "{When} сильная пыльная буря. Будьте дома. AralShield"},
        "sms_general_degraded": {"orange": "{When} вероятна пыльная буря (неточно). Закройте окна.",
                                 "red": "{When} сильная пыльная буря (неточно). Будьте дома."},
        "sms_none": "(SMS отправляется только при оранжевом и красном уровне)",
        "feedback_q": "Вчера мы предупреждали о пыли в вашем пункте ({place}). Было пыльно?",
        "fb_yes": "Да", "fb_no": "Нет", "fb_unknown": "Не знаю",
        "summary_head": "Сводка AralShield на 3 суток.",
        "summary_issue": " (выпуск {date})",
        "summary_max": "Наибольший риск — {place}, {when}, {p}",
        "summary_src": ", источник — {sector} часть высохшего дна",
        "summary_green": "Пылевых дней не ожидается.",
        "summary_none": "Актуальный прогноз ещё не выпущен.",
        "summary_acc_live": "Точность оповещений оранжевого и красного уровня за {days} {days_w}: {hit} из {n}.",
        "summary_acc_archive": ("Точность оповещений оранжевого и красного уровня (архив тестовых лет "
                                f"{ARCHIVE_YEARS}, {{frm}} — {{to}}): {{hit}} из {{n}}."),
        "demo": "ДЕМО: архивное событие {date}",
    },
    "kk": {
        "level_word": {"green": "екіталай", "yellow": "мүмкін", "orange": "ықтимал", "red": "өте ықтимал"},
        "level_name": {"green": "Жасыл", "yellow": "Сары", "orange": "Қызғылт сары", "red": "Қызыл"},
        "dust_day": "шаңды күн болуы",
        "strong": "күшті шаңды дауылдың ықтималдығы жоғары",
        "elevated": {"default": "мүмкін, сізге қауіпті", "child": "мүмкін, балаларға қауіпті",
                     "doctor": "мүмкін, науқастарға қауіпті"},
        "rel_day": ["бүгін", "ертең", "бүрсігүні"],
        "wind": "{dir} {v:.0f} м/с дейін жел",
        "after_dry": "{n} құрғақ күннен кейін ",
        "source": "Ықтимал шаң көзі — Арал түбінің {sector} бөлігі.",
        "todo": "Не істеу керек: ",
        "disclaimer": f"Ақпараттық болжам, ресми ескерту емес: {SHORT_URL}",
        "degraded": "⚠️ Дерек толық емес, сенімділік төмен.",
        "stale": "⚠️ {date} шыққан болжам: жаңа болжам әлі шықпады.",
        "actions": {
            "public": {"yellow": ["демікпеңіз болса, дәріңізді жаныңызда ұстаңыз"],
                       "orange": ["терезелерді жабыңыз", "балаларды далада ұзақ қалдырмаңыз",
                                  "демікпеңіз болса, дәріңізді жаныңызда ұстаңыз"],
                       "red": ["далада болу уақытын азайтыңыз", "терезелерді жабыңыз",
                               "далаға шықсаңыз, бетперде киіңіз"]},
            "health": {"yellow": ["ингаляторды жаныңызда ұстаңыз"],
                       "orange": ["ингаляторды жаныңызда ұстаңыз", "терезелерді жабыңыз", "серуенді кейінге қалдырыңыз"],
                       "red": ["ғимарат ішінде болыңыз", "дәрігеріңіздің іс-әрекет жоспарын орындаңыз",
                               "терезелерді жабыңыз"]},
            "parent": {"yellow": ["баланың жағдайын қадағалаңыз"],
                       "orange": ["балаларды далаға ұзаққа жібермеңіз", "терезелерді жабыңыз",
                                  "баланың дәрісін дайын ұстаңыз"],
                       "red": ["балаларды үйде ұстаңыз", "терезелерді жабыңыз",
                               "жағдайы нашарласа, дәрігерге қаралыңыз"]},
            "school": {"yellow": ["далада өтетін сабақтарды залға ауыстыруға дайын болыңыз"],
                       "orange": ["дене шынықтыру сабағын залда өткізіңіз", "терезелерді жабыңыз",
                                  "ата-аналарды ескертіңіз"],
                       "red": ["далада өтетін сабақтарды өткізбеңіз", "терезелерді жабыңыз",
                               "ата-аналарды ескертіңіз"]},
            "doctor": {"yellow": ["демікпе және ӨСОА бар науқастарға іс-әрекет жоспарын еске салыңыз"],
                       "orange": ["жүгінулер көбеюіне дайындалыңыз",
                                  "демікпелі науқастарға жоспарды еске салыңыз"],
                       "red": ["емхананы жүгінулер көбеюіне әзірлеңіз",
                               "демікпелі науқастарға жоспарды еске салыңыз"]},
            "farmer": {"yellow": ["болжамды қадағалаңыз"],
                       "orange": ["малды қорада ұстаңыз", "мүмкіндігінше өскіндерді жабыңыз"],
                       "red": ["малды қорада ұстаңыз", "отарды өріске шығармаңыз", "ғимарат ішінде болыңыз"]},
            "akimat": {"yellow": ["болжам жаңартуларын қадағалаңыз"],
                       "orange": ["аудан мектептері мен емханаларын ескертіңіз", "хабарлау арналарын тексеріңіз"],
                       "red": ["қызметтерді әзірлікке келтіріңіз", "халықты хабардар етіңіз"]},
        },
        "parents_head": "Ата-аналарға арналған мәтін (сынып чатына жіберіңіз):",
        "parents": "{Day} шаңды ауа райы күтіледі, далада өтетін сабақтар ғимарат ішіне ауыстырылады.",
        "share": "Ата-аналарға жіберу",
        "green_line": "{place}, {when}: шаңды күн болуы екіталай — {p}.",
        "sms": {"orange": "{When} шаңды дауыл ықтимал. Малды қорада ұстаңыз. AralShield",
                "red": "{When} күшті шаңды дауыл. Малды қорада ұстаңыз. AralShield"},
        "sms_degraded": {"orange": "{When} шаңды дауыл ықтимал (шамамен). Малды қорада ұстаңыз.",
                         "red": "{When} күшті шаңды дауыл (шамамен). Малды қорада ұстаңыз."},
        "sms_general": {"orange": "{When} шаңды дауыл ықтимал. Терезені жабыңыз. AralShield",
                        "red": "{When} күшті шаңды дауыл. Үйде болыңыз. AralShield"},
        "sms_general_degraded": {"orange": "{When} шаңды дауыл ықтимал (шамамен). Терезені жабыңыз.",
                                 "red": "{When} күшті шаңды дауыл (шамамен). Үйде болыңыз."},
        "sms_none": "(SMS тек қызғылт сары және қызыл деңгейде жіберіледі)",
        "feedback_q": "Кеше сіздің елді мекеніңізде ({place}) шаң болатынын ескерткен едік. Шаң болды ма?",
        "fb_yes": "Иә", "fb_no": "Жоқ", "fb_unknown": "Білмеймін",
        "summary_head": "AralShield 3 тәулікке арналған шолу.",
        "summary_issue": " ({date} шыққан)",
        "summary_max": "Ең жоғары қауіп — {place}, {when}, {p}",
        "summary_src": ", шаң көзі — құрғаған теңіз түбінің {sector} бөлігі",
        "summary_green": "Шаңды күн күтілмейді.",
        "summary_none": "Өзекті болжам әлі шықпады.",
        "summary_acc_live": "Соңғы {days} күндегі қызғылт сары және қызыл деңгейлі ескертулердің дәлдігі: {n}-{abl} {hit}.",
        "summary_acc_archive": ("Қызғылт сары және қызыл деңгейлі ескертулердің дәлдігі "
                                f"({ARCHIVE_YEARS} тест жылдарының мұрағаты, {{frm}} — {{to}}): {{n}}-{{abl}} {{hit}}."),
        "demo": "ДЕМО: мұрағаттағы оқиға, {date}",
    },
}

# 16 румбов → 8 направлений, прилагательные в нужной форме
SECTOR8 = {"N": "N", "NNE": "NE", "NE": "NE", "ENE": "E", "E": "E", "ESE": "SE", "SE": "SE", "SSE": "S",
           "S": "S", "SSW": "SW", "SW": "SW", "WSW": "W", "W": "W", "WNW": "NW", "NW": "NW", "NNW": "N"}
DIR_ADJ = {
    "ru": {"wind": {"N": "Северный", "NE": "Северо-восточный", "E": "Восточный", "SE": "Юго-восточный",
                    "S": "Южный", "SW": "Юго-западный", "W": "Западный", "NW": "Северо-западный"},
           "part": {"N": "северная", "NE": "северо-восточная", "E": "восточная", "SE": "юго-восточная",
                    "S": "южная", "SW": "юго-западная", "W": "западная", "NW": "северо-западная"},
           # короткая форма для сообщения (25.1: до 400 знаков)
           "area": {"N": "север", "NE": "северо-восток", "E": "восток", "SE": "юго-восток",
                    "S": "юг", "SW": "юго-запад", "W": "запад", "NW": "северо-запад"}},
    # казахский: направление ветра в исходном падеже («солтүстік-шығыстан»)
    "kk": {"wind": {"N": "Солтүстіктен", "NE": "Солтүстік-шығыстан", "E": "Шығыстан", "SE": "Оңтүстік-шығыстан",
                    "S": "Оңтүстіктен", "SW": "Оңтүстік-батыстан", "W": "Батыстан", "NW": "Солтүстік-батыстан"},
           "part": {"N": "солтүстік", "NE": "солтүстік-шығыс", "E": "шығыс", "SE": "оңтүстік-шығыс",
                    "S": "оңтүстік", "SW": "оңтүстік-батыс", "W": "батыс", "NW": "солтүстік-батыс"}},
}
# Советы: родитель получает советы для детей, а не инструкции пациента (T11-12)
ROLE_ACTIONS = {"parent": "parent", "asthma": "health", "resident": "public", "teacher": "school",
                "doctor": "doctor", "farmer": "farmer", "akimat": "akimat", "dchs": "akimat",
                "forestry": "public"}
ELEVATED_KIND = {"parent": "child", "teacher": "child", "doctor": "doctor"}


def _today() -> date:
    from aralshield.db import local_today
    return local_today()


def ref_date(fc: dict, today: date | None = None) -> date:
    """Дата, от которой считаются «сегодня/завтра»: сегодня по Кызылорде для боевых
    прогнозов и дата выпуска для архивных (демо, повтор события)."""
    if today:
        return today
    if fc.get("mode") == "backtest":
        return date.fromisoformat(fc["issue_date"])
    return _today()


def place_name(station_id: str, lang: str) -> str:
    st = STATION_BY_ID[station_id]
    return st.name_kk if lang == "kk" else st.name_ru


def when_text(day: date, lang: str, today: date | None = None) -> str:
    """«завтра (вторник)» для 0–2 дней вперёд, иначе «29 октября (четверг)»."""
    today = today or _today()
    h = (day - today).days
    if 0 <= h < 3:
        return f"{T[lang]['rel_day'][h]} ({WEEKDAYS[lang][day.weekday()]})"
    return date_text(day, lang)


def day_word(day: date, lang: str, today: date | None = None) -> str:
    """«Завтра» / «29 октября» — с заглавной, без дня недели (SMS, текст для родителей)."""
    today = today or _today()
    h = (day - today).days
    w = T[lang]["rel_day"][h] if 0 <= h < 3 else date_text(day, lang, weekday=False)
    return w[:1].upper() + w[1:]


def pct(p: float, lang: str = "ru", short: bool = False) -> str:
    """Вероятность словом-числом; выше 95% — «>95%»: прогноз не бывает приговором."""
    if p >= 0.955:
        return ">95%" if short or lang == "ru" else "95%-дан жоғары"
    return f"{int(round(p * 100))}%"


def _levels(fc: dict) -> dict:
    lj = fc.get("levels_json")
    if isinstance(lj, str):
        try:
            return json.loads(lj or "{}")
        except ValueError:
            return {}
    return lj or {}


def _head_word(fc: dict, role: str, lang: str) -> str:
    t, level = T[lang], fc["level"]
    if level == "red" and role in ("doctor", "akimat", "dchs"):
        return t["strong"]
    public = fc.get("public_level") or _levels(fc).get("public") or level
    if (RANK[level] >= RANK["orange"] and RANK.get(public, 0) < RANK["orange"]
            and ROLE_GROUP.get(role, "public") in ("health", "school")):
        return f"{t['dust_day']} {t['elevated'][ELEVATED_KIND.get(role, 'default')]}"
    return f"{t['dust_day']} {t['level_word'][level]}"


def forecast_message(fc: dict, role: str = "resident", lang: str = "ru", today: date | None = None,
                     place: str | None = None) -> str:
    """Сообщение для одного прогноза (пункт, сутки). fc — строка из таблицы forecasts,
    level — уже уровень группы подписчика. place — заголовок вместо названия станции."""
    t = T[lang]
    level = fc["level"]
    place = place or place_name(fc["station"], lang)
    ref = ref_date(fc, today)
    day = date.fromisoformat(fc["day"])
    when = when_text(day, lang, ref)
    p = pct(fc["probability"], lang)
    if level == "green":
        lines = [f"{LEVEL_ICON[level]} " + t["green_line"].format(place=place, when=when, p=p)]
        if fc.get("degraded"):
            lines.append(t["degraded"])
        return "\n".join(lines + [t["disclaimer"]])
    lines = [f"{LEVEL_ICON[level]} {place}, {when}: {_head_word(fc, role, lang)} — {p}."]
    sec = SECTOR8.get(fc.get("wind_dir_sector") or "", None)
    if fc.get("wind_max") and sec:
        dry = int(fc["dry_days"]) if fc.get("dry_days") and fc["dry_days"] >= 3 else None
        w = t["wind"].format(dir=DIR_ADJ[lang]["wind"][sec], v=fc["wind_max"])
        if dry:  # «После 21 сухого дня — северо-восточный ветер…» / «21 құрғақ күннен кейін солтүстік-шығыстан…»
            days = plural_ru(dry, "сухого дня", "сухих дней", "сухих дней") if lang == "ru" else ""
            w = t["after_dry"].format(n=dry, days=days) + w[:1].lower() + w[1:]
        lines.append(w + ".")
    src = SECTOR8.get(fc.get("source_sector") or "", None)
    if src:
        lines.append(t["source"].format(sector=DIR_ADJ[lang].get("area", DIR_ADJ[lang]["part"])[src]))
    acts = t["actions"][ROLE_ACTIONS.get(role, "public")][level][:3]
    lines.append(t["todo"] + "; ".join(acts) + ".")
    if fc.get("degraded"):
        lines.append(t["degraded"])
    lines.append(t["disclaimer"])
    return "\n".join(lines)


def parents_message(fc: dict, lang: str = "ru", today: date | None = None) -> str:
    """Готовый текст для пересылки родителям (учитель, оранжевый и красный уровень)."""
    day = date.fromisoformat(fc["day"])
    return T[lang]["parents"].format(Day=day_word(day, lang, ref_date(fc, today)))


def share_url(text: str) -> str:
    """Кнопка «Переслать»: t.me/share открывает выбор чата и работает без inline-режима бота."""
    from urllib.parse import quote
    return f"https://t.me/share/url?url={quote(KAZHYDROMET_URL, safe='')}&text={quote(text, safe='')}"


def sms_message(fc: dict, lang: str = "kk", today: date | None = None, role: str | None = None) -> str | None:
    """Короткое SMS (только оранжевый и красный уровни, ≤70 знаков кириллицей).
    Совет про скот — чабанам и фермерам (и по умолчанию: SMS-канал создан для них), остальным — общий."""
    if fc["level"] not in ("orange", "red"):
        return None
    day = date.fromisoformat(fc["day"])
    key = "sms" if role in (None, "farmer") else "sms_general"
    tpl = T[lang][key + "_degraded" if fc.get("degraded") else key][fc["level"]]
    return tpl.format(When=day_word(day, lang, ref_date(fc, today)))


def _acc_line(acc, lang: str) -> str | None:
    t = T[lang]
    if isinstance(acc, (tuple, list)):  # прежний формат (hit, n)
        acc = {"hit": acc[0], "n": acc[1], "mode": "live", "days": 30}
    if not acc or not acc.get("n"):
        return None
    hit, n = int(acc["hit"]), int(acc["n"])
    if acc.get("mode") == "backtest":
        def _d(s):
            try:
                d = date.fromisoformat(s)
            except (TypeError, ValueError):
                return "?"
            return f"{date_text(d, lang, weekday=False)} {d.year}"
        return t["summary_acc_archive"].format(hit=hit, n=n, abl=kk_abl(n), frm=_d(acc.get("from")),
                                               to=_d(acc.get("to")))
    days = int(acc.get("days", 30))
    return t["summary_acc_live"].format(hit=hit, n=n, abl=kk_abl(n), days=days,
                                        days_w=plural_ru(days, "день", "дня", "дней"))


def summary_message(forecasts: list[dict], lang: str = "ru", acc=None, today: date | None = None) -> str:
    """Ежедневная сводка для акимата и ДЧС (приложение В). Прошедшие дни не показываются,
    устаревший выпуск помечается датой; источник называется, только если он известен."""
    t = T[lang]
    today = today or _today()
    by_st: dict[str, list[dict]] = {}
    for f in forecasts:
        if date.fromisoformat(f["day"]) >= today:
            by_st.setdefault(f["station"], []).append(f)
    lines = [t["summary_head"]]
    for st, fs in by_st.items():
        fs = sorted(fs, key=lambda x: x["day"])
        issue = date.fromisoformat(fs[0]["issue_date"])
        mark = t["summary_issue"].format(date=date_text(issue, lang, weekday=False)) if issue < today else ""
        lines.append(f"{place_name(st, lang)}{mark}: " + " / ".join(pct(f["probability"], lang, short=True) for f in fs))
    current = [f for fs in by_st.values() for f in fs]
    if not current:
        lines.append(t["summary_none"])
    elif all(f["level"] == "green" for f in current):
        lines.append(t["summary_green"])
    else:
        top = max(current, key=lambda f: (f["probability"], -f["horizon"]))
        line = t["summary_max"].format(place=place_name(top["station"], lang),
                                       when=when_text(date.fromisoformat(top["day"]), lang, today),
                                       p=pct(top["probability"], lang, short=True))
        src = SECTOR8.get(top.get("source_sector") or "", None)
        if src:
            line += t["summary_src"].format(sector=DIR_ADJ[lang]["part"][src])
        lines.append(line + ".")
    acc_line = _acc_line(acc, lang)
    if acc_line:
        lines.append(acc_line)
    lines.append(t["disclaimer"])
    return "\n".join(lines)


def feedback_question(station_id: str, lang: str) -> str:
    return T[lang]["feedback_q"].format(place=place_name(station_id, lang))


def demo_header(event_date: date, lang: str) -> str:
    return T[lang]["demo"].format(date=date_text(event_date, lang, weekday=False) + f" {event_date.year}")
