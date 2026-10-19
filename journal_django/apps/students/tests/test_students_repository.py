"""
Unit/integration тесты для StudentsRepository.

Используют реальную БД (managed=False, продовая).
Все созданные строки удаляются в teardown.

Тестируют:
  - list_students: форма ответа {rows, total, page, page_size}
  - list_students: фильтры, сортировка
  - get_student: существующий/несуществующий
  - create_student: RETURNING * работает, поля заполнены
  - update_student: COALESCE обновление
  - student_stats: форма ответа (keys: student_id, directions, groups, overall)
  - get_student_balance: форма ответа (keys: paid_by_direction, attended_by_direction, total_balance, total_paid_amount, payments)
"""
from __future__ import annotations

import datetime

import pytest
from django.db import connection

from apps.core.tests.minimal_school import ensure_minimal_school

from apps.students import repository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cleanup_student(student_id: int) -> None:
    """Прямой DELETE — как Nest e2e after() через пул."""
    with connection.cursor() as cur:
        cur.execute('DELETE FROM students WHERE id = %s', [student_id])


def _make_student_data(**overrides) -> dict:
    return {
        'full_name': '__test_repo_student__',
        'birth_date': None,
        'platform_id': None,
        'crm_link': None,
        'parent1_name': None,
        'parent1_phone': None,
        'parent1_email': None,
        'parent2_name': None,
        'parent2_phone': None,
        'parent2_email': None,
        **overrides,
    }


# ---------------------------------------------------------------------------
# TestListStudents
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestListStudents:
    """Тесты list_students()."""

    def test_returns_correct_shape(self):
        result = repository.list_students()
        assert 'rows' in result
        assert 'total' in result
        assert 'page' in result
        assert 'page_size' in result

    def test_page_and_page_size_defaults(self):
        result = repository.list_students()
        assert result['page'] == 1
        assert result['page_size'] == 50

    def test_total_is_int(self):
        result = repository.list_students()
        assert isinstance(result['total'], int)

    def test_rows_is_list(self):
        result = repository.list_students()
        assert isinstance(result['rows'], list)

    def test_page_size_respected(self):
        result = repository.list_students(page=1, page_size=2)
        assert result['page_size'] == 2
        assert len(result['rows']) <= 2

    def test_filter_full_name_no_match(self):
        """Несуществующее имя → пустой список."""
        result = repository.list_students(
            filters={'full_name': '__nonexistent_xyz_student_filter__'}
        )
        assert result['rows'] == []

    def test_sort_by_full_name_asc(self):
        """sort_by=full_name&sort_dir=asc принимается без ошибок."""
        result = repository.list_students(sort_by='full_name', sort_dir='asc', page_size=5)
        assert isinstance(result['rows'], list)

    def test_sort_by_created_at_desc(self):
        """sort_by=created_at&sort_dir=desc принимается без ошибок."""
        result = repository.list_students(sort_by='created_at', sort_dir='desc', page_size=5)
        assert isinstance(result['rows'], list)

    def test_sort_by_id_asc(self):
        result = repository.list_students(sort_by='id', sort_dir='asc', page_size=5)
        assert isinstance(result['rows'], list)

    def test_filter_by_manager_id_no_match(self):
        """Несуществующий manager_id → пустой список (не падает на приведении типа)."""
        result = repository.list_students(filters={'manager_id': '999999999'})
        assert result['rows'] == []


# ---------------------------------------------------------------------------
# TestGetStudent
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGetStudent:
    """Тесты get_student()."""

    def test_nonexistent_returns_none(self):
        result = repository.get_student(999_999_999)
        assert result is None

    def test_existing_returns_dict(self):
        data = _make_student_data(full_name='__test_get_student__')
        student = repository.create_student(data)
        sid = student['id']
        try:
            result = repository.get_student(sid)
            assert result is not None
            assert result['id'] == sid
            assert result['full_name'] == '__test_get_student__'
        finally:
            _cleanup_student(sid)

    def test_existing_has_required_fields(self):
        data = _make_student_data(full_name='__test_get_fields__')
        student = repository.create_student(data)
        sid = student['id']
        try:
            result = repository.get_student(sid)
            for field in ['id', 'full_name', 'stage', 'created_at', 'manager_id', 'manager_name']:
                assert field in result
        finally:
            _cleanup_student(sid)

    def test_manager_null_by_default(self):
        data = _make_student_data(full_name='__test_get_manager_default__')
        student = repository.create_student(data)
        sid = student['id']
        try:
            result = repository.get_student(sid)
            assert result['manager_id'] is None
            assert result['manager_name'] is None
        finally:
            _cleanup_student(sid)


