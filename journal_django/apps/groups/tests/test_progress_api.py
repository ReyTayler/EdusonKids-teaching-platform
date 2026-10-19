"""
API-тесты обзорной матрицы посещаемости:
  GET /api/admin/groups/:id/progress

Права: IsManagerOrAdmin. Аутентификация — JWT (клиенты из conftest).

Проверяем:
  - RBAC (anon 401 / teacher 403 / manager 200);
  - 404 для несуществующей группы;
  - контракт ответа (slots/students + счётчики);
  - слоты доводятся до direction.total_lessons (плановые уроки видны);
  - ячейки: True=был, False=не был, None=не проведён / не в составе;
  - N+1: не более 3 запросов данных на всю матрицу.
"""
from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

pytestmark = pytest.mark.django_db


@pytest.fixture
def progress_group():
    """
    Группа (direction.total_lessons=8) + 2 ученика + 2 проведённых урока
    (№1, №2) с посещаемостью. Возвращает dict с id-шниками для ассертов.

    Урок 1: Аня был, Боря не был.  Урок 2: Аня был, Боря был.
    Боре запись на урок 1 создаём как present=false (в составе, но не был).
    """
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name, active) VALUES ('__pg_t__', true) RETURNING id")
        teacher_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO directions (name,total_lessons,active) "
            "VALUES ('__pg_d__',8,true) RETURNING id"
        )
        direction_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name,direction_id,teacher_id,is_individual,lesson_duration_minutes,active,lesson_number_offset) "
            "VALUES ('__pg_g__',%s,%s,false,60,true,0) RETURNING id",
            [direction_id, teacher_id],
        )
        group_id = cur.fetchone()[0]

        # 'Аня' сортируется раньше 'Боря' (порядок строк по имени).
        cur.execute("INSERT INTO students (full_name) VALUES ('__pg Аня__') RETURNING id")
        anya = cur.fetchone()[0]
        cur.execute("INSERT INTO students (full_name) VALUES ('__pg Боря__') RETURNING id")
        borya = cur.fetchone()[0]
        for sid in (anya, borya):
            cur.execute(
                "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) VALUES (%s,%s,0,true)",
                [group_id, sid],
            )

        lesson_ids = []
        for num, date in ((1, '2026-03-02'), (2, '2026-03-05')):
            cur.execute(
                "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
                "lesson_duration_minutes, lesson_type, submitted_by_token) "
                "VALUES (%s,%s,%s,%s,60,'regular','test') RETURNING id",
                [group_id, teacher_id, date, num],
            )
            lesson_ids.append(cur.fetchone()[0])

        att = [
            (lesson_ids[0], anya, True), (lesson_ids[0], borya, False),
            (lesson_ids[1], anya, True), (lesson_ids[1], borya, True),
        ]
        for lid, sid, present in att:
            cur.execute(
                "INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s,%s,%s)",
                [lid, sid, present],
            )

    yield {'group_id': group_id, 'anya': anya, 'borya': borya, 'lesson_ids': lesson_ids}

    with connection.cursor() as cur:
        cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = ANY(%s)', [lesson_ids])
        cur.execute('DELETE FROM lessons WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM groups WHERE id = %s', [group_id])
        cur.execute('DELETE FROM students WHERE id = ANY(%s)', [[anya, borya]])
        cur.execute('DELETE FROM directions WHERE id = %s', [direction_id])
        cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])


def _url(gid: int) -> str:
    return f'/api/admin/groups/{gid}/progress'


class TestAuth:
    def test_anon_401(self, anon_client, progress_group):
        assert anon_client.get(_url(progress_group['group_id'])).status_code == 401

    def test_teacher_403(self, teacher_client, progress_group):
        assert teacher_client.get(_url(progress_group['group_id'])).status_code == 403

    @pytest.mark.parametrize('client_fixture', ['manager_client', 'admin_client'])
    def test_staff_200(self, request, client_fixture, progress_group):
        client = request.getfixturevalue(client_fixture)
        assert client.get(_url(progress_group['group_id'])).status_code == 200


def test_404_for_missing_group(manager_client):
    resp = manager_client.get(_url(999999999))
    assert resp.status_code == 404
    assert resp.json() == {'error': 'Not found'}


