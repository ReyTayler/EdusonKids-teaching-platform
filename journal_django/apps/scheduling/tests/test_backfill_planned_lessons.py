"""
Тесты бэкфилла planned_lessons: команда + repository (persist_plan/link_facts).

Опираются на фикстуру sched_setup (conftest): группа A преподавателя A со стартом
2026-06-01 (пн), слот Пн 10:00, direction.total_lessons=8, duration 60 → 8 курсовых
строк seq 1..8. Проверяем: план разворачивается, прошлые строки линкуются с фактом
(status='done'), повторный прогон идемпотентен (без дублей и без перезаписи done).

managed-схема eduson_test; planned_lessons чистим в teardown локальной фикстуры
ДО того, как sched_setup удалит группы (FK planned_lessons.group_id).
"""
from __future__ import annotations

import datetime
from dataclasses import replace
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db import connection

from apps.scheduling import repository
from apps.scheduling.models import PlannedLesson
from apps.scheduling.occurrences import DONE

pytestmark = pytest.mark.django_db

# Результат пересборки today-независим (будущее считается от даты последнего факта),
# поэтому даты в тестах детерминированы без инъекции «сегодня».


@pytest.fixture
def backfill_setup(sched_setup):
    """sched_setup + гарантированная очистка planned_lessons перед удалением групп."""
    yield sched_setup
    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM planned_lessons WHERE group_id IN (%s, %s)',
            [sched_setup['group_a'], sched_setup['group_b']],
        )