# ---------------------------------------------------------------------------
# TestCreateStudent
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCreateStudent:
    """Тесты create_student()."""

    def test_create_returns_dict(self):
        data = _make_student_data(full_name='__test_create_student__')
        student = repository.create_student(data)
        try:
            assert isinstance(student, dict)
            assert 'id' in student
            assert student['full_name'] == '__test_create_student__'
        finally:
            _cleanup_student(student['id'])

    def test_created_student_in_db(self):
        data = _make_student_data(full_name='__test_create_db__')
        student = repository.create_student(data)
        sid = student['id']
        try:
            fetched = repository.get_student(sid)
            assert fetched is not None
            assert fetched['id'] == sid
        finally:
            _cleanup_student(sid)

    def test_create_with_parent_contacts_and_birth_date(self):
        data = _make_student_data(
            full_name='__test_create_full__',
            parent1_name='Иван Петров',
            parent1_phone='+79001234567',
            parent1_email='parent1@example.com',
            parent2_name='Мария Петрова',
            parent2_phone='+79007654321',
            parent2_email='parent2@example.com',
            crm_link='https://bitrix24.example/crm/deal/1',
            birth_date='2013-05-20',
        )
        student = repository.create_student(data)
        try:
            assert student['parent1_name'] == 'Иван Петров'
            assert student['parent1_phone'] == '+79001234567'
            assert student['parent1_email'] == 'parent1@example.com'
            assert student['parent2_name'] == 'Мария Петрова'
            assert student['parent2_phone'] == '+79007654321'
            assert student['parent2_email'] == 'parent2@example.com'
            assert student['crm_link'] == 'https://bitrix24.example/crm/deal/1'
            # repository отдаёт сырой ORM-объект date; строкой станет в сериализаторе
            assert student['birth_date'] == datetime.date(2013, 5, 20)
            assert 'age' not in student
        finally:
            _cleanup_student(student['id'])


# ---------------------------------------------------------------------------
# TestUpdateStudent
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestUpdateStudent:
    """Тесты update_student()."""

    def test_update_nonexistent_returns_none(self):
        result = repository.update_student(999_999_999, {'full_name': 'ghost'})
        assert result is None

    def test_update_full_name(self):
        data = _make_student_data(full_name='__test_upd_before__')
        student = repository.create_student(data)
        sid = student['id']
        try:
            updated = repository.update_student(sid, {'full_name': '__test_upd_after__'})
            assert updated['full_name'] == '__test_upd_after__'
        finally:
            _cleanup_student(sid)

    def test_update_coalesce_keeps_old_values(self):
        """Если поля не переданы — старые значения сохраняются."""
        data = _make_student_data(
            full_name='__test_coalesce__',
            birth_date='2012-01-15',
        )
        student = repository.create_student(data)
        sid = student['id']
        try:
            updated = repository.update_student(sid, {'full_name': '__test_coalesce_new__'})
            assert updated['birth_date'] == datetime.date(2012, 1, 15)
        finally:
            _cleanup_student(sid)