class TestContract:
    def test_slots_extend_to_total_lessons(self, manager_client, progress_group):
        body = manager_client.get(_url(progress_group['group_id'])).json()
        # direction.total_lessons=8 → 8 слотов, из них 2 проведено.
        assert body['total_slots'] == 8
        assert body['held_slots'] == 2
        assert len(body['slots']) == 8
        assert [s['held'] for s in body['slots']] == [True, True] + [False] * 6
        # Плановые слоты — без урока/даты.
        assert body['slots'][2]['lesson_id'] is None
        assert body['slots'][2]['date'] is None
        # DATE-инвариант: дата строкой без сдвига.
        assert body['slots'][0]['date'] == '2026-03-02'

    def test_student_rows_and_cells(self, manager_client, progress_group):
        body = manager_client.get(_url(progress_group['group_id'])).json()
        rows = {r['student_id']: r for r in body['students']}

        anya = rows[progress_group['anya']]
        # Аня: был на обоих проведённых → 2/2 = 100%.
        assert anya['present'] == 2
        assert anya['held'] == 2
        assert anya['pct'] == 100
        # cells выровнены по слотам: был, был, затем None по плановым.
        assert anya['cells'][:2] == [True, True]
        assert anya['cells'][2:] == [None] * 6

        borya = rows[progress_group['borya']]
        # Боря: не был на 1, был на 2 → 1/2 = 50%.
        assert borya['present'] == 1
        assert borya['held'] == 2
        assert borya['pct'] == 50
        assert borya['cells'][:2] == [False, True]
        # Без компенсаций — все ячейки не жёлтые.
        assert all(c is False for c in borya['compensated'])

    def test_compensated_cell_marks_makeup_or_burned(self, manager_client, progress_group):
        """Пропуск, закрытый доп.уроком/сожжённый, помечается compensated=true
        (фронт красит жёлтым). Исходная ячейка при этом остаётся present=false."""
        gid = progress_group['group_id']
        missed = progress_group['lesson_ids'][0]  # Боря не был на уроке 1
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO absence_resolutions (missed_lesson_id, student_id, status, created_at) "
                "VALUES (%s,%s,'burned',now())", [missed, progress_group['borya']])
        try:
            body = manager_client.get(_url(gid)).json()
            borya = next(r for r in body['students'] if r['student_id'] == progress_group['borya'])
            assert borya['cells'][0] is False        # исходный пропуск остаётся present=false
            assert borya['compensated'][0] is True   # жёлтая ячейка
            assert borya['compensated'][1] is False  # был вживую — не компенсация
            # Компенсированный пропуск засчитывается как посещение → Боря 2/2 = 100%.
            assert borya['present'] == 2 and borya['held'] == 2 and borya['pct'] == 100
            anya = next(r for r in body['students'] if r['student_id'] == progress_group['anya'])
            assert all(c is False for c in anya['compensated'])
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM absence_resolutions WHERE missed_lesson_id=%s', [missed])

    def test_compensated_without_attendance_row_shows_yellow(self, manager_client, progress_group):
        """Ученик добавлен в группу ПОСЛЕ проведения урока (строки-посещения нет),
        но его пропуск закрыт доп.уроком (makeup_done) → ячейка жёлтая (compensated),
        а НЕ «Не проведён» (None). Регресс: раньше нет строки → сразу None."""
        gid = progress_group['group_id']
        l1 = progress_group['lesson_ids'][0]
        with connection.cursor() as cur:
            cur.execute("INSERT INTO students (full_name) "
                        "VALUES ('__pg Гена__') RETURNING id")
            gena = cur.fetchone()[0]
            cur.execute("INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                        "VALUES (%s,%s,0,true)", [gid, gena])
            # Строки lesson_attendance на l1 для Гены НЕТ, но есть закрытая резолюция.
            cur.execute("INSERT INTO absence_resolutions (missed_lesson_id, student_id, status, created_at) "
                        "VALUES (%s,%s,'makeup_done',now())", [l1, gena])
        try:
            body = manager_client.get(_url(gid)).json()
            row = next(r for r in body['students'] if r['student_id'] == gena)
            assert row['cells'][0] is False        # не None — ячейка есть
            assert row['compensated'][0] is True    # жёлтая (отработан доп.уроком)
            # Отработанный доп.урок засчитывается как посещение (present, не только held).
            assert row['present'] >= 1 and row['held'] >= 1
        finally:
            with connection.cursor() as cur:
                cur.execute("DELETE FROM absence_resolutions WHERE student_id=%s", [gena])
                cur.execute("DELETE FROM group_memberships WHERE student_id=%s", [gena])
                cur.execute("DELETE FROM students WHERE id=%s", [gena])

    def test_free_and_unpaid_skip_cells(self, manager_client, progress_group):
        """Бесплатное занятие → free[i]=true (серый), засчитано как присутствие;
        неоплачиваемый пропуск → unpaid_skip[i]=true (синий), в held НЕ входит."""
        gid = progress_group['group_id']
        l1 = progress_group['lesson_ids'][0]
        with connection.cursor() as cur:
            # Аня на уроке 1 — бесплатное занятие (present=true)
            cur.execute("UPDATE lesson_attendance SET is_free=true WHERE lesson_id=%s AND student_id=%s",
                        [l1, progress_group['anya']])
            # Боря на уроке 1 — неоплачиваемый пропуск (present уже false)
            cur.execute("UPDATE lesson_attendance SET unpaid_skip=true WHERE lesson_id=%s AND student_id=%s",
                        [l1, progress_group['borya']])
        body = manager_client.get(_url(gid)).json()

        anya = next(r for r in body['students'] if r['student_id'] == progress_group['anya'])
        assert anya['cells'][0] is True
        assert anya['free'][0] is True
        assert anya['unpaid_skip'][0] is False
        # free засчитан как присутствие — Аня по-прежнему 2/2
        assert anya['present'] == 2 and anya['held'] == 2

        borya = next(r for r in body['students'] if r['student_id'] == progress_group['borya'])
        assert borya['cells'][0] is False
        assert borya['unpaid_skip'][0] is True
        assert borya['free'][0] is False
        assert borya['compensated'][0] is False
        # неопл.пропуск исключён из held: Боря теперь 1/1 (урок 2 был), а не 1/2
        assert borya['present'] == 1 and borya['held'] == 1

    def test_inactive_members_excluded(self, manager_client, progress_group):
        # Выбывший ученик (membership active=false) не должен быть строкой матрицы.
        gid = progress_group['group_id']
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO students (full_name) "
                "VALUES ('__pg Выбывший__') RETURNING id"
            )
            gone = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                "VALUES (%s,%s,0,false)",
                [gid, gone],
            )
        try:
            body = manager_client.get(_url(gid)).json()
            ids = {r['student_id'] for r in body['students']}
            assert gone not in ids
            assert ids == {progress_group['anya'], progress_group['borya']}
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM group_memberships WHERE student_id = %s', [gone])
                cur.execute('DELETE FROM students WHERE id = %s', [gone])

    def test_no_n_plus_one(self, manager_client, progress_group):
        # Матрица считается ≤3 запросами данных (участники + уроки + посещаемость).
        # Прогреваем кэш аутентификации/аккаунта отдельным запросом.
        manager_client.get(_url(progress_group['group_id']))
        with CaptureQueriesContext(connection) as ctx:
            resp = manager_client.get(_url(progress_group['group_id']))
        assert resp.status_code == 200
        data_queries = [
            q for q in ctx.captured_queries
            if any(t in q['sql'] for t in ('group_memberships', 'lessons', 'lesson_attendance',
                                           'lesson_skips', 'groups'))
        ]
        # 4 данные (участники + уроки + посещаемость + пометки-пропуски)
        # + возможный lookup группы/direction.
        assert len(data_queries) <= 5


