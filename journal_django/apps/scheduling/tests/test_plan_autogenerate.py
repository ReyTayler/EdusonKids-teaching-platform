"""
Тесты автогенерации плана при первичной настройке группы (Механизм 1).

repository.plan_exists — guard first-time-only (+active).
services.autogenerate_plan_on_setup — оркестратор: генерит план ОДИН раз, когда
у активной группы впервые есть старт+слот+total. В журнал ИБ не пишет (доменное
действие — см. «Журнал изменений»), поэтому проверяем только эффект на плане.

Опора на фикстуру sched_setup (conftest): group_a активна, старт 2026-06-01,
слот Пн 10:00, total_lessons=8 → 8 курсовых строк.
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.scheduling import repository, services
from apps.scheduling.models import PlannedLesson

pytestmark = pytest.mark.django_db


@pytest.fixture
def autogen_setup(sched_setup):
    """Прежняя пост-очистка security_audit_log больше не нужна: автогенерация
    плана в журнал ИБ не пишет."""
    return sched_setup


class TestPlanExists:
    def test_false_when_no_plan(self, autogen_setup):
        assert repository.plan_exists(autogen_setup['group_a']) is False

    def test_true_when_plan_present(self, autogen_setup):
        repository.generate_for_group(autogen_setup['group_a'])
        assert repository.plan_exists(autogen_setup['group_a']) is True

    def test_active_only_skips_inactive_group(self, autogen_setup):
        gid = autogen_setup['group_a']
        with connection.cursor() as cur:
            cur.execute('UPDATE groups SET active=false WHERE id=%s', [gid])
        # неактивная группа без плана → active_only считает «план есть» (пропустить)
        assert repository.plan_exists(gid, active_only=True) is True
        assert repository.plan_exists(gid, active_only=False) is False


class TestAutogenerate:
    def test_generates_plan(self, autogen_setup):
        gid = autogen_setup['group_a']
        services.autogenerate_plan_on_setup(gid)
        assert PlannedLesson.objects.filter(group_id=gid).count() == 8

    def test_does_not_write_security_log(self, autogen_setup):
        """Генерация плана — доменное действие: журнал ИБ она не трогает."""
        from apps.audit.models import SecurityAuditLog
        gid = autogen_setup['group_a']
        before = SecurityAuditLog.objects.count()
        services.autogenerate_plan_on_setup(gid)
        assert SecurityAuditLog.objects.count() == before

    def test_noop_when_plan_already_exists(self, autogen_setup):
        gid = autogen_setup['group_a']
        repository.generate_for_group(gid)
        services.autogenerate_plan_on_setup(gid)
        assert PlannedLesson.objects.filter(group_id=gid).count() == 8  # без дублей

    def test_noop_for_inactive_group(self, autogen_setup):
        gid = autogen_setup['group_a']
        with connection.cursor() as cur:
            cur.execute('UPDATE groups SET active=false WHERE id=%s', [gid])
        services.autogenerate_plan_on_setup(gid)
        assert PlannedLesson.objects.filter(group_id=gid).count() == 0

    def test_noop_when_no_slots_yet(self, autogen_setup):
        """Старт есть, слота ещё нет → generate вернёт reason, план пуст
        (сработает позже, когда слот появится)."""
        gid = autogen_setup['group_a']
        with connection.cursor() as cur:
            cur.execute('DELETE FROM group_schedule_slots WHERE group_id=%s', [gid])
        services.autogenerate_plan_on_setup(gid)
        assert PlannedLesson.objects.filter(group_id=gid).count() == 0


class TestAutogenerateWiring:
    """Интеграция триггера через groups.services (Фаза E). Проверяет оба порядка
    ввода и то, что генерация срабатывает при слотах, созданных bulk_create
    (доказывает, что мы НЕ на post_save-сигналах)."""

    @pytest.fixture
    def wiring(self, sched_setup):
        created: list[int] = []
        yield sched_setup, created
        with connection.cursor() as cur:
            for gid in created:
                cur.execute('DELETE FROM planned_lessons WHERE group_id=%s', [gid])
                cur.execute('DELETE FROM group_schedule_slots WHERE group_id=%s', [gid])
                cur.execute('DELETE FROM security_audit_log WHERE target_id=%s', [gid])
                cur.execute('DELETE FROM groups WHERE id=%s', [gid])

    def _base(self, s, name):
        return {
            'name': name, 'direction_id': s['direction_id'], 'teacher_id': s['teacher_a'],
            'lesson_duration_minutes': 60,
        }

    def test_create_with_start_and_slot_autogenerates(self, wiring):
        from apps.groups import services as groups_services
        s, created = wiring
        data = self._base(s, '__ag_create__')
        data['group_start_date'] = '2026-06-01'
        data['slots'] = [{'day_of_week': 1, 'start_time': '10:00'}]  # bulk_create путь
        group = groups_services.create_group(data)
        created.append(group['id'])
        # план сгенерирован, несмотря на bulk_create слотов (не сигналы)
        assert PlannedLesson.objects.filter(group_id=group['id']).count() == 8

    def test_start_then_slot_via_schedule_change(self, wiring):
        from apps.groups import services as groups_services
        s, created = wiring
        data = self._base(s, '__ag_startfirst__')
        data['group_start_date'] = '2026-06-01'
        data['slots'] = []
        group = groups_services.create_group(data)
        gid = group['id']
        created.append(gid)
        assert PlannedLesson.objects.filter(group_id=gid).count() == 0  # нет слота — пусто

        groups_services.apply_schedule_change(gid, {
            'effective_from': '2026-06-01',
            'slots': [{'day_of_week': 1, 'start_time': '10:00'}],
        })
        assert PlannedLesson.objects.filter(group_id=gid).count() == 8  # слот появился → план

    def test_slot_then_start_via_update(self, wiring):
        from apps.groups import services as groups_services
        s, created = wiring
        data = self._base(s, '__ag_slotfirst__')
        data['group_start_date'] = None
        data['slots'] = [{'day_of_week': 1, 'start_time': '10:00'}]
        group = groups_services.create_group(data)
        gid = group['id']
        created.append(gid)
        assert PlannedLesson.objects.filter(group_id=gid).count() == 0  # нет старта — пусто

        groups_services.update_group(gid, {'group_start_date': '2026-06-01'})
        assert PlannedLesson.objects.filter(group_id=gid).count() == 8  # старт появился → план

    def test_bootstrap_start_and_slots_via_actions(self, wiring):
        """Группа создана без даты старта И без слотов (план пуст). Сценарий кнопки
        «Задать расписание» на вкладке расписания: сначала update_group ставит дату
        начала, затем apply_schedule_change задаёт слоты → план генерируется."""
        from apps.groups import services as groups_services
        s, created = wiring
        data = self._base(s, '__ag_bootstrap__')
        data['group_start_date'] = None
        data['slots'] = []
        group = groups_services.create_group(data)
        gid = group['id']
        created.append(gid)
        assert PlannedLesson.objects.filter(group_id=gid).count() == 0  # ни старта, ни слота

        groups_services.update_group(gid, {'group_start_date': '2026-06-01'})
        assert PlannedLesson.objects.filter(group_id=gid).count() == 0  # старт есть, слотов нет
        groups_services.apply_schedule_change(gid, {
            'effective_from': '2026-06-01',
            'slots': [{'day_of_week': 1, 'start_time': '10:00'}],
        })
        assert PlannedLesson.objects.filter(group_id=gid).count() == 8  # оба заданы → план

    def test_second_update_does_not_regenerate(self, wiring):
        from apps.groups import services as groups_services
        s, created = wiring
        data = self._base(s, '__ag_once__')
        data['group_start_date'] = '2026-06-01'
        data['slots'] = [{'day_of_week': 1, 'start_time': '10:00'}]
        group = groups_services.create_group(data)
        gid = group['id']
        created.append(gid)
        assert PlannedLesson.objects.filter(group_id=gid).count() == 8

        groups_services.update_group(gid, {'name': '__ag_once_renamed__'})
        assert PlannedLesson.objects.filter(group_id=gid).count() == 8  # без перегенерации

    def test_45min_generates_double_and_snaps_to_slot(self, wiring):
        """Верификация «Задать расписание» (уже реализовано, план Task 11):
        45-минутная группа → half-lesson step=0.5, план направления с
        total_lessons=8 разворачивается в 2×8=16 курсовых строк. Старт 2026-07-20
        (Пн) со слотом Пт(5) → первое занятие встаёт на первый слот-день ≥ старта
        (2026-07-24), а не на сам понедельник старта."""
        from apps.groups import services as groups_services
        s, created = wiring
        data = self._base(s, '__ag_45__')
        data['lesson_duration_minutes'] = 45
        data['group_start_date'] = None
        data['slots'] = []
        group = groups_services.create_group(data)
        gid = group['id']
        created.append(gid)
        assert PlannedLesson.objects.filter(group_id=gid).count() == 0  # ни старта, ни слота

        groups_services.update_group(gid, {'group_start_date': '2026-07-20'})
        assert PlannedLesson.objects.filter(group_id=gid).count() == 0  # старт есть, слотов нет
        groups_services.apply_schedule_change(gid, {
            'effective_from': '2026-07-20',
            'slots': [{'day_of_week': 5, 'start_time': '17:00'}],
        })
        rows = list(
            PlannedLesson.objects.filter(group_id=gid, seq__isnull=False).order_by('seq')
        )
        assert len(rows) == 16  # 2 × total_lessons(8) направления sched_setup
        assert str(rows[0].scheduled_date) == '2026-07-24'


def test_generate_for_group_honors_lesson_number_offset(autogen_setup):
    """
    Группе (total_lessons=8, ещё без плана) выставлен lesson_number_offset=6 —
    план должен начинаться с lesson_number=7.0 (первый урок ПОСЛЕ офсета),
    seq=1 (первая созданная строка), и содержать оставшиеся 8-6=2 строки, а не 8.
    """
    from decimal import Decimal
    from apps.groups.models import Group

    gid = autogen_setup['group_a']
    Group.objects.filter(id=gid).update(lesson_number_offset=Decimal('6'))
    result = repository.generate_for_group(gid)
    assert result['written'] == 2
    rows = sorted(result['plan'], key=lambda r: r['seq'])
    assert rows[0]['seq'] == 1
    assert float(rows[0]['lesson_number']) == 7.0
    assert float(rows[-1]['lesson_number']) == 8.0
