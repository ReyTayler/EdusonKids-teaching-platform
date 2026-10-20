"""Мягкое обновление: удаление учебных фактов групп старой системы перед заливкой
свежих, перенос ручных правок зарплаты через эту замену и выключение журнала
изменений на её время.

ЧТО ТАКОЕ «ФАКТЫ». Всё, что шаги импорта пишут по группе заново из сессий и
участников Eduson: членства, уроки с посещаемостью, план, отзывы, плюс то, что
платформа повесила на эти уроки (зарплата, пропуски, доп.уроки-пропуски).
Сами группы и ученики не удаляются — их обновляет sync_groups на месте.

ЖУРНАЛ ИЗМЕНЕНИЙ. Замена фактов — это ~30 тыс. удалений и столько же вставок на
каждое обновление: в журнале изменений они были бы шумом, который вытесняет
настоящие правки людей. Поэтому на время замены триггеры pghistory ЭТИХ моделей
выключены (pgtrigger.ignore по URI, только историю, не защитные триггеры).
Сюда же — начисления за отмены: их не удаляют, но удаление маркеров обнуляет у
них ссылку, а пересчёт после заливки привязывает заново — та же замена, не правка.
Обновление групп и учеников идёт с журналом — это редкие осмысленные правки.

РУЧНЫЕ ПРАВКИ ЗАРПЛАТЫ (спека 2026-10-20-eduson-motivation-payroll-design, §3).
Начисление за урок удаляется вместе с уроком, а урок после заливки — уже другая
строка. Поэтому перед удалением ручные суммы снимаются вместе с ключом урока, а
после пересчёта возвращаются на новый урок (правила — restore_manual_payroll).
Отмены переносить не нужно: их начисление переживает удаление маркера (SET NULL)
и привязывается движком к новому маркеру (apps/payroll/recalc.py).
"""
from __future__ import annotations

import datetime
from collections import Counter
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal

import pgtrigger
import pghistory.trigger
from django.db import models
from pgtrigger import registry

from apps.eduson_import.context import ImportContext
from apps.extra_lessons.models import AbsenceResolution
from apps.feedback.models import LessonFeedback
from apps.groups.models import Group
from apps.lessons.models import Lesson, LessonAttendance, LessonSkip
from apps.memberships.models import GroupMembership
from apps.payroll.models import CancellationPay, Payroll
from apps.scheduling.models import PlannedLesson
from apps.scheduling.repository import lock_plan_rows

# Модели фактов, которые мягкое обновление удаляет и пишет заново.
FACT_MODELS: tuple[type[models.Model], ...] = (
    Payroll, AbsenceResolution, LessonFeedback, PlannedLesson, LessonAttendance, Lesson,
    LessonSkip, GroupMembership,
)
# Без журнала на время замены: факты + начисления за отмены (см. шапку).
HISTORYLESS_MODELS: tuple[type[models.Model], ...] = (*FACT_MODELS, CancellationPay)

# Естественный ключ урока: (group_id, lesson_date, lesson_number); день — (group_id, lesson_date).
LessonKey = tuple[int, datetime.date, Decimal]
DayKey = tuple[int, datetime.date]


@dataclass(frozen=True)
class ManualLessonPay:
    """Ручная правка урока, снятая перед удалением фактов."""
    key: LessonKey
    teacher_id: int
    payment: Decimal
    penalty: Decimal


@dataclass
class ManualSnapshot:
    """Снятые ручные правки + сколько уроков было у группы в каждый день: запасной
    поиск по дню допустим, только если и до, и после замены урок в этот день один."""
    pays: dict[LessonKey, ManualLessonPay] = field(default_factory=dict)
    # Вторая и следующие правки на один ключ — перенести их некуда, только в отчёт.
    duplicates: list[LessonKey] = field(default_factory=list)
    old_lessons_per_day: Counter[DayKey] = field(default_factory=Counter)


def history_trigger_uris(model_classes: Iterable[type[models.Model]]) -> list[str]:
    """URI триггеров pghistory у моделей ('lessons.Lesson:insert_insert' …)."""
    labels = {m._meta.label for m in model_classes}
    return sorted(trigger.get_uri(model) for model, trigger in registry.registered()
                  if model._meta.label in labels and isinstance(trigger, pghistory.trigger.Event))


