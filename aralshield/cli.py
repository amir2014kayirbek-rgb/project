"""Командная строка AralShield.

    python -m aralshield.cli truth      # М0: станции NOAA ISD → каталог пылевых дней
    python -m aralshield.cli eye        # М1: Sentinel-2 → карта классов, секторы источников
    python -m aralshield.cli dataset    # признаки ERA5 + метки
    python -m aralshield.cli train      # М2: обучение и честная проверка
    python -m aralshield.cli shield     # М3: приоритеты посадок
    python -m aralshield.cli backtest   # архив прогнозов тестовых лет в базу
    python -m aralshield.cli daily      # ежедневный выпуск прогноза (J3–J7)
    python -m aralshield.cli notify     # рассылка (J8); --hour 19 — вечерняя рассылка
    python -m aralshield.cli serve      # API + веб-панель
    python -m aralshield.cli bot        # Telegram-бот
    python -m aralshield.cli apikey --owner "Лесхоз"   # выдать ключ партнёру
    python -m aralshield.cli apikey --list             # ключи (без секретов)
    python -m aralshield.cli apikey --revoke "Лесхоз"  # отозвать по владельцу или id из --list
    python -m aralshield.cli all        # полный конвейер с нуля
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from aralshield import config
from aralshield.config import REPORTS

EYE_TAG = getattr(config, "EYE_TAG", "2025v2")  # контракт K1
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):  # консоль/канал Windows: кириллица и казахские буквы
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _setup_logging(verbose: bool) -> None:
    from aralshield.voice.notify import TokenMaskFilter
    handlers = [logging.StreamHandler(sys.stdout),
                RotatingFileHandler(REPORTS / "pipeline.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")]
    mask = TokenMaskFilter()  # токен бота не попадает ни в консоль, ни в файл журнала
    for h in handlers:
        h.addFilter(mask)
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    for noisy in ("rasterio", "urllib3", "pystac_client", "matplotlib", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_truth(a):
    from aralshield import db
    from aralshield.truth import isd
    ev = isd.run()
    db.init()
    n = db.load_events(ev)
    print(f"каталог событий: {int(ev.dust.sum())} пылевых дней, {n} станционных суток в базе")


def cmd_eye(a):
    from aralshield.eye import classify, composite
    composite.build_composite(season=a.season, tag=a.tag, per_tile=a.per_tile)
    res = classify.train_and_map(tag=a.tag)
    print(json.dumps(res["cv"], ensure_ascii=False, indent=1)[:1500])


def cmd_dataset(a):
    from aralshield.forecast import dataset
    ds = dataset.build()
    print(f"набор признаков: {len(ds)} строк")


def cmd_train(a):
    from aralshield.forecast import train
    r = train.run()
    print(json.dumps(r["gates"], ensure_ascii=False, indent=1))


def cmd_shield(a):
    from aralshield.shield import priority
    r = priority.run(tag=a.tag)
    print(json.dumps({k: r[k] for k in ("cells", "sensitivity", "class_counts")}, ensure_ascii=False, indent=1))


def cmd_backtest(a):
    from aralshield.forecast import predict
    n = predict.backtest_to_db()
    print(f"архив прогнозов тестовых лет: {n} записей")


def cmd_daily(a):
    from aralshield.forecast import predict
    out = predict.issue_daily()
    for f in out:
        print(f"{f['station']:10s} {f['day']} h{f['horizon']}: {f['probability']:.2f} {f['level']}"
              + (" (упрощённый режим)" if f.get("degraded") else ""))


def cmd_notify(a):
    from aralshield.voice import notify
    print(notify.run(dry_run=a.dry_run, hour=a.hour))


def cmd_serve(a):
    import uvicorn
    if a.host not in LOOPBACK:
        # снаружи внутренние методы бота доступны только по ключу, а Host проверяется
        if not os.environ.get("ARALSHIELD_INTERNAL_KEY"):
            raise SystemExit("Для --host вне localhost задайте ARALSHIELD_INTERNAL_KEY (внутренние методы бота).")
        if a.host not in ("0.0.0.0", "::") and not os.environ.get("ARALSHIELD_ALLOWED_HOSTS"):
            os.environ["ARALSHIELD_ALLOWED_HOSTS"] = a.host
    # За обратным прокси (Caddy в docker-compose) реальный IP клиента берётся из X-Forwarded-For,
    # но только от доверенных адресов — иначе лимит запросов был бы общим на всех пользователей.
    trusted = os.environ.get("ARALSHIELD_TRUSTED_PROXIES", "127.0.0.1")
    uvicorn.run("aralshield.api.main:app", host=a.host, port=a.port, reload=False,
                proxy_headers=True, forwarded_allow_ips=trusted)


def cmd_bot(a):
    from aralshield.voice import bot
    bot.main()


def cmd_apikey(a):
    from aralshield import db
    db.init()
    if a.list:
        for k in db.list_api_keys():
            print(f"{k['id']}  {k['owner']:20s} {k['role']:8s} {k['created_at']}  "
                  + ("отозван" if k["revoked"] else "действует"))
        return
    if a.revoke:
        print(f"отозвано ключей: {db.revoke_api_key(a.revoke)}")
        return
    key = db.create_api_key(a.owner, a.role)
    print(f"Ключ API для «{a.owner}» (сохраните, повторно не показывается):\n{key}")


def cmd_all(a):
    for step in (cmd_truth, cmd_eye, cmd_dataset, cmd_train, cmd_shield, cmd_backtest, cmd_daily):
        logging.info("=== %s ===", step.__name__)
        step(a)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aralshield", description="AralShield «Тұз дауылы»")
    p.add_argument("command", choices=["truth", "eye", "dataset", "train", "shield", "backtest", "daily",
                                       "notify", "serve", "bot", "apikey", "all"])
    p.add_argument("--owner", default="partner", help="владелец ключа API (для apikey)")
    p.add_argument("--role", default="partner", choices=["partner", "analyst", "admin"])
    p.add_argument("--list", action="store_true", help="apikey: показать ключи (без секретов)")
    p.add_argument("--revoke", metavar="OWNER_OR_ID", help="apikey: отозвать ключи владельца или по id")
    p.add_argument("--season", default="2025-06-01/2025-09-30", help="период композита Sentinel-2")
    p.add_argument("--tag", default=EYE_TAG, help="метка карты «Глаза» (по умолчанию ARALSHIELD_EYE_TAG)")
    p.add_argument("--per-tile", type=int, default=4, help="сцен на тайл MGRS")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--dry-run", action="store_true", help="рассылка без отправки")
    p.add_argument("--hour", type=int, choices=range(24), metavar="0-23",
                   help="notify: только подписки с этим временем рассылки (вечер ≥12 — с завтрашнего дня)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv=None):
    _utf8_console()  # до argparse: --help в канале или файле не падает на «Тұз»
    a = build_parser().parse_args(argv)
    _setup_logging(a.verbose)
    globals()[f"cmd_{a.command}"](a)


if __name__ == "__main__":
    main()