# ---------------------------------------------------------------------------
# TestStudentStats
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestStudentStats:
    """Тесты student_stats() — форма ответа."""

    def test_shape_for_new_student(self):
        """Ученик без посещений — структура ответа корректная."""
        data = _make_student_data(full_name='__test_stats_shape__')
        student = repository.create_student(data)
        sid = student['id']
        try:
            result = repository.student_stats(sid)
            assert 'student_id' in result
            assert 'directions' in result
            assert 'groups' in result
            assert 'overall' in result
            assert result['student_id'] == sid
            assert isinstance(result['directions'], list)
            assert isinstance(result['groups'], list)
        finally:
            _cleanup_student(sid)

    def test_overall_shape(self):
        """overall содержит нужные ключи."""
        data = _make_student_data(full_name='__test_stats_overall__')
        student = repository.create_student(data)
        sid = student['id']
        try:
            result = repository.student_stats(sid)
            overall = result['overall']
            for key in [
                'lessons_recorded', 'attended_count', 'missed_count',
                'denominator', 'attendance_pct', 'this_month',
            ]:
                assert key in overall, f"Missing key '{key}' in overall"
            assert 'lessons_recorded' in overall['this_month']
            assert 'attended_count' in overall['this_month']
            assert 'attendance_pct' in overall['this_month']
        finally:
            _cleanup_student(sid)

    def test_empty_stats_for_new_student(self):
        """Новый ученик без групп — нули в overall."""
        data = _make_student_data(full_name='__test_stats_zeros__')
        student = repository.create_student(data)
        sid = student['id']
        try:
            result = repository.student_stats(sid)
            assert result['overall']['lessons_recorded'] == 0
            assert result['overall']['attended_count'] == 0
            assert result['overall']['missed_count'] == 0
            assert result['overall']['denominator'] == 0
            assert result['overall']['attendance_pct'] is None
            assert result['directions'] == []
            assert result['groups'] == []
        finally:
            _cleanup_student(sid)

    def test_half_lesson_weighting_45min(self):
        """45-мин занятие = 0.5 урока: 3 занятия по 45 мин → attended 1.5 урока, а не 3.
        Прогресс считается против плана курса в уроках (denominator), поэтому
        pct = 1.5 / 8 = 18.75 → 18.8, а не 3/8=37.5 (был баг сырого COUNT)."""
        data = _make_student_data(full_name='__test_stats_half__')
        student = repository.create_student(data)
        sid = student['id']
        gid = did = tid = None
        try:
            with connection.cursor() as cur:
                cur.execute("INSERT INTO teachers (name, active) VALUES ('__half_teacher__', true) RETURNING id")
                tid = cur.fetchone()[0]
                cur.execute("INSERT INTO directions (name, active, total_lessons) "
                            "VALUES ('__half_dir__', true, 8) RETURNING id")
                did = cur.fetchone()[0]
                cur.execute("INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
                            "lesson_duration_minutes, active, lesson_number_offset) "
                            "VALUES ('__half_group__', %s, %s, true, 45, true, 0) RETURNING id",
                            [did, tid])
                gid = cur.fetchone()[0]
                cur.execute("INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                            "VALUES (%s, %s, 1.5, true)", [gid, sid])
                # 3 проведённых 45-мин урока, на всех ученик присутствовал.
                for i in range(1, 4):
                    cur.execute("INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
                                "lesson_duration_minutes, lesson_type, submitted_at, submitted_by_token) "
                                "VALUES (%s, %s, %s, %s, 45, 'regular', now(), %s) RETURNING id",
                                [gid, tid, f'2026-05-0{i}', i * 0.5, f'__half_tok_{i}__'])
                    lid = cur.fetchone()[0]
                    cur.execute("INSERT INTO lesson_attendance (lesson_id, student_id, present) "
                                "VALUES (%s, %s, true)", [lid, sid])

            result = repository.student_stats(sid)
            grp = result['groups'][0]
            assert grp['attended_count'] == 1.5      # 3 × 0.5, а не 3
            assert grp['lessons_recorded'] == 1.5
            assert grp['denominator'] == 8           # план курса в уроках
            assert grp['attendance_pct'] == 18.8     # 1.5/8, не 37.5
            assert result['overall']['attended_count'] == 1.5
        finally:
            with connection.cursor() as cur:
                if gid is not None:
                    cur.execute("DELETE FROM lesson_attendance WHERE student_id = %s", [sid])
                    cur.execute("DELETE FROM lessons WHERE group_id = %s", [gid])
                    cur.execute("DELETE FROM group_memberships WHERE group_id = %s", [gid])
                    cur.execute("DELETE FROM groups WHERE id = %s", [gid])
                if did is not None:
                    cur.execute("DELETE FROM directions WHERE id = %s", [did])
                if tid is not None:
                    cur.execute("DELETE FROM teachers WHERE id = %s", [tid])
            _cleanup_student(sid)

    def test_progress_capped_at_100_over_plan(self):
        """Доп.урок сверх плана (проведено больше, чем total_lessons курса): completion
        курса зажат на 100%, не 125%. План 4 урока, проведено/присутствовал 5 (5-й —
        сверх курса, lesson_type='extra'). attended_count=5 (сырое число честное),
        denominator=4, но attendance_pct=100.0, не 125. «Сверх курса» (attended−denom=1)
        выводит фронт из этих же полей."""
        data = _make_student_data(full_name='__test_stats_over__')
        student = repository.create_student(data)
        sid = student['id']
        gid = did = tid = None
        try:
            with connection.cursor() as cur:
                cur.execute("INSERT INTO teachers (name, active) VALUES ('__over_teacher__', true) RETURNING id")
                tid = cur.fetchone()[0]
                cur.execute("INSERT INTO directions (name, active, total_lessons) "
                            "VALUES ('__over_dir__', true, 4) RETURNING id")
                did = cur.fetchone()[0]
                cur.execute("INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
                            "lesson_duration_minutes, active, lesson_number_offset) "
                            "VALUES ('__over_group__', %s, %s, true, 60, true, 0) RETURNING id",
                            [did, tid])
                gid = cur.fetchone()[0]
                cur.execute("INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                            "VALUES (%s, %s, 5, true)", [gid, sid])
                # 4 плановых урока + 1 сверх курса (lesson_type='extra'), на всех присутствовал.
                for i in range(1, 6):
                    ltype = 'extra' if i == 5 else 'regular'
                    cur.execute("INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
                                "lesson_duration_minutes, lesson_type, submitted_at, submitted_by_token) "
                                "VALUES (%s, %s, %s, %s, 60, %s, now(), %s) RETURNING id",
                                [gid, tid, f'2026-05-0{i}', i, ltype, f'__over_tok_{i}__'])
                    lid = cur.fetchone()[0]
                    cur.execute("INSERT INTO lesson_attendance (lesson_id, student_id, present) "
                                "VALUES (%s, %s, true)", [lid, sid])

            result = repository.student_stats(sid)
            grp = result['groups'][0]
            assert grp['attended_count'] == 5        # честное число сохраняется
            assert grp['denominator'] == 4           # план курса
            assert grp['attendance_pct'] == 100.0    # зажат, не 125
            assert max(grp['attended_count'] - grp['denominator'], 0) == 1  # «+1 сверх курса»
            direction = result['directions'][0]
            assert direction['attendance_pct'] == 100.0
            assert result['overall']['attendance_pct'] == 100.0
        finally:
            with connection.cursor() as cur:
                if gid is not None:
                    cur.execute("DELETE FROM lesson_attendance WHERE student_id = %s", [sid])
                    cur.execute("DELETE FROM lessons WHERE group_id = %s", [gid])
                    cur.execute("DELETE FROM group_memberships WHERE group_id = %s", [gid])
                    cur.execute("DELETE FROM groups WHERE id = %s", [gid])
                if did is not None:
                    cur.execute("DELETE FROM directions WHERE id = %s", [did])
                if tid is not None:
                    cur.execute("DELETE FROM teachers WHERE id = %s", [tid])
            _cleanup_student(sid)


# ---------------------------------------------------------------------------
# TestStudentStatsRemaining
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestStudentStatsRemaining:
    """remaining в group_stats — вычисляемый общий баланс ученика (не колонка gm.remaining)."""

    def test_group_remaining_matches_balance_for_student(self):
        from apps.finances.repository import balance_for_student

        data = _make_student_data(full_name='__test_stats_remaining__')
        student = repository.create_student(data)
        sid = student['id']
        direction_id = group_id = teacher_id = None
        try:
            with connection.cursor() as cur:
                cur.execute(
                    "INSERT INTO teachers (name, active) VALUES ('__stats_rem_teacher__', true) "
                    "RETURNING id"
                )
                teacher_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO directions (name, active) "
                    "VALUES ('__stats_rem_dir__', true) RETURNING id"
                )
                direction_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
                    "lesson_duration_minutes, active, lesson_number_offset) "
                    "VALUES ('__stats_rem_group__', %s, %s, false, 60, true, 0) RETURNING id",
                    [direction_id, teacher_id],
                )
                group_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                    "VALUES (%s, %s, 0, true)",
                    [group_id, sid],
                )
                cur.execute(
                    "INSERT INTO payments (student_id, direction_id, subscriptions_count, "
                    "lessons_count, unit_price, total_amount, paid_at, created_by) "
                    "VALUES (%s,%s,1,4,2000,2000,'2026-06-01','test')",
                    [sid, direction_id],
                )

            result = repository.student_stats(sid)
            assert len(result['groups']) == 1
            assert result['groups'][0]['remaining'] == balance_for_student(sid) == 4
        finally:
            with connection.cursor() as cur:
                if group_id is not None:
                    cur.execute('DELETE FROM payments WHERE student_id = %s', [sid])
                    cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [group_id])
                    cur.execute('DELETE FROM groups WHERE id = %s', [group_id])
                if direction_id is not None:
                    cur.execute('DELETE FROM directions WHERE id = %s', [direction_id])
                if teacher_id is not None:
                    cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])
            _cleanup_student(sid)