@contextmanager
def facts_without_history():
    """Внутри блока изменения фактов и начислений не пишутся в журнал изменений.

    ⚠️ pgtrigger.ignore дописывает префикс к каждому запросу блока: сырой SQL с
    именованными параметрами (%(name)s) внутри ломается — такое держать снаружи."""
    uris = history_trigger_uris(HISTORYLESS_MODELS)
    with pgtrigger.ignore(*uris):
        yield


def purge_group_facts(group_ids: Iterable[int]) -> tuple[dict[str, int], ManualSnapshot]:
    """Удалить факты групп → ({таблица: удалено строк}, снятые ручные правки зарплаты).

    Блокировки — в порядке проекта «урок → план → деньги» (шапка apps/payroll/recalc.py):
    кнопка «Обновить данные с прода» запускает синк при работающем сервере, а
    пересчёт по смене ставок и операции расписания идут параллельно. Уроки охвата
    лочатся по id, строки плана — порядком scheduling (lock_plan_rows), начисления
    уроков — после них; удаление — только затем. Порядок удалений — от зависимых
    к главным: у зарплаты FK на урок без каскада, у позиций плана — fact_lesson."""
    group_ids = list(group_ids)
    lessons = Lesson.objects.filter(group_id__in=group_ids)
    plan = PlannedLesson.objects.filter(group_id__in=group_ids)
    memberships = GroupMembership.objects.filter(group_id__in=group_ids)

    snapshot = ManualSnapshot()
    for group_id, day in (lessons.select_for_update().order_by('id')
                          .values_list('group_id', 'lesson_date')):
        snapshot.old_lessons_per_day[(group_id, day)] += 1
    lock_plan_rows(plan)
    _take_manual_payroll(lessons, snapshot)

    # Перевод из группы старой системы в группу платформы: ссылка «переведён из» на
    # удаляемое членство (FK без каскада) обнуляется — сам перевод остаётся.
    GroupMembership.objects.filter(transferred_from__in=memberships).exclude(
        group_id__in=group_ids).update(transferred_from=None)
    counts = {
        'payroll': Payroll.objects.filter(lesson__in=lessons).delete()[0],
        'absence_resolutions': AbsenceResolution.objects.filter(group_id__in=group_ids).delete()[0],
        'lesson_feedback': LessonFeedback.objects.filter(group_id__in=group_ids).delete()[0],
        'planned_lessons': plan.delete()[0],
        'lesson_attendance': LessonAttendance.objects.filter(lesson__in=lessons).delete()[0],
        'lessons': lessons.delete()[0],
        'lesson_skips': LessonSkip.objects.filter(group_id__in=group_ids).delete()[0],
    }
    # Внутри набора удаляемых членств «переведён из» тоже ссылается на удаляемые строки.
    memberships.update(transferred_from=None)
    counts['group_memberships'] = memberships.delete()[0]
    return counts, snapshot


def _take_manual_payroll(lessons, snapshot: ManualSnapshot) -> None:
    """Снять ручные суммы уроков охвата в snapshot.

    Лочатся ВСЕ начисления охвата, а ручные отбираются уже в Python: с условием
    is_manual в запросе параллельная правка из админки (PATCH, который сам ставит
    is_manual) не попала бы ни в выборку, ни под лок и потерялась бы молча. После
    лока такая правка либо уже закоммичена и видна здесь, либо ждёт удаления строки.
    Дубль ключа (два урока с одним ключом) — первая правка по id урока, остальные
    в snapshot.duplicates."""
    rows = (Payroll.objects.select_for_update(of=('self',))
            .filter(lesson__in=lessons).order_by('lesson_id')
            .values_list('is_manual', 'lesson__group_id', 'lesson__lesson_date',
                         'lesson__lesson_number', 'lesson__teacher_id', 'payment', 'penalty'))
    for is_manual, group_id, day, number, teacher_id, payment, penalty in rows:
        if not is_manual:
            continue
        key = (group_id, day, number)
        if key in snapshot.pays:
            snapshot.duplicates.append(key)
            continue
        snapshot.pays[key] = ManualLessonPay(key, teacher_id, payment, penalty)


