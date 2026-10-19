"""
Production settings for journal_django project.

Extends base.py. Activate via:
  DJANGO_SETTINGS_MODULE=config.settings.production
"""
import environ

from .base import *  # noqa: F401, F403

env = environ.Env()

DEBUG = False

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=[])

# ---------------------------------------------------------------------------
# HTTPS / Secure headers
# ---------------------------------------------------------------------------
SECURE_SSL_REDIRECT = True
# HSTS НЕ эмитим из Django: единый источник — nginx (server-уровень), он покрывает
# и HTML-страницы (их Django не отдаёт), и проксируемые /api. Здесь оставить выключенным,
# иначе на /api-ответах было бы два Strict-Transport-Security. См. конфиг nginx в deploy/ (появится на этапе развёртывания Eduson).
SECURE_HSTS_SECONDS = 0
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# SPA читает CSRF-токен из cookie (js-доступ обязателен).
CSRF_COOKIE_HTTPONLY = False
SECURE_CONTENT_TYPE_NOSNIFF = True
# Выравниваем с nginx (no-referrer). Django по умолчанию шлёт 'same-origin' —
# на проксируемых /api-ответах это конфликтовало бы со значением nginx. helmet
# в Express ставил no-referrer; держим одно значение в обоих слоях.
SECURE_REFERRER_POLICY = 'no-referrer'

# ---------------------------------------------------------------------------
# CSRF trusted origins (SPA ↔ API — единый origin через nginx)
# ---------------------------------------------------------------------------
# Укажите реальный домен в CSRF_TRUSTED_ORIGINS_LIST в .env, например:
#   CSRF_TRUSTED_ORIGINS_LIST=https://<домен Eduson>
# Пустой список → SPA не пройдёт CSRF на кросс-ориджин (безопасный дефолт).
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS_LIST', default=[])

# ---------------------------------------------------------------------------
# django-ratelimit — IP source
# ---------------------------------------------------------------------------
# gunicorn слушает unix-сокет (см. deploy/gunicorn.conf.py): у такого соединения
# нет TCP-peer, поэтому REMOTE_ADDR приходит пустым и django-ratelimit падает
# с ImproperlyConfigured на первом же /api/auth/login. nginx подставляет
# реальный IP клиента в X-Real-IP (конфиг nginx в deploy/), читаем
# оттуда. В dev (TCP runserver/local nginx) REMOTE_ADDR и так непустой —
# настройка нужна только там, где перед Django стоит unix-сокет (прод).
RATELIMIT_IP_META_KEY = 'HTTP_X_REAL_IP'

# ---------------------------------------------------------------------------
# CORS — production origins from env
# ---------------------------------------------------------------------------
# Читаем ту же переменную, что и Express (server.js: env.CORS_ORIGINS) — один
# источник whitelist на оба бэкенда во время сосуществования. Fallback на
# CORS_ALLOWED_ORIGINS для совместимости. Пусто → cross-origin запрещён (SPA
# отдаётся тем же origin, что и API, — безопасный дефолт, как в server.js).
CORS_ALLOWED_ORIGINS = env.list('CORS_ORIGINS', default=None) or env.list(
    'CORS_ALLOWED_ORIGINS', default=[]
)
CORS_ALLOW_CREDENTIALS = True

# ---------------------------------------------------------------------------
# Email — SMTP backend (Фаза 2)
# ---------------------------------------------------------------------------
# Креды и хост берутся из EMAIL_* в base.py (маппинг SMTP_* env-переменных).
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'

# ---------------------------------------------------------------------------
# JWT-cookie — Secure обязателен на HTTPS
# ---------------------------------------------------------------------------
SIMPLE_JWT = {
    **SIMPLE_JWT,  # noqa: F405  — импортировано через from .base import *
    'AUTH_COOKIE_SECURE': True,
}

# ---------------------------------------------------------------------------
# Logging — structured JSON-like output for prod
# ---------------------------------------------------------------------------
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
}
