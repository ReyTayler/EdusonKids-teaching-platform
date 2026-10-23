"""Сквозные граничные случаи причин отмены (спека 2026-10-23, §7) — как пользователь, через API.

Дополняют, а не повторяют соседние файлы: те проверяют правило, валидацию и движок
по частям, здесь — цепочки «superadmin настроил причины → manager отменил →
вкладка группы → зарплата в admin и в кабинете преподавателя», снимок при правках
справочника, переживание маркера, граница дат по МСК, матрица прав, старые отмены.

Данные выдуманные. Группа — 12 pending-занятий по вторникам с 2026-07-07
(слот вт 18:00), ставки payroll_rates: групповой урок 500, отмена 50 % → 250.
Клиенты ролей запрашиваются через eg ДО самой группы (см. teardown fixture eg).
"""
from __future__ import annotations

import datetime
import json
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone

pytestmark = pytest.mark.django_db

D = Decimal
T18 = datetime.time(18, 0)
FIRST_TUESDAY = datetime.date(2026, 7, 7)
SECRET_TEXT = 'Мама Сидорова Пети уехала на дачу'
MSK = datetime.timezone(datetime.timedelta(hours=3))


# ---------------------------------------------------------------------------
# Фикстуры и помощники
# ---------------------------------------------------------------------------

@pytest.fixture
def eg(db, payroll_rates, manager_client, admin_client, superadmin_client):
    """Группа с планом из 12 занятий + ставки. Клиенты ролей — параметры: они
    ставятся до группы, а их учётки удаляются сырым DELETE уже после очистки
    записей отмен (отложенный FK created_by сверяется в конце теста)."""
    from apps.cancellations.models import LessonCancellation
    from apps.directions.models import Direction
    from apps.groups.models import Group, GroupScheduleSlot
    from apps.scheduling.models import PlannedLesson
    from apps.teachers.models import Teacher

    now = timezone.now()
    direction = Direction.objects.create(name='Тест Края Направление', total_lessons=12)
    teacher = Teacher.objects.create(name='Тест Края Преподаватель', created_at=now)
    group = Group.objects.create(
        name='Тест Края Группа', direction=direction, teacher=teacher,
        is_individual=False, lesson_duration_minutes=60, lessons_per_week=1,
        active=True, lesson_number_offset=0, created_at=now,
    )
    GroupScheduleSlot.objects.create(group=group, day_of_week=2, start_time=T18,
                                     effective_from=datetime.date(2026, 7, 1))
    for i in range(12):
        PlannedLesson.objects.create(
            group=group, seq=i + 1, lesson_number=i + 1,
            scheduled_date=FIRST_TUESDAY + datetime.timedelta(weeks=i),
            scheduled_time=T18, teacher=teacher, status='pending',
            created_at=now, updated_at=now)
    yield {'group': group, 'gid': group.id, 'teacher': teacher, 'tid': teacher.id,
           'direction': direction}
    LessonCancellation.objects.filter(group=group).delete()


@pytest.fixture
def own_teacher_client(eg):
    """Кабинет преподавателя группы: учётка role=teacher с teacher_id группы."""
    from conftest import _create_account, _delete_account, _jwt_client_for_id

    acc_id, _ = _create_account('__edge_own_teacher__@test.local', 'teacher',
                                teacher_id=eg['tid'])
    yield _jwt_client_for_id(acc_id)
    _delete_account(acc_id)


def _first_pending(gid: int):
    from apps.scheduling.models import PlannedLesson
    return (PlannedLesson.objects.filter(group_id=gid, seq__isnull=False, status='pending')
            .order_by('seq').first())


def _cancel_url(gid: int, lid: int) -> str:
    return f'/api/admin/groups/{gid}/plan/{lid}/cancel'


def _cancel_first(client, eg, **body):
    """Отменить первое незавершённое занятие плана. Возвращает дату занятия: строка
    плана после отмены уезжает в конец курса, поэтому дату берём до запроса."""
    row = _first_pending(eg['gid'])
    day = row.scheduled_date
    resp = client.post(_cancel_url(eg['gid'], row.id),
                       {'lesson_date': day.isoformat(), **body}, format='json')
    assert resp.status_code == 200, resp.content
    return day


def _iso(day: datetime.date, shift: int = 0) -> str:
    return (day + datetime.timedelta(days=shift)).isoformat()


def _admin_pay_rows(superadmin_client, gid: int) -> dict[str, dict]:
    """Начисления за отмены группы в admin «Зарплата», ключ — дата урока."""
    resp = superadmin_client.get(
        f'/api/admin/payroll?filter[group_id]={gid}&filter[kind]=cancellation&page_size=100')
    assert resp.status_code == 200, resp.content
    return {r['lesson_date']: r for r in resp.json()['rows']}


