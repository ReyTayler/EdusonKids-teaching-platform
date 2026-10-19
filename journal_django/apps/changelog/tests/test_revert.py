"""
Откат операции по контексту: обратный порядок, конфликт-детекция,
запрет accounts, восстановление hard-delete с поправкой sequence.
"""
from __future__ import annotations

import pghistory
import pytest
from django.apps import apps

from apps.changelog import revert
from apps.changelog.revert import RevertConflict, RevertForbidden
from apps.directions.models import Direction

pytestmark = pytest.mark.django_db


def _ctx_of(direction_id):
    """UUID контекста последнего события по Direction."""
    ev = apps.get_model('directions', 'DirectionEvent')
    return (ev.objects.filter(pgh_obj_id=direction_id, pgh_context_id__isnull=False)
            .order_by('-pgh_id').first().pgh_context_id)


def _make(name='__chg_rev__'):
    return Direction.objects.create(name=name)


def test_revert_update():
    d = _make()
    with pghistory.context(url='/t', method='PATCH'):
        Direction.objects.filter(id=d.id).update(name='__chg_rev_new__')
    summary = revert.revert_context(_ctx_of(d.id))
    d.refresh_from_db()
    assert d.name == '__chg_rev__'
    assert summary['reverted_events'] == 1


def test_revert_insert_deletes_row():
    with pghistory.context(url='/t', method='POST'):
        d = _make('__chg_rev_ins__')
    revert.revert_context(_ctx_of(d.id))
    assert not Direction.objects.filter(id=d.id).exists()


def test_revert_delete_restores_row_and_sequence():
    d = _make('__chg_rev_del__')
    d_id = d.id
    with pghistory.context(url='/t', method='DELETE'):
        Direction.objects.filter(id=d_id).delete()
    revert.revert_context(_ctx_of(d_id))
    restored = Direction.objects.get(id=d_id)
    assert restored.name == '__chg_rev_del__'
    # sequence поправлен: следующая вставка не конфликтует по PK
    d2 = _make('__chg_rev_after__')
    assert d2.id > d_id


def test_revert_composite_operation():
    """Insert + update разных строк в одном контексте откатываются вместе."""
    d1 = _make('__chg_comp_1__')
    with pghistory.context(url='/t', method='POST'):
        d2 = Direction.objects.create(name='__chg_comp_2__')
        Direction.objects.filter(id=d1.id).update(active=False)
    revert.revert_context(_ctx_of(d2.id))
    d1.refresh_from_db()
    assert d1.active is True
    assert not Direction.objects.filter(id=d2.id).exists()


def test_revert_multiple_updates_same_row_restores_pre_context_state():
    """Несколько update одной строки в контексте → возврат к состоянию ДО контекста."""
    d = _make('__chg_multi__')
    with pghistory.context(url='/t', method='POST'):
        Direction.objects.filter(id=d.id).update(name='__chg_multi_v2__')
        Direction.objects.filter(id=d.id).update(name='__chg_multi_v3__')
    revert.revert_context(_ctx_of(d.id))
    d.refresh_from_db()
    assert d.name == '__chg_multi__'


def test_revert_conflict_on_later_change():
    d = _make('__chg_confl__')
    with pghistory.context(url='/t', method='PATCH'):
        Direction.objects.filter(id=d.id).update(name='__chg_confl_v2__')
    ctx = _ctx_of(d.id)
    # Более позднее изменение той же строки (в проде — другой запрос со своим
    # контекстом; GUC pghistory транзакционно-локальный, поэтому в тесте новый
    # контекст открываем явно) → конфликт
    with pghistory.context(url='/t2', method='PATCH'):
        Direction.objects.filter(id=d.id).update(name='__chg_confl_v3__')
    with pytest.raises(RevertConflict) as exc_info:
        revert.revert_context(ctx)
    assert exc_info.value.conflicts
    d.refresh_from_db()
    assert d.name == '__chg_confl_v3__'  # ничего не изменилось


