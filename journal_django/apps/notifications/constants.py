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

KIND_CHOICES = [
    KIND_MORNING_DIGEST, KIND_FILL_DIGEST,
    KIND_MAKEUP_ASSIGNED, KIND_MAKEUP_CHANGED, KIND_MAKEUP_CANCELLED,
    KIND_LESSON_MOVED, KIND_LESSON_CANCELLED,
    KIND_SUBSTITUTE_ASSIGNED, KIND_SUBSTITUTE_REMOVED,
]

# --- Каналы ----------------------------------------------------------------
CHANNEL_DM = 'dm'                # личка преподавателя
CHANNEL_GROUP = 'group_chat'     # общий чат сотрудников
CHANNEL_CHOICES = [CHANNEL_DM, CHANNEL_GROUP]

# --- Статусы доставки ------------------------------------------------------
STATUS_QUEUED = 'queued'
STATUS_SENT = 'sent'
STATUS_FAILED = 'failed'
STATUS_CHOICES = [STATUS_QUEUED, STATUS_SENT, STATUS_FAILED]

# Терминальные статусы — только они подлежат подрезке хвоста.
TERMINAL_STATUSES = (STATUS_SENT, STATUS_FAILED)

# Сколько попыток отправки делаем до перевода в failed.
MAX_ATTEMPTS = 5