def _teacher_pay_rows(client) -> tuple[dict[str, dict], str]:
    """Строки-отмены из кабинета преподавателя за июнь–декабрь 2026 + весь JSON."""
    rows, raw = {}, ''
    for month in ('2026-06', '2026-07', '2026-08', '2026-09', '2026-10', '2026-11', '2026-12'):
        resp = client.get(f'/api/my-payroll?month={month}')
        assert resp.status_code == 200, resp.content
        raw += resp.content.decode()
        for r in resp.json()['rows']:
            if r['kind'] == 'cancellation':
                rows[r['date']] = r
    return rows, raw


def _tab(client, gid: int) -> list[dict]:
    resp = client.get(f'/api/admin/groups/{gid}/cancellations?page_size=100')
    assert resp.status_code == 200, resp.content
    return resp.json()['rows']


def _reason(client, side: str, name: str, compensation: bool) -> dict:
    resp = client.post('/api/admin/cancellation-reasons',
                       {'side': side, 'name': name, 'compensation': compensation},
                       format='json')
    assert resp.status_code == 201, resp.content
    return resp.json()


def _fake_msk_now(monkeypatch, moment: datetime.datetime) -> None:
    """Подменить «сейчас по МСК» там, где его читает валидация notified_on."""
    monkeypatch.setattr('apps.scheduling.serializers.msk_now', lambda: moment)


# ---------------------------------------------------------------------------
# 1. Полный цикл: все ветки правила → вкладка → зарплата admin и преподавателя
# ---------------------------------------------------------------------------

BRANCHES = [
    # (ключ, сторона, причина-ключ, сдвиг notified_on от даты урока, платят?, пояснение)
    ('t_on', 'teacher', 'Болезнь преподавателя', None, True, 'Преподаватель — Болезнь преподавателя · '),
    ('t_off', 'teacher', 'Отпуск преподавателя', None, False, 'Без компенсации: Преподаватель — Отпуск преподавателя'),
    ('c_on_before', 'client', 'Заболел ребёнок', -1, False, 'Без компенсации: Клиент — Заболел ребёнок'),
    ('c_on_same', 'client', 'Заболел ребёнок', 0, True, 'Клиент — Заболел ребёнок · '),
    ('c_on_after', 'client', 'Заболел ребёнок', 1, True, 'Клиент — Заболел ребёнок · '),
    ('c_off_same', 'client', 'Нет желания', 0, False, 'Без компенсации: Клиент — Нет желания'),
    ('t_other', 'teacher', None, None, False, 'Без компенсации: Преподаватель — Другая причина'),
    ('c_other', 'client', None, 0, False, 'Без компенсации: Клиент — Другая причина'),
]


def test_full_cycle_all_branches_tab_admin_payroll_and_teacher_cabinet(
        eg, superadmin_client, manager_client, own_teacher_client):
    reasons = {
        ('teacher', 'Болезнь преподавателя'): _reason(superadmin_client, 'teacher',
                                                      'Болезнь преподавателя', True),
        ('teacher', 'Отпуск преподавателя'): _reason(superadmin_client, 'teacher',
                                                     'Отпуск преподавателя', False),
        ('client', 'Заболел ребёнок'): _reason(superadmin_client, 'client',
                                               'Заболел ребёнок', True),
        ('client', 'Нет желания'): _reason(superadmin_client, 'client', 'Нет желания', False),
    }
    # manager видит справочник (форма отмены), но без флага компенсации.
    listing = manager_client.get('/api/admin/cancellation-reasons').json()
    assert [r['name'] for r in listing['client']] == ['Заболел ребёнок', 'Нет желания']
    assert all('compensation' not in r for r in listing['client'] + listing['teacher'])

    expected: dict[str, tuple[str, bool, str, bool]] = {}  # дата → (side, платят, note, other)
    for key, side, name, shift, paid, note in BRANCHES:
        body: dict = {'side': side}
        if name is None:
            body['other_text'] = SECRET_TEXT
        else:
            body['reason_id'] = reasons[(side, name)]['id']
        row_day = _first_pending(eg['gid']).scheduled_date
        if side == 'client':
            body['notified_on'] = _iso(row_day, shift)
        day = _cancel_first(manager_client, eg, **body)
        assert day == row_day
        expected[day.isoformat()] = (side, paid, note, name is None)

    # Вкладка группы: все восемь, новые сверху, «Другая» — полным текстом, без флага.
    tab = _tab(manager_client, eg['gid'])
    assert len(tab) == len(BRANCHES)
    assert [r['lesson_date'] for r in tab] == sorted(expected, reverse=True)
    for r in tab:
        side, _paid, _note, other = expected[r['lesson_date']]
        assert r['side'] == side and r['is_other'] is other
        assert 'compensated' not in r and 'compensation' not in r
        assert (r['reason_text'] == SECRET_TEXT) is other
        assert (r['notified_on'] is None) is (side == 'teacher')

    # Admin «Зарплата» (superadmin): сумма и пояснение по каждой ветке.
    admin_rows = _admin_pay_rows(superadmin_client, eg['gid'])
    assert set(admin_rows) == set(expected)
    for day, (_side, paid, note, other) in expected.items():
        pay = admin_rows[day]
        assert D(pay['payment']) == (D('250') if paid else D('0')), (day, pay)
        assert pay['is_manual'] is False
        assert pay['rule_note'].startswith(note), (day, pay['rule_note'])
        assert SECRET_TEXT not in pay['rule_note']

    # Кабинет преподавателя: то же самое, текст «Другой» не утекает никуда.
    cab_rows, raw = _teacher_pay_rows(own_teacher_client)
    assert set(cab_rows) == set(expected)
    for day, (_side, paid, note, _other) in expected.items():
        row = cab_rows[day]
        assert D(row['payment']) == (D('250') if paid else D('0')), (day, row)
        assert row['ruleNote'].startswith(note), (day, row['ruleNote'])
    assert SECRET_TEXT not in raw and 'Сидорова' not in raw


