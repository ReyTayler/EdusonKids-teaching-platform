"""Отмена урока с причиной: POST /api/admin/groups/<pk>/plan/<lid>/cancel (B4 плана).

Тело обязательно: lesson_date (дата занятия, как её видел клиент), side,
reason_id XOR other_text, notified_on (только «Клиент»). Вся валидация — до
мутаций; маркер, запись LessonCancellation и начисление — в одной транзакции.
Повторный POST того же занятия — 409 (строка уже уехала в конец курса), а не
отмена следующего урока с ещё одним начислением.

Группа выдуманная: 4 pending-занятия по вторникам июля 2026, слот вт 18:00.
"""
from __future__ import annotations

import datetime

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db

DAYS = ['2026-07-07', '2026-07-14', '2026-07-21', '2026-07-28']


@pytest.fixture
def cgroup(db, payroll_rates):
    """Группа с планом из 4 занятий + ставки (начисление за отмену не 0)."""
    from apps.cancellations.models import LessonCancellation
    from apps.directions.models import Direction
    from apps.groups.models import Group, GroupScheduleSlot
    from apps.scheduling.models import PlannedLesson
    from apps.teachers.models import Teacher

    now = timezone.now()
    direction = Direction.objects.create(name='Тест Отмены Направление', total_lessons=4)
    teacher = Teacher.objects.create(name='Тест Отмены Преподаватель', created_at=now)
    group = Group.objects.create(
        name='Тест Отмены Группа', direction=direction, teacher=teacher,
        is_individual=False, lesson_duration_minutes=60, lessons_per_week=1,
        active=True, lesson_number_offset=0, created_at=now,
    )
    # Вс=0 → 2 = вторник: совпадает с датами строк, нужен для поиска слота в конце курса.
    GroupScheduleSlot.objects.create(group=group, day_of_week=2,
                                     start_time=datetime.time(18, 0),
                                     effective_from=datetime.date(2026, 7, 1))
    rows = [
        PlannedLesson.objects.create(
            group=group, seq=i, lesson_number=i, scheduled_date=d,
            scheduled_time=datetime.time(18, 0), teacher=teacher, status='pending',
            created_at=now, updated_at=now)
        for i, d in enumerate(DAYS, start=1)
    ]
    yield {'group': group, 'teacher': teacher, 'rows': rows}
    # Запись ссылается на учётку автора, а фикстура клиента удаляет учётку сырым
    # DELETE; Django сверяет отложенные FK в конце теста. Поэтому клиентскую
    # фикстуру тест запрашивает РАНЬШЕ cgroup — эта очистка идёт до её teardown.
    LessonCancellation.objects.filter(group=group).delete()


def _url(gid: int, lid: int) -> str:
    return f'/api/admin/groups/{gid}/plan/{lid}/cancel'


def _body(**over):
    """Валидное тело стороны «Преподаватель» с «Другой причиной»."""
    return {'lesson_date': '2026-07-21', 'side': 'teacher', 'other_text': 'Срочная поездка',
            **over}


def _snapshot(gid: int):
    """Состояние, которое не должна менять отклонённая отмена: строки плана
    (id, дата, время, статус, seq), начисления и записи об отменах, уведомления."""
    from apps.cancellations.models import LessonCancellation
    from apps.notifications.models import NotificationMessage
    from apps.payroll.models import CancellationPay
    from apps.scheduling.models import PlannedLesson

    plan = list(PlannedLesson.objects.filter(group_id=gid).order_by('id').values_list(
        'id', 'scheduled_date', 'scheduled_time', 'status', 'seq', 'lesson_number'))
    return (plan,
            CancellationPay.objects.filter(group_id=gid).count(),
            LessonCancellation.objects.filter(group_id=gid).count(),
            NotificationMessage.objects.count())


def _manager_id() -> int:
    from apps.accounts.models import Account
    return Account.objects.get(email='__root_manager__@test.local').id


# ---------------------------------------------------------------------------
# Успех
# ---------------------------------------------------------------------------

