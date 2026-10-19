"""
Development settings for journal_django project (форк Eduson).

Extends base.py. Activate via:
  DJANGO_SETTINGS_MODULE=config.settings.development
(manage.py sets this as default)
"""
from .base import *  # noqa: F401, F403

DEBUG = True

# In development accept any host
ALLOWED_HOSTS = ['*']

# JWT-cookie без Secure в dev (HTTP локально).
# Переопределяем только нужный ключ; остальные наследуются из base.py.
SIMPLE_JWT = {
    **SIMPLE_JWT,  # noqa: F405  — импортировано через from .base import *
    'AUTH_COOKIE_SECURE': False,
}

# ---------------------------------------------------------------------------
# Email в dev — console backend (Фаза 2)
# ---------------------------------------------------------------------------
# У Eduson локально нет SMTP: без console backend вход с email-OTP упал бы на
# отправке письма. Код входа печатается в консоль runserver.
# В prod переопределяется на smtp.EmailBackend (production.py); тесты pytest-django
# подменяет на locmem сам.
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# ---------------------------------------------------------------------------
# Порты разработки Eduson
# ---------------------------------------------------------------------------
# Форк Eduson живёт на одной машине с KOTOKOD (runserver :8000, Vite :5173/:5174,
# nginx :8080), поэтому у него свой набор портов, чтобы оба стенда работали
# одновременно: runserver :8100, admin Vite :5183, teacher Vite :5184,
# локальный nginx :8180 (deploy/nginx/local/, CSP-сток :19976).

# CORS: Vite dev-серверы admin и teacher.
CORS_ALLOWED_ORIGINS = [
    'http://localhost:5183',
    'http://127.0.0.1:5183',
    'http://localhost:5184',
    'http://127.0.0.1:5184',
]
CORS_ALLOW_CREDENTIALS = True

# CSRF: runserver :8100. Через proxy Vite (changeOrigin:false) Host и Origin
# совпадают, и встроенный same-origin shortcut Django срабатывает сам; явный
# origin нужен, когда перед runserver стоит прокси: Origin браузера несёт порт
# прокси, а не runserver. В prod CSRF_TRUSTED_ORIGINS — из env.
CSRF_TRUSTED_ORIGINS = [
    'http://localhost:8100',
    'http://127.0.0.1:8100',
    # Локальный nginx Eduson (deploy/nginx/local/nginx.conf): SPA и API на :8180.
    'http://localhost:8180',
    'http://127.0.0.1:8180',
]

# ---------------------------------------------------------------------------
# Logging — simple console output in dev
# ---------------------------------------------------------------------------
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
}