# ---------------------------------------------------------------------------
# TestGetStudentBalance
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGetStudentBalance:
    """Тесты get_student_balance() — форма ответа. Постоянный дом — apps/finances/."""

    def test_shape_for_new_student(self):
        """Ученик без оплат — структура ответа корректная."""
        from apps.payments import repository as payments_repo
        data = _make_student_data(full_name='__test_balance_shape__')
        student = repository.create_student(data)
        sid = student['id']
        try:
            result = payments_repo.get_student_balance(sid)
            assert 'paid_by_direction' in result
            assert 'attended_by_direction' in result
            assert 'total_balance' in result
            assert 'total_paid_amount' in result
            assert 'payments' in result
            assert isinstance(result['paid_by_direction'], list)
            assert isinstance(result['attended_by_direction'], list)
            assert isinstance(result['payments'], list)
        finally:
            _cleanup_student(sid)

    def test_zero_balance_for_new_student(self):
        """Ученик без оплат — нулевые балансы."""
        from apps.payments import repository as payments_repo
        data = _make_student_data(full_name='__test_balance_zeros__')
        student = repository.create_student(data)
        sid = student['id']
        try:
            result = payments_repo.get_student_balance(sid)
            assert result['total_balance'] == 0
            assert result['total_paid_amount'] == 0
            assert result['paid_by_direction'] == []
            assert result['attended_by_direction'] == []
            assert result['payments'] == []
        finally:
            _cleanup_student(sid)

    def test_paid_by_direction_shape(self):
        """У ученика с оплатой paid_by_direction непуст и содержит нужные ключи."""
        from apps.payments import repository as payments_repo
        student_id = ensure_minimal_school()['student_id']

        result = payments_repo.get_student_balance(student_id)
        assert result['paid_by_direction']
        d = result['paid_by_direction'][0]
        for key in ['direction_id', 'direction_name', 'direction_color', 'total_paid_amount']:
            assert key in d, f"Missing key '{key}' in paid_by_direction item"

    def test_attended_by_direction_shape(self):
        """У ученика с посещением attended_by_direction непуст и содержит нужные ключи."""
        from apps.payments import repository as payments_repo
        student_id = ensure_minimal_school()['student_id']

        result = payments_repo.get_student_balance(student_id)
        assert result['attended_by_direction']
        d = result['attended_by_direction'][0]
        for key in ['direction_id', 'direction_name', 'direction_color', 'attended_lessons']:
            assert key in d, f"Missing key '{key}' in attended_by_direction item"
