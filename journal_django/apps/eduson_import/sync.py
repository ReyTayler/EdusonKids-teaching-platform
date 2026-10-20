"""Мягкое обновление: удаление учебных фактов групп старой системы перед заливкой
свежих и выключение журнала изменений на время этой замены.

ЧТО ТАКОЕ «ФАКТЫ». Всё, что шаги импорта пишут по группе заново из сессий и
участников Eduson: членства, уроки с посещаемостью, план, отзывы, плюс то, что
платформа повесила на эти уроки (зарплата, пропуски, доп.уроки-пропуски).
Сами группы и ученики не удаляются — их обновляет sync_groups на месте.

ЖУРНАЛ ИЗМЕНЕНИЙ. Замена фактов — это ~30 тыс. удалений и столько же вставок на
каждое обновление: в журнале изменений они были бы шумом, который вытесняет
настоящие правки людей. Поэтому на время замены триггеры pghistory ЭТИХ моделей
выключены (pgtrigger.ignore по URI, только историю, не защитные триггеры).
Обновление групп и учеников идёт с журналом — это редкие осмысленные правки.
"""
from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager

import pgtrigger
import pghistory.trigger
from django.db import models
from pgtrigger import registry

from apps.extra_lessons.models import AbsenceResolution
from apps.feedback.models import LessonFeedback
from apps.lessons.models import Lesson, LessonAttendance, LessonSkip
from apps.memberships.models import GroupMembership
from apps.payroll.models import Payroll
from apps.scheduling.models import PlannedLesson

# Модели фактов, которые мягкое обновление удаляет и пишет заново.
FACT_MODELS: tuple[type[models.Model], ...] = (
    Payroll, AbsenceResolution, LessonFeedback, PlannedLesson, LessonAttendance, Lesson,
    LessonSkip, GroupMembership,
)


def history_trigger_uris(model_classes: Iterable[type[models.Model]]) -> list[str]:
    """URI триггеров pghistory у моделей ('lessons.Lesson:insert_insert' …)."""
    labels = {m._meta.label for m in model_classes}
    return sorted(trigger.get_uri(model) for model, trigger in registry.registered()
                  if model._meta.label in labels and isinstance(trigger, pghistory.trigger.Event))


@contextmanager
def facts_without_history():
    """Внутри блока изменения фактов не пишутся в журнал изменений."""
    uris = history_trigger_uris(FACT_MODELS)
    with pgtrigger.ignore(*uris):
        yield


def purge_group_facts(group_ids: Iterable[int]) -> dict[str, int]:
    """Удалить факты групп → {таблица: удалено строк}. Порядок — от зависимых к
    главным: у зарплаты FK на урок без каскада, у позиций плана — fact_lesson."""
    group_ids = list(group_ids)
    lessons = Lesson.objects.filter(group_id__in=group_ids)
    memberships = GroupMembership.objects.filter(group_id__in=group_ids)
    # Перевод из группы старой системы в группу платформы: ссылка «переведён из» на
    # удаляемое членство (FK без каскада) обнуляется — сам перевод остаётся.
    GroupMembership.objects.filter(transferred_from__in=memberships).exclude(
        group_id__in=group_ids).update(transferred_from=None)
    counts = {
        'payroll': Payroll.objects.filter(lesson__in=lessons).delete()[0],
        'absence_resolutions': AbsenceResolution.objects.filter(group_id__in=group_ids).delete()[0],
        'lesson_feedback': LessonFeedback.objects.filter(group_id__in=group_ids).delete()[0],
        'planned_lessons': PlannedLesson.objects.filter(group_id__in=group_ids).delete()[0],
        'lesson_attendance': LessonAttendance.objects.filter(lesson__in=lessons).delete()[0],
        'lessons': lessons.delete()[0],
        'lesson_skips': LessonSkip.objects.filter(group_id__in=group_ids).delete()[0],
    }
    # Внутри набора удаляемых членств «переведён из» тоже ссылается на удаляемые строки.
    memberships.update(transferred_from=None)
    counts['group_memberships'] = memberships.delete()[0]
    return counts
