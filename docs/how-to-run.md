# Как запускать проект

Установка с нуля и первая учётка — в [`README.md`](../README.md#быстрый-старт-локально).
Здесь — повседневный запуск и порты стенда.

## Повседневный запуск

```bash
cd journal_django
../.venv/Scripts/python manage.py runserver 127.0.0.1:8100
```

Затем в PowerShell из корня репозитория:

```powershell
./deploy/nginx/local/start-local-nginx.ps1          # запуск
./deploy/nginx/local/start-local-nginx.ps1 -Reload  # перечитать конфиг
./deploy/nginx/local/start-local-nginx.ps1 -Stop    # остановить
```

Открыть <http://127.0.0.1:8180/> → страница входа. После входа:
- преподаватель → `/teacher`;
- администратор / менеджер → `/admin`.

Без nginx страницы SPA не открываются: runserver отдаёт только API.
Используйте именно `127.0.0.1`, а не `localhost` — иначе часть инструментов уходит на IPv6.

## Порты

| Что | Адрес |
|---|---|
| Django runserver | `127.0.0.1:8100` |
| nginx (точка входа) | `127.0.0.1:8180` |
| сток отчётов CSP | `127.0.0.1:19976` |
| Vite admin (`npm run dev`) | `5183` |
| Vite teacher (`npm run dev`) | `5184` |

## Пересборка фронтенда

После правки React-исходников:

```bash
cd journal_django/frontend/admin-src && npm run build
cd ../teacher-src && npm run build
```

Собранные `admin-dist/` и `teacher-dist/` в git не хранятся.

## Боевое развёртывание

Пока не выполнялось. Требования к серверу собраны в
[`docs/eduson/stage4-deploy-backlog.md`](eduson/stage4-deploy-backlog.md):
`DJANGO_SETTINGS_MODULE=config.settings.production`, обязательный Redis, nginx с заголовками
безопасности, SMTP для кодов входа.
