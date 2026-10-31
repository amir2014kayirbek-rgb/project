# Хостинг AralShield

AralShield — это не статический сайт: работает API (FastAPI) с базой SQLite, раз в сутки
выпускается прогноз, Telegram-бот работает постоянно. Поэтому нужен **сервер (VPS)**,
а не GitHub Pages или Vercel. Всё упаковано в Docker, запуск — одной командой.

## 1. Какой сервер взять

| Параметр | Минимум | Комфортно |
|---|---|---|
| CPU | 2 vCPU | 4 vCPU |
| RAM | 2 ГБ (если данные готовите на своём компьютере) | 4 ГБ (если весь конвейер считается на сервере) |
| Диск | 20 ГБ SSD | 40 ГБ |
| ОС | Ubuntu 22.04 / 24.04 | — |

**Где.** Подписчики бота — это персональные данные (идентификатор чата), а закон РК
«О персональных данных» требует хранить их на территории Казахстана (раздел 27 отчёта).
Для пилота с реальными пользователями берите VPS в Казахстане — например, PS Cloud (ps.kz),
Hoster.kz или Kazteleport. Для демонстрации на хакатоне подойдёт любой VPS (Hetzner,
DigitalOcean, Timeweb и т. п.) — стоимость от ~5 $ в месяц.

**Домен** не обязателен, но с ним сразу будет HTTPS (сертификат Let's Encrypt выпускается сам).

## 2. Подготовка сервера

```bash
# на сервере, под пользователем с sudo
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
git clone https://github.com/<ваш-логин>/<репозиторий>.git aralshield
cd aralshield
cp .env.example .env
nano .env        # заполнить переменные, см. раздел 3
```

## 3. Переменные окружения (`.env`)

| Переменная | Зачем | Пример |
|---|---|---|
| `DOMAIN` | домен для HTTPS (A-запись должна указывать на IP сервера) | `aralshield.kz` |
| `TELEGRAM_BOT_TOKEN` | токен бота от @BotFather (без него бот не запускается) | `123456:ABC…` |
| `ARALSHIELD_INTERNAL_KEY` | **обязателен на сервере**: ключ внутренних методов API, без него сервис не стартует вне localhost | `openssl rand -hex 32` |
| `ARALSHIELD_HASH_PEPPER` | секрет для обезличивания ответов «Было пыльно?» | `openssl rand -hex 32` |
| `ARALSHIELD_ALLOWED_HOSTS` | **обязателен**: имя домена (и IP, если заходите по IP) — запросы с другим `Host` получают 400 | `aralshield.kz` или `203.0.113.10` |
| `SMS_GATEWAY_URL`, `SMS_GATEWAY_KEY` | SMS-провайдер; без них SMS пишутся в `data/outbox` | — |
| `ARALSHIELD_DAILY_AT` | время выпуска прогноза (UTC+5) | `05:30` |
| `ARALSHIELD_EVENING_AT` | вечерняя рассылка (SMS чабанам накануне); пусто — выключено | `18:00` |

Файл `.env` в git не попадает — секреты храните только на сервере.

## 4. Данные и модели

Сервису нужны каталоги `data/`, `models/`, `reports/` (в git их нет — они большие и
пересчитываются). Два способа:

**А. Перенести готовые с компьютера (быстро, 2–3 минуты).** На своём компьютере, где уже
отработал конвейер, в PowerShell из папки проекта:

```powershell
tar -czf aralshield-data.tgz --exclude=data/raw --exclude=data/outbox data models reports
scp aralshield-data.tgz user@IP_СЕРВЕРА:~/aralshield/
```

(`data/raw` — кэш загрузок, переносить не нужно.) Затем на сервере:

```bash
cd ~/aralshield && tar -xzf aralshield-data.tgz && rm aralshield-data.tgz
```

**Б. Посчитать всё на сервере (40–60 минут, нужно 4 ГБ RAM).**

```bash
docker compose build
docker compose run --rm api python -m aralshield.cli all
```

В обоих случаях дайте контейнеру права на запись (он работает от пользователя с uid 1000):

```bash
mkdir -p data models reports && sudo chown -R 1000:1000 data models reports
```

## 5. Запуск

```bash
docker compose up -d --build                  # API + панель + планировщик + HTTPS
docker compose --profile bot up -d            # плюс Telegram-бот (нужен TELEGRAM_BOT_TOKEN)
docker compose run --rm scheduler python deploy/scheduler.py --now &   # разовый выпуск прямо сейчас (необязательно)
```

Проверка:

```bash
curl -s https://ВАШ_ДОМЕН/v1/health
docker compose ps
docker compose logs -f api scheduler bot
```

Панель: `https://ВАШ_ДОМЕН/`, документация API: `https://ВАШ_ДОМЕН/docs`.

**Без домена.** В `docker-compose.yml` у сервиса `api` замените `expose: ["8000"]` на
`ports: ["8000:8000"]`, не запускайте `caddy` (`docker compose up -d api scheduler`)
и откройте `http://IP_СЕРВЕРА:8000`. Это без шифрования — только для демонстрации.

## 6. Что работает по расписанию

Контейнер `scheduler` каждый день в 05:30 (UTC+5) выполняет `daily` (свежий прогноз
погоды → признаки → модель → база), затем `notify` (рассылка подписчикам); в 18:00 —
вечернюю рассылку. Если выпуск не удался, рассылка не запускается, а `/v1/health`
показывает статус `stale`/`degraded`.

## 7. Повседневные операции

```bash
# ключ API для партнёра (лесхоз, акимат) — нужен для выгрузок GeoJSON/KML/Shapefile
docker compose exec api python -m aralshield.cli apikey --owner "Лесхоз Аральского района"

# обновление после git push
git pull && docker compose up -d --build

# резервная копия базы (подписчики, прогнозы) — раз в сутки через cron
docker compose exec api python -c "import sqlite3; s=sqlite3.connect('/app/data/aralshield.sqlite'); d=sqlite3.connect('/app/data/backup.sqlite'); s.backup(d)"
```

Переобучение модели (раз в год, после пылевого сезона, раздел 23.3):

```bash
docker compose run --rm api sh -c "python -m aralshield.cli truth && python -m aralshield.cli dataset && python -m aralshield.cli train && python -m aralshield.cli backtest"
```

## 8. Быстрое демо с ноутбука без сервера

Если сервер ещё не готов, а ссылку надо показать жюри: запустите панель локально
(`scripts\serve.ps1`) и откройте туннель Cloudflare:

```powershell
winget install Cloudflare.cloudflared
cloudflared tunnel --url http://127.0.0.1:8000
```

Он выдаст адрес вида `https://….trycloudflare.com`, который работает, пока ноутбук включён.

## 9. Частые проблемы

| Симптом | Причина и решение |
|---|---|
| `PermissionError` в логах `api` | не выданы права на тома — `sudo chown -R 1000:1000 data models reports` |
| Caddy не выпускает сертификат | A-запись домена не указывает на сервер или закрыты порты 80/443 в файрволе |
| `/v1/forecasts` пустой | не перенесены данные или ещё не было выпуска — запустите `scheduler.py --now` |
| Бот молчит | нет `TELEGRAM_BOT_TOKEN` или бот запущен без `--profile bot` |
| Прогноз помечен «упрощённый режим» | нет свежих данных (Open-Meteo/NOAA недоступны) — см. `degraded_reasons` в `/v1/forecasts` |
