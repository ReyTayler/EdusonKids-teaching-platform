"""Откат вставки не должен каскадом удалять неоткатываемые данные.

undo-insert удаляет вставленные строки ORM-ом, а Django-коллектор каскадит
по on_delete=CASCADE. Записи об отменах уроков (revertable=False) висят на группе
CASCADE'ом: откат «создания группы» молча стёр бы их в обход запрета (находка
ревью M1). Теперь удаляемое собирается коллектором заранее, и неоткатываемая
модель среди него — RevertForbidden (400).

Запись об отмене создаётся в СВОЁМ pghistory.context: GUC контекста живёт до
конца транзакции, а тест целиком — одна транзакция; без явного контекста запись
попала бы в операцию создания группы, и откат запретил бы уже обычный
revertable-фильтр — тест прошёл бы, ничего не проверив.
"""
from __future__ import annotations

import datetime

import pghistory
import pytest
from django.apps import apps
from django.utils import timezone

from apps.changelog import revert
from apps.changelog.revert import RevertForbidden

pytestmark = pytest.mark.django_db


def _insert_ctx(model_label: str, obj_id: int):
    app_label, name = model_label.split('.')
    ev = apps.get_model(app_label, f'{name}Event')
    return (ev.objects.filter(pgh_obj_id=obj_id, pgh_label='insert',
                              pgh_context_id__isnull=False)
            .order_by('-pgh_id').first().pgh_context_id)


@pytest.fixture
def dir_teacher(db):
    from apps.directions.models import Direction
    from apps.teachers.models import Teacher
    direction = Direction.objects.create(name='__chg_casc_dir__', total_lessons=4)
    teacher = Teacher.objects.create(name='__chg_casc_t__', created_at=timezone.now())
    return direction, teacher


def _record(group_id: int, teacher_id: int | None, planned_lesson=None):
    from apps.cancellations.models import LessonCancellation
    with pghistory.context(url='/api/admin/groups/x/plan/y/cancel', method='POST'):
        return LessonCancellation.objects.create(
            group_id=group_id, lesson_date=datetime.date(2026, 7, 21),
            scheduled_time=datetime.time(18, 0), teacher_id=teacher_id,
            planned_lesson=planned_lesson, side='teacher', reason=None,
            reason_text='тест', is_other=True, notified_on=None, compensated=False,
        )


def test_revert_group_create_forbidden_when_it_would_delete_cancellations(
        superadmin_client, dir_teacher):
    from apps.cancellations.models import LessonCancellation
    from apps.groups.models import Group

    direction, teacher = dir_teacher
    resp = superadmin_client.post('/api/admin/groups', {
        'name': '__chg_casc_g__', 'direction_id': direction.id, 'teacher_id': teacher.id,
        'is_individual': False, 'lesson_duration_minutes': 60, 'lessons_per_week': 1,
        'slots': [],
    }, format='json')
    assert resp.status_code == 201, resp.content
    gid = resp.json()['id']
    ctx = _insert_ctx('groups.Group', gid)
    rec = _record(gid, teacher.id)

    resp = superadmin_client.post(f'/api/admin/changelog/{ctx}/revert')
    assert resp.status_code == 400, resp.content
    assert 'записи об отменах уроков' in resp.json()['error']
    assert Group.objects.filter(id=gid).exists()
    assert LessonCancellation.objects.filter(id=rec.id).exists()


def test_revert_group_create_without_cancellations_still_works(dir_teacher):
    """Контроль: без записей об отменах откат создания группы проходит."""
    from apps.groups.models import Group

    direction, teacher = dir_teacher
    with pghistory.context(url='/api/admin/groups', method='POST'):
        g = Group.objects.create(name='__chg_casc_g2__', direction=direction, teacher=teacher,
                                 is_individual=False, lesson_duration_minutes=60,
                                 lessons_per_week=1, created_at=timezone.now())
    revert.revert_context(_insert_ctx('groups.Group', g.id))
    assert not Group.objects.filter(id=g.id).exists()


