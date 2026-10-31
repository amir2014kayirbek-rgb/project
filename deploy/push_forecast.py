"""Считает живой выпуск прогноза и отправляет его на сайт (POST /v1/forecasts/import).

Запускается в GitHub Actions (.github/workflows/wake.yml): у бесплатного хостинга общий IP,
и Open-Meteo отвечает ему HTTP 429, а с раннеров GitHub запросы проходят.

    SITE_URL=https://aralshield.onrender.com ARALSHIELD_INTERNAL_KEY=... python deploy/push_forecast.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _default(o):
    return o.item() if hasattr(o, "item") else str(o)


def main() -> int:
    import requests

    from aralshield.forecast import predict

    site = os.environ["SITE_URL"].rstrip("/")
    key = os.environ["ARALSHIELD_INTERNAL_KEY"]
    items = predict.issue_daily(save=False)
    models = sorted({it["model_version"] for it in items})
    print(f"выпуск {items[0]['issue_date']}: {len(items)} записей, модели: {models}")
    body = json.dumps({"items": items}, ensure_ascii=False, default=_default)
    for attempt in range(6):  # сайт мог только проснуться
        try:
            r = requests.post(f"{site}/v1/forecasts/import", data=body.encode(), timeout=120,
                              headers={"Content-Type": "application/json", "X-Internal-Key": key})
            if r.ok:
                print("отправлено:", r.json())
                return 0
            print(f"HTTP {r.status_code}: {r.text[:300]}")
            if r.status_code in (401, 403, 422):
                return 1
        except requests.RequestException as e:
            print("сеть:", e)
        time.sleep(30)
    return 1


if __name__ == "__main__":
    sys.exit(main())
