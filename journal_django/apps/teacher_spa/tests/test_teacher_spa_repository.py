"""
test_teacher_spa_repository.py — интеграционные тесты repository слоя teacher_spa.

Покрытие:
  - read_own_students: структура data[teacher][group], поля студента,
    lessonsDone (max), startDate формат 'DD.MM.YYYY'.
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.teacher_spa import repository

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _set_secret(settings):
    settings.ADMIN_COOKIE_SECRET = 'deadbeef' * 16


# ---------------------------------------------------------------------------
# read_own_students — формат среза (общая сборка _build_from_rows)
# ---------------------------------------------------------------------------

class TestReadOwnStudents:

    def test_returns_data_and_index(
        self, teacher_fixture, group_fixture, student_fixture, membership_fixture
    ):
        """Базовая структура: data[teacher][group] = {students, lessonsDone, ...}."""
        _, teacher_name = teacher_fixture
        result = repository.read_own_students(teacher_fixture[0])
        assert 'data' in result
        assert 'index' in result
        assert teacher_name in result['data']

    def test_group_fields(
        self, teacher_fixture, group_fixture, student_fixture, membership_fixture
    ):
        """Поля группы: students, lessonsDone, pm, vkChat, startDate, isGroup."""
        _, teacher_name = teacher_fixture
        result = repository.read_own_students(teacher_fixture[0])
        teacher_data = result['data'][teacher_name]
        # Ищем нашу тестовую группу
        assert len(teacher_data) >= 1
        # Находим группу по ID
        group_name = '__spa_test_group__ пн 10:00'
        assert group_name in teacher_data
        grp = teacher_data[group_name]
        assert 'students' in grp
        assert 'lessonsDone' in grp
        assert 'pm' in grp
        assert 'vkChat' in grp
        assert 'startDate' in grp
        assert 'isGroup' in grp
        assert isinstance(grp['isGroup'], bool)

    def test_student_fields(
        self, teacher_fixture, group_fixture, student_fixture, membership_fixture
    ):
        """Поля студента: name, lessonsDone, remaining, birthDate, sheetName, sheetRow."""
        _, teacher_name = teacher_fixture
        result = repository.read_own_students(teacher_fixture[0])
        group_name = '__spa_test_group__ пн 10:00'
        grp = result['data'][teacher_name][group_name]
        assert len(grp['students']) >= 1
        stu = next(s for s in grp['students'] if s['name'] == '__spa_test_student__')
        assert 'name' in stu
        assert 'lessonsDone' in stu
        assert 'remaining' in stu
        assert 'birthDate' in stu
        assert 'sheetName' in stu
        assert 'sheetRow' in stu
        # lessonsDone=0 в фикстуре → 0 (JS Number()||0)
        assert stu['lessonsDone'] == 0
        # remaining — вычисляемый общий баланс ученика; membership_fixture теперь
        # включает оплату на 8 уроков (см. conftest.py) → 8
        assert stu['remaining'] == 8
        # birthDate пустая строка (NULL в БД)
        assert stu['birthDate'] == ''

    def test_lessons_done_max(
        self, teacher_fixture, group_fixture, student_fixture
    ):
        """lessonsDone у группы = max по ученикам."""
        # Создаём второго ученика с lessons_done=5
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO students (full_name) "
                "VALUES ('__spa_stu2__') RETURNING id"
            )
            stu2_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO group_memberships (group_id, student_id, lessons_done, active)
                VALUES (%s, %s, 5, true) RETURNING id
                """,
                [group_fixture, stu2_id],
            )
            mem2_id = cur.fetchone()[0]
            # Первый ученик без membership — создаём с lessons_done=2
            cur.execute(
                """
                INSERT INTO group_memberships (group_id, student_id, lessons_done, active)
                VALUES (%s, %s, 2, true) RETURNING id
                """,
                [group_fixture, student_fixture],
            )
            mem1_id = cur.fetchone()[0]

        try:
            _, teacher_name = teacher_fixture
            result = repository.read_own_students(teacher_fixture[0])
            group_name = '__spa_test_group__ пн 10:00'
            grp = result['data'][teacher_name][group_name]
            # lessonsDone группы = 5 (max)
            assert grp['lessonsDone'] == 5
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM group_memberships WHERE id IN (%s, %s)', [mem1_id, mem2_id])
                cur.execute('DELETE FROM students WHERE id = %s', [stu2_id])


