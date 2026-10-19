"""
Base Django settings for journal_django project.

Shared across all environments. Do not import this directly —
use development.py or production.py.
"""
from datetime import timedelta
from pathlib import Path

import environ

try:
    from celery.schedules import crontab
except ImportError:
    # celery не установлен (напр. локальная разработка на Windows) — см. тот же
    # приём в config/__init__.py. Расписания на базе crontab ниже просто не
    # добавляются в CELERY_BEAT_SCHEDULE; beat всё равно не запускают без Celery.
    crontab = None

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# BASE_DIR = .../journal_django/
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Корень репозитория (platform/): на уровень выше journal_django/
REPO_ROOT = BASE_DIR.parent

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
env = environ.Env(
    DEBUG=(bool, False),
    SECRET_KEY=(str, ''),
    ADMIN_COOKIE_SECRET=(str, ''),
    ALLOWED_HOSTS=(list, []),
    PG_POOL_MAX=(int, 20),
    # SMTP для email-OTP (раздел 07)
    SMTP_HOST=(str, ''),
    SMTP_PORT=(int, 465),
    SMTP_USER=(str, ''),
    SMTP_PASS=(str, ''),
    SMTP_FROM=(str, ''),
    # Telegram-уведомления (спека 2026-08-03)
    TELEGRAM_BOT_TOKEN=(str, ''),
    TELEGRAM_GENERAL_CHAT_ID=(int, 0),
    BOT_SERVICE_TOKEN=(str, ''),
    NOTIFICATIONS_HISTORY_LIMIT=(int, 200),
    # Публичный адрес журнала (https://<домен>) — для ссылок в письмах.
    PUBLIC_URL=(str, ''),
    # Гард оплат: запрет отмечать ученика без оплаченных уроков (наследие KOTOKOD).
    PAYMENT_BALANCE_GUARD=(bool, False),
)

# .env — в корне репозитория форка (platform/.env), на уровень выше journal_django/.
environ.Env.read_env(REPO_ROOT / '.env')

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
# TODO: в prod обязателен отдельный SECRET_KEY в окружении (128+ символов энтропии).
# Fallback на ADMIN_COOKIE_SECRET нужен только для dev, пока не задан свой SECRET_KEY.
_secret_key_raw = env('SECRET_KEY', default='') or env('DJANGO_SECRET_KEY', default='')
SECRET_KEY = _secret_key_raw or env('ADMIN_COOKIE_SECRET')

# ADMIN_COOKIE_SECRET остаётся как fallback для SECRET_KEY в dev.
# После Фазы 2 _secret() в services.py удалена — email-OTP challenge подписывается
# стандартным Signer на SECRET_KEY (через TimestampSigner в twofa.py).
ADMIN_COOKIE_SECRET: str = env('ADMIN_COOKIE_SECRET')

# SMTP (раздел 07 — email-OTP). Оставляем как источник для маппинга ниже.
SMTP_HOST: str = env('SMTP_HOST')
SMTP_PORT: int = env('SMTP_PORT')
SMTP_USER: str = env('SMTP_USER')
SMTP_PASS: str = env('SMTP_PASS')
SMTP_FROM: str = env('SMTP_FROM')

# ---------------------------------------------------------------------------
# Email — django.core.mail (Фаза 2)
# Маппинг существующих SMTP_* переменных на стандартные Django EMAIL_*.
# Транспорт (EMAIL_BACKEND) переопределяется в development.py / production.py.
# ---------------------------------------------------------------------------
EMAIL_HOST: str = SMTP_HOST
EMAIL_PORT: int = SMTP_PORT
EMAIL_HOST_USER: str = SMTP_USER
EMAIL_HOST_PASSWORD: str = SMTP_PASS
DEFAULT_FROM_EMAIL: str = SMTP_FROM
SERVER_EMAIL: str = SMTP_FROM

# port=465 → SSL (как nodemailer secure=true); иначе STARTTLS.
# Зеркало логики прежнего mailer.py (smtplib).
if SMTP_PORT == 465:
    EMAIL_USE_SSL: bool = True
    EMAIL_USE_TLS: bool = False
else:
    EMAIL_USE_SSL: bool = False
    EMAIL_USE_TLS: bool = True

