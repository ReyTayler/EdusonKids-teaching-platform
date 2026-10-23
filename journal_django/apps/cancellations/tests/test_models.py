"""Инварианты справочника причин и записи об отмене на уровне БД.

Почему так строго в БД, а не только в сериализаторе: запись — снимок решения о
деньгах преподавателя; несогласованная строка (клиент без даты уведомления,
«Другая причина» с компенсацией) дала бы неверное начисление при пересчёте,
откуда бы она ни пришла — API, скрипт или будущий импорт."""
from __future__ import annotations

import datetime

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

pytestmark = pytest.mark.django_db

DAY = datetime.date(2026, 11, 10)


def _rejected(fn, **kwargs):
    # savepoint: после IntegrityError транзакция теста остаётся рабочей
    with pytest.raises(IntegrityError), transaction.atomic():
        fn(**kwargs)


# ---------------------------------------------------------------------------
# CancellationReason
# ---------------------------------------------------------------------------

def test_reason_side_whitelist(make_reason):
    _rejected(make_reason, side='student')


@pytest.mark.parametrize('name', ['', '   '])
def test_reason_blank_name_rejected(make_reason, name):
    _rejected(make_reason, name=name)


def test_reason_active_name_unique_case_insensitive(make_reason):
    make_reason(side='client', name='Заболел')
    _rejected(make_reason, side='client', name='заболел')


def test_reason_name_reusable_after_soft_delete(make_reason):
    first = make_reason(side='client', name='Заболел')
    first.deleted_at = timezone.now()
    first.save(update_fields=['deleted_at'])
    again = make_reason(side='client', name='Заболел')
    assert again.pk != first.pk


def test_reason_same_name_in_other_side_allowed(make_reason):
    make_reason(side='client', name='Заболел')
    other = make_reason(side='teacher', name='Заболел')
    assert other.side == 'teacher'


def test_reason_compensation_defaults_to_false(db):
    from apps.cancellations.models import CancellationReason

    r = CancellationReason.objects.create(side='teacher', name='Отпуск', position=0)
    r.refresh_from_db()
    assert r.compensation is False


# ---------------------------------------------------------------------------
# LessonCancellation
# ---------------------------------------------------------------------------

@pytest.fixture
def base(school, make_reason):
    reason = make_reason(side='client', name='Заболел')
    return {'group_id': school['group_id'], 'teacher_id': school['teacher_id'],
            'lesson_date': DAY, 'reason': reason}


def test_record_valid_client(base, make_record):
    rec = make_record(**base)
    rec.refresh_from_db()
    assert (rec.side, rec.notified_on, rec.compensated) == ('client', DAY, True)


def test_record_teacher_may_be_null(base, make_record):
    """В2: эффективного преподавателя у маркера может не быть — отмена не падает."""
    rec = make_record(**{**base, 'teacher_id': None})
    rec.refresh_from_db()
    assert rec.teacher_id is None


def test_record_side_whitelist(base, make_record):
    _rejected(make_record, **base, side='student', notified_on=DAY)


def test_record_client_requires_notified_on(base, make_record):
    _rejected(make_record, **base, notified_on=None)


def test_record_teacher_forbids_notified_on(base, make_record, make_reason):
    t_reason = make_reason(side='teacher', name='Болезнь')
    _rejected(make_record, **{**base, 'reason': t_reason}, side='teacher', notified_on=DAY)


def test_record_other_with_reason_rejected(base, make_record):
    _rejected(make_record, **base, is_other=True, compensated=False)


def test_record_not_other_without_reason_rejected(base, make_record):
    _rejected(make_record, **{**base, 'reason': None}, is_other=False)


def test_record_other_never_compensated(base, make_record):
    _rejected(make_record, **{**base, 'reason': None}, is_other=True, compensated=True,
              reason_text='Срочная поездка')


def test_record_other_valid(base, make_record):
    rec = make_record(**{**base, 'reason': None}, is_other=True, compensated=False,
                      reason_text='Срочная поездка')
    assert rec.is_other and rec.reason_id is None


@pytest.mark.parametrize('text', ['', '  \n '])
def test_record_blank_text_rejected(base, make_record, text):
    _rejected(make_record, **base, reason_text=text)