def test_client_reason_creates_one_record(manager_client, cgroup, make_reason):
    from apps.cancellations.models import LessonCancellation
    from apps.scheduling.models import PlannedLesson

    gid, row = cgroup['group'].id, cgroup['rows'][2]
    reason = make_reason(side='client', name='Заболел', compensation=True)
    resp = manager_client.post(_url(gid, row.id), _body(
        side='client', other_text=None, reason_id=reason.id, notified_on='2026-07-21',
    ), format='json')
    assert resp.status_code == 200, resp.content
    assert isinstance(resp.json(), list)  # ответ — план, как раньше

    marker = PlannedLesson.objects.get(group_id=gid, seq__isnull=True, status='cancelled')
    rec = LessonCancellation.objects.get(group_id=gid)
    assert rec.planned_lesson_id == marker.id
    assert (rec.side, rec.reason_id, rec.reason_text, rec.is_other) == (
        'client', reason.id, 'Заболел', False)
    assert rec.notified_on == datetime.date(2026, 7, 21)
    # Уведомили в день урока, причина с компенсацией → компенсация есть.
    assert rec.compensated is True
    assert rec.created_by_id == _manager_id()
    assert rec.teacher_id == cgroup['teacher'].id
    assert (rec.lesson_date, rec.scheduled_time) == (marker.scheduled_date,
                                                     marker.scheduled_time)
    assert rec.lesson_date == datetime.date(2026, 7, 21)


def test_client_notified_before_lesson_day_not_compensated(manager_client, cgroup,
                                                           make_reason):
    """Снимок решения считается по правилу §4: клиент предупредил заранее →
    компенсации нет, хотя у причины флаг включён."""
    from apps.cancellations.models import LessonCancellation

    gid, row = cgroup['group'].id, cgroup['rows'][2]
    reason = make_reason(side='client', compensation=True)
    resp = manager_client.post(_url(gid, row.id), _body(
        side='client', other_text=None, reason_id=reason.id, notified_on='2026-07-20',
    ), format='json')
    assert resp.status_code == 200, resp.content
    assert LessonCancellation.objects.get(group_id=gid).compensated is False


def test_teacher_reason_snapshot(manager_client, cgroup, make_reason):
    from apps.cancellations.models import LessonCancellation

    gid, row = cgroup['group'].id, cgroup['rows'][2]
    reason = make_reason(side='teacher', name='Болезнь преподавателя', compensation=True)
    resp = manager_client.post(_url(gid, row.id), _body(other_text=None, reason_id=reason.id),
                               format='json')
    assert resp.status_code == 200, resp.content
    rec = LessonCancellation.objects.get(group_id=gid)
    assert (rec.side, rec.reason_id, rec.reason_text, rec.is_other, rec.notified_on,
            rec.compensated) == ('teacher', reason.id, 'Болезнь преподавателя', False, None,
                                 True)


def test_other_reason_trimmed_and_never_compensated(manager_client, cgroup):
    from apps.cancellations.models import LessonCancellation

    gid, row = cgroup['group'].id, cgroup['rows'][2]
    resp = manager_client.post(_url(gid, row.id), _body(other_text='  Срочная поездка  '),
                               format='json')
    assert resp.status_code == 200, resp.content
    rec = LessonCancellation.objects.get(group_id=gid)
    assert (rec.reason_id, rec.reason_text, rec.is_other, rec.compensated) == (
        None, 'Срочная поездка', True, False)


def test_teacher_side_ignores_notified_on(manager_client, cgroup):
    """В7а: дата уведомления для стороны «Преподаватель» игнорируется, а не 400."""
    from apps.cancellations.models import LessonCancellation

    gid, row = cgroup['group'].id, cgroup['rows'][2]
    resp = manager_client.post(_url(gid, row.id), _body(notified_on='2026-07-20'),
                               format='json')
    assert resp.status_code == 200, resp.content
    assert LessonCancellation.objects.get(group_id=gid).notified_on is None


def test_record_teacher_is_effective_substitute(manager_client, cgroup):
    """Запись несёт эффективного преподавателя маркера: замену на дату, если была."""
    from apps.cancellations.models import LessonCancellation
    from apps.teachers.models import Teacher

    gid, row = cgroup['group'].id, cgroup['rows'][2]
    sub = Teacher.objects.create(name='Тест Отмены Замена', created_at=timezone.now())
    row.substitute_teacher = sub
    row.save(update_fields=['substitute_teacher'])
    resp = manager_client.post(_url(gid, row.id), _body(), format='json')
    assert resp.status_code == 200, resp.content
    assert LessonCancellation.objects.get(group_id=gid).teacher_id == sub.id


