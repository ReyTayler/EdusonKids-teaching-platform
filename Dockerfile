# syntax=docker/dockerfile:1
#
# Образы Eduson Kids. Одна сборка — три этапа (multi-stage), два итоговых образа:
#
#   front — собирает оба SPA (Node.js нужен ТОЛЬКО здесь, в итоговые образы не попадает);
#   app   — Django + gunicorn: Python 3.11 и зависимости, без Node.js и без исходников фронта;
#   web   — nginx: раздаёт собранный фронт и проксирует /api в app.
#
# Собирается из корня репозитория (контекст = platform/):
#   docker build --target app -t eduson-app .
#   docker build --target web -t eduson-web .
# Обычно — через compose: docker compose -f deploy/docker/compose.staging.yml build
#
# Порядок слоёв: сначала то, что меняется редко (зависимости), потом код. Docker
# переиспользует закешированные слои, пока их вход не изменился, поэтому правка
# кода не переустанавливает пакеты.

# ---------------------------------------------------------------------------
# Этап 1: сборка фронта
# ---------------------------------------------------------------------------
FROM node:24-alpine AS front
WORKDIR /src/frontend

# Сначала только манифесты: слой с npm ci пересобирается лишь при смене зависимостей.
COPY journal_django/frontend/admin-src/package.json journal_django/frontend/admin-src/package-lock.json admin-src/
COPY journal_django/frontend/teacher-src/package.json journal_django/frontend/teacher-src/package-lock.json teacher-src/
RUN cd admin-src && npm ci --no-audit --no-fund \
 && cd ../teacher-src && npm ci --no-audit --no-fund

# Теперь исходники. Admin — первым: teacher берёт общий код из admin-src (alias @shared).
COPY journal_django/frontend/ ./
RUN cd admin-src && npm run build \
 && cd ../teacher-src && npm run build

# ---------------------------------------------------------------------------
# Этап 2: приложение (Django + gunicorn)
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS app

# Не писать .pyc, не буферизовать вывод (логи сразу видны в docker logs).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    TZ=Europe/Moscow

# tzdata — чтобы TZ=Europe/Moscow реально действовал (статус урока считается по МСК).
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

# Отдельный непривилегированный пользователь: процесс в контейнере не root.
RUN groupadd --system --gid 1000 eduson \
 && useradd --system --uid 1000 --gid eduson --home-dir /app --shell /usr/sbin/nologin eduson

WORKDIR /app
COPY journal_django/requirements.txt .
RUN pip install -r requirements.txt

# Код бэкенда. Фронт-исходники и node_modules сюда не нужны (.dockerignore их режет).
COPY --chown=eduson:eduson journal_django/ ./

# Каталог файлов базы знаний (в compose на него монтируется том).
RUN mkdir -p /media && chown eduson:eduson /media

USER eduson
EXPOSE 8000

# 1 процесс + 2 потока — минимум для staging. Слушаем TCP внутри сети compose:
# наружу порт не публикуется, снаружи доступен только nginx.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "1", "--threads", "2", \
     "--timeout", "60", \
     "--access-logfile", "-", "--error-logfile", "-"]

# ---------------------------------------------------------------------------
# Этап 3: nginx со статикой фронта
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS web

# openssl — выпустить самоподписанный сертификат при первом старте (staging без домена).
RUN apk add --no-cache openssl

# Тот же каталог, что и в приложении (/app = journal_django): общий сниппет статики
# проекта (deploy/nginx/snippets/journal-static.conf) опирается на $app_root.
COPY journal_django/frontend/login /app/frontend/login
COPY journal_django/frontend/fonts /app/frontend/fonts
COPY journal_django/frontend/favicon.ico journal_django/frontend/favicon.svg /app/frontend/
COPY --from=front /src/frontend/admin-dist /app/frontend/admin-dist
COPY --from=front /src/frontend/teacher-dist /app/frontend/teacher-dist

COPY deploy/nginx/snippets/journal-static.conf /etc/nginx/snippets/journal-static.conf
COPY deploy/docker/nginx/proxy-headers.conf /etc/nginx/snippets/proxy-headers.conf
COPY deploy/docker/nginx/staging.conf /etc/nginx/conf.d/default.conf
COPY deploy/docker/nginx/10-self-signed-cert.sh /docker-entrypoint.d/10-self-signed-cert.sh
RUN chmod +x /docker-entrypoint.d/10-self-signed-cert.sh
