"""Общая политика сетевых запросов (разделы 21.2, 22.5).

3 попытки, таймауты (соединение 10 с, чтение 60 с), без повтора на 4xx кроме 429
(с учётом Retry-After), без паузы после последней попытки, необязательный срок
`deadline` (time.monotonic()), после которого повторы прекращаются.
"""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

TRIES = 3
TIMEOUT = (10, 60)          # (соединение, чтение), с
BACKOFF_S = (2, 4)          # паузы между попытками 1→2 и 2→3
RETRY_AFTER_MAX_S = 30      # верхняя граница ожидания по Retry-After


class HTTPGiveUp(RuntimeError):
    """Запрос не удался после всех попыток (или постоянная ошибка 4xx)."""


def _retry_after(r: requests.Response, attempt: int) -> float:
    try:
        return min(float(r.headers.get("Retry-After", "")), RETRY_AFTER_MAX_S)
    except ValueError:
        return min(10.0 * (attempt + 1), RETRY_AFTER_MAX_S)


def get(url: str, params: dict | None = None, *, tries: int = TRIES, timeout=TIMEOUT,
        allow_404: bool = False, deadline: float | None = None) -> requests.Response:
    """GET с повторами. 404 возвращается как есть при allow_404, иначе — исключение."""
    last = None
    for attempt in range(tries):
        if deadline is not None and time.monotonic() > deadline:
            raise HTTPGiveUp(f"истёк срок выполнения: {url}")
        wait = BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 404 and allow_404:
                return r
            if r.status_code == 429:  # лимит запросов
                wait = _retry_after(r, attempt)
                last = "HTTP 429"
                log.warning("%s: HTTP 429, попытка %d/%d", url, attempt + 1, tries)
            elif 400 <= r.status_code < 500:  # постоянная ошибка — не повторяем
                raise HTTPGiveUp(f"HTTP {r.status_code}: {url}")
            else:
                r.raise_for_status()
                return r
        except HTTPGiveUp:
            raise
        except requests.RequestException as e:
            last = type(e).__name__
            log.warning("%s: %s, попытка %d/%d", url, last, attempt + 1, tries)
        if attempt < tries - 1:  # после последней попытки не ждём
            if deadline is not None and time.monotonic() + wait > deadline:
                break
            time.sleep(wait)
    raise HTTPGiveUp(f"не удалось получить {url} ({last})")


def atomic_write_bytes(path, data: bytes) -> None:
    """Запись через временный файл и os.replace: оборванная запись не оставляет «битый» кэш."""
    import os
    from pathlib import Path
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_to_parquet(df, path) -> None:
    import os
    from pathlib import Path
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def is_open_cache(path, period_end, latency_days: float, max_age_days: float = 1.0) -> bool:
    """Кэш «открытого» периода: файл скачан раньше, чем источник мог выдать данные до
    конца периода (period_end + latency), и он старше max_age_days — нужно обновить."""
    from datetime import datetime, timedelta
    from pathlib import Path
    path = Path(path)
    if not path.exists():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    complete_after = datetime.combine(period_end, datetime.min.time()) + timedelta(days=latency_days)
    return mtime < complete_after and datetime.now() - mtime > timedelta(days=max_age_days)