# Таймаут сокета SMTP, сек. Django по умолчанию ждёт бесконечно: зависший
# сервер держал бы диспетчер очереди (и строку под FOR UPDATE) до перезапуска
# воркера — встали бы и письма, и Telegram. Таймаут = временная ошибка, повтор.
EMAIL_TIMEOUT: int = 20

# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------
# django.contrib.auth и contenttypes включены — они нужны AbstractUser (accounts.Account)
# и стандартной Permission-системе Django.
# sessions НЕ включаем — аутентификация через JWT HttpOnly-cookie (CookieJWTAuthentication).
# token_blacklist НЕ включаем — отзыв через token_version (см. architecture_v2.md).
INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'rest_framework',
    'corsheaders',
    'pgtrigger',
    'pghistory',
    'apps.core',
    'apps.auth_app',
    'apps.groups',
    'apps.teachers',
    'apps.directions',
    'apps.discounts',
    'apps.audit',
    'apps.students',
    'apps.memberships',
    'apps.payments',
    'apps.finances',
    'apps.lessons',
    'apps.renewals',
    'apps.payroll',
    'apps.dashboard',
    'apps.accounts',
    'apps.teacher_spa',
    'apps.scheduling',
    'apps.extra_lessons',
    'apps.feedback',
    'apps.changelog',
    'apps.sync',
    'apps.reports',
    'apps.notifications',
    'django.contrib.postgres',
    'apps.knowledge',
    'apps.taskboard',
    'apps.eduson_import',
]

# SessionMiddleware и AuthenticationMiddleware убраны:
# сессии не используются, аутентификация — JWT через CookieJWTAuthentication.
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'apps.changelog.middleware.ChangelogMiddleware',
]

# ---------------------------------------------------------------------------
# django-pghistory — журнал изменений (apps.changelog)
# Контекст открывается только на мутирующих методах: GET-запросы не создают
# записей в pghistory_context.
# ---------------------------------------------------------------------------
PGHISTORY_MIDDLEWARE_METHODS = ('POST', 'PUT', 'PATCH', 'DELETE')

ROOT_URLCONF = 'config.urls'

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASES = {
    'default': env.db('DATABASE_URL'),
}
# Переиспользуем соединение между запросами: открывать новое к PostgreSQL на
# каждый запрос — лишняя задержка (TCP + аутентификация) и лишняя нагрузка на
# сервер БД, а запросов у нас много и они короткие.
#
# Прежнее значение 0 стояло под комментарий «пусть существующий pg-пул
# разруливает» — тот пул жил в Node-бэкенде (services/db.js), а он удалён вместе
# с Express. Комментарий пережил свою причину.
#
# CONN_HEALTH_CHECKS обязателен в паре: без него переиспользованное соединение,
# оборванное со стороны БД (рестарт PostgreSQL, таймаут), даст ошибку на первом
# же запросе вместо переподключения.
DATABASES['default']['CONN_MAX_AGE'] = 60
DATABASES['default']['CONN_HEALTH_CHECKS'] = True

# ---------------------------------------------------------------------------
# Cache — Redis (django-redis) при заданном REDIS_URL, иначе локальный in-memory.
# Кэширует дорогой снимок «Реестра куратора» (apps/dashboard/registry_service).
#
# GRACEFUL DEGRADATION (важно для локальной разработки на Windows):
#   • REDIS_URL не задан (dev/тесты) → LocMemCache — пакет redis НЕ нужен, ничего
#     не поднимаем; путь django_redis активируется ТОЛЬКО в проде (см. deploy/).
#   • Redis временно недоступен в проде → IGNORE_EXCEPTIONS=True: get/set «мимо»
#     (кэш-промах), запрос не падает.
# Cache — оптимизация, не источник правды: registry_service всё равно оборачивает
# доступ в try/except и падает обратно на синхронный расчёт.
# ---------------------------------------------------------------------------
REDIS_URL: str = env('REDIS_URL', default='')

if REDIS_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': REDIS_URL,
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.DefaultClient',
                'IGNORE_EXCEPTIONS': True,  # Redis down → кэш-промах, не 500
            },
            # Свой префикс: Redis может быть общим с KOTOKOD на одной машине.
            'KEY_PREFIX': 'eduson',
        },
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'eduson-locmem',
        },
    }