# ---------------------------------------------------------------------------
# 2. Снимок: правки справочника не меняют прошлое
# ---------------------------------------------------------------------------

def test_reason_edits_do_not_change_past_cancellations(eg, superadmin_client, manager_client,
                                                       own_teacher_client):
    on = _reason(superadmin_client, 'client', 'Заболел ребёнок', True)
    off = _reason(superadmin_client, 'client', 'Нет желания', False)
    _cancel_first(manager_client, eg, side='client', reason_id=on['id'],
                  notified_on=_iso(FIRST_TUESDAY))
    _cancel_first(manager_client, eg, side='client', reason_id=off['id'],
                  notified_on=_iso(FIRST_TUESDAY, 7))

    def state():
        return (_tab(manager_client, eg['gid']),
                {k: (v['payment'], v['rule_note'], v['is_manual'])
                 for k, v in _admin_pay_rows(superadmin_client, eg['gid']).items()},
                {k: (v['payment'], v['ruleNote'])
                 for k, v in _teacher_pay_rows(own_teacher_client)[0].items()})

    before = state()
    assert {p[0] for p in before[1].values()} == {'250.00', '0.00'}

    # Флаг «Заболел» выключили, «Нет желания» включили и переименовали.
    assert superadmin_client.patch(f'/api/admin/cancellation-reasons/{on["id"]}',
                                   {'compensation': False}, format='json').status_code == 200
    assert superadmin_client.patch(f'/api/admin/cancellation-reasons/{off["id"]}',
                                   {'compensation': True, 'name': 'Переименована'},
                                   format='json').status_code == 200
    assert state() == before

    # Пересчёты: сервисный, групповой и команда на всю историю.
    from apps.payroll.recalc import recalculate, recalculate_cancellations
    recalculate_cancellations(group_ids=[eg['gid']])
    recalculate(group_ids=[eg['gid']])
    call_command('recalculate_payroll')
    assert state() == before

    # Удаление причины: из формы пропала, история и деньги не изменились.
    assert superadmin_client.delete(
        f'/api/admin/cancellation-reasons/{on["id"]}').status_code == 204
    names = [r['name'] for r in manager_client.get('/api/admin/cancellation-reasons').json()['client']]
    assert 'Заболел ребёнок' not in names
    call_command('recalculate_payroll', '--group', str(eg['gid']))
    assert state() == before


# ---------------------------------------------------------------------------
# 3. Переживание маркера через API: перевод ученика, смена расписания
# ---------------------------------------------------------------------------

WHEN = pytest.mark.parametrize('when', ['past', 'future'])


def _today():
    from apps.core.utils.dates import msk_now
    return msk_now().date()


def _shift_plan_to_future(eg) -> None:
    """Перенести план группы на ближайшие вторники от сегодня+14 дней: отмена
    такого занятия — будущая, её начисление при удалении маркера удаляется."""
    from apps.scheduling.models import PlannedLesson

    start = _today() + datetime.timedelta(days=14)
    start += datetime.timedelta(days=(1 - start.weekday()) % 7)  # вторник
    for row in PlannedLesson.objects.filter(group_id=eg['gid'], seq__isnull=False):
        row.scheduled_date = start + datetime.timedelta(weeks=row.seq - 1)
        row.save(update_fields=['scheduled_date'])