def test_revert_marker_insert_keeps_record_via_set_null(dir_teacher):
    """SET NULL — не удаление: откат вставки маркера отвязывает запись, а не
    стирает её, и откат не запрещается (запись переживает маркер, §3)."""
    from apps.cancellations.models import LessonCancellation
    from apps.groups.models import Group
    from apps.scheduling.models import PlannedLesson

    direction, teacher = dir_teacher
    g = Group.objects.create(name='__chg_casc_g3__', direction=direction, teacher=teacher,
                             is_individual=False, lesson_duration_minutes=60,
                             lessons_per_week=1, created_at=timezone.now())
    now = timezone.now()
    with pghistory.context(url='/t', method='POST'):
        marker = PlannedLesson.objects.create(
            group=g, seq=None, lesson_number=None, scheduled_date=datetime.date(2026, 7, 21),
            scheduled_time=datetime.time(18, 0), teacher=teacher, status='cancelled',
            created_at=now, updated_at=now)
    rec = _record(g.id, teacher.id, planned_lesson=marker)

    revert.revert_context(_insert_ctx('scheduling.PlannedLesson', marker.id))
    assert not PlannedLesson.objects.filter(id=marker.id).exists()
    rec.refresh_from_db()
    assert rec.planned_lesson_id is None


def test_revert_teacher_create_protected_is_forbidden_not_500(dir_teacher):
    """Преподаватель, на которого ссылается запись об отмене (PROTECT): откат его
    создания — понятный отказ, а не ProtectedError (500)."""
    from apps.groups.models import Group
    from apps.teachers.models import Teacher

    direction, teacher = dir_teacher
    with pghistory.context(url='/api/admin/teachers', method='POST'):
        t2 = Teacher.objects.create(name='__chg_casc_t2__', created_at=timezone.now())
    # Группа — своя операция: иначе GUC контекста выше протёк бы на неё, и откат
    # удалял бы ещё и группу (каскад), а не упирался в PROTECT.
    with pghistory.context(url='/api/admin/groups', method='POST'):
        g = Group.objects.create(name='__chg_casc_g4__', direction=direction, teacher=teacher,
                                 is_individual=False, lesson_duration_minutes=60,
                                 lessons_per_week=1, created_at=timezone.now())
    _record(g.id, t2.id)
    with pytest.raises(RevertForbidden) as exc_info:
        revert.revert_context(_insert_ctx('teachers.Teacher', t2.id))
    assert str(exc_info.value) == ('Откат недоступен: на удаляемые данные ссылаются '
                                   'другие записи (отмена урока).')
    assert Teacher.objects.filter(id=t2.id).exists()


def test_revert_student_create_restricted_by_deal_human_text(superadmin_client):
    """RESTRICT (сделка продления держит ученика): отказ общим текстом с русской
    подписью сущности, без технического label модели."""
    from apps.renewals.models import RenewalDeal, RenewalPipeline, RenewalStage
    from apps.students.models import Student

    with pghistory.context(url='/api/admin/students', method='POST'):
        st = Student.objects.create(full_name='__chg_casc_st__', created_at=timezone.now())
    with pghistory.context(url='/api/admin/renewals', method='POST'):
        pipe = RenewalPipeline.objects.create(name='__chg_casc_pipe__')
        stage = RenewalStage.objects.create(pipeline=pipe, key='__chg_casc__', label='X',
                                            sort_order=0, kind=RenewalStage.Kind.values[0])
        RenewalDeal.objects.create(student=st, cycle_no=1, pipeline=pipe, stage=stage)

    ctx = _insert_ctx('students.Student', st.id)
    resp = superadmin_client.post(f'/api/admin/changelog/{ctx}/revert')
    assert resp.status_code == 400, resp.content
    error = resp.json()['error']
    assert error.startswith('Откат недоступен: на удаляемые данные ссылаются другие записи')
    assert 'сделка продления' in error
    assert 'renewals.' not in error and 'RenewalDeal' not in error
    assert Student.objects.filter(id=st.id).exists()