# ---------------------------------------------------------------------------
# Celery (Фаза 3, спека 2026-07-11) — асинхронный прогрев кэша реестра.
#
# Broker/result — Redis (тот же REDIS_URL). beat_schedule раз в 60с обновляет
# снимок «Реестра куратора», держа кэш (TTL 120с) тёплым в проде.
#
# GRACEFUL DEGRADATION: без REDIS_URL (dev) TASK_ALWAYS_EAGER=True — любые .delay()
# выполняются синхронно, без брокера/воркера. Воркер/beat в dev НЕ запускают;
# приложение работает и без них (registry_service считает снимок синхронно).
# Локально пакет celery может быть НЕ установлен — config/__init__.py импортирует
# celery-app под try/except (Django стартует без Celery). Юниты — deploy/systemd/.
# ---------------------------------------------------------------------------
# База Redis №2: KOTOKOD на той же машине занимает /0 — очереди не должны смешиваться.
CELERY_BROKER_URL = REDIS_URL or 'redis://localhost:6379/2'
# cache+memory:// — in-process backend без внешних зависимостей. Раньше здесь тоже
# был 'redis://localhost:6379/0' — не проблема, пока ничего не читало eager-результат
# из backend'а. apps.sync.views.SyncStatusView — первый потребитель, которому это
# нужно и в dev/тестах (REDIS_URL не задан, Redis намеренно не поднят локально).
CELERY_RESULT_BACKEND = REDIS_URL or 'cache+memory://'
CELERY_TIMEZONE = 'Europe/Moscow'
CELERY_ENABLE_UTC = False
CELERY_TASK_ALWAYS_EAGER = not REDIS_URL
# В eager-режиме результат по умолчанию НЕ кладётся в result backend — доступен
# только напрямую из возврата .delay(). SyncStatusView всегда читает через
# AsyncResult(task_id) из backend'а, поэтому без этой опции dev/тесты не работали бы.
CELERY_TASK_STORE_EAGER_RESULT = True
# Очереди (спека 2026-07-13, фаза A): один воркер слушает обе
# (-Q interactive,default, см. deploy/systemd/journal-celery-worker.service).
# interactive — то, чего косвенно ждёт человек (email-OTP входа);
# default — прогревы кэша и фоновые/ночные задачи.
CELERY_TASK_DEFAULT_QUEUE = 'default'
CELERY_TASK_ROUTES = {
    'apps.auth_app.tasks.send_otp_email_task': {'queue': 'interactive'},
}
CELERY_BEAT_SCHEDULE = {
    'refresh-registry-summary': {
        'task': 'apps.dashboard.tasks.refresh_registry_summary',
        'schedule': 60.0,  # < TTL(120с) → кэш сводки всегда тёплый
    },
    'refresh-finance-dashboard': {
        'task': 'apps.dashboard.tasks.refresh_finance_dashboard',
        'schedule': 60.0,  # < TTL(120с) → финансовая сводка всегда тёплая
    },
    'notifications-dispatch-outbox': {
        'task': 'apps.notifications.tasks.dispatch_outbox',
        'schedule': 60.0,  # страховка: точечные уведомления уходят раньше, по on_commit
    },
}
if crontab is not None:
    CELERY_BEAT_SCHEDULE.update({
        'notifications-morning-digest': {
            'task': 'apps.notifications.tasks.send_morning_digest',
            'schedule': crontab(hour=8, minute=0),   # CELERY_TIMEZONE='Europe/Moscow'
        },
        'notifications-fill-digest': {
            'task': 'apps.notifications.tasks.send_fill_digest',
            'schedule': crontab(hour=21, minute=0),
        },
        # Email-напоминания Eduson (спека этапа 3, раздел D). Письма ставятся в
        # ту же очередь; уходят они с минутным тиком notifications-dispatch-outbox.
        'notifications-email-teacher-schedule': {
            'task': 'apps.notifications.tasks.send_teacher_schedule_email',
            'schedule': crontab(hour=8, minute=0),
        },
        # Окно письма — 3 часа после конца урока; 15 минут — с большим запасом.
        'notifications-email-unrecorded': {
            'task': 'apps.notifications.tasks.send_unrecorded_reminders',
            'schedule': crontab(minute='*/15'),
        },
        'notifications-email-parent-day-before': {
            'task': 'apps.notifications.tasks.send_parent_day_before',
            'schedule': crontab(hour=10, minute=0),
        },
        # Окно «через час» — 10 минут (55–65), шаг 5 минут: каждый урок попадает
        # в два тика, второй гасит dedup_key.
        'notifications-email-parent-hour-before': {
            'task': 'apps.notifications.tasks.send_parent_hour_before',
            'schedule': crontab(minute='*/5'),
        },
        'knowledge-cleanup-orphan-images': {
            'task': 'apps.knowledge.tasks.cleanup_orphan_images',
            'schedule': crontab(hour=4, minute=30),   # CELERY_TIMEZONE='Europe/Moscow'
        },
        # Через пять минут после картинок, а не одновременно: обе задачи ходят по
        # диску, и на двух ядрах им незачем толкаться.
        'knowledge-cleanup-orphan-files': {
            'task': 'apps.knowledge.tasks.cleanup_orphan_files',
            'schedule': crontab(hour=4, minute=35),
        },
    })