def _cancel_no_comp_and_edit_by_hand(eg, manager_client, superadmin_client, when='past'):
    """Отмена «без компенсации». Прошлая — плюс ручная правка суммы 300 ₽ (pay_id);
    будущая — правка не нужна: её начисление после удаления маркера удаляется."""
    if when == 'future':
        _shift_plan_to_future(eg)
    reason = _reason(superadmin_client, 'client', 'Нет желания', False)
    notified = _iso(FIRST_TUESDAY) if when == 'past' else _today().isoformat()
    day = _cancel_first(manager_client, eg, side='client', reason_id=reason['id'],
                        notified_on=notified)
    pay = _admin_pay_rows(superadmin_client, eg['gid'])[day.isoformat()]
    assert D(pay['payment']) == D('0')
    if when == 'future':
        return day, pay['id']
    resp = superadmin_client.patch(f'/api/admin/payroll/cancellations/{pay["id"]}',
                                   {'payment': '300.00'}, format='json')
    assert resp.status_code == 200, resp.content
    return day, pay['id']


def _assert_survived(eg, manager_client, superadmin_client, day, pay_id, when='past'):
    tab = _tab(manager_client, eg['gid'])
    assert [(r['lesson_date'], r['side'], r['reason_text']) for r in tab] == [
        (day.isoformat(), 'client', 'Нет желания')]
    rows = _admin_pay_rows(superadmin_client, eg['gid'])
    if when == 'future':
        # Решение В5: запись остаётся в истории группы, а начисление будущей
        # отмены с удалённым маркером удаляется (деньги за несостоявшееся не платят).
        assert rows == {}
        return
    pay = rows[day.isoformat()]
    assert pay['id'] == pay_id
    assert (D(pay['payment']), pay['is_manual'], D(pay['calc_payment'])) == (
        D('300'), True, D('0'))
    assert pay['rule_note'] == 'Без компенсации: Клиент — Нет желания'


@WHEN
def test_transfer_student_rebuilds_plan_cancellation_and_manual_pay_survive(
        eg, manager_client, superadmin_client, when):
    """Перевод ученика (memberships/transfer → reset_plan целевой группы) удаляет
    маркер: запись во вкладке остаётся; прошлое начисление с ручной суммой тоже,
    будущее — удаляется (В5)."""
    from apps.groups.models import Group
    from apps.memberships import repository as membership_repo
    from apps.scheduling.models import PlannedLesson
    from apps.students.models import Student

    day, pay_id = _cancel_no_comp_and_edit_by_hand(eg, manager_client, superadmin_client, when)
    assert PlannedLesson.objects.filter(group_id=eg['gid'], seq__isnull=True).exists()

    source = Group.objects.create(
        name='Тест Края Откуда', direction=eg['direction'], teacher=eg['teacher'],
        is_individual=False, lesson_duration_minutes=60, lessons_per_week=1, active=True,
        created_at=timezone.now())
    student = Student.objects.create(full_name='Тест Края Ученик', created_at=timezone.now())
    old = membership_repo.add_membership({'group_id': source.id, 'student_id': student.id,
                                          'lessons_done': 3})
    resp = superadmin_client.post(f'/api/admin/memberships/{old["id"]}/transfer',
                                  {'to_group_id': eg['gid']}, format='json')
    assert resp.status_code == 200, resp.content

    # Предпосылка: маркер действительно удалён пересборкой плана.
    assert not PlannedLesson.objects.filter(group_id=eg['gid'], seq__isnull=True,
                                            status='cancelled').exists()
    _assert_survived(eg, manager_client, superadmin_client, day, pay_id, when)
    call_command('recalculate_payroll', '--group', str(eg['gid']))
    _assert_survived(eg, manager_client, superadmin_client, day, pay_id, when)


@WHEN
def test_permanent_schedule_change_keeps_cancellation_and_manual_pay(
        eg, manager_client, superadmin_client, when):
    from apps.scheduling.models import PlannedLesson

    day, pay_id = _cancel_no_comp_and_edit_by_hand(eg, manager_client, superadmin_client, when)
    # С даты не позже маркера, иначе wipe_one_offs его не заденет.
    effective_from = '2026-07-01' if when == 'past' else day.isoformat()
    resp = manager_client.post(
        f'/api/admin/groups/{eg["gid"]}/plan/permanent-change',
        {'from_seq': 1, 'effective_from': effective_from,
         'new_slots': [{'day_of_week': 3, 'start_time': '11:00'}]}, format='json')
    assert resp.status_code == 200, resp.content

    # Смена расписания с даты до маркера сносит маркер (wipe_one_offs).
    assert not PlannedLesson.objects.filter(group_id=eg['gid'], seq__isnull=True,
                                            status='cancelled').exists()
    _assert_survived(eg, manager_client, superadmin_client, day, pay_id, when)
    call_command('recalculate_payroll', '--group', str(eg['gid']))
    _assert_survived(eg, manager_client, superadmin_client, day, pay_id, when)