# ---------------------------------------------------------------------------
# 400 — до мутаций
# ---------------------------------------------------------------------------

def _tomorrow_msk() -> str:
    from apps.core.utils.dates import msk_now
    return (msk_now().date() + datetime.timedelta(days=1)).isoformat()


@pytest.mark.parametrize('case', [
    'no_body', 'bad_side', 'both', 'neither', 'blank_other', 'too_long', 'unknown_field',
    'invisible_other', 'invisible_mix_other',
    'missing_lesson_date', 'bad_lesson_date', 'client_no_notified', 'notified_tomorrow',
    'reason_missing', 'reason_deleted', 'reason_other_side',
])
def test_invalid_body_400_and_nothing_changes(manager_client, cgroup, make_reason, case):
    gid, row = cgroup['group'].id, cgroup['rows'][2]
    client_reason = make_reason(side='client', name='Заболел')
    deleted = make_reason(side='teacher', name='Удалённая', deleted_at=timezone.now())
    bodies = {
        'no_body': None,
        'bad_side': _body(side='parent'),
        'both': _body(reason_id=client_reason.id),
        'neither': _body(other_text=None),
        'blank_other': _body(other_text='   '),
        'too_long': _body(other_text='я' * 1001),
        # Только невидимые символы форматирования (Unicode Cf) — причины нет.
        'invisible_other': _body(other_text='\u200b'),
        'invisible_mix_other': _body(other_text='\ufeff \u2060'),
        'unknown_field': _body(comment='лишнее'),
        'missing_lesson_date': {k: v for k, v in _body().items() if k != 'lesson_date'},
        'bad_lesson_date': _body(lesson_date='21.07.2026'),
        'client_no_notified': _body(side='client', other_text='Уехали'),
        'notified_tomorrow': _body(side='client', other_text='Уехали',
                                   notified_on=_tomorrow_msk()),
        'reason_missing': _body(other_text=None, reason_id=999_999_999),
        'reason_deleted': _body(other_text=None, reason_id=deleted.id),
        'reason_other_side': _body(other_text=None, reason_id=client_reason.id),
    }
    before = _snapshot(gid)
    body = bodies[case]
    if body is None:
        resp = manager_client.post(_url(gid, row.id))
    else:
        resp = manager_client.post(_url(gid, row.id), body, format='json')
    assert resp.status_code == 400, (case, resp.content)
    assert 'error' in resp.json()
    assert _snapshot(gid) == before


def test_reason_errors_are_human_readable(manager_client, cgroup, make_reason):
    gid, row = cgroup['group'].id, cgroup['rows'][2]
    client_reason = make_reason(side='client', name='Заболел')
    resp = manager_client.post(_url(gid, row.id), _body(other_text=None, reason_id=999_999_999),
                               format='json')
    assert resp.json()['error'] == 'Причина не найдена или удалена.'
    resp = manager_client.post(_url(gid, row.id),
                               _body(other_text=None, reason_id=client_reason.id),
                               format='json')
    assert resp.json()['error'] == 'Причина не относится к выбранной стороне.'


def test_cancel_marker_row_rejected_without_record(manager_client, cgroup):
    """Отмена самого маркера — прежний 400 «не курсовое», запись не появляется."""
    from apps.cancellations.models import LessonCancellation
    from apps.scheduling.models import PlannedLesson

    gid, row = cgroup['group'].id, cgroup['rows'][2]
    assert manager_client.post(_url(gid, row.id), _body(), format='json').status_code == 200
    marker = PlannedLesson.objects.get(group_id=gid, seq__isnull=True, status='cancelled')
    resp = manager_client.post(_url(gid, marker.id), _body(), format='json')
    assert resp.status_code == 400
    assert LessonCancellation.objects.filter(group_id=gid).count() == 1


def test_missing_lesson_404(manager_client, cgroup):
    resp = manager_client.post(_url(cgroup['group'].id, 999_999_999), _body(), format='json')
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 409 и двойная отмена (В1)
# ---------------------------------------------------------------------------

