# AralShield «Тұз дауылы» — образ для сервера (API + веб-панель + бот + планировщик).
# Сборка: docker build -t aralshield .

# ---- 1. Сборка веб-панели (React + TypeScript)
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. Python-приложение
FROM python:3.10-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Qyzylorda \
    ARALSHIELD_DATA=/app/data
# libgomp1 нужен LightGBM; tzdata — для расписания по местному времени
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 tzdata ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY aralshield/ ./aralshield/
COPY deploy/ ./deploy/
COPY --from=web /web/dist ./frontend/dist
# Готовые результаты конвейера из репозитория: панель работает сразу после развёртывания.
# В docker compose каталоги перекрываются томами с сервера (см. docs/HOSTING.md).
COPY data/ ./data/
COPY models/ ./models/
COPY reports/ ./reports/

RUN mkdir -p /app/data /app/models /app/reports \
 && useradd --create-home --uid 1000 aral \
 && chown -R aral:aral /app
USER aral

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD curl -fsS http://127.0.0.1:8000/v1/settlements > /dev/null || exit 1
CMD ["python", "-m", "aralshield.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