def test_soft_update_purge_and_new_marker_keeps_cancellation_and_manual_pay(
        eg, manager_client, superadmin_client):
    """Мягкое обновление: факты группы удалены и залиты заново, маркер на тот же
    ключ создан снова — запись и начисление идут к нему, сумма и решение прежние."""
    from apps.cancellations.models import LessonCancellation
    from apps.eduson_import.sync import facts_without_history, purge_group_facts
    from apps.payroll.recalc import recalculate
    from apps.scheduling.models import PlannedLesson

    day, pay_id = _cancel_no_comp_and_edit_by_hand(eg, manager_client, superadmin_client)
    marker = PlannedLesson.objects.get(group_id=eg['gid'], seq__isnull=True)
    with facts_without_history():
        purge_group_facts([eg['gid']])
    assert not PlannedLesson.objects.filter(group_id=eg['gid']).exists()

    new_marker = PlannedLesson.objects.create(
        group_id=eg['gid'], seq=None, lesson_number=None, scheduled_date=day,
        scheduled_time=marker.scheduled_time, teacher_id=eg['tid'], status='cancelled',
        created_at=timezone.now(), updated_at=timezone.now())
    recalculate(group_ids=[eg['gid']])

    assert LessonCancellation.objects.get(group_id=eg['gid']).planned_lesson_id == new_marker.id
    _assert_survived(eg, manager_client, superadmin_client, day, pay_id)


# ---------------------------------------------------------------------------
# 4. Граница дат по МСК
# ---------------------------------------------------------------------------

@pytest.fixture
def day_group(eg):
    """Два занятия вокруг «сегодня» 2026-10-23: сегодня 18:00 и завтра 18:00 (pending)."""
    from apps.scheduling.models import PlannedLesson

    PlannedLesson.objects.filter(group_id=eg['gid']).delete()
    now = timezone.now()
    rows = {}
    for i, day in enumerate([datetime.date(2026, 10, 23), datetime.date(2026, 10, 24)]):
        rows[day] = PlannedLesson.objects.create(
            group_id=eg['gid'], seq=i + 1, lesson_number=i + 1, scheduled_date=day,
            scheduled_time=T18, teacher_id=eg['tid'], status='pending',
            created_at=now, updated_at=now)
    return rows


# Полночь МСК = 21:00 UTC предыдущих суток. 00:30 МСК 23-го — по UTC ещё 22-е.
AFTER_MIDNIGHT_MSK = datetime.datetime(2026, 10, 23, 0, 30, tzinfo=MSK)
BEFORE_MIDNIGHT_MSK = datetime.datetime(2026, 10, 23, 23, 59, tzinfo=MSK)


@pytest.mark.parametrize('moment', [AFTER_MIDNIGHT_MSK, BEFORE_MIDNIGHT_MSK],
                         ids=['00:30 MSK', '23:59 MSK'])
def test_notified_today_for_lesson_today_is_compensated(eg, day_group, manager_client,
                                                        superadmin_client, monkeypatch,
                                                        moment):
    from apps.cancellations.models import LessonCancellation

    _fake_msk_now(monkeypatch, moment)
    reason = _reason(superadmin_client, 'client', 'Заболел ребёнок', True)
    row = day_group[datetime.date(2026, 10, 23)]
    resp = manager_client.post(_cancel_url(eg['gid'], row.id), {
        'lesson_date': '2026-10-23', 'side': 'client', 'reason_id': reason['id'],
        'notified_on': '2026-10-23'}, format='json')
    assert resp.status_code == 200, resp.content
    rec = LessonCancellation.objects.get(group_id=eg['gid'])
    assert rec.notified_on == datetime.date(2026, 10, 23) and rec.compensated is True


def test_notified_today_for_lesson_tomorrow_not_compensated(eg, day_group, manager_client,
                                                            superadmin_client, monkeypatch):
    from apps.cancellations.models import LessonCancellation

    _fake_msk_now(monkeypatch, AFTER_MIDNIGHT_MSK)
    reason = _reason(superadmin_client, 'client', 'Заболел ребёнок', True)
    row = day_group[datetime.date(2026, 10, 24)]
    resp = manager_client.post(_cancel_url(eg['gid'], row.id), {
        'lesson_date': '2026-10-24', 'side': 'client', 'reason_id': reason['id'],
        'notified_on': '2026-10-23'}, format='json')
    assert resp.status_code == 200, resp.content
    assert LessonCancellation.objects.get(group_id=eg['gid']).compensated is False


@pytest.mark.parametrize('moment', [AFTER_MIDNIGHT_MSK, BEFORE_MIDNIGHT_MSK],
                         ids=['00:30 MSK', '23:59 MSK'])