def restore_manual_payroll(snapshot: ManualSnapshot, ctx: ImportContext) -> tuple[int, int]:
    """Вернуть снятые ручные суммы на новые уроки → (восстановлено, не перенесено).

    Звать после пересчёта: начисления новых уроков уже созданы движком с calc_*,
    здесь им возвращаются только payment/penalty и пометка «вручную».

    Поиск урока (решение координатора 2026-10-20):
      1. по ключу (группа, дата, номер); несколько уроков — первый по id, ключ в отчёт;
      2. не нашлось — по (группа, дата), если у группы в этот день ровно один урок
         и до замены, и после: в старой системе раньше появился урок, номера
         сдвинулись, а занятие то же;
      3. у найденного урока другой преподаватель — правку не переносим: сумма
         назначалась другому человеку.
    Всё, что не перенесено, — строка в отчёт; в ней только код группы, дата и
    номер — без ПДн."""
    if not snapshot.pays and not snapshot.duplicates:
        return 0, 0
    group_ids = {key[0] for key in (*snapshot.pays, *snapshot.duplicates)}
    by_key: dict[LessonKey, list[tuple[int, int]]] = {}
    by_day: dict[DayKey, list[tuple[int, int]]] = {}
    for group_id, day, number, lesson_id, teacher_id in (
            Lesson.objects.filter(group_id__in=group_ids).order_by('id')
            .values_list('group_id', 'lesson_date', 'lesson_number', 'id', 'teacher_id')):
        by_key.setdefault((group_id, day, number), []).append((lesson_id, teacher_id))
        by_day.setdefault((group_id, day), []).append((lesson_id, teacher_id))

    targets: dict[int, ManualLessonPay] = {}
    lost: list[tuple[str, LessonKey]] = [
        ('вторая правка на тот же урок', key) for key in snapshot.duplicates]
    several: list[LessonKey] = []
    for key, manual in snapshot.pays.items():
        found = by_key.get(key)
        if not found:
            same_day = by_day.get(key[:2], [])
            if snapshot.old_lessons_per_day[key[:2]] == 1 and len(same_day) == 1:
                found = same_day
        if not found:
            lost.append(('урок не найден', key))
            continue
        if len(found) > 1:
            several.append(key)
        lesson_id, teacher_id = found[0]
        if teacher_id != manual.teacher_id:
            lost.append(('сменился преподаватель урока', key))
            continue
        targets[lesson_id] = manual

    rows = list(Payroll.objects.select_for_update()
                .filter(lesson_id__in=list(targets)).order_by('lesson_id'))
    for row in rows:
        manual = targets.pop(row.lesson_id)
        row.payment, row.penalty, row.is_manual = manual.payment, manual.penalty, True
    Payroll.objects.bulk_update(rows, ['payment', 'penalty', 'is_manual'], batch_size=500)
    # Урок есть, а начисления нет (движок его не начислил) — правке некуда встать.
    lost.extend(('у урока нет начисления', m.key) for m in targets.values())

    codes = dict(Group.objects.filter(id__in=group_ids).values_list('id', 'name'))
    for reason, key in sorted(lost, key=lambda item: (item[1], item[0])):
        ctx.report.append(f'ручная правка зарплаты не перенесена: {reason} ({_key_label(key, codes)})')
    for key in sorted(several):
        ctx.report.append('ручная правка зарплаты — на первый из нескольких уроков: '
                          + _key_label(key, codes))
    return len(rows), len(lost)


def _key_label(key: LessonKey, codes: dict[int, str]) -> str:
    """«ПИ1001, 2026-07-06, урок 1»; дробный номер 45-минутных групп — «урок 1.5»."""
    group_id, day, number = key
    shown = number.quantize(Decimal(1)) if number == number.to_integral_value() else number
    return f'{codes.get(group_id, f"группа #{group_id}")}, {day.isoformat()}, урок {shown}'