# ---------------------------------------------------------------------------
# Telegram-уведомления (спека 2026-08-03)
# ---------------------------------------------------------------------------
# Токен Telegram-бота школы. Отправлять с одним токеном
# может несколько процессов — ограничение Telegram касается только getUpdates.
TELEGRAM_BOT_TOKEN: str = env('TELEGRAM_BOT_TOKEN')
# Общий чат сотрудников: сюда дублируются точечные уведомления об изменениях.
# Дайджесты сюда НЕ идут (см. спеку, п. 2.5).
TELEGRAM_GENERAL_CHAT_ID: int = env('TELEGRAM_GENERAL_CHAT_ID')
# Общий секрет журнал ↔ бот для служебных эндпоинтов /api/integrations/telegram.
BOT_SERVICE_TOKEN: str = env('BOT_SERVICE_TOKEN')
# Сколько ЗАВЕРШЁННЫХ записей очереди храним. Строки в очереди не удаляются никогда.
NOTIFICATIONS_HISTORY_LIMIT: int = env('NOTIFICATIONS_HISTORY_LIMIT')
# Публичный адрес журнала без завершающего «/» (https://<домен>): ссылка на
# кабинет преподавателя в email-напоминаниях. Пусто → письмо без ссылки.
PUBLIC_URL: str = env('PUBLIC_URL')

# ---------------------------------------------------------------------------
# Гард оплат
# ---------------------------------------------------------------------------
# True — поведение KOTOKOD: ученика с балансом (куплено − пройдено) <= 0 нельзя
# отметить присутствующим (запись урока, правка ячейки, доп.урок), а сделку
# продления менеджер не может закрыть «Продлён» без положительного баланса.
# False (по умолчанию) — у Eduson оплаты в платформе не ведутся, баланс у всех
# <= 0, и гард заблокировал бы любую запись урока. Сам расчёт баланса не
# меняется: выключается только запрет. Читается в момент вызова
# (django.conf.settings), а не при импорте, — чтобы override_settings в тестах работал.
PAYMENT_BALANCE_GUARD: bool = env('PAYMENT_BALANCE_GUARD')

# ---------------------------------------------------------------------------
# База знаний (apps.knowledge) — хранилище картинок
# ---------------------------------------------------------------------------
# Корень файлового хранилища. Локально — journal_django/media (в .gitignore),
# на проде — отдельный каталог вне репозитория.
KNOWLEDGE_MEDIA_ROOT: str = env('KNOWLEDGE_MEDIA_ROOT', default=str(BASE_DIR / 'media'))

# Префикс internal-локации nginx для X-Accel-Redirect. Пусто → Django отдаёт
# файл сам через FileResponse (локальная разработка на runserver без nginx).
KNOWLEDGE_X_ACCEL_PREFIX: str = env('KNOWLEDGE_X_ACCEL_PREFIX', default='')

# Потолок размера загружаемой картинки.
KNOWLEDGE_MAX_IMAGE_BYTES: int = 10 * 1024 * 1024

# Защита от декомпрессионных бомб: 2 МБ файла могут развернуться в гигабайты RAM.
KNOWLEDGE_MAX_IMAGE_PIXELS: int = 50_000_000