def test_lesson_date_mismatch_409(manager_client, cgroup):
    gid, row = cgroup['group'].id, cgroup['rows'][2]
    before = _snapshot(gid)
    resp = manager_client.post(_url(gid, row.id), _body(lesson_date='2026-07-14'),
                               format='json')
    assert resp.status_code == 409, resp.content
    assert 'обновите план' in resp.json()['error']
    assert _snapshot(gid) == before


def test_double_cancel_409_one_marker_one_record_one_pay(manager_client, cgroup):
    """Двойной клик / две вкладки: второй POST того же занятия с той же датой
    не отменяет новый последний урок курса."""
    from apps.cancellations.models import LessonCancellation
    from apps.payroll.models import CancellationPay
    from apps.scheduling.models import PlannedLesson

    gid, row = cgroup['group'].id, cgroup['rows'][2]
    assert manager_client.post(_url(gid, row.id), _body(), format='json').status_code == 200
    after_first = _snapshot(gid)
    resp = manager_client.post(_url(gid, row.id), _body(), format='json')
    assert resp.status_code == 409, resp.content
    assert _snapshot(gid) == after_first
    assert PlannedLesson.objects.filter(group_id=gid, seq__isnull=True,
                                        status='cancelled').count() == 1
    assert LessonCancellation.objects.filter(group_id=gid).count() == 1
    assert CancellationPay.objects.filter(group_id=gid).count() == 1


def test_existing_marker_on_key_400_when_row_stays(manager_client, cgroup):
    """Нет свободного слота в конце курса → строка остаётся на месте, и сверка
    даты повтор не ловит. Второй страж — маркер на (группа, дата, время) → 400."""
    from apps.cancellations.models import LessonCancellation
    from apps.groups.models import GroupScheduleSlot
    from apps.payroll.models import CancellationPay

    gid, row = cgroup['group'].id, cgroup['rows'][2]
    # Закрытый слот: _next_slot_after не найдёт места, строка не переедет.
    GroupScheduleSlot.objects.filter(group_id=gid).update(
        effective_to=datetime.date(2026, 7, 31))
    assert manager_client.post(_url(gid, row.id), _body(), format='json').status_code == 200
    row.refresh_from_db()
    assert row.scheduled_date == datetime.date(2026, 7, 21)  # предпосылка теста
    after_first = _snapshot(gid)
    resp = manager_client.post(_url(gid, row.id), _body(), format='json')
    assert resp.status_code == 400, resp.content
    assert resp.json()['error'] == ('На это время уже есть отменённое занятие. '
                                    'Перенесите занятие на другое время и повторите.')
    assert _snapshot(gid) == after_first
    assert LessonCancellation.objects.filter(group_id=gid).count() == 1
    assert CancellationPay.objects.filter(group_id=gid).count() == 1


# ---------------------------------------------------------------------------
# Права
# ---------------------------------------------------------------------------

def test_teacher_403(teacher_client, cgroup):
    from apps.cancellations.models import LessonCancellation

    gid, row = cgroup['group'].id, cgroup['rows'][2]
    assert teacher_client.post(_url(gid, row.id), _body(), format='json').status_code == 403
    assert not LessonCancellation.objects.filter(group_id=gid).exists()


@pytest.mark.parametrize('role_client', ['manager_client', 'admin_client', 'superadmin_client'])
def test_staff_can_cancel(request, role_client):
    client = request.getfixturevalue(role_client)
    cgroup = request.getfixturevalue('cgroup')  # после клиента — см. teardown cgroup
    gid, row = cgroup['group'].id, cgroup['rows'][2]
    assert client.post(_url(gid, row.id), _body(), format='json').status_code == 200


# ---------------------------------------------------------------------------
# Атомарность
# ---------------------------------------------------------------------------

def test_record_failure_rolls_back_whole_cancel(cgroup, monkeypatch):
    """Запись причины не создалась → отмены нет: ни маркера, ни переноса строки,
    ни начисления. (Уведомление ставится в очередь после cancel_lesson, до него
    этот сценарий не доходит — outbox здесь не проверяется по существу.)"""
    from apps.scheduling import services

    def boom(*args, **kwargs):
        raise RuntimeError('сбой записи отмены')

    monkeypatch.setattr('apps.cancellations.repository.create_for_marker', boom)
    gid, row = cgroup['group'].id, cgroup['rows'][2]
    before = _snapshot(gid)
    data = {'lesson_date': '2026-07-21', 'side': 'teacher', 'other_text': 'тест',
            'reason_id': None, 'notified_on': None}
    with pytest.raises(RuntimeError):
        services.cancel(gid, row.id, data, None)
    assert _snapshot(gid) == before