class TestUnpaidSkipSlots:
    """Пометка «неоплачиваемый пропуск» живёт на СЛОТЕ группы (lesson_skips) и не
    ждёт, пока группа до этого урока дойдёт."""

    @staticmethod
    def _add_member_with_skips(group_id: int, name: str, numbers) -> int:
        with connection.cursor() as cur:
            cur.execute('INSERT INTO students (full_name) VALUES (%s) RETURNING id', [name])
            sid = cur.fetchone()[0]
            cur.execute(
                'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
                'VALUES (%s,%s,0,true)', [group_id, sid])
            for num in numbers:
                cur.execute(
                    'INSERT INTO lesson_skips (group_id, student_id, lesson_number, created_at) '
                    'VALUES (%s,%s,%s,NOW())', [group_id, sid, num])
        return sid

    @staticmethod
    def _cleanup(student_id: int) -> None:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_skips WHERE student_id = %s', [student_id])
            cur.execute('DELETE FROM group_memberships WHERE student_id = %s', [student_id])
            cur.execute('DELETE FROM students WHERE id = %s', [student_id])

    def test_skip_on_not_held_slot_is_blue_immediately(self, manager_client, progress_group):
        """Ученик пришёл в группу с 6-го урока: пропуски 1..5 проставлены, а уроки
        проведены только на слотах 1-2. Все пять ячеек обязаны быть синими сразу.

        Регрессия: матрица читала только lesson_attendance.unpaid_skip, поэтому
        синими выходили лишь пропуски под уже проведёнными уроками (1-2), а
        остальные дорисовывались по мере того, как группа доходила до слота.
        """
        gid = progress_group['group_id']
        gena = self._add_member_with_skips(gid, '__pg Гена__', (1, 2, 3, 4, 5))
        try:
            body = manager_client.get(_url(gid)).json()
            row = next(r for r in body['students'] if r['student_id'] == gena)

            # 1-2 — уроки проведены, строки посещаемости у Гены нет (его не было
            # в составе); 3-5 — уроков ещё нет вовсе. Синие все пять.
            assert row['unpaid_skip'][:5] == [True] * 5
            assert row['cells'][:5] == [False] * 5
            # Дальше — обычные плановые слоты (total_lessons=8).
            assert row['unpaid_skip'][5:] == [False] * 3
            assert row['cells'][5:] == [None] * 3
            # Неоплачиваемый пропуск — не обязательство посещения.
            assert row['held'] == 0 and row['present'] == 0
        finally:
            self._cleanup(gena)

    def test_skip_beyond_course_length_widens_matrix(self, manager_client, progress_group):
        """Помеченному пропуску всегда есть где показаться: слот 10 при курсе в
        8 уроков расширяет матрицу до 10 столбцов, а не теряет пометку."""
        gid = progress_group['group_id']
        dina = self._add_member_with_skips(gid, '__pg Дина__', (9, 10))
        try:
            body = manager_client.get(_url(gid)).json()
            assert body['total_slots'] == 10
            row = next(r for r in body['students'] if r['student_id'] == dina)
            assert len(row['cells']) == 10
            assert row['unpaid_skip'][8:] == [True, True]
            assert row['cells'][8:] == [False, False]
            # Соседи по группе от расширения не пострадали — у них там пусто.
            anya = next(r for r in body['students'] if r['student_id'] == progress_group['anya'])
            assert anya['cells'][8:] == [None, None]
        finally:
            self._cleanup(dina)

    def test_real_attendance_wins_over_skip_mark(self, manager_client, progress_group):
        """Пометка не перекрывает факт: если ученик на этом уроке отмечен как
        присутствовавший, ячейка зелёная, а не синяя."""
        gid = progress_group['group_id']
        with connection.cursor() as cur:
            # У Ани на уроке 1 есть present=true; вешаем поверх пометку на слот 1.
            cur.execute(
                'INSERT INTO lesson_skips (group_id, student_id, lesson_number, created_at) '
                'VALUES (%s,%s,1,NOW())', [gid, progress_group['anya']])
        try:
            body = manager_client.get(_url(gid)).json()
            anya = next(r for r in body['students'] if r['student_id'] == progress_group['anya'])
            assert anya['cells'][0] is True
            assert anya['unpaid_skip'][0] is False
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM lesson_skips WHERE group_id = %s', [gid])