def test_read_own_students_marks_locked_transferred_student(
    teacher_fixture, direction_fixture, group_fixture, student_fixture, membership_fixture,
):
    """Ученик с B=5 (source membership lessons_done=5) — отдаём lockedThrough=5.0.

    Готового флага locked в ответе нет намеренно: блокировка = «номер урока <= B», а
    номер заполняемого урока знает только форма записи (он из плана занятия, а не из
    прогресса группы). Здесь проверяем сам факт."""
    teacher_id, teacher_name = teacher_fixture
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE group_memberships SET lessons_done = 2 WHERE id = %s", [membership_fixture],
        )
        cur.execute(
            "INSERT INTO groups (name,direction_id,teacher_id,is_individual,"
            "lesson_duration_minutes,active,lesson_number_offset) VALUES ('__spa_locked_src__',%s,%s,false,60,false,0) "
            "RETURNING id",
            [direction_fixture, teacher_id],
        )
        src_group_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
            "VALUES (%s,%s,5,false) RETURNING id",
            [src_group_id, student_fixture],
        )
        src_membership_id = cur.fetchone()[0]
        cur.execute(
            "UPDATE group_memberships SET transferred_from_id = %s WHERE id = %s",
            [src_membership_id, membership_fixture],
        )
    try:
        result = repository.read_own_students(teacher_fixture[0])
        group_data = result['data'][teacher_name]['__spa_test_group__ пн 10:00']
        student_row = next(s for s in group_data['students'] if s['name'] == '__spa_test_student__')
        assert student_row['lockedThrough'] == 5.0
        assert 'locked' not in student_row
    finally:
        with connection.cursor() as cur:
            cur.execute("UPDATE group_memberships SET transferred_from_id = NULL WHERE id = %s",
                        [membership_fixture])
            cur.execute('DELETE FROM group_memberships WHERE id = %s', [src_membership_id])
            cur.execute('DELETE FROM groups WHERE id = %s', [src_group_id])


def test_read_own_students_returns_skips_by_lesson_number(
    teacher_fixture, group_fixture, student_fixture, membership_fixture,
):
    """Маркеры LessonSkip уходят на фронт картой {номер урока: [имена]}, чтобы форма
    записи применила их к ЗАПОЛНЯЕМОМУ уроку. Служебные поля (_student_id/_group_id)
    в ответ не утекают."""
    _, teacher_name = teacher_fixture
    with connection.cursor() as cur:
        cur.execute("UPDATE group_memberships SET lessons_done = 2 WHERE id = %s", [membership_fixture])
        cur.execute("INSERT INTO lesson_skips (group_id, student_id, lesson_number, created_at) "
                    "VALUES (%s, %s, 3, now())", [group_fixture, student_fixture])
    try:
        result = repository.read_own_students(teacher_fixture[0])
        grp = result['data'][teacher_name]['__spa_test_group__ пн 10:00']
        assert '_group_id' not in grp
        assert grp['skips'] == {'3': ['__spa_test_student__']}
        row = next(s for s in grp['students'] if s['name'] == '__spa_test_student__')
        assert '_student_id' not in row
        assert 'skip' not in row
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_skips WHERE group_id = %s', [group_fixture])


def test_read_own_students_keeps_skip_off_the_progress_number(
    teacher_fixture, group_fixture, student_fixture, membership_fixture,
):
    """Регрессия: пропуск на уроке №5 при прогрессе группы 0 («следующий по прогрессу»
    = №1) раньше терялся — срез отдавал маркеры только для одного номера, выведенного
    из max(lessons_done)+шаг. Урок же записывается по номеру ИЗ ПЛАНА, и на группах,
    где план разошёлся с прогрессом (перевод, сгорание, перенос), форма не показывала
    блокер вовсе: преподаватель отмечал «Пришёл», а бэк молча ставил unpaid_skip."""
    _, teacher_name = teacher_fixture
    with connection.cursor() as cur:
        cur.execute("UPDATE group_memberships SET lessons_done = 0 WHERE id = %s", [membership_fixture])
        cur.execute("INSERT INTO lesson_skips (group_id, student_id, lesson_number, created_at) "
                    "VALUES (%s, %s, 5, now())", [group_fixture, student_fixture])
    try:
        result = repository.read_own_students(teacher_fixture[0])
        grp = result['data'][teacher_name]['__spa_test_group__ пн 10:00']
        assert grp['lessonsDone'] == 0
        assert grp['skips'] == {'5': ['__spa_test_student__']}
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_skips WHERE group_id = %s', [group_fixture])


def test_read_own_students_skips_half_lesson_number_key(
    teacher_fixture, half_group_fixture, student_fixture,
):
    """Half-lesson: номер урока дробный, ключ карты — '2.5' (как даёт JS String(2.5))."""
    _, teacher_name = teacher_fixture
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
            "VALUES (%s, %s, 2, true) RETURNING id", [half_group_fixture, student_fixture],
        )
        membership_id = cur.fetchone()[0]
        cur.execute("INSERT INTO lesson_skips (group_id, student_id, lesson_number, created_at) "
                    "VALUES (%s, %s, 2.5, now())", [half_group_fixture, student_fixture])
    try:
        result = repository.read_own_students(teacher_fixture[0])
        grp = result['data'][teacher_name]['__spa_half_group__ 45 минут вт 11:00']
        assert grp['skips'] == {'2.5': ['__spa_test_student__']}
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_skips WHERE group_id = %s', [half_group_fixture])
            cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership_id])