def test_failure_after_record_rolls_back_record_marker_and_move(cgroup, monkeypatch):
    """Сбой ПОСЛЕ создания записи (в пересчёте начислений) откатывает и уже
    созданную запись, и маркер, и перенос строки в конец курса."""
    from apps.cancellations.models import LessonCancellation
    from apps.scheduling import services
    from apps.scheduling.models import PlannedLesson

    gid, row = cgroup['group'].id, cgroup['rows'][2]
    seen = {}

    def boom(*args, **kwargs):
        # Предпосылка: к моменту сбоя запись и маркер уже вставлены, строка
        # перенесена — иначе тест проверял бы откат пустого места.
        seen['records'] = LessonCancellation.objects.filter(group_id=gid).count()
        seen['markers'] = PlannedLesson.objects.filter(
            group_id=gid, seq__isnull=True, status='cancelled').count()
        seen['row_date'] = PlannedLesson.objects.get(id=row.id).scheduled_date
        raise RuntimeError('сбой пересчёта начислений')

    monkeypatch.setattr('apps.scheduling.repository._recalc_cancellations', boom)
    before = _snapshot(gid)
    data = {'lesson_date': '2026-07-21', 'side': 'teacher', 'other_text': 'тест',
            'reason_id': None, 'notified_on': None}
    with pytest.raises(RuntimeError):
        services.cancel(gid, row.id, data, None)
    assert seen == {'records': 1, 'markers': 1, 'row_date': datetime.date(2026, 8, 4)}
    assert _snapshot(gid) == before


def test_other_text_drops_invisible_chars_keeps_lines(manager_client, cgroup):
    """Невидимые символы форматирования (Cf) вырезаются, переводы строк —
    сохраняются: многострочное описание причины остаётся многострочным."""
    from apps.cancellations.models import LessonCancellation

    gid, row = cgroup['group'].id, cgroup['rows'][2]
    resp = manager_client.post(_url(gid, row.id),
                               _body(other_text='\u200bТекст\nвторая\u2060 строка'),
                               format='json')
    assert resp.status_code == 200, resp.content
    assert LessonCancellation.objects.get(group_id=gid).reason_text == 'Текст\nвторая строка'


def test_marker_uses_locked_row_not_stale_anchor(manager_client, cgroup, monkeypatch):
    """Гонка: между ранним чтением строки и локом курса другой менеджер ставит
    замену и меняет время. Маркер, запись и начисление обязаны взять данные
    залоченной строки, а не устаревший снимок."""
    from apps.cancellations.models import LessonCancellation
    from apps.payroll.models import CancellationPay
    from apps.scheduling import repository
    from apps.scheduling.models import PlannedLesson
    from apps.teachers.models import Teacher

    gid, row = cgroup['group'].id, cgroup['rows'][2]
    sub = Teacher.objects.create(name='Тест Отмены Гонка', created_at=timezone.now())
    original = repository.get_plan_lesson

    def stale_then_change(group_id, lesson_id):
        anchor = original(group_id, lesson_id)
        PlannedLesson.objects.filter(id=lesson_id).update(
            substitute_teacher=sub, scheduled_time=datetime.time(19, 30))
        return anchor

    monkeypatch.setattr(repository, 'get_plan_lesson', stale_then_change)
    resp = manager_client.post(_url(gid, row.id), _body(), format='json')
    assert resp.status_code == 200, resp.content

    marker = PlannedLesson.objects.get(group_id=gid, seq__isnull=True, status='cancelled')
    rec = LessonCancellation.objects.get(group_id=gid)
    pay = CancellationPay.objects.get(group_id=gid)
    assert (marker.teacher_id, marker.scheduled_time) == (sub.id, datetime.time(19, 30))
    assert (rec.teacher_id, rec.scheduled_time) == (sub.id, datetime.time(19, 30))
    assert (pay.teacher_id, pay.scheduled_time) == (sub.id, datetime.time(19, 30))