def test_future_notified_on_is_400_by_msk_date_not_utc(eg, day_group, manager_client,
                                                       monkeypatch, moment):
    """Завтра по МСК — будущее и в 00:30, и в 23:59 (подмена msk_now целиком —
    границу UTC/МСК проверяет тест с подменой часов модуля dates)."""
    from apps.cancellations.models import LessonCancellation
    from apps.scheduling.models import PlannedLesson

    _fake_msk_now(monkeypatch, moment)
    row = day_group[datetime.date(2026, 10, 24)]
    before = list(PlannedLesson.objects.filter(group_id=eg['gid']).values_list(
        'id', 'scheduled_date', 'status'))
    resp = manager_client.post(_cancel_url(eg['gid'], row.id), {
        'lesson_date': '2026-10-24', 'side': 'client', 'other_text': 'Уехали',
        'notified_on': '2026-10-24'}, format='json')
    assert resp.status_code == 400, resp.content
    assert 'будущем' in json.dumps(resp.json(), ensure_ascii=False)
    assert not LessonCancellation.objects.filter(group_id=eg['gid']).exists()
    assert list(PlannedLesson.objects.filter(group_id=eg['gid']).values_list(
        'id', 'scheduled_date', 'status')) == before


def test_msk_date_comes_from_real_msk_now_across_utc_midnight(eg, day_group, manager_client,
                                                              monkeypatch):
    """Подмена не msk_now, а datetime внутри apps.core.utils.dates: 21:30 UTC 22 октября
    — это уже 23 октября по МСК. Значит, 23-е допустимо, а 24-е — будущее."""
    import types

    from apps.cancellations.models import LessonCancellation
    from apps.core.utils import dates

    moment_utc = datetime.datetime(2026, 10, 22, 21, 30, tzinfo=datetime.timezone.utc)

    class FrozenDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return moment_utc.astimezone(tz) if tz else moment_utc.replace(tzinfo=None)

    monkeypatch.setattr(dates, 'datetime', types.SimpleNamespace(
        datetime=FrozenDatetime, timedelta=datetime.timedelta, date=datetime.date,
        timezone=datetime.timezone))
    assert dates.msk_now().date() == datetime.date(2026, 10, 23)  # предпосылка

    row = day_group[datetime.date(2026, 10, 24)]
    body = {'lesson_date': '2026-10-24', 'side': 'client', 'other_text': 'Уехали'}
    assert manager_client.post(_cancel_url(eg['gid'], row.id),
                               {**body, 'notified_on': '2026-10-24'},
                               format='json').status_code == 400
    assert manager_client.post(_cancel_url(eg['gid'], row.id),
                               {**body, 'notified_on': '2026-10-23'},
                               format='json').status_code == 200
    assert LessonCancellation.objects.get(group_id=eg['gid']).notified_on == datetime.date(
        2026, 10, 23)


def test_notified_on_becomes_valid_after_msk_midnight(eg, day_group, manager_client,
                                                      monkeypatch):
    """23:59 МСК 23-го: 24-е — будущее (400); через две минуты, в 00:01 МСК 24-го
    (21:01 UTC 23-го), та же дата уже допустима."""
    row = day_group[datetime.date(2026, 10, 24)]
    body = {'lesson_date': '2026-10-24', 'side': 'client', 'other_text': 'Уехали',
            'notified_on': '2026-10-24'}
    _fake_msk_now(monkeypatch, BEFORE_MIDNIGHT_MSK)
    assert manager_client.post(_cancel_url(eg['gid'], row.id), body,
                               format='json').status_code == 400
    _fake_msk_now(monkeypatch, datetime.datetime(2026, 10, 24, 0, 1, tzinfo=MSK))
    assert manager_client.post(_cancel_url(eg['gid'], row.id), body,
                               format='json').status_code == 200


def test_teacher_side_future_notified_on_is_ignored_not_400(eg, day_group, manager_client,
                                                            monkeypatch):
    """Сквозной дубль ради API. Сторона «Преподаватель»: дата уведомления игнорируется, даже будущая (В7а)."""
    from apps.cancellations.models import LessonCancellation

    _fake_msk_now(monkeypatch, AFTER_MIDNIGHT_MSK)
    row = day_group[datetime.date(2026, 10, 24)]
    resp = manager_client.post(_cancel_url(eg['gid'], row.id), {
        'lesson_date': '2026-10-24', 'side': 'teacher', 'other_text': 'Поездка',
        'notified_on': '2030-01-01'}, format='json')
    assert resp.status_code == 200, resp.content
    assert LessonCancellation.objects.get(group_id=eg['gid']).notified_on is None