def test_reason_protected_from_hard_delete(base, make_record):
    """Причины удаляются только мягко: жёсткое удаление использованной — ошибка."""
    from django.db.models import ProtectedError

    make_record(**base)
    with pytest.raises(ProtectedError), transaction.atomic():
        base['reason'].delete()


def test_one_record_per_marker(base, make_record, make_marker):
    marker = make_marker(base['group_id'], base['teacher_id'], DAY)
    make_record(**base, planned_lesson=marker)
    _rejected(make_record, **base, planned_lesson=marker)


# ---------------------------------------------------------------------------
# Переживание маркера: SET NULL на уровне БД и ORM
# ---------------------------------------------------------------------------

def test_raw_marker_delete_sets_null(base, make_record, make_marker):
    """Маркеры чистят и сырым SQL — FK обязан быть ON DELETE SET NULL в самой БД."""
    marker = make_marker(base['group_id'], base['teacher_id'], DAY)
    rec = make_record(**base, planned_lesson=marker)
    with connection.cursor() as cur:
        cur.execute('DELETE FROM planned_lessons WHERE id = %s', [marker.id])
        # FK DEFERRABLE INITIALLY DEFERRED: проверка — на коммите; форсируем сейчас.
        cur.execute('SET CONSTRAINTS ALL IMMEDIATE')
    rec.refresh_from_db()
    assert rec.planned_lesson_id is None


def test_marker_fk_is_set_null_in_catalog(db):
    """Ловит AlterField, который молча пересоздаст FK без ON DELETE (см. модель)."""
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT confdeltype FROM pg_constraint
            WHERE conrelid = 'lesson_cancellations'::regclass AND contype = 'f'
              AND conkey = (SELECT array_agg(attnum) FROM pg_attribute
                            WHERE attrelid = 'lesson_cancellations'::regclass
                              AND attname = 'planned_lesson_id')
            """
        )
        rows = cur.fetchall()
    assert rows == [('n',)]


# ---------------------------------------------------------------------------
# Журнал изменений
# ---------------------------------------------------------------------------

def test_insert_tracked(base, make_record):
    from apps.cancellations.models import LessonCancellationEvent

    rec = make_record(**base)
    labels = list(LessonCancellationEvent.objects.filter(pgh_obj_id=rec.id)
                  .values_list('pgh_label', flat=True))
    assert labels == ['insert']


def test_marker_delete_creates_no_update_event(base, make_record, make_marker):
    """В4: обнуление ссылки коллектором не порождает событий записи — иначе перевод
    ученика и смена расписания стали бы неоткатываемыми операциями."""
    from apps.cancellations.models import LessonCancellation, LessonCancellationEvent
    from apps.scheduling.models import PlannedLesson

    marker = make_marker(base['group_id'], base['teacher_id'], DAY)
    rec = make_record(**base, planned_lesson=marker)
    PlannedLesson.objects.filter(id=marker.id).delete()
    assert LessonCancellation.objects.get(pk=rec.pk).planned_lesson_id is None
    labels = list(LessonCancellationEvent.objects.filter(pgh_obj_id=rec.id)
                  .values_list('pgh_label', flat=True))
    assert labels == ['insert']


def test_reason_update_tracked(make_reason):
    from apps.cancellations.models import CancellationReasonEvent

    r = make_reason(side='client', name='Заболел', compensation=True)
    r.compensation = False
    r.save()
    labels = list(CancellationReasonEvent.objects.filter(pgh_obj_id=r.id)
                  .order_by('pgh_id').values_list('pgh_label', flat=True))
    assert labels == ['insert', 'update']


def test_reason_order_index_is_partial_on_active(db):
    """Индекс под list_active: (position, id) только по активным причинам."""
    with connection.cursor() as cur:
        cur.execute("SELECT indexdef FROM pg_indexes WHERE indexname = 'cancel_reason_order_idx'")
        (indexdef,) = cur.fetchone()
    assert '("position", id)' in indexdef or '("position", "id")' in indexdef
    assert 'WHERE (deleted_at IS NULL)' in indexdef
