"""Запросы раздела «Уведомления»."""
from __future__ import annotations

import datetime

from django.db.models import Exists, F, Max, OuterRef, Q, QuerySet

from apps.accounts.models import Account
from apps.memberships.models import GroupMembership
from apps.notifications.constants import (
    CHANNEL_CHOICES, KIND_CHOICES, KIND_FILL_DIGEST, KIND_LESSON_UNRECORDED,
    KIND_MORNING_DIGEST, KIND_PARENT_DAY_BEFORE, KIND_PARENT_HOUR_BEFORE,
    STATUS_CHOICES,
)
from apps.notifications.models import NotificationMessage
from apps.scheduling.models import PlannedLesson
from apps.scheduling.occurrences import PENDING
from apps.teachers.models import Teacher

# Виды сообщений, которые ставит задача по расписанию (вкладка «Расписание»).
SCHEDULED_KINDS = [
    KIND_MORNING_DIGEST, KIND_FILL_DIGEST, KIND_LESSON_UNRECORDED,
    KIND_PARENT_DAY_BEFORE, KIND_PARENT_HOUR_BEFORE,
]


def filtered(*, kind: str | None, channel: str | None, status: str | None) -> QuerySet:
    """Лента сообщений, новые сверху. Неизвестные значения фильтров игнорируются.

    Сортировка по (-created_at, -id): у auto_now_add секунд/микросекунд может
    не хватить, чтобы различить строки, созданные в одной транзакции — id как
    тай-брейкер даёт детерминированный порядок.
    """
    qs = (NotificationMessage.objects
          .select_related('recipient_teacher')
          .order_by('-created_at', '-id'))
    if kind in KIND_CHOICES:
        qs = qs.filter(kind=kind)
    if channel in CHANNEL_CHOICES:
        qs = qs.filter(channel=channel)
    if status in STATUS_CHOICES:
        qs = qs.filter(status=status)
    return qs


def last_runs() -> dict[str, object]:
    """
    Когда каждая задача по расписанию отработала в последний раз.

    Считаем по данным очереди (max created_at по kind), а не опрашивая
    внутренности Celery-beat: это дёшево и не ломается при перезапуске beat.
    Один запрос на все виды. Ключи — значения kind, плюс 'dispatch'.
    """
    rows = (NotificationMessage.objects
            .filter(kind__in=SCHEDULED_KINDS)
            .values('kind')
            .annotate(last=Max('created_at')))
    by_kind = {row['kind']: row['last'] for row in rows}
    return {
        **{kind: by_kind.get(kind) for kind in SCHEDULED_KINDS},
        'dispatch': NotificationMessage.objects.aggregate(last=Max('sent_at'))['last'],
    }


def counts_by_status() -> dict[str, int]:
    """Сколько сообщений в каждом статусе — для шапки вкладки «Расписание»."""
    return {
        status: NotificationMessage.objects.filter(status=status).count()
        for status in STATUS_CHOICES
    }


# ---------------------------------------------------------------------------
# Email-напоминания (спека этапа 3, раздел D). Все выборки пакетные: один запрос
# на всю школу, группировка в памяти у вызывающего (apps.notifications.reminders).
# ---------------------------------------------------------------------------

def teacher_contacts(teacher_ids) -> dict[int, dict]:
    """
    teacher_id → {'email', 'name'} для писем; преподаватели без адреса не попадают.

    Канон адреса — email УЧЁТКИ преподавателя, запас — email из карточки.
    Почему учётка: этим адресом человек входит в журнал и на него же приходят
    одноразовые коды 2FA — адрес заведомо живой и принадлежит именно ему (поле
    уникально). Email карточки — справочное поле, его никто не проверяет; он
    берётся только для преподавателя без активной учётки, чтобы тот не остался
    совсем без писем.

    Два запроса на любое число преподавателей.
    """
    ids = {i for i in teacher_ids if i is not None}
    if not ids:
        return {}
    cards = {
        r['id']: r for r in Teacher.objects.filter(id__in=ids).values('id', 'name', 'email')
    }
    login_emails = dict(
        Account.objects
        .filter(teacher_id__in=ids, is_active=True)
        .values_list('teacher_id', 'email')
    )
    out: dict[int, dict] = {}
    for teacher_id, card in cards.items():
        email = login_emails.get(teacher_id) or (card['email'] or '').strip()
        if email:
            out[teacher_id] = {'email': email, 'name': card['name']}
    return out