# Прикреплённые файлы (не картинки). 25 МБ покрывают методичку с иллюстрациями,
# презентацию и скан договора. Значение обязано быть согласовано с
# client_max_body_size на локации загрузки в nginx — там оно с запасом на
# накладные расходы multipart.
KNOWLEDGE_MAX_FILE_BYTES: int = 25 * 1024 * 1024

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'Europe/Moscow'
USE_I18N = False
USE_TZ = True

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        # Единственный backend: читает JWT из HttpOnly-cookie, проверяет token_version.
        'apps.core.authentication.CookieJWTAuthentication',
    ],
    'DEFAULT_RENDERER_CLASSES': [
        'apps.core.renderers.DateSafeJSONRenderer',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        # Views that need auth set their own permission_classes.
        # Default: open (health check etc.)
        'rest_framework.permissions.AllowAny',
    ],
    'EXCEPTION_HANDLER': 'apps.core.exceptions.custom_exception_handler',
    'DEFAULT_PAGINATION_CLASS': 'apps.core.pagination.StandardPagination',
    'PAGE_SIZE': 50,
    # django.contrib.auth включён (AbstractUser), AnonymousUser доступен.
    # Оставляем 'UNAUTHENTICATED_USER': None чтобы DRF не использовал AnonymousUser
    # в контексте запросов без аутентификации — views сами выставляют permission_classes.
    'UNAUTHENTICATED_USER': None,
    # Точечные лимиты по throttle_scope на конкретных вьюхах (DEFAULT_THROTTLE_CLASSES
    # НЕ включаем — иначе лимит применился бы ко всем вьюхам проекта разом).
    'DEFAULT_THROTTLE_RATES': {
        # Служебный API Telegram-бота (apps/notifications/integration_views.py).
        'bot_service': '120/min',
        # Загрузка картинок базы знаний — чтобы не залить диск (apps/knowledge).
        'knowledge_upload': '60/min',
    },
}

# ---------------------------------------------------------------------------
# URL behaviour
# ---------------------------------------------------------------------------
# Frontend never sends trailing slashes — match Express behaviour.
APPEND_SLASH = False

# ---------------------------------------------------------------------------
# Default primary key type (unused — all models are managed=False)
# ---------------------------------------------------------------------------
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'accounts.Account'

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
]

# Основной алгоритм — PBKDF2 (первый в списке). Пароли, перенесённые из Eduson
# (bcryptjs), импорт кладёт обёрнутыми в PBKDF2 (apps/accounts/hashers.py):
# голый bcrypt проверялся бы в разы быстрее и по времени ответа выдавал бы
# существующие учётки. После успешного входа Django перехэширует пароль в PBKDF2.
# Голого BCryptPasswordHasher здесь нет намеренно: такой формат не пишется.
# Остальное — дефолт Django 5.1.
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher',
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.BCryptSHA256PasswordHasher',
    'django.contrib.auth.hashers.ScryptPasswordHasher',
    'apps.accounts.hashers.PBKDF2WrappedBCryptPasswordHasher',
]

# ---------------------------------------------------------------------------
# Валидаторы паролей (используются при установке пароля через invite)
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# ---------------------------------------------------------------------------
# CSRF cookie
# ---------------------------------------------------------------------------
# SameSite фиксируем явно (как у JWT-cookie 'Lax') — чтобы будущая смена
# дефолта Django не поменяла поведение молча. HttpOnly НЕ ставим: SPA читает
# токен из JS и шлёт X-CSRFToken (см. production.py: CSRF_COOKIE_HTTPONLY=False).
CSRF_COOKIE_SAMESITE = 'Lax'

# ---------------------------------------------------------------------------
# JWT — djangorestframework-simplejwt
# Транспорт: HttpOnly-cookie. Blacklist НЕ используем.
# Отзыв токенов — через token_version (CookieJWTAuthentication.get_user).
# ---------------------------------------------------------------------------
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=60),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),

    # Ротация и blacklist отключены — отзыв через token_version.
    'ROTATE_REFRESH_TOKENS': False,
    'BLACKLIST_AFTER_ROTATION': False,

    # Cookie-транспорт (имена используются в apps/core/authentication.py).
    'AUTH_COOKIE': 'access',
    'AUTH_REFRESH_COOKIE': 'refresh',
    # refresh-cookie шлётся только на эндпоинт обновления — меньше exposure.
    'AUTH_REFRESH_COOKIE_PATH': '/api/auth/refresh',
    'AUTH_COOKIE_HTTPONLY': True,
    'AUTH_COOKIE_SAMESITE': 'Lax',
    # AUTH_COOKIE_SECURE переопределяется в development.py / production.py.
    'AUTH_COOKIE_SECURE': False,

    # SIGNING_KEY по умолчанию = SECRET_KEY (не переопределяем).
    # USER_ID_CLAIM по умолчанию = 'user_id' — оставляем.
}