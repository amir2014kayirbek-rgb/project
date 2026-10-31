"""Запуск на бесплатном хостинге без постоянного диска (Render Free и аналоги).

Что делает:
1. Собирает базу SQLite из данных, лежащих в репозитории: каталог пылевых дней
   (data/clean/dust_events.parquet) и архив прогнозов тестовых лет (backtest).
2. Запускает API на порту $PORT.
3. В фоне выпускает свежий прогноз (Open-Meteo) и затем раз в час проверяет,
   не устарел ли он — так прогноз обновляется сам после «пробуждения» сервиса.

Подписки бота и обратная связь на таком хостинге не сохраняются между перезапусками:
это режим демонстрации. Для постоянной работы — VPS и docker compose (docs/HOSTING.md).
"""
from __future__ import annotations

import logging
import os
import secrets
import sys
import threading
import time
from pathlib import Path

# Скрипт запускается как файл (python deploy/render_start.py) — пакет лежит уровнем выше
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
log = logging.getLogger("render_start")


def _env_defaults() -> None:
    host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")  # Render задаёт имя сервиса сам
    if host:
        extra = os.environ.get("ARALSHIELD_ALLOWED_HOSTS", "")
        if host not in extra.split(","):
            os.environ["ARALSHIELD_ALLOWED_HOSTS"] = ",".join(x for x in (extra, host) if x)
    # Внутренние методы бота здесь не нужны, но без ключа API не слушает внешний интерфейс
    os.environ.setdefault("ARALSHIELD_INTERNAL_KEY", secrets.token_urlsafe(32))
    os.environ.setdefault("ARALSHIELD_TRUSTED_PROXIES", "*")  # перед сервисом прокси хостинга


ARCHIVE = Path(__file__).resolve().parent.parent / "data" / "archive" / "backtest_forecasts.parquet"


def export_archive() -> None:
    """Сохраняет архив прогнозов тестовых лет из локальной базы в файл репозитория
    (выполняется на машине разработчика после `cli backtest`)."""
    import pandas as pd

    from aralshield import db
    df = pd.DataFrame(db.rows("SELECT * FROM forecasts WHERE mode='backtest'")).drop(columns=["id"])
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(ARCHIVE, index=False)
    print(f"{ARCHIVE}: {len(df)} записей")


def _bootstrap_db() -> None:
    import pandas as pd

    from aralshield import db
    from aralshield.config import CLEAN
    from aralshield.forecast import predict

    db.init()
    if not db.rows("SELECT 1 FROM dust_events LIMIT 1"):
        ev = pd.read_parquet(CLEAN / "dust_events.parquet")
        ev["date"] = pd.to_datetime(ev["date"])
        log.info("каталог событий: %d станционных суток", db.load_events(ev))
    if not db.rows("SELECT 1 FROM forecasts WHERE mode='backtest' LIMIT 1"):
        if ARCHIVE.exists():  # готовый архив из репозитория — без загрузки ERA5 при каждом запуске
            items = pd.read_parquet(ARCHIVE).astype(object).where(lambda d: d.notna(), None).to_dict("records")
            db.save_forecasts(items, replace_mode="backtest")
            log.info("архив прогнозов тестовых лет из файла: %d записей", len(items))
        else:
            log.info("архив прогнозов тестовых лет: %d записей", predict.backtest_to_db())


def _refresh_loop() -> None:
    from aralshield import db
    from aralshield.forecast import predict

    try:  # база собирается в фоне: порт открыт сразу, хостинг не считает запуск зависшим
        _bootstrap_db()
    except Exception:
        log.exception("сборка базы не удалась")
    while True:
        try:
            last = db.rows("SELECT MAX(issue_date) AS d FROM forecasts WHERE mode='live'")[0]["d"]
            if last != predict.local_today().isoformat():
                n = len(predict.issue_daily())
                log.info("выпущен прогноз на %s: %d записей", predict.local_today(), n)
        except Exception:  # сеть или Open-Meteo недоступны — повторим через час
            log.exception("выпуск прогноза не удался")
        time.sleep(3600)


def main() -> None:
    if "--export" in sys.argv:
        export_archive()
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _env_defaults()
    from aralshield import db
    db.init()  # таблицы — сразу, данные — в фоне
    threading.Thread(target=_refresh_loop, daemon=True, name="refresh").start()
    from aralshield.cli import main as cli
    cli(["serve", "--host", "0.0.0.0", "--port", os.environ.get("PORT", "10000")])


if __name__ == "__main__":
    main()