def test_revert_accounts_forbidden():
    """Операции с Account не откатываются (v1)."""
    from apps.accounts.models import Account
    with pghistory.context(url='/t', method='POST'):
        acc = Account.objects.create(
            email='__chg_acc__@test.local', role='manager', password='x')
    ev = apps.get_model('accounts', 'AccountEvent')
    ctx = (ev.objects.filter(pgh_obj_id=acc.id, pgh_context_id__isnull=False)
           .order_by('-pgh_id').first().pgh_context_id)
    with pytest.raises(RevertForbidden):
        revert.revert_context(ctx)
    acc.delete()


def test_revert_is_itself_tracked():
    d = _make('__chg_track_rev__')
    with pghistory.context(url='/t', method='PATCH'):
        Direction.objects.filter(id=d.id).update(name='__chg_track_rev2__')
    revert.revert_context(_ctx_of(d.id))
    ev = apps.get_model('directions', 'DirectionEvent')
    last = ev.objects.filter(pgh_obj_id=d.id).order_by('-pgh_id').first()
    assert last.pgh_context is not None
    assert last.pgh_context.metadata.get('operation') == 'changelog.revert'


def test_revert_already_reverted_forbidden():
    """Повторный откат уже откаченной операции запрещён на уровне кода."""
    d = _make('__chg_double__')
    with pghistory.context(url='/t', method='PATCH'):
        Direction.objects.filter(id=d.id).update(name='__chg_double_v2__')
    ctx = _ctx_of(d.id)
    revert.revert_context(ctx)  # первый откат — успех
    with pytest.raises(RevertForbidden):
        revert.revert_context(ctx)  # повторный — отказ


def test_revert_of_revert_forbidden():
    """Сам откат откатывать нельзя (иначе бесконечный redo-цикл)."""
    from pghistory.models import Context
    d = _make('__chg_revrev__')
    with pghistory.context(url='/t', method='PATCH'):
        Direction.objects.filter(id=d.id).update(name='__chg_revrev_v2__')
    revert.revert_context(_ctx_of(d.id))
    revert_ctx = (Context.objects
                  .filter(metadata__operation='changelog.revert')
                  .order_by('-created_at').first().pk)
    with pytest.raises(RevertForbidden):
        revert.revert_context(revert_ctx)


def test_revert_attendance_composite_identity(admin_client):
    """lesson_attendance: pgh_obj_id (=lesson_id) не уникален per-row —
    откат идентифицирует строки парой (lesson_id, student_id)."""
    from apps.lessons.models import Lesson, LessonAttendance
    from apps.groups.models import Group
    from apps.students.models import Student
    from apps.teachers.models import Teacher
    from django.utils import timezone

    t = Teacher.objects.create(name='__chg_att_t__', created_at=timezone.now())
    dr = Direction.objects.create(name='__chg_att_d__')
    g = Group.objects.create(name='__chg_att_g__', direction=dr, teacher=t, is_individual=False, created_at=timezone.now())
    s1 = Student.objects.create(full_name='__chg_att_s1__', created_at=timezone.now())
    s2 = Student.objects.create(full_name='__chg_att_s2__', created_at=timezone.now())
    lesson = Lesson.objects.create(
        group=g, teacher=t, lesson_date='2026-07-01', lesson_number=1,
        lesson_duration_minutes=90, lesson_type='regular',
        submitted_at=timezone.now(), submitted_by_token='__chg__',
    )
    LessonAttendance.objects.bulk_create([
        LessonAttendance(lesson=lesson, student=s1, present=True),
        LessonAttendance(lesson=lesson, student=s2, present=True),
    ])
    # Операция: одна клетка меняется на False
    with pghistory.context(url='/t', method='PATCH'):
        LessonAttendance.objects.filter(lesson=lesson, student=s2).update(present=False)
    ev = apps.get_model('lessons', 'LessonAttendanceEvent')
    ctx = (ev.objects.filter(pgh_context_id__isnull=False)
           .order_by('-pgh_id').first().pgh_context_id)
    revert.revert_context(ctx)
    a1 = LessonAttendance.objects.get(lesson=lesson, student=s1)
    a2 = LessonAttendance.objects.get(lesson=lesson, student=s2)
    assert a1.present is True   # чужая строка не тронута
    assert a2.present is True   # откачено