class TestTransferredLessons:
    """transferred_lessons/transferred_from_group_name в ответе матрицы."""

    def test_transferred_student_gets_capped_count(self, manager_client, progress_group):
        """
        Боря переведён из архивной группы того же направления, где отработал
        5 уроков (direction.total_lessons=8 в фикстуре progress_group) —
        transferred_lessons должен быть 5 (меньше total_slots=8, не капается).
        """
        gid = progress_group['group_id']
        with connection.cursor() as cur:
            cur.execute(
                "SELECT direction_id, teacher_id FROM groups WHERE id = %s", [gid],
            )
            direction_id, teacher_id = cur.fetchone()
            cur.execute(
                "INSERT INTO groups (name,direction_id,teacher_id,is_individual,"
                "lesson_duration_minutes,active,lesson_number_offset) VALUES ('__pg_old_g__',%s,%s,false,60,false,0) "
                "RETURNING id",
                [direction_id, teacher_id],
            )
            old_group_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                "VALUES (%s,%s,5,false) RETURNING id",
                [old_group_id, progress_group['borya']],
            )
            old_membership_id = cur.fetchone()[0]
            cur.execute(
                "UPDATE group_memberships SET transferred_from_id = %s "
                "WHERE group_id = %s AND student_id = %s",
                [old_membership_id, gid, progress_group['borya']],
            )
        try:
            body = manager_client.get(_url(gid)).json()
            rows = {r['student_id']: r for r in body['students']}
            borya = rows[progress_group['borya']]
            assert borya['transferred_lessons'] == 5
            assert borya['transferred_from_group_name'] == '__pg_old_g__'

            anya = rows[progress_group['anya']]
            assert anya['transferred_lessons'] == 0
            assert anya['transferred_from_group_name'] is None
        finally:
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE group_memberships SET transferred_from_id = NULL "
                    "WHERE group_id = %s AND student_id = %s",
                    [gid, progress_group['borya']],
                )
                cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [old_group_id])
                cur.execute('DELETE FROM groups WHERE id = %s', [old_group_id])

    def test_transferred_count_capped_at_total_slots(self, manager_client, progress_group):
        """
        Отработано в старой группе (20) больше, чем всего слотов в этой матрице
        (total_slots=8) — transferred_lessons не может превышать total_slots.
        """
        gid = progress_group['group_id']
        with connection.cursor() as cur:
            cur.execute(
                "SELECT direction_id, teacher_id FROM groups WHERE id = %s", [gid],
            )
            direction_id, teacher_id = cur.fetchone()
            cur.execute(
                "INSERT INTO groups (name,direction_id,teacher_id,is_individual,"
                "lesson_duration_minutes,active,lesson_number_offset) VALUES ('__pg_old_g2__',%s,%s,false,60,false,0) "
                "RETURNING id",
                [direction_id, teacher_id],
            )
            old_group_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                "VALUES (%s,%s,20,false) RETURNING id",
                [old_group_id, progress_group['anya']],
            )
            old_membership_id = cur.fetchone()[0]
            cur.execute(
                "UPDATE group_memberships SET transferred_from_id = %s "
                "WHERE group_id = %s AND student_id = %s",
                [old_membership_id, gid, progress_group['anya']],
            )
        try:
            body = manager_client.get(_url(gid)).json()
            rows = {r['student_id']: r for r in body['students']}
            assert rows[progress_group['anya']]['transferred_lessons'] == 8
        finally:
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE group_memberships SET transferred_from_id = NULL "
                    "WHERE group_id = %s AND student_id = %s",
                    [gid, progress_group['anya']],
                )
                cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [old_group_id])
                cur.execute('DELETE FROM groups WHERE id = %s', [old_group_id])

    def test_half_lesson_floored(self, manager_client, progress_group):
        """lessons_done=4.5 в старой группе → transferred_lessons=4 (floor, не round)."""
        gid = progress_group['group_id']
        with connection.cursor() as cur:
            cur.execute(
                "SELECT direction_id, teacher_id FROM groups WHERE id = %s", [gid],
            )
            direction_id, teacher_id = cur.fetchone()
            cur.execute(
                "INSERT INTO groups (name,direction_id,teacher_id,is_individual,"
                "lesson_duration_minutes,active,lesson_number_offset) VALUES ('__pg_old_g3__',%s,%s,false,45,false,0) "
                "RETURNING id",
                [direction_id, teacher_id],
            )
            old_group_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                "VALUES (%s,%s,4.5,false) RETURNING id",
                [old_group_id, progress_group['borya']],
            )
            old_membership_id = cur.fetchone()[0]
            cur.execute(
                "UPDATE group_memberships SET transferred_from_id = %s "
                "WHERE group_id = %s AND student_id = %s",
                [old_membership_id, gid, progress_group['borya']],
            )
        try:
            body = manager_client.get(_url(gid)).json()
            rows = {r['student_id']: r for r in body['students']}
            assert rows[progress_group['borya']]['transferred_lessons'] == 4
        finally:
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE group_memberships SET transferred_from_id = NULL "
                    "WHERE group_id = %s AND student_id = %s",
                    [gid, progress_group['borya']],
                )
                cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [old_group_id])
                cur.execute('DELETE FROM groups WHERE id = %s', [old_group_id])

    def test_multi_hop_chain_sums_lessons(self, manager_client, progress_group):
        """
        Боря: А(3 урока, архивная) → Б(2 урока, архивная) → текущая группа
        (progress_group, total_slots=8). transferred_lessons = min(3+2, 8) = 5 —
        сумма по всей цепочке, без капа (5 < 8), источник — Б (не А).
        """
        gid = progress_group['group_id']
        with connection.cursor() as cur:
            cur.execute("SELECT direction_id, teacher_id FROM groups WHERE id = %s", [gid])
            direction_id, teacher_id = cur.fetchone()
            cur.execute(
                "INSERT INTO groups (name,direction_id,teacher_id,is_individual,"
                "lesson_duration_minutes,active,lesson_number_offset) VALUES ('__pg_chain_a__',%s,%s,false,60,false,0) "
                "RETURNING id",
                [direction_id, teacher_id],
            )
            group_a = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO groups (name,direction_id,teacher_id,is_individual,"
                "lesson_duration_minutes,active,lesson_number_offset) VALUES ('__pg_chain_b__',%s,%s,false,60,false,0) "
                "RETURNING id",
                [direction_id, teacher_id],
            )
            group_b = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                "VALUES (%s,%s,3,false) RETURNING id",
                [group_a, progress_group['borya']],
            )
            membership_a = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO group_memberships (group_id, student_id, lessons_done, active, transferred_from_id) "
                "VALUES (%s,%s,2,false,%s) RETURNING id",
                [group_b, progress_group['borya'], membership_a],
            )
            membership_b = cur.fetchone()[0]
            cur.execute(
                "UPDATE group_memberships SET transferred_from_id = %s "
                "WHERE group_id = %s AND student_id = %s",
                [membership_b, gid, progress_group['borya']],
            )
        try:
            body = manager_client.get(_url(gid)).json()
            rows = {r['student_id']: r for r in body['students']}
            assert rows[progress_group['borya']]['transferred_lessons'] == 5  # 3+2, не капается (< total_slots=8)
            assert rows[progress_group['borya']]['transferred_from_group_name'] == '__pg_chain_b__'
        finally:
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE group_memberships SET transferred_from_id = NULL "
                    "WHERE group_id = %s AND student_id = %s",
                    [gid, progress_group['borya']],
                )
                cur.execute('DELETE FROM group_memberships WHERE group_id IN (%s, %s)', [group_a, group_b])
                cur.execute('DELETE FROM groups WHERE id IN (%s, %s)', [group_a, group_b])

    def test_locked_through_exposes_raw_uncapped_value(self, manager_client, progress_group):
        """locked_through — сырое B (cumulative_transferred_lessons), НЕ капается
        total_slots=8 (в отличие от transferred_lessons, который капается для покраски)."""
        gid = progress_group['group_id']
        with connection.cursor() as cur:
            cur.execute("SELECT direction_id, teacher_id FROM groups WHERE id = %s", [gid])
            direction_id, teacher_id = cur.fetchone()
            cur.execute(
                "INSERT INTO groups (name,direction_id,teacher_id,is_individual,"
                "lesson_duration_minutes,active,lesson_number_offset) VALUES ('__pg_old_g2__',%s,%s,false,60,false,0) "
                "RETURNING id",
                [direction_id, teacher_id],
            )
            old_group_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                "VALUES (%s,%s,20,false) RETURNING id",
                [old_group_id, progress_group['borya']],
            )
            old_membership_id = cur.fetchone()[0]
            cur.execute(
                "UPDATE group_memberships SET transferred_from_id = %s "
                "WHERE group_id = %s AND student_id = %s",
                [old_membership_id, gid, progress_group['borya']],
            )
        try:
            body = manager_client.get(_url(gid)).json()
            rows = {r['student_id']: r for r in body['students']}
            borya = rows[progress_group['borya']]
            assert borya['transferred_lessons'] == 8       # капается total_slots=8
            assert float(borya['locked_through']) == 20.0  # сырое B — НЕ капается
        finally:
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE group_memberships SET transferred_from_id = NULL "
                    "WHERE group_id = %s AND student_id = %s",
                    [gid, progress_group['borya']],
                )
                cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [old_group_id])
                cur.execute('DELETE FROM groups WHERE id = %s', [old_group_id])


