"""
Значения enum-полей очереди уведомлений — единый источник.

Строки НЕ дублируются по коду: и модели (CheckConstraint), и сервисы, и тесты
берут их отсюда. Подписи для фронта — во frontend/admin-src/src/lib/labels.ts.
"""
from __future__ import annotations

# --- Типы сообщений --------------------------------------------------------
KIND_MORNING_DIGEST = 'morning_digest'
KIND_FILL_DIGEST = 'fill_digest'
KIND_MAKEUP_ASSIGNED = 'makeup_assigned'
KIND_MAKEUP_CHANGED = 'makeup_changed'
KIND_MAKEUP_CANCELLED = 'makeup_cancelled'
KIND_LESSON_MOVED = 'lesson_moved'
KIND_LESSON_CANCELLED = 'lesson_cancelled'
KIND_SUBSTITUTE_ASSIGNED = 'substitute_assigned'
KIND_SUBSTITUTE_REMOVED = 'substitute_removed'
# Email-напоминания Eduson (спека этапа 3, раздел D). Утреннее письмо
# преподавателю — тот же morning_digest, только канал email.
KIND_LESSON_UNRECORDED = 'lesson_unrecorded'
KIND_PARENT_DAY_BEFORE = 'parent_day_before'
KIND_PARENT_HOUR_BEFORE = 'parent_hour_before'

KIND_CHOICES = [
    KIND_MORNING_DIGEST, KIND_FILL_DIGEST,
    KIND_MAKEUP_ASSIGNED, KIND_MAKEUP_CHANGED, KIND_MAKEUP_CANCELLED,
    KIND_LESSON_MOVED, KIND_LESSON_CANCELLED,
    KIND_SUBSTITUTE_ASSIGNED, KIND_SUBSTITUTE_REMOVED,
    KIND_LESSON_UNRECORDED, KIND_PARENT_DAY_BEFORE, KIND_PARENT_HOUR_BEFORE,
]

# --- Каналы ----------------------------------------------------------------
CHANNEL_DM = 'dm'                # личка преподавателя
CHANNEL_GROUP = 'group_chat'     # общий чат сотрудников
CHANNEL_EMAIL = 'email'          # письмо на адрес recipient_email
CHANNEL_CHOICES = [CHANNEL_DM, CHANNEL_GROUP, CHANNEL_EMAIL]
# Каналы Telegram: адресат — chat_id. У email адресат — recipient_email.
TELEGRAM_CHANNELS = [CHANNEL_DM, CHANNEL_GROUP]

# --- Статусы доставки ------------------------------------------------------
STATUS_QUEUED = 'queued'
STATUS_SENT = 'sent'
STATUS_FAILED = 'failed'
STATUS_CHOICES = [STATUS_QUEUED, STATUS_SENT, STATUS_FAILED]

# Терминальные статусы — только они подлежат подрезке хвоста.
TERMINAL_STATUSES = (STATUS_SENT, STATUS_FAILED)

# Сколько попыток отправки делаем до перевода в failed.
MAX_ATTEMPTS = 5

# --- Email-напоминания -----------------------------------------------------
# «Урок не записан»: первое письмо — в течение REMIND_WINDOW после конца урока,
# затем до UNRECORDED_MAX_REPEATS повторов с шагом REPEAT_EVERY (каждый — в своём
# окне той же ширины). Окно нужно, чтобы задача по расписанию не будила
# преподавателя письмами про уроки недельной давности.
UNRECORDED_WINDOW_HOURS = 3
UNRECORDED_REPEAT_EVERY_HOURS = 24
UNRECORDED_MAX_REPEATS = 3
# «Урок через час»: занятия, до начала которых (55; 65] минут.
HOUR_BEFORE_FROM_MINUTES = 55
HOUR_BEFORE_TO_MINUTES = 65
# Сколько держать завершённые email-строки, не отдавая их подрезке хвоста.
# Идемпотентность оконных напоминаний держится на dedup_key — удали строку
# раньше, чем закроется окно, и следующий тик пришлёт письмо повторно.
EMAIL_DEDUP_HORIZON_HOURS = 24
