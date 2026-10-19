"""
test_get_all_data_scope.py — граница видимости POST /api/getAllData.

Эндпоинт нужен форме записи урока, когда преподаватель отмечает ЧУЖУЮ группу
(«Сменить преподавателя» / разовая замена). Раньше он отдавал срез всей школы —
любая учётка преподавателя одним запросом получала ФИО, даты рождения и остаток
оплаченных уроков всех учеников (аудит ПДн 2026-09-30).

Теперь: свои группы + чужие, где преподавателю назначено ещё НЕ проведённое и НЕ
отменённое плановое занятие. «Назначено» — то же правило, что в календаре и в
submitLesson: эффективный преподаватель занятия = разовая замена, если она задана,
иначе преподаватель занятия.
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.teacher_spa.tests.test_teacher_spa_api import _client

pytestmark = pytest.mark.django_db

GROUP = '__spa_test_group__ пн 10:00'
STUDENT = '__spa_test_student__'


def _all_data(account_id: int) -> dict:
    resp = _client('teacher', account_id).post('/api/getAllData', {}, format='json')
    assert resp.status_code == 200
    return resp.json()['data']


def _own_data(account_id: int) -> dict:
    resp = _client('teacher', account_id).post('/api/getData', {}, format='json')
    assert resp.status_code == 200
    return resp.json()['data']


@pytest.fixture
def plan():
    """
    Фабрика плановых занятий: plan(group_id, teacher_id, substitute_id=None,
    status='pending', fact_lesson_id=None, seq=1) → id. Строки удаляются после теста.
    """
    created: list[int] = []

    def _make(group_id, teacher_id, *, substitute_id=None, status='pending',
              fact_lesson_id=None, seq=1):
        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
                'scheduled_time, teacher_id, substitute_teacher_id, status, fact_lesson_id, '
                'created_at, updated_at) '
                "VALUES (%s, %s, %s, '2026-06-10', '10:00', %s, %s, %s, %s, NOW(), NOW()) "
                'RETURNING id',
                [group_id, seq, seq, teacher_id, substitute_id, status, fact_lesson_id],
            )
            pid = cur.fetchone()[0]
        created.append(pid)
        return pid

    yield _make
    with connection.cursor() as cur:
        cur.execute('DELETE FROM planned_lessons WHERE id = ANY(%s)', [created])


@pytest.fixture
def fact_lesson(group_fixture, teacher_fixture):
    """Проведённый урок группы (для planned_lessons.fact_lesson_id)."""
    teacher_id, _ = teacher_fixture
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO lessons (lesson_date, teacher_id, group_id, lesson_number, '
            'lesson_duration_minutes, lesson_type, submitted_by_token) '
            "VALUES ('2026-06-10', %s, %s, 1, 60, 'regular', '__spa_scope_test__') RETURNING id",
            [teacher_id, group_fixture],
        )
        lesson_id = cur.fetchone()[0]
    yield lesson_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])


@pytest.fixture
def foreign_group(sub_teacher_fixture, direction_fixture):
    """Группа ДРУГОГО преподавателя (sub) со своим учеником — чужая для владельца."""
    sub_id, _ = sub_teacher_fixture
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO groups (name, direction_id, teacher_id, is_individual, '
            'lesson_duration_minutes, active, lesson_number_offset) '
            "VALUES ('__spa_scope_foreign__ вт 12:00', %s, %s, false, 60, true, 0) RETURNING id",
            [direction_fixture, sub_id],
        )
        group_id = cur.fetchone()[0]
        cur.execute("INSERT INTO students (full_name) VALUES ('__spa_scope_foreign_student__') "
                    'RETURNING id')
        student_id = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
            'VALUES (%s, %s, 0, true)', [group_id, student_id])
    yield group_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM students WHERE id = %s', [student_id])
        cur.execute('DELETE FROM groups WHERE id = %s', [group_id])


class TestGetAllDataScope:

    def test_foreign_group_without_assignment_is_hidden(
        self, teacher_fixture, sub_teacher_fixture, sub_account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Чужая группа без назначенных занятий не отдаётся вовсе."""
        assert _all_data(sub_account_fixture) == {}

    def test_other_teachers_groups_not_leaked_to_owner(
        self, teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture, foreign_group,
    ):
        """Преподаватель со своими группами не получает группы остальной школы."""
        _, owner_name = teacher_fixture
        data = _all_data(account_fixture)
        assert list(data) == [owner_name]
        assert list(data[owner_name]) == [GROUP]

    def test_group_with_assigned_lesson_is_returned(
        self, teacher_fixture, sub_teacher_fixture, sub_account_fixture,
        group_fixture, student_fixture, membership_fixture, plan,
    ):
        """«Сменить преподавателя»: занятие группы назначено (teacher_id) → группа видна."""
        _, owner_name = teacher_fixture
        sub_id, _ = sub_teacher_fixture
        plan(group_fixture, sub_id)

        data = _all_data(sub_account_fixture)
        assert list(data) == [owner_name]
        assert [s['name'] for s in data[owner_name][GROUP]['students']] == [STUDENT]

    def test_group_with_one_off_substitution_is_returned(
        self, teacher_fixture, sub_teacher_fixture, sub_account_fixture,
        group_fixture, student_fixture, membership_fixture, plan,
    ):
        """Разовая замена на дату (substitute_teacher_id) → группа видна заменяющему."""
        owner_id, owner_name = teacher_fixture
        sub_id, _ = sub_teacher_fixture
        plan(group_fixture, owner_id, substitute_id=sub_id)

        assert GROUP in _all_data(sub_account_fixture)[owner_name]

    def test_replaced_teacher_does_not_get_group_by_that_lesson(
        self, teacher_fixture, account_fixture, sub_teacher_fixture, sub_account_fixture,
        foreign_group, plan,
    ):
        """
        Занятие чужой группы числится за преподавателем (teacher_id), но на эту дату
        поставлена замена — вести его будет другой, значит и группа ему не нужна.
        """
        owner_id, _ = teacher_fixture
        sub_id, _ = sub_teacher_fixture
        plan(foreign_group, owner_id, substitute_id=sub_id)

        assert _all_data(account_fixture) == {}

    def test_cancelled_assignment_does_not_open_group(
        self, teacher_fixture, sub_teacher_fixture, sub_account_fixture,
        group_fixture, student_fixture, membership_fixture, plan,
    ):
        """Отменённое занятие права на группу не даёт."""
        sub_id, _ = sub_teacher_fixture
        plan(group_fixture, sub_id, status='cancelled')

        assert _all_data(sub_account_fixture) == {}

    def test_already_recorded_assignment_does_not_open_group(
        self, teacher_fixture, sub_teacher_fixture, sub_account_fixture,
        group_fixture, student_fixture, membership_fixture, plan, fact_lesson,
    ):
        """
        Замена уже проведена и записана — форма записи больше не понадобится, и
        доступ к составу чужой группы не должен оставаться навсегда.
        """
        sub_id, _ = sub_teacher_fixture
        plan(group_fixture, sub_id, status='done', fact_lesson_id=fact_lesson)

        assert _all_data(sub_account_fixture) == {}

    def test_pending_lesson_keeps_group_open_next_to_recorded_one(
        self, teacher_fixture, sub_teacher_fixture, sub_account_fixture,
        group_fixture, student_fixture, membership_fixture, plan, fact_lesson,
    ):
        """Одно занятие проведено, следующее ещё впереди → группа по-прежнему видна."""
        _, owner_name = teacher_fixture
        sub_id, _ = sub_teacher_fixture
        plan(group_fixture, sub_id, status='done', fact_lesson_id=fact_lesson, seq=1)
        plan(group_fixture, sub_id, seq=2)

        assert GROUP in _all_data(sub_account_fixture)[owner_name]

    def test_query_count_does_not_grow_with_school_size(
        self, teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture, foreign_group,
        django_assert_max_num_queries,
    ):
        """Запросов — фиксированное число (без N+1 по группам и ученикам)."""
        client = _client('teacher', account_fixture)
        with django_assert_max_num_queries(8):
            assert client.post('/api/getAllData', {}, format='json').status_code == 200