@pytest.fixture
def progress_group_45():
    """
    45-минутная группа (half-lesson, direction.total_lessons=8 → 16 слотов
    по step=0.5) + 1 ученик + 3 проведённых урока подряд: lesson_number
    0.5, 1.0, 1.5 — три РАЗНЫЕ половинки, не должны схлопываться в один слот.
    """
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name, active) VALUES ('__pg45_t__', true) RETURNING id")
        teacher_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO directions (name,total_lessons,active) "
            "VALUES ('__pg45_d__',8,true) RETURNING id"
        )
        direction_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name,direction_id,teacher_id,is_individual,lesson_duration_minutes,active,lesson_number_offset) "
            "VALUES ('__pg45_g__',%s,%s,false,45,true,0) RETURNING id",
            [direction_id, teacher_id],
        )
        group_id = cur.fetchone()[0]

        cur.execute("INSERT INTO students (full_name) VALUES ('__pg45 Аня__') RETURNING id")
        anya = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) VALUES (%s,%s,0,true)",
            [group_id, anya],
        )

        lesson_ids = []
        for num, date in ((0.5, '2026-03-02'), (1.0, '2026-03-04'), (1.5, '2026-03-06')):
            cur.execute(
                "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
                "lesson_duration_minutes, lesson_type, submitted_by_token) "
                "VALUES (%s,%s,%s,%s,45,'regular','test') RETURNING id",
                [group_id, teacher_id, date, num],
            )
            lesson_ids.append(cur.fetchone()[0])

        for lid in lesson_ids:
            cur.execute(
                "INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s,%s,true)",
                [lid, anya],
            )

    yield {'group_id': group_id, 'anya': anya, 'lesson_ids': lesson_ids}

    with connection.cursor() as cur:
        cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = ANY(%s)', [lesson_ids])
        cur.execute('DELETE FROM lessons WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM groups WHERE id = %s', [group_id])
        cur.execute('DELETE FROM students WHERE id = %s', [anya])
        cur.execute('DELETE FROM directions WHERE id = %s', [direction_id])
        cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])