# ---------------------------------------------------------------------------
# 5. Права: матрица ролей на все шесть эндпоинтов
# ---------------------------------------------------------------------------

ROLES = ['anon_client', 'teacher_client', 'manager_client', 'admin_client', 'superadmin_client']
DENIED = (401, 403)


def _role_client(request, name):
    return request.getfixturevalue(name)


@pytest.mark.parametrize('role', ROLES)
def test_permissions_reasons_list(request, eg, superadmin_client, role):
    """Сквозной дубль ради API: флаг compensation видит только superadmin."""
    _reason(superadmin_client, 'teacher', 'Болезнь', True)
    _reason(superadmin_client, 'client', 'Заболел', False)
    resp = _role_client(request, role).get('/api/admin/cancellation-reasons')
    if role in ('anon_client', 'teacher_client'):
        assert resp.status_code in DENIED
        return
    assert resp.status_code == 200
    rows = [r for side in resp.json().values() for r in side]
    assert len(rows) == 2
    for row in rows:
        assert ('compensation' in row) is (role == 'superadmin_client'), (role, row)


@pytest.mark.parametrize('role', ROLES)
def test_permissions_reason_create_patch_delete_superadmin_only(request, eg, superadmin_client,
                                                                role):
    victim = _reason(superadmin_client, 'client', 'Чужая причина', False)
    client = _role_client(request, role)
    base = '/api/admin/cancellation-reasons'
    create = client.post(base, {'side': 'client', 'name': 'Новая', 'compensation': True},
                         format='json')
    patch = client.patch(f'{base}/{victim["id"]}', {'compensation': True}, format='json')
    delete = client.delete(f'{base}/{victim["id"]}')
    if role == 'superadmin_client':
        assert (create.status_code, patch.status_code, delete.status_code) == (201, 200, 204)
        return
    for resp in (create, patch, delete):
        assert resp.status_code in DENIED, (role, resp.status_code)
    # Отказ — до мутации: причина цела и не изменена.
    from apps.cancellations.models import CancellationReason
    reason = CancellationReason.objects.get(id=victim['id'])
    assert reason.deleted_at is None and reason.compensation is False
    assert not CancellationReason.objects.filter(name='Новая').exists()


@pytest.mark.parametrize('role', ROLES)
def test_permissions_cancel_and_group_tab(request, eg, role):
    from apps.cancellations.models import LessonCancellation

    client = _role_client(request, role)
    row = _first_pending(eg['gid'])
    resp = client.post(_cancel_url(eg['gid'], row.id), {
        'lesson_date': row.scheduled_date.isoformat(), 'side': 'teacher',
        'other_text': 'Поездка'}, format='json')
    tab = client.get(f'/api/admin/groups/{eg["gid"]}/cancellations')
    if role in ('anon_client', 'teacher_client'):
        assert resp.status_code in DENIED and tab.status_code in DENIED
        assert not LessonCancellation.objects.filter(group_id=eg['gid']).exists()
    else:
        assert resp.status_code == 200 and tab.status_code == 200
        assert LessonCancellation.objects.filter(group_id=eg['gid']).count() == 1


# ---------------------------------------------------------------------------
# 6. Атомарность и повтор
# ---------------------------------------------------------------------------

def test_deleted_reason_name_can_be_reused_and_new_reason_works(eg, manager_client,
                                                                superadmin_client):
    """Сквозной дубль ради API (проверено строже в соседних файлах)."""
    old = _reason(superadmin_client, 'client', 'Заболел', True)
    assert superadmin_client.delete(
        f'/api/admin/cancellation-reasons/{old["id"]}').status_code == 204
    new = _reason(superadmin_client, 'client', 'Заболел', False)
    assert new['id'] != old['id']
    day = _cancel_first(manager_client, eg, side='client', reason_id=new['id'],
                        notified_on=_iso(FIRST_TUESDAY))
    assert _tab(manager_client, eg['gid'])[0]['lesson_date'] == day.isoformat()


def test_reason_of_other_side_rejected_each_way(eg, manager_client, superadmin_client):
    """Сквозной дубль ради API (проверено строже в соседних файлах)."""
    t = _reason(superadmin_client, 'teacher', 'Болезнь', True)
    c = _reason(superadmin_client, 'client', 'Заболел', True)
    row = _first_pending(eg['gid'])
    day = row.scheduled_date.isoformat()
    for body in ({'side': 'client', 'reason_id': t['id'], 'notified_on': day},
                 {'side': 'teacher', 'reason_id': c['id']}):
        resp = manager_client.post(_cancel_url(eg['gid'], row.id),
                                   {'lesson_date': day, **body}, format='json')
        assert resp.status_code == 400
        assert resp.json()['error'] == 'Причина не относится к выбранной стороне.'
    assert _tab(manager_client, eg['gid']) == []


