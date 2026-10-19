# Eduson Kids — бэклог этапа развёртывания (из аудита безопасности этапа 1)

Пункты, которые нельзя закрыть до появления сервера Eduson. Перед выкаткой пройти все.

- **nginx-шаблон.** Перенести в `deploy/nginx/` шаблон прод-конфига с заголовками
  HSTS, CSP (`script-src 'self'`), `X-Frame-Options: DENY` и
  `proxy_set_header X-Real-IP $remote_addr;` на всех `location`, проксирующих в Django.
  Без X-Real-IP rate-limit ломается: `production.py` читает IP клиента из
  `HTTP_X_REAL_IP` (`RATELIMIT_IP_META_KEY`), а за unix-сокетом `REMOTE_ADDR` пуст.
- **Файл `deploy/nginx/journal-eduson.conf`.** Его ищет
  `apps/knowledge/tests/test_upload_limits_in_sync.py` (вместе с
  `deploy/nginx/local/nginx.conf`). Локальный конфиг уже есть (2026-10-18) и
  проверяется; две параметризации прод-конфига до появления файла пропускаются
  с причиной «прод-шаблон nginx Eduson — этап 4» и включатся сами, как только
  шаблон ляжет в репозиторий. Если конфиг назовём иначе, поправить путь в тесте.
  Шаблон подключает общий `deploy/nginx/snippets/journal-static.conf` (статика
  login/admin/teacher) и держит те же `client_max_body_size` на
  `/api/admin/knowledge/{images,files}` (12m/26m), что и локальный конфиг; CSP —
  то же значение, что в `deploy/nginx/local/nginx.conf`.
- **`REDIS_URL` в проде обязателен.** Без него кэш — LocMemCache, и счётчики
  rate-limit живут отдельно в каждом воркере gunicorn: реальный лимит умножается
  на число воркеров. Брать свободную базу Redis (не `/0`), если Redis общий.
- **Fail-fast на `SECRET_KEY`.** В `config/settings/production.py` требовать явный
  `SECRET_KEY` длиной не меньше 50 символов и падать при старте, если его нет.
  Сейчас `base.py` молча берёт `ADMIN_COOKIE_SECRET`, если `SECRET_KEY` не задан.
- **`DJANGO_SETTINGS_MODULE=config.settings.production`** — всегда на сервере
  (юниты systemd gunicorn/Celery worker/beat, `manage.py` из shell). Иначе
  включатся настройки development: DEBUG, console-почта, открытые CORS-origin'ы Vite.
- **SMTP.** Локально письма с кодом входа печатаются в консоль (`development.py`).
  Для прода нужен SMTP-ящик Eduson и `SMTP_*` в `.env`.
- **PDF и картинки в документации.** Три PDF KOTOKOD удалены: у всех был .md-исходник,
  проверенный grep'ом. Любой новый PDF или скриншот в `docs/` и `journal_django/docs/`
  перед коммитом проверять глазами на реальные данные учеников и инфраструктуры:
  текстовый grep их содержимое не видит.
