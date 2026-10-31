"""Планировщик для сервера: ежедневный выпуск прогноза и рассылка по местному времени (UTC+5).

Запускается отдельным контейнером (см. docker-compose.yml). Делает то же, что
scripts/daily.ps1 на Windows: `daily` → `notify`, а вечером — рассылку тем,
кто выбрал вечернее время (чабаны, SMS накануне).

Переменные окружения:
    ARALSHIELD_DAILY_AT   время выпуска, по умолчанию 05:30
    ARALSHIELD_EVENING_AT время вечерней рассылки, по умолчанию 18:00 (пусто — выключено)
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

LOCAL = timezone(timedelta(hours=5))
log = logging.getLogger("scheduler")


def _parse(hhmm: str) -> tuple[int, int]:
    h, m = hhmm.strip().split(":")
    return int(h), int(m)


def _run(*args: str) -> int:
    cmd = [sys.executable, "-m", "aralshield.cli", *args]
    log.info("запуск: %s", " ".join(cmd))
    code = subprocess.call(cmd)
    if code:
        log.error("шаг %s завершился с кодом %d", args[0], code)
    return code


def _next(now: datetime, at: tuple[int, int]) -> datetime:
    t = now.replace(hour=at[0], minute=at[1], second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    jobs = {"daily": _parse(os.environ.get("ARALSHIELD_DAILY_AT", "05:30"))}
    evening = os.environ.get("ARALSHIELD_EVENING_AT", "18:00").strip()
    if evening:
        jobs["evening"] = _parse(evening)
    if "--now" in sys.argv:  # разовый выпуск при старте (удобно после развёртывания)
        if _run("daily") == 0:
            _run("notify")
    while True:
        now = datetime.now(LOCAL)
        name, when = min(((n, _next(now, at)) for n, at in jobs.items()), key=lambda x: x[1])
        log.info("следующая задача «%s» в %s (UTC+5)", name, when.strftime("%Y-%m-%d %H:%M"))
        time.sleep(max(1.0, (when - datetime.now(LOCAL)).total_seconds()))
        if name == "daily":
            if _run("daily") == 0:  # без свежего прогноза не рассылаем
                _run("notify")
        else:
            _run("notify", "--hour", str(jobs["evening"][0]))


if __name__ == "__main__":
    main()