# ---------------------------------------------------------------------------
# 7. Старые отмены без записи
# ---------------------------------------------------------------------------

def test_legacy_cancellation_without_record_paid_as_before_and_hidden_in_tab(
        eg, manager_client, superadmin_client, own_teacher_client):
    from apps.payroll.recalc import recalculate_cancellations
    from apps.scheduling.models import PlannedLesson

    legacy_day = datetime.date(2026, 6, 30)
    PlannedLesson.objects.create(
        group_id=eg['gid'], seq=None, lesson_number=None, scheduled_date=legacy_day,
        scheduled_time=T18, teacher_id=eg['tid'], status='cancelled',
        created_at=timezone.now(), updated_at=timezone.now())
    recalculate_cancellations(group_ids=[eg['gid']])
    # Новая отмена с записью «без компенсации» рядом.
    new_day = _cancel_first(manager_client, eg, side='teacher', other_text='Поездка')

    admin_rows = _admin_pay_rows(superadmin_client, eg['gid'])
    legacy = admin_rows[legacy_day.isoformat()]
    assert D(legacy['payment']) == D('250')
    assert 'компенсац' not in legacy['rule_note'].lower()
    assert D(admin_rows[new_day.isoformat()]['payment']) == D('0')

    cab_rows, _ = _teacher_pay_rows(own_teacher_client)
    assert D(cab_rows[legacy_day.isoformat()]['payment']) == D('250')
    assert 'компенсац' not in cab_rows[legacy_day.isoformat()]['ruleNote'].lower()

    # Пересчёты старую отмену не трогают; во вкладке её нет.
    call_command('recalculate_payroll', '--group', str(eg['gid']))
    assert D(_admin_pay_rows(superadmin_client, eg['gid'])[legacy_day.isoformat()]['payment']) == D('250')
    assert [r['lesson_date'] for r in _tab(manager_client, eg['gid'])] == [new_day.isoformat()]


# ---------------------------------------------------------------------------
# Дополнительно: персональная ставка «все уроки» и откат через журнал изменений
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('compensation, expected', [(True, D('500')), (False, D('0'))])
def test_personal_all_lessons_rate_is_base_of_cancellation_percent(
        eg, manager_client, superadmin_client, own_teacher_client, compensation, expected):
    """Персональная фикс-ставка 1000 «на все уроки»: отмена = 50 % от 1000 = 500
    при компенсации (вместо 250 от школьной 500), без компенсации — 0."""
    from apps.payroll.models import PersonalRate, PersonalRateTerm

    rate = PersonalRate.objects.create(teacher_id=eg['tid'], group_id=eg['gid'])
    PersonalRateTerm.objects.create(
        rate=rate, teacher_id=eg['tid'], group_id=eg['gid'], valid_from=None,
        valid_until=None, lesson_from=None, lesson_to=None, kind='fixed', value=D('1000'))
    reason = _reason(superadmin_client, 'teacher', 'Болезнь', compensation)

    day = _cancel_first(manager_client, eg, side='teacher', reason_id=reason['id'])

    admin_row = _admin_pay_rows(superadmin_client, eg['gid'])[day.isoformat()]
    cab_row = _teacher_pay_rows(own_teacher_client)[0][day.isoformat()]
    assert D(admin_row['payment']) == D(cab_row['payment']) == expected
    if compensation:
        assert '1 000' in admin_row['rule_note'] and admin_row['rule_note'].startswith(
            'Преподаватель — Болезнь · ')
    else:
        assert admin_row['rule_note'] == 'Без компенсации: Преподаватель — Болезнь'


@pytest.mark.parametrize('role', ['superadmin_client', 'admin_client'])
def test_changelog_revert_of_cancel_operation_is_rejected(request, eg, manager_client,
                                                          superadmin_client, role):
    """Откат операции отмены урока (с записью LessonCancellation, revertable=False)
    запрещён: 400, отмена, начисление и запись остаются на месте."""
    from apps.payroll.models import CancellationPay

    reason = _reason(superadmin_client, 'teacher', 'Болезнь', True)
    day = _cancel_first(manager_client, eg, side='teacher', reason_id=reason['id'])
    feed = superadmin_client.get('/api/admin/changelog', {'page_size': 50}).json()['rows']
    op = next(r for r in feed if r['operation'] == 'plan.cancel')

    resp = request.getfixturevalue(role).post(f'/api/admin/changelog/{op["id"]}/revert')

    assert resp.status_code == 400, resp.content
    assert resp.json()['error']
    assert [r['lesson_date'] for r in _tab(manager_client, eg['gid'])] == [day.isoformat()]
    assert CancellationPay.objects.filter(group_id=eg['gid']).count() == 1