class TestHalfLessonSlots:
    """45-минутные группы: раздел «Уроки»/матрица прогресса должны показывать
    2×total_lessons ячеек (half-lesson step=0.5), без коллапса половинок в один слот."""

    def test_total_slots_doubled_for_45min_group(self, manager_client, progress_group_45):
        body = manager_client.get(_url(progress_group_45['group_id'])).json()
        # direction.total_lessons=8, step=0.5 → 16 слотов, не 8.
        assert body['total_slots'] == 16
        assert len(body['slots']) == 16

    def test_each_half_lesson_gets_own_slot(self, manager_client, progress_group_45):
        """Три проведённых половинки (0.5, 1.0, 1.5) занимают слоты 1, 2, 3 —
        каждая своя, без схлопывания в один (старый баг: ceil(0.5)==ceil(1.0)==1)."""
        body = manager_client.get(_url(progress_group_45['group_id'])).json()
        assert body['held_slots'] == 3
        assert [s['held'] for s in body['slots'][:4]] == [True, True, True, False]
        # Даты слотов 1..3 — три разных проведённых урока, по порядку lesson_number.
        assert body['slots'][0]['date'] == '2026-03-02'
        assert body['slots'][1]['date'] == '2026-03-04'
        assert body['slots'][2]['date'] == '2026-03-06'

    def test_student_cells_reflect_three_distinct_lessons(self, manager_client, progress_group_45):
        body = manager_client.get(_url(progress_group_45['group_id'])).json()
        anya = next(r for r in body['students'] if r['student_id'] == progress_group_45['anya'])
        assert anya['held'] == 3
        assert anya['present'] == 3
        assert anya['cells'][:3] == [True, True, True]
        assert anya['cells'][3:] == [None] * 13