def _insert_fact(group_id: int, teacher_id: int, date: str, number) -> int:
    """Вставить проведённый урок (факт) и вернуть его id."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_at, submitted_by_token) "
            "VALUES (%s, %s, %s, %s, 60, 'regular', NOW(), 'test-backfill') RETURNING id",
            [group_id, teacher_id, date, number],
        )
        return cur.fetchone()[0]


class TestBackfillCommand:
    def test_no_facts_future_from_start(self, backfill_setup):
        """Фактов нет → 8 будущих позиций от старта по слоту (Пн 10:00)."""
        call_command('backfill_planned_lessons')

        rows = list(
            PlannedLesson.objects
            .filter(group_id=backfill_setup['group_a'])
            .order_by('seq')
        )
        assert [r.seq for r in rows] == [1, 2, 3, 4, 5, 6, 7, 8]
        assert rows[0].scheduled_date == datetime.date(2026, 6, 1)
        assert rows[1].scheduled_date == datetime.date(2026, 6, 8)
        assert all(r.scheduled_time == datetime.time(10, 0) for r in rows)
        assert all(r.status != DONE for r in rows)

    def test_fact_done_and_future_after_last_fact(self, backfill_setup):
        """Факт → done на факт-дате; будущее начинается со СЛЕДУЮЩЕГО слот-дня после
        последнего факта (провели 06-03 Ср → следующий по слоту Пн = 06-08)."""
        f = _insert_fact(backfill_setup['group_a'], backfill_setup['teacher_a'],
                         '2026-06-03', Decimal('1'))
        call_command('backfill_planned_lessons')

        gid = backfill_setup['group_a']
        seq1 = PlannedLesson.objects.get(group_id=gid, seq=1)
        assert seq1.status == DONE
        assert seq1.fact_lesson_id == f
        assert seq1.scheduled_date == datetime.date(2026, 6, 3)   # факт-дата
        seq2 = PlannedLesson.objects.get(group_id=gid, seq=2)
        assert seq2.status != DONE
        assert seq2.fact_lesson_id is None
        assert seq2.scheduled_date == datetime.date(2026, 6, 8)   # Пн после 06-03
        assert PlannedLesson.objects.filter(group_id=gid).count() == 8

    def test_rebuild_is_destructive_removes_stray_rows(self, backfill_setup):
        """Пересборка начисто (reset встроен): стрей-строки удаляются без --reset."""
        gid = backfill_setup['group_a']
        call_command('backfill_planned_lessons')
        assert PlannedLesson.objects.filter(group_id=gid).count() == 8

        from apps.core.utils.dates import msk_now
        now = msk_now()
        PlannedLesson.objects.create(
            group_id=gid, seq=None, lesson_number=None,
            scheduled_date=datetime.date(2026, 6, 2), scheduled_time=datetime.time(11, 0),
            teacher_id=backfill_setup['teacher_a'], status='cancelled',
            created_at=now, updated_at=now,
        )
        assert PlannedLesson.objects.filter(group_id=gid).count() == 9

        call_command('backfill_planned_lessons')
        assert PlannedLesson.objects.filter(group_id=gid).count() == 8
        assert not PlannedLesson.objects.filter(group_id=gid, seq__isnull=True).exists()

    def test_reset_flag_deprecated_still_rebuilds(self, backfill_setup):
        """--reset устарел (варн), но команда отрабатывает как обычная пересборка."""
        call_command('backfill_planned_lessons', '--reset')
        assert PlannedLesson.objects.filter(group_id=backfill_setup['group_a']).count() == 8

    def test_dry_run_writes_nothing(self, backfill_setup):
        call_command('backfill_planned_lessons', '--dry-run')
        assert not PlannedLesson.objects.filter(
            group_id=backfill_setup['group_a'],
        ).exists()

    def test_rerun_is_stable_and_today_independent(self, backfill_setup):
        """Повтор → тот же результат (без дублей), одна done-привязка. Даты от фактов."""
        _insert_fact(backfill_setup['group_a'], backfill_setup['teacher_a'],
                     '2026-06-01', Decimal('1'))
        gid = backfill_setup['group_a']

        call_command('backfill_planned_lessons')
        first = [(r.seq, r.scheduled_date) for r in
                 PlannedLesson.objects.filter(group_id=gid).order_by('seq')]
        call_command('backfill_planned_lessons')
        second = [(r.seq, r.scheduled_date) for r in
                  PlannedLesson.objects.filter(group_id=gid).order_by('seq')]
        assert first == second                                   # идентичный план
        assert len(first) == 8
        assert PlannedLesson.objects.filter(group_id=gid, status=DONE).count() == 1

    def test_rerun_overwrites_manual_future(self, backfill_setup):
        """Свободный повтор ПЕРЕЗАПИСЫВАЕТ ручную правку будущего (по решению польз.)."""
        gid = backfill_setup['group_a']
        call_command('backfill_planned_lessons')
        fut = PlannedLesson.objects.filter(group_id=gid).order_by('seq').first()
        original = fut.scheduled_date
        PlannedLesson.objects.filter(id=fut.id).update(scheduled_date=datetime.date(2026, 12, 25))

        call_command('backfill_planned_lessons')
        again = PlannedLesson.objects.get(group_id=gid, seq=fut.seq)
        assert again.scheduled_date == original     # ручная правка снесена пересборкой


class TestRepositoryWrite:
    def _rows(self):
        from apps.scheduling import planner
        from apps.scheduling.occurrences import Slot
        return planner.generate(
            start_date=datetime.date(2026, 6, 1),
            slots=[Slot(day_of_week=1, start_time=datetime.time(10, 0),
                        effective_from=datetime.date(2026, 6, 1))],
            total_lessons=8, duration_minutes=60, default_teacher_id=None,
        )

    def test_persist_plan_idempotent(self, backfill_setup):
        gid = backfill_setup['group_a']
        rows = self._rows()
        # Проставим реального преподавателя, иначе teacher_id=None (для чистоты FK).
        rows = [replace(r, teacher_id=backfill_setup['teacher_a']) for r in rows]

        first = repository.persist_plan(gid, rows)
        assert first == 8  # все созданы
        second = repository.persist_plan(gid, rows)
        assert second == 0  # ничего не изменилось

    def test_link_facts_idempotent_and_respects_done(self, backfill_setup):
        gid = backfill_setup['group_a']
        rows = self._rows()
        rows = [replace(r, teacher_id=backfill_setup['teacher_a']) for r in rows]
        repository.persist_plan(gid, rows)

        _insert_fact(gid, backfill_setup['teacher_a'], '2026-06-08', Decimal('2'))

        linked_first = repository.link_facts(gid)
        assert linked_first == 1
        linked_second = repository.link_facts(gid)
        assert linked_second == 0

        # persist_plan после линковки не перезаписывает done-строку.
        assert repository.persist_plan(gid, rows) == 0
        seq2 = PlannedLesson.objects.get(group_id=gid, seq=2)
        assert seq2.status == DONE

    def test_link_facts_by_lesson_number_moves_planned_date(self, backfill_setup):
        """Регресс синхронизации: факт прошёл на СДВИНУТОЙ дате (не по recurrence).
        Линкуем по lesson_number → status=done, факт привязан. Плановая дата
        (scheduled_date) ПЕРЕЗАПИСЫВАЕТСЯ датой факта — инвариант спеки
        2026-08-05 §2 (см. test_sync_position_date.py): проведённая позиция
        обязана стоять в календаре на дне, когда занятие реально прошло.
        """
        gid = backfill_setup['group_a']
        rows = [replace(r, teacher_id=backfill_setup['teacher_a']) for r in self._rows()]
        repository.persist_plan(gid, rows)

        # seq=1 по плану на 2026-06-01, а урок реально прошёл 2026-06-03.
        _insert_fact(gid, backfill_setup['teacher_a'], '2026-06-03', Decimal('1'))

        assert repository.link_facts(gid) == 1
        seq1 = PlannedLesson.objects.get(group_id=gid, seq=1)
        assert seq1.status == DONE
        assert seq1.fact_lesson_id is not None
        assert seq1.scheduled_date == datetime.date(2026, 6, 3)  # переехала на факт

        # get_plan отдаёт совпавшие плановую и фактическую даты.
        row = next(r for r in repository.get_plan(gid) if r['seq'] == 1)
        assert row['scheduled_date'] == '2026-06-03'
        assert row['fact_date'] == '2026-06-03'

    def test_link_facts_date_fallback_without_lesson_number(self, backfill_setup):
        """Факт без совпадения по номеру, но с совпадающей датой — линкуется
        по дате (fallback)."""
        gid = backfill_setup['group_a']
        rows = [replace(r, teacher_id=backfill_setup['teacher_a']) for r in self._rows()]
        repository.persist_plan(gid, rows)

        # lesson_number=99 (вне курса) но дата = плановой seq=3 (2026-06-15).
        _insert_fact(gid, backfill_setup['teacher_a'], '2026-06-15', Decimal('99'))

        assert repository.link_facts(gid) == 1
        seq3 = PlannedLesson.objects.get(group_id=gid, seq=3)
        assert seq3.status == DONE
        assert seq3.fact_lesson_id is not None


class TestRebuildFromFacts:
    """repository.rebuild_from_facts — прошлое=факты (коллапс даты) + будущее от даты
    последнего факта по открытому слоту. today-независимо."""

    def test_collapses_done_and_future_after_last_fact(self, backfill_setup):
        gid = backfill_setup['group_a']
        ta = backfill_setup['teacher_a']
        tb = backfill_setup['teacher_b']
        # 2 факта: seq1 на 06-01 (Пн); seq2 на СДВИНУТОЙ 06-09 (Вт), последний факт.
        f1 = _insert_fact(gid, ta, '2026-06-01', Decimal('1'))
        f2 = _insert_fact(gid, tb, '2026-06-09', Decimal('2'))

        res = repository.rebuild_from_facts(gid)
        assert res['written'] == 8
        assert res['reason'] is None

        by_seq = {r.seq: r for r in PlannedLesson.objects.filter(group_id=gid).order_by('seq')}
        # прошлое = факты, дата = фактическая
        assert by_seq[1].status == DONE and by_seq[1].scheduled_date == datetime.date(2026, 6, 1)
        assert by_seq[1].fact_lesson_id == f1
        assert by_seq[2].status == DONE and by_seq[2].scheduled_date == datetime.date(2026, 6, 9)
        assert by_seq[2].fact_lesson_id == f2
        assert by_seq[2].teacher_id == tb              # препод из факта
        # будущее: seq3 — Пн ПОСЛЕ последнего факта (06-09) = 06-15, дальше по неделе
        assert by_seq[3].status != DONE and by_seq[3].scheduled_date == datetime.date(2026, 6, 15)
        assert by_seq[3].fact_lesson_id is None
        assert by_seq[4].scheduled_date == datetime.date(2026, 6, 22)
        assert by_seq[8].scheduled_date == datetime.date(2026, 7, 20)

    def test_is_destructive_reset_each_run(self, backfill_setup):
        """reset+rebuild: повторный прогон не плодит дублей; стрей-строки удаляются."""
        gid = backfill_setup['group_a']
        from apps.core.utils.dates import msk_now
        now = msk_now()
        PlannedLesson.objects.create(
            group_id=gid, seq=None, lesson_number=None,
            scheduled_date=datetime.date(2026, 6, 2), scheduled_time=datetime.time(11, 0),
            teacher_id=backfill_setup['teacher_a'], status='cancelled',
            created_at=now, updated_at=now,
        )
        repository.rebuild_from_facts(gid)
        assert PlannedLesson.objects.filter(group_id=gid).count() == 8   # стрей снесён
        repository.rebuild_from_facts(gid)
        assert PlannedLesson.objects.filter(group_id=gid).count() == 8   # без дублей

    def test_no_open_slots_only_past(self, backfill_setup):
        """Нет ОТКРЫТЫХ слотов (effective_to задан) → только прошлое (факты), будущее
        не развернуть (reason=no_open_slots)."""
        gid = backfill_setup['group_a']
        f = _insert_fact(gid, backfill_setup['teacher_a'], '2026-06-01', Decimal('1'))
        # Закрыть слот датой в будущем (валидный диапазон, но effective_to IS NOT NULL
        # → «открытых» слотов нет).
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE group_schedule_slots SET effective_to='2026-12-31' WHERE group_id=%s",
                [gid],
            )
        res = repository.rebuild_from_facts(gid)
        assert res['reason'] == 'no_open_slots'
        assert res['written'] == 1   # только done-строка факта
        row = PlannedLesson.objects.get(group_id=gid, seq=1)
        assert row.status == DONE and row.fact_lesson_id == f

    def test_missing_group_returns_none(self):
        assert repository.rebuild_from_facts(99999999) is None