class TestGetDataScope:
    """
    POST /api/getData — только группы, где преподаватель ВЛАДЕЛЕЦ. Чужая группа с
    назначенным занятием сюда не попадает: для неё форма записи идёт в getAllData.
    """

    def test_returns_only_own_groups(
        self, teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture, foreign_group,
    ):
        assert list(_own_data(account_fixture)) == [GROUP]

    def test_substituted_group_is_not_own(
        self, teacher_fixture, sub_teacher_fixture, sub_account_fixture,
        group_fixture, student_fixture, membership_fixture, plan,
    ):
        """Назначенное занятие чужой группы делает её видимой в getAllData, но не своей."""
        _, owner_name = teacher_fixture
        sub_id, _ = sub_teacher_fixture
        plan(group_fixture, sub_id)

        assert _own_data(sub_account_fixture) == {}
        assert GROUP in _all_data(sub_account_fixture)[owner_name]

    def test_own_read_is_scoped_in_sql(
        self, teacher_fixture, group_fixture, student_fixture, membership_fixture,
        foreign_group,
    ):
        """
        Смысл доработки: getData зовётся на каждый вход в кабинет и не должен читать
        учеников всей школы, чтобы потом выбрать своих. Фильтр по преподавателю обязан
        стоять в самом запросе membership, а не после чтения.
        """
        from django.test.utils import CaptureQueriesContext

        from apps.teacher_spa.repository import read_own_students

        teacher_id, owner_name = teacher_fixture
        with CaptureQueriesContext(connection) as ctx:
            result = read_own_students(teacher_id)

        assert list(result['data']) == [owner_name]
        assert list(result['data'][owner_name]) == [GROUP]
        membership_sql = [q['sql'] for q in ctx if 'FROM "group_memberships"' in q['sql']]
        assert membership_sql, 'ожидался запрос к group_memberships'
        assert all(f'"teacher_id" = {teacher_id}' in sql for sql in membership_sql)