def unrecorded_positions(date_from: datetime.date, date_to: datetime.date) -> list[dict]:
    """
    Незаписанные позиции плана за окно дат — источник письма «Урок не записан».

    Предикаты — те же, что у scheduling.repository.unfilled_planned_lessons
    (вкладка «Заполнить» и вечерний дайджест): активная группа с активными
    учениками, статус pending, факта нет. Отличие одно — нижняя граница дат:
    задача ходит каждые 15 минут, и читать весь исторический хвост
    незаполненного ей незачем (окно повторов — несколько суток).
    """
    has_students = GroupMembership.objects.filter(
        group_id=OuterRef('group_id'), active=True)
    return list(
        PlannedLesson.objects
        .filter(
            Exists(has_students),
            group__active=True,
            status=PENDING,
            fact_lesson__isnull=True,
            scheduled_date__gte=date_from,
            scheduled_date__lte=date_to,
        )
        .order_by('scheduled_date', 'scheduled_time', 'id')
        .values(
            'id', 'seq', 'scheduled_date', 'scheduled_time',
            'teacher_id', 'substitute_teacher_id',
            group_name=F('group__name'),
            direction_name=F('group__direction__name'),
            duration=F('group__lesson_duration_minutes'),
        )
    )


def _upcoming(window: Q) -> list[dict]:
    """
    Предстоящие занятия активных групп в окне — для родительских напоминаний.

    Только status=pending: у будущего занятия другого «живого» статуса нет
    (overdue — прошедшее, done — урок уже записан), а отменённые и перенесённые
    строки напоминать нельзя — это звать ребёнка на урок, которого не будет.
    Равенство по pending попадает в частичный индекс
    planned_lessons_pending_dt_idx (выборка идёт по всей школе каждые 5 мин).
    """
    return list(
        PlannedLesson.objects
        .filter(window, group__active=True, status=PENDING)
        .order_by('scheduled_date', 'scheduled_time', 'id')
        .values(
            'id', 'seq', 'scheduled_date', 'scheduled_time',
            group_pk=F('group_id'),
            direction_name=F('group__direction__name'),
        )
    )


def upcoming_positions_on(day: datetime.date) -> list[dict]:
    """Занятия на дату (напоминание «урок завтра»)."""
    return _upcoming(Q(scheduled_date=day))


def upcoming_positions_between(start: datetime.datetime,
                               end: datetime.datetime) -> list[dict]:
    """
    Занятия, начинающиеся в (start; end] по МСК (напоминание «через час»).

    Дата и время лежат раздельными колонками, поэтому окно раскладывается на
    условия по дням: окно через полночь — два дня, иначе один.
    """
    window = Q(pk__in=[])
    day = start.date()
    while day <= end.date():
        cond = Q(scheduled_date=day)
        if day == start.date():
            cond &= Q(scheduled_time__gt=start.time())
        if day == end.date():
            cond &= Q(scheduled_time__lte=end.time())
        window |= cond
        day += datetime.timedelta(days=1)
    return _upcoming(window)


def family_contacts_by_group(group_ids) -> dict[int, list[dict]]:
    """
    group_id → активные ученики группы с адресами: ученик, родитель 1, родитель 2.

    Один запрос на любое число групп. Пустые адреса отбрасываются здесь, чтобы
    вызывающему не пришлось помнить, что '' — тоже «адреса нет».
    """
    ids = list({i for i in group_ids if i is not None})
    if not ids:
        return {}
    out: dict[int, list[dict]] = {}
    rows = (GroupMembership.objects
            .filter(group_id__in=ids, active=True)
            .order_by('group_id', 'student_id')
            .values('group_id', 'student_id',
                    name=F('student__full_name'),
                    email=F('student__email'),
                    parent1_email=F('student__parent1_email'),
                    parent2_email=F('student__parent2_email')))
    for r in rows:
        emails = [e.strip() for e in (r['email'], r['parent1_email'], r['parent2_email'])
                  if e and e.strip()]
        out.setdefault(r['group_id'], []).append({
            'student_id': r['student_id'], 'name': r['name'], 'emails': emails,
        })
    return out
