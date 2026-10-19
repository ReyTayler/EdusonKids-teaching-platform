"""
test_teacher_spa_api.py — e2e тесты для teacher SPA эндпоинтов.

Фаза 4: аутентификация через JWT (access-cookie), не HMAC session-cookie.
_client(role, account_id) создаёт JWT-клиент для реального аккаунта из БД.

Покрытие:
  Auth:
    - нет cookie → 401
    - role=manager/admin → 403 (teacher-only)
    - role=teacher, account_id не существует → 401 (token_version mismatch)
    - role=teacher, account привязан → 200

  submitLesson, refreshData — без изменений по смыслу.
"""
from __future__ import annotations

import datetime

import pytest
from django.conf import settings
from django.db import connection
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

pytestmark = pytest.mark.django_db

_ACCESS_COOKIE = settings.SIMPLE_JWT.get('AUTH_COOKIE', 'access')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client(role: str | None, account_id: int | None = None) -> APIClient:
    """
    Создать APIClient.
    - role=None → анонимный клиент (401)
    - role + account_id из БД → JWT access-cookie для реального аккаунта
    - role + несуществующий account_id → JWT с несовпадающей token_version → 401
    """
    c = APIClient()
    if role is None:
        return c

    if account_id is not None:
        from apps.accounts.models import Account
        try:
            user = Account.objects.get(pk=account_id)
            refresh = RefreshToken.for_user(user)
            refresh['token_version'] = user.token_version
            c.cookies[_ACCESS_COOKIE] = str(refresh.access_token)
        except Account.DoesNotExist:
            # Несуществующий аккаунт: выдаём JWT с выдуманным user_id →
            # simplejwt не найдёт пользователя → AuthenticationFailed → 401
            from rest_framework_simplejwt.tokens import AccessToken
            token = AccessToken()
            token['user_id'] = account_id
            token['token_version'] = 0
            c.cookies[_ACCESS_COOKIE] = str(token)
    return c


def _cleanup_lesson(lesson_id: int) -> None:
    """Чистим урок и связанные записи (FK-безопасный порядок)."""
    with connection.cursor() as cur:
        cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])


def _get_lesson_id(group_id: int, token: str) -> int | None:
    """Найти последний урок по group_id и submitted_by_token."""
    with connection.cursor() as cur:
        cur.execute(
            'SELECT id FROM lessons WHERE group_id = %s AND submitted_by_token = %s '
            'ORDER BY id DESC LIMIT 1',
            [group_id, token],
        )
        row = cur.fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Auth — общие для всех POST и GET эндпоинтов
# ---------------------------------------------------------------------------

class TestAuthRequirements:

    def test_get_data_no_cookie_401(self):
        resp = _client(None).post('/api/getData', {}, format='json')
        assert resp.status_code == 401

    def test_get_data_manager_403(self, manager_client):
        resp = manager_client.post('/api/getData', {}, format='json')
        assert resp.status_code == 403

    def test_get_data_admin_403(self, admin_client):
        resp = admin_client.post('/api/getData', {}, format='json')
        assert resp.status_code == 403

    def test_submit_lesson_no_cookie_401(self):
        resp = _client(None).post('/api/submitLesson', {}, format='json')
        assert resp.status_code == 401

    def test_refresh_data_no_cookie_401(self):
        resp = _client(None).post('/api/refreshData', {}, format='json')
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Auth — teacher с несуществующим account_id
#
# После перехода на JWT: несуществующий user_id в токене →
# simplejwt не найдёт пользователя в БД → AuthenticationFailed → 401.
# ---------------------------------------------------------------------------

class TestUnlinkedTeacherAccount:

    def test_get_data_invalid_account_id_401(self):
        """
        account_id=999999 (не существует в БД) → JWT создаётся с выдуманным user_id
        → simplejwt не найдёт пользователя → 401 Unauthorized.
        """
        resp = _client('teacher', 999999).post('/api/getData', {}, format='json')
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# getData / getAllData — teacher linked
# ---------------------------------------------------------------------------

class TestGetData:

    def test_get_data_returns_teacher_and_data(
        self, teacher_fixture, account_fixture, group_fixture, student_fixture, membership_fixture
    ):
        """Привязанный teacher → {teacher, data}."""
        _, teacher_name = teacher_fixture
        resp = _client('teacher', account_fixture).post('/api/getData', {}, format='json')
        assert resp.status_code == 200
        body = resp.json()
        assert body['teacher'] == teacher_name
        assert isinstance(body['data'], dict)

    def test_get_all_data_returns_all(
        self, teacher_fixture, account_fixture, group_fixture, student_fixture, membership_fixture
    ):
        """getAllData → {teacher, data}; свои группы в срезе есть (граница видимости
        чужих — test_get_all_data_scope.py)."""
        _, teacher_name = teacher_fixture
        resp = _client('teacher', account_fixture).post('/api/getAllData', {}, format='json')
        assert resp.status_code == 200
        body = resp.json()
        assert body['teacher'] == teacher_name
        assert isinstance(body['data'], dict)
        assert teacher_name in body['data']

    def test_get_all_data_gives_substitute_skips_for_planned_number(
        self, teacher_fixture, account_fixture,
        sub_teacher_fixture, sub_account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """
        Заменяющий преподаватель открывает форму записи через getAllData (своей эта
        группа не является, но занятие №3 ему назначено). Маркеры «неоплачиваемый пропуск» должны приехать картой
        по номерам уроков — включая номер ИЗ ПЛАНА (№3), который здесь НЕ равен
        «следующему по прогрессу» (прогресс группы 0 → №1).

        Регрессия: срез отдавал один флаг skip, посчитанный по прогрессу, поэтому на
        таком расхождении заменщик не видел блокера вовсе — отмечал ученика «Пришёл»,
        а record_lesson молча ставил ему unpaid_skip и считал другую выплату.
        """
        _, owner_name = teacher_fixture
        sub_id, _ = sub_teacher_fixture
        group_name = '__spa_test_group__ пн 10:00'
        planned_id = _make_position(group_fixture, sub_id, 3, 3, '2026-06-10')
        with connection.cursor() as cur:
            cur.execute('UPDATE group_memberships SET lessons_done = 0 WHERE id = %s',
                        [membership_fixture])
            cur.execute('INSERT INTO lesson_skips (group_id, student_id, lesson_number, created_at) '
                        'VALUES (%s, %s, 3, now())', [group_fixture, student_fixture])
        try:
            resp = _client('teacher', sub_account_fixture).post('/api/getAllData', {}, format='json')
            assert resp.status_code == 200
            grp = resp.json()['data'][owner_name][group_name]
            assert grp['lessonsDone'] == 0
            assert grp['skips'] == {'3': ['__spa_test_student__']}
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM lesson_skips WHERE group_id = %s', [group_fixture])
                cur.execute('DELETE FROM planned_lessons WHERE id = %s', [planned_id])


# ---------------------------------------------------------------------------
# submitLesson
# ---------------------------------------------------------------------------

class TestSubmitLesson:

    def _submit(self, account_id: int, payload: dict):
        return _client('teacher', account_id).post('/api/submitLesson', payload, format='json')

    def test_group_not_found(self, teacher_fixture, account_fixture):
        """Несуществующая группа → 200 {success:false, error:'Группа не найдена'}."""
        _, teacher_name = teacher_fixture
        resp = self._submit(account_fixture, {
            'group': '__nonexistent_group__',
            'date': '2026-06-10',
            'students': [],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body['success'] is False
        assert 'Группа не найдена' in body['error']

    def test_future_date_rejected(self, teacher_fixture, account_fixture):
        """Дата урока в будущем (позже сегодняшней МСК) → 200 {success:false}, без побочных эффектов."""
        resp = self._submit(account_fixture, {
            'group': '__nonexistent_group__',
            'date': '2099-01-01',
            'students': [],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body['success'] is False
        assert 'наступил' in body['error']

    def test_valid_submit_creates_records(
        self,
        teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
        lessons_done,
    ):
        """Валидный запрос → lesson + attendance + payroll, lessonsDone инкрементирован."""
        _, teacher_name = teacher_fixture
        student_name = '__spa_test_student__'
        group_name = '__spa_test_group__ пн 10:00'
        token = f'acct:{account_fixture}'

        resp = self._submit(account_fixture, {
            'group': group_name,
            'date': '2026-06-10',
            'students': [{'name': student_name, 'present': True}],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body['success'] is True
        assert 'payment' in body
        assert 'penalty' in body
        assert 'lessonNumber' in body

        lesson_id = _get_lesson_id(group_fixture, token)
        assert lesson_id is not None

        try:
            with connection.cursor() as cur:
                cur.execute(
                    'SELECT present FROM lesson_attendance '
                    'WHERE lesson_id = %s AND student_id = %s',
                    [lesson_id, student_fixture],
                )
                row = cur.fetchone()
            assert row is not None
            assert row[0] is True

            with connection.cursor() as cur:
                cur.execute(
                    'SELECT payment FROM payroll WHERE lesson_id = %s', [lesson_id]
                )
                pay_row = cur.fetchone()
            assert pay_row is not None

            done = lessons_done(group_fixture, student_fixture)
            assert done == 1.0
        finally:
            _cleanup_lesson(lesson_id)
            with connection.cursor() as cur:
                cur.execute(
                    'UPDATE group_memberships SET lessons_done = 0 WHERE id = %s',
                    [membership_fixture],
                )

    def test_half_lesson_step(
        self,
        teacher_fixture, account_fixture,
        half_group_fixture, student_fixture, half_membership_fixture,
        lessons_done,
    ):
        """Группа с '45 минут' в названии → step=0.5, lessonNumber=0.5."""
        group_name = '__spa_half_group__ 45 минут вт 11:00'
        student_name = '__spa_test_student__'
        token = f'acct:{account_fixture}'

        resp = self._submit(account_fixture, {
            'group': group_name,
            'date': '2026-06-10',
            'students': [{'name': student_name, 'present': True}],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body['success'] is True
        assert body['lessonNumber'] == 0.5

        lesson_id = _get_lesson_id(half_group_fixture, token)
        assert lesson_id is not None
        try:
            done = lessons_done(half_group_fixture, student_fixture)
            assert done == 0.5
        finally:
            _cleanup_lesson(lesson_id)
            with connection.cursor() as cur:
                cur.execute(
                    'UPDATE group_memberships SET lessons_done = 0 WHERE id = %s',
                    [half_membership_fixture],
                )

    def test_half_lesson_determined_by_duration_not_name(
        self, teacher_fixture, account_fixture, student_fixture,
    ):
        """Ф4: half-lesson по lesson_duration_minutes==45, даже если в имени НЕТ '45 минут'."""
        teacher_id, _ = teacher_fixture
        group_name = '__f4_group__ пн 10:00'  # имя без '45 минут'
        lid = None
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO directions (name,total_lessons,active) "
                "VALUES ('__f4_dir__',8,true) RETURNING id"
            )
            did = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO groups (name,direction_id,teacher_id,is_individual,"
                "lesson_duration_minutes,active,lesson_number_offset) VALUES (%s,%s,%s,false,45,true,0) RETURNING id",
                [group_name, did, teacher_id],
            )
            gid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO group_memberships (group_id,student_id,lessons_done,active) "
                "VALUES (%s,%s,0,true) RETURNING id",
                [gid, student_fixture],
            )
            mid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count, "
                "unit_price, total_amount, paid_at, created_by) "
                "VALUES (%s, %s, 1, 4, 1000, 4000, '2026-06-01', 'test') RETURNING id",
                [student_fixture, did],
            )
            pid = cur.fetchone()[0]
        token = f'acct:{account_fixture}'
        try:
            resp = _client('teacher', account_fixture).post('/api/submitLesson', {
                'group': group_name, 'date': '2026-06-10',
                'students': [{'name': '__spa_test_student__', 'present': True}],
            }, format='json')
            assert resp.status_code == 200
            body = resp.json()
            assert body['success'] is True
            assert body['lessonNumber'] == 0.5  # half по duration=45, а не по имени
            lid = _get_lesson_id(gid, token)
        finally:
            if lid:
                _cleanup_lesson(lid)
            with connection.cursor() as cur:
                cur.execute('DELETE FROM group_memberships WHERE id = %s', [mid])
                cur.execute('DELETE FROM payments WHERE id = %s', [pid])
                cur.execute('DELETE FROM groups WHERE id = %s', [gid])
                cur.execute('DELETE FROM directions WHERE id = %s', [did])

    def test_all_absent_blocked(
        self,
        teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Урок без единого присутствующего (все «Не пришёл») → success:false,
        ничего не пишется. Блокер «пустого» урока (нет посещаемости — нет урока)."""
        group_name = '__spa_test_group__ пн 10:00'
        student_name = '__spa_test_student__'
        token = f'acct:{account_fixture}'

        resp = self._submit(account_fixture, {
            'group': group_name,
            'date': '2026-06-10',
            'students': [{'name': student_name, 'present': False}],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body['success'] is False
        assert 'присутств' in body['error']

        assert _get_lesson_id(group_fixture, token) is None

    def test_present_blocked_when_no_paid_balance(
        self,
        teacher_fixture, account_fixture,
        group_fixture, student_fixture,
    ):
        """
        Ученик без оплаченных уроков (remaining<=0, membership без payments) +
        present:true → success:false, урок/attendance/payroll не создаются.
        """
        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
                'VALUES (%s, %s, 0, true) RETURNING id',
                [group_fixture, student_fixture],
            )
            membership_id = cur.fetchone()[0]

        try:
            resp = self._submit(account_fixture, {
                'group': '__spa_test_group__ пн 10:00',
                'date': '2026-06-10',
                'students': [{'name': '__spa_test_student__', 'present': True}],
            })
            assert resp.status_code == 200
            body = resp.json()
            assert body['success'] is False
            assert '__spa_test_student__' in body['error']

            token = f'acct:{account_fixture}'
            assert _get_lesson_id(group_fixture, token) is None
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership_id])

    def test_empty_students_list_blocked(
        self,
        teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Пустой список students (нет ни одного присутствующего) → success:false,
        ничего не пишется — тот же блокер «пустого» урока, что и all-absent."""
        token = f'acct:{account_fixture}'
        resp = self._submit(account_fixture, {
            'group': '__spa_test_group__ пн 10:00',
            'date': '2026-06-10',
            'students': [],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body['success'] is False
        assert 'присутств' in body['error']
        assert _get_lesson_id(group_fixture, token) is None

    def test_payment_calculation_small_group(
        self,
        teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Малая группа (1 ученик, пришёл) → payment=500."""
        group_name = '__spa_test_group__ пн 10:00'
        student_name = '__spa_test_student__'
        token = f'acct:{account_fixture}'

        resp = self._submit(account_fixture, {
            'group': group_name,
            'date': '2026-06-10',
            'students': [{'name': student_name, 'present': True}],
        })
        body = resp.json()
        assert body['success'] is True
        assert body['payment'] == 500

        lesson_id = _get_lesson_id(group_fixture, token)
        if lesson_id:
            _cleanup_lesson(lesson_id)
        with connection.cursor() as cur:
            cur.execute(
                'UPDATE group_memberships SET lessons_done = 0 WHERE id = %s',
                [membership_fixture],
            )

    def test_substitution_derived_from_assigned_planned_lesson(
        self,
        teacher_fixture, account_fixture,
        sub_teacher_fixture, sub_account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """
        Замена выводится сервером: чужая группа + плановое занятие, назначенное
        заменщику админом («Сменить преподавателя»), → lesson_type='substitution',
        teacher_id=заменщик, original_teacher_id=владелец группы.
        """
        owner_id, _ = teacher_fixture
        sub_id, _ = sub_teacher_fixture
        group_name = '__spa_test_group__ пн 10:00'
        student_name = '__spa_test_student__'
        token = f'acct:{sub_account_fixture}'

        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
                'scheduled_time, teacher_id, status, created_at, updated_at) '
                "VALUES (%s, 1, 1, '2026-06-10', '10:00', %s, 'pending', NOW(), NOW()) RETURNING id",
                [group_fixture, sub_id],
            )
            planned_id = cur.fetchone()[0]

        try:
            resp = self._submit(sub_account_fixture, {
                'group': group_name,
                'date': '2026-06-10',
                'students': [{'name': student_name, 'present': True}],
            })
            assert resp.status_code == 200
            assert resp.json()['success'] is True

            lesson_id = _get_lesson_id(group_fixture, token)
            assert lesson_id is not None
            try:
                with connection.cursor() as cur:
                    cur.execute(
                        'SELECT lesson_type, teacher_id, original_teacher_id FROM lessons WHERE id = %s',
                        [lesson_id],
                    )
                    lt, tid, orig = cur.fetchone()
                assert lt == 'substitution'
                assert tid == sub_id
                assert orig == owner_id
            finally:
                _cleanup_lesson(lesson_id)
                with connection.cursor() as cur:
                    cur.execute(
                        'UPDATE group_memberships SET lessons_done = 0 WHERE id = %s',
                        [membership_fixture],
                    )
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM planned_lessons WHERE id = %s', [planned_id])

    def test_substitute_teacher_can_submit(
        self,
        teacher_fixture, account_fixture,
        sub_teacher_fixture, sub_account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """
        Разовая замена на дату (planned_lessons.substitute_teacher_id) — такое же
        основание отметить урок чужой группы, как и смена преподавателя контента.

        Регрессия 2026-07-30: has_assigned_planned_lesson смотрела только teacher_id,
        поэтому замещающий видел занятие в своём календаре (там скоуп учитывает
        substitute_teacher_id), но при сохранении получал 403, а фронт показывал
        «Сессия истекла. Обновите страницу и войдите заново».
        """
        owner_id, _ = teacher_fixture
        sub_id, _ = sub_teacher_fixture

        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
                'scheduled_time, teacher_id, substitute_teacher_id, status, created_at, '
                'updated_at) '
                "VALUES (%s, 1, 1, '2026-06-10', '10:00', %s, %s, 'pending', NOW(), NOW()) "
                'RETURNING id',
                [group_fixture, owner_id, sub_id],
            )
            planned_id = cur.fetchone()[0]

        try:
            resp = self._submit(sub_account_fixture, {
                'group': '__spa_test_group__ пн 10:00',
                'date': '2026-06-10',
                'students': [{'name': '__spa_test_student__', 'present': True}],
            })
            assert resp.status_code == 200, resp.json()

            lesson_id = _get_lesson_id(group_fixture, f'acct:{sub_account_fixture}')
            assert lesson_id is not None
            try:
                with connection.cursor() as cur:
                    cur.execute(
                        'SELECT lesson_type, teacher_id, original_teacher_id '
                        'FROM lessons WHERE id = %s', [lesson_id])
                    lt, tid, orig = cur.fetchone()
                assert lt == 'substitution'
                assert tid == sub_id
                assert orig == owner_id
            finally:
                _cleanup_lesson(lesson_id)
                with connection.cursor() as cur:
                    cur.execute(
                        'UPDATE group_memberships SET lessons_done = 0 WHERE id = %s',
                        [membership_fixture])
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM planned_lessons WHERE id = %s', [planned_id])

    def test_foreign_group_without_assignment_403(
        self,
        teacher_fixture, account_fixture,
        sub_teacher_fixture, sub_account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Чужая группа БЕЗ назначенного планового занятия → 403, урок не создан."""
        resp = self._submit(sub_account_fixture, {
            'group': '__spa_test_group__ пн 10:00',
            'date': '2026-06-10',
            'students': [{'name': '__spa_test_student__', 'present': True}],
        })
        assert resp.status_code == 403
        assert _get_lesson_id(group_fixture, f'acct:{sub_account_fixture}') is None

    def test_client_substitution_fields_rejected_400(
        self,
        teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Клиентские isSubstitution/originalTeacher/lessonType больше не принимаются → 400."""
        _, teacher_name = teacher_fixture
        base = {
            'group': '__spa_test_group__ пн 10:00',
            'date': '2026-06-10',
            'students': [{'name': '__spa_test_student__', 'present': True}],
        }
        assert self._submit(account_fixture, {**base, 'isSubstitution': True}).status_code == 400
        assert self._submit(account_fixture, {**base, 'isSubstitution': False}).status_code == 400
        assert self._submit(account_fixture, {**base, 'originalTeacher': teacher_name}).status_code == 400
        assert self._submit(account_fixture, {**base, 'lessonType': 'reschedule'}).status_code == 400
        assert self._submit(account_fixture, {**base, 'lessonType': 'regular'}).status_code == 400
        assert _get_lesson_id(group_fixture, f'acct:{account_fixture}') is None

    def test_reschedule_derived_from_moved_planned_lesson(
        self,
        teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """
        Плановая строка своей группы перенесена НА дату отметки (moved_from_date
        задан) → сервер пишет lesson_type='reschedule' без клиентского флага.
        """
        teacher_id, _ = teacher_fixture
        token = f'acct:{account_fixture}'

        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
                'scheduled_time, teacher_id, status, moved_from_date, created_at, updated_at) '
                "VALUES (%s, 1, 1, '2026-06-10', '10:00', %s, 'pending', '2026-06-08', NOW(), NOW()) "
                'RETURNING id',
                [group_fixture, teacher_id],
            )
            planned_id = cur.fetchone()[0]

        try:
            resp = self._submit(account_fixture, {
                'group': '__spa_test_group__ пн 10:00',
                'date': '2026-06-10',
                'students': [{'name': '__spa_test_student__', 'present': True}],
            })
            assert resp.status_code == 200
            assert resp.json()['success'] is True

            lesson_id = _get_lesson_id(group_fixture, token)
            assert lesson_id is not None
            try:
                with connection.cursor() as cur:
                    cur.execute('SELECT lesson_type FROM lessons WHERE id = %s', [lesson_id])
                    assert cur.fetchone()[0] == 'reschedule'
            finally:
                _cleanup_lesson(lesson_id)
                with connection.cursor() as cur:
                    cur.execute(
                        'UPDATE group_memberships SET lessons_done = 0 WHERE id = %s',
                        [membership_fixture],
                    )
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM planned_lessons WHERE id = %s', [planned_id])

    def test_submit_lesson_links_fact_to_planned_lesson(
        self,
        teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """
        submitLesson должен сам привязывать факт к плановой строке (fact_lesson_id +
        status='done'), иначе занятие остаётся «не проведено» в расписании/календаре,
        пока кто-то вручную не прогонит backfill_planned_lessons.
        """
        teacher_id, _ = teacher_fixture
        token = f'acct:{account_fixture}'

        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
                'scheduled_time, teacher_id, status, created_at, updated_at) '
                "VALUES (%s, 1, 1, '2026-06-10', '10:00', %s, 'pending', NOW(), NOW()) "
                'RETURNING id',
                [group_fixture, teacher_id],
            )
            planned_id = cur.fetchone()[0]

        try:
            resp = self._submit(account_fixture, {
                'group': '__spa_test_group__ пн 10:00',
                'date': '2026-06-10',
                'students': [{'name': '__spa_test_student__', 'present': True}],
            })
            assert resp.status_code == 200
            assert resp.json()['success'] is True

            lesson_id = _get_lesson_id(group_fixture, token)
            assert lesson_id is not None
            try:
                with connection.cursor() as cur:
                    cur.execute(
                        'SELECT fact_lesson_id, status FROM planned_lessons WHERE id = %s',
                        [planned_id],
                    )
                    fact_lesson_id, status = cur.fetchone()
                assert fact_lesson_id == lesson_id
                assert status == 'done'
            finally:
                _cleanup_lesson(lesson_id)
                with connection.cursor() as cur:
                    cur.execute(
                        'UPDATE group_memberships SET lessons_done = 0 WHERE id = %s',
                        [membership_fixture],
                    )
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM planned_lessons WHERE id = %s', [planned_id])

    def test_blocked_when_earlier_lesson_unfilled(
        self,
        teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Незакрытое занятие прошлой недели → отказ, урок не создаётся."""
        teacher_id, _ = teacher_fixture
        token = f'acct:{account_fixture}'
        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
                'scheduled_time, teacher_id, status, created_at, updated_at) '
                "VALUES (%s, 1, 1, '2026-06-03', '10:00', %s, 'pending', NOW(), NOW()), "
                "       (%s, 2, 2, '2026-06-10', '10:00', %s, 'pending', NOW(), NOW())",
                [group_fixture, teacher_id, group_fixture, teacher_id],
            )
        try:
            resp = self._submit(account_fixture, {
                'group': '__spa_test_group__ пн 10:00',
                'date': '2026-06-10',
                'students': [{'name': '__spa_test_student__', 'present': True}],
            })
            assert resp.status_code == 200
            body = resp.json()
            assert body['success'] is False
            assert body['error'] == (
                'Есть не отмеченные занятия. Обратитесь к менеджеру или '
                'администратору за правкой расписания.'
            )
            assert _get_lesson_id(group_fixture, token) is None
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM planned_lessons WHERE group_id = %s', [group_fixture])

    def test_not_blocked_when_earlier_lesson_cancelled(
        self,
        teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Отменённое занятие долгом не считается — урок записывается."""
        teacher_id, _ = teacher_fixture
        token = f'acct:{account_fixture}'
        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
                'scheduled_time, teacher_id, status, created_at, updated_at) '
                "VALUES (%s, 1, 1, '2026-06-03', '10:00', %s, 'cancelled', NOW(), NOW()), "
                "       (%s, 2, 2, '2026-06-10', '10:00', %s, 'pending', NOW(), NOW())",
                [group_fixture, teacher_id, group_fixture, teacher_id],
            )
        try:
            resp = self._submit(account_fixture, {
                'group': '__spa_test_group__ пн 10:00',
                'date': '2026-06-10',
                'students': [{'name': '__spa_test_student__', 'present': True}],
            })
            assert resp.json()['success'] is True
            lesson_id = _get_lesson_id(group_fixture, token)
            try:
                assert lesson_id is not None
            finally:
                if lesson_id is not None:
                    _cleanup_lesson(lesson_id)
                with connection.cursor() as cur:
                    cur.execute(
                        'UPDATE group_memberships SET lessons_done = 0 WHERE id = %s',
                        [membership_fixture],
                    )
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM planned_lessons WHERE group_id = %s', [group_fixture])

    def test_not_blocked_when_earlier_lesson_done(
        self,
        teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Проведённое занятие долгом не считается — урок записывается."""
        teacher_id, _ = teacher_fixture
        token = f'acct:{account_fixture}'
        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
                'scheduled_time, teacher_id, status, created_at, updated_at) '
                "VALUES (%s, 1, 1, '2026-06-03', '10:00', %s, 'done', NOW(), NOW()), "
                "       (%s, 2, 2, '2026-06-10', '10:00', %s, 'pending', NOW(), NOW())",
                [group_fixture, teacher_id, group_fixture, teacher_id],
            )
        try:
            resp = self._submit(account_fixture, {
                'group': '__spa_test_group__ пн 10:00',
                'date': '2026-06-10',
                'students': [{'name': '__spa_test_student__', 'present': True}],
            })
            assert resp.json()['success'] is True
            lesson_id = _get_lesson_id(group_fixture, token)
            try:
                assert lesson_id is not None
            finally:
                if lesson_id is not None:
                    _cleanup_lesson(lesson_id)
                with connection.cursor() as cur:
                    cur.execute(
                        'UPDATE group_memberships SET lessons_done = 0 WHERE id = %s',
                        [membership_fixture],
                    )
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM planned_lessons WHERE group_id = %s', [group_fixture])

    def test_not_blocked_by_non_course_row(
        self,
        teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Строка без seq (маркер/разовое занятие) блокером не является."""
        teacher_id, _ = teacher_fixture
        token = f'acct:{account_fixture}'
        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
                'scheduled_time, teacher_id, status, created_at, updated_at) '
                "VALUES (%s, NULL, NULL, '2026-06-03', '10:00', %s, 'pending', NOW(), NOW()), "
                "       (%s, 1, 1, '2026-06-10', '10:00', %s, 'pending', NOW(), NOW())",
                [group_fixture, teacher_id, group_fixture, teacher_id],
            )
        try:
            resp = self._submit(account_fixture, {
                'group': '__spa_test_group__ пн 10:00',
                'date': '2026-06-10',
                'students': [{'name': '__spa_test_student__', 'present': True}],
            })
            assert resp.json()['success'] is True
            lesson_id = _get_lesson_id(group_fixture, token)
            try:
                assert lesson_id is not None
            finally:
                if lesson_id is not None:
                    _cleanup_lesson(lesson_id)
                with connection.cursor() as cur:
                    cur.execute(
                        'UPDATE group_memberships SET lessons_done = 0 WHERE id = %s',
                        [membership_fixture],
                    )
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM planned_lessons WHERE group_id = %s', [group_fixture])

    def test_not_blocked_by_later_unfilled_lesson(
        self,
        teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Незакрытое занятие ПОЗЖЕ отмечаемой даты не мешает ретро-отметке."""
        teacher_id, _ = teacher_fixture
        token = f'acct:{account_fixture}'
        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
                'scheduled_time, teacher_id, status, created_at, updated_at) '
                "VALUES (%s, 1, 1, '2026-06-10', '10:00', %s, 'pending', NOW(), NOW()), "
                "       (%s, 2, 2, '2026-06-17', '10:00', %s, 'pending', NOW(), NOW())",
                [group_fixture, teacher_id, group_fixture, teacher_id],
            )
        try:
            resp = self._submit(account_fixture, {
                'group': '__spa_test_group__ пн 10:00',
                'date': '2026-06-10',
                'students': [{'name': '__spa_test_student__', 'present': True}],
            })
            assert resp.json()['success'] is True
            lesson_id = _get_lesson_id(group_fixture, token)
            try:
                assert lesson_id is not None
            finally:
                if lesson_id is not None:
                    _cleanup_lesson(lesson_id)
                with connection.cursor() as cur:
                    cur.execute(
                        'UPDATE group_memberships SET lessons_done = 0 WHERE id = %s',
                        [membership_fixture],
                    )
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM planned_lessons WHERE group_id = %s', [group_fixture])

    def test_transaction_rollback_on_payroll_failure(
        self, monkeypatch,
        teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
        lessons_done,
    ):
        """Ошибка в payroll → полный rollback."""
        from apps.lessons import repository as lessons_repository
        from apps.teacher_spa import services

        def _boom(*args, **kwargs):
            raise RuntimeError('payroll insert failed')

        # submit_lesson теперь делегирует запись в apps.lessons.services.record_lesson,
        # которое зовёт apps.lessons.repository.insert_payroll (единое ядро — см.
        # docs/superpowers/specs/2026-07-14-unify-lesson-recording-design.md).
        monkeypatch.setattr(lessons_repository, 'insert_payroll', _boom)

        with pytest.raises(RuntimeError):
            services.submit_lesson(account_fixture, {
                'group': '__spa_test_group__ пн 10:00',
                'date': '2026-06-10',
                'students': [{'name': '__spa_test_student__', 'present': True}],
            })

        token = f'acct:{account_fixture}'
        assert _get_lesson_id(group_fixture, token) is None
        with connection.cursor() as cur:
            cur.execute(
                'SELECT COUNT(*) FROM lesson_attendance la JOIN lessons l ON l.id=la.lesson_id '
                'WHERE l.group_id = %s', [group_fixture],
            )
            assert cur.fetchone()[0] == 0
        assert lessons_done(group_fixture, student_fixture) == 0.0

    def test_invalid_body_400(self, teacher_fixture, account_fixture):
        """Тело без обязательных полей → 400."""
        resp = self._submit(account_fixture, {'group': 'test'})
        assert resp.status_code == 400

    def test_substitute_teacher_is_blocked_too(
        self,
        teacher_fixture, account_fixture,
        sub_teacher_fixture, sub_account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Подменяющий преподаватель блокируется так же: долг группы важнее того,
        кто именно отмечает. Закрыть чужой долг он не вправе — текст отправляет
        его к менеджеру.

        Схема — как в test_substitution_derived_from_assigned_planned_lesson:
        group_fixture принадлежит teacher_fixture (владелец), sub_account_fixture —
        заменщик с плановым занятием, назначенным на дату отметки (иначе 403
        «занятие не назначено» вместо блокера). Плюс незакрытое занятие ГРУППЫ на
        более раннюю дату (назначено владельцу) — has_unfilled_before считает по
        group_id, не по teacher_id, поэтому чужой долг блокирует и заменщика.
        """
        teacher_id, _ = teacher_fixture
        sub_id, _ = sub_teacher_fixture
        token = f'acct:{sub_account_fixture}'
        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
                'scheduled_time, teacher_id, status, created_at, updated_at) '
                "VALUES (%s, 1, 1, '2026-06-03', '10:00', %s, 'pending', NOW(), NOW()), "
                "       (%s, 2, 2, '2026-06-10', '10:00', %s, 'pending', NOW(), NOW())",
                [group_fixture, teacher_id, group_fixture, sub_id],
            )
        try:
            resp = self._submit(sub_account_fixture, {
                'group': '__spa_test_group__ пн 10:00',
                'date': '2026-06-10',
                'students': [{'name': '__spa_test_student__', 'present': True}],
            })
            assert resp.status_code == 200
            body = resp.json()
            assert body['success'] is False
            assert body['error'] == (
                'Есть не отмеченные занятия. Обратитесь к менеджеру или '
                'администратору за правкой расписания.'
            )
            assert _get_lesson_id(group_fixture, token) is None
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM planned_lessons WHERE group_id = %s', [group_fixture])


# ---------------------------------------------------------------------------
# submitLesson — номер урока из позиции курса + защита от дублей
# ---------------------------------------------------------------------------

def _make_position(group_id, teacher_id, seq, number, date, time='10:00'):
    """Плановая позиция курса. Возвращает id."""
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
            'scheduled_time, teacher_id, status, created_at, updated_at) '
            "VALUES (%s, %s, %s, %s, %s, %s, 'pending', NOW(), NOW()) RETURNING id",
            [group_id, seq, number, date, time, teacher_id],
        )
        return cur.fetchone()[0]


def _position_row(planned_id):
    with connection.cursor() as cur:
        cur.execute(
            'SELECT fact_lesson_id, status FROM planned_lessons WHERE id = %s',
            [planned_id],
        )
        return cur.fetchone()


def _lesson_number(lesson_id):
    with connection.cursor() as cur:
        cur.execute('SELECT lesson_number FROM lessons WHERE id = %s', [lesson_id])
        return float(cur.fetchone()[0])


def _count_lessons(group_id, token):
    with connection.cursor() as cur:
        cur.execute(
            'SELECT count(*) FROM lessons WHERE group_id = %s AND submitted_by_token = %s',
            [group_id, token],
        )
        return cur.fetchone()[0]


class TestLessonNumberFromPlan:
    """
    Номер урока берётся из ПОЗИЦИИ КУРСА (planned_lessons), а не из прогресса
    учеников, и позиция захватывается при записи.

    Зачем (инцидент ПГ215, 31.07.2026): раньше номер считался как
    max(lessons_done)+step — из состояния, которое сама запись инкрементирует.
    Поэтому (а) номер уезжал, стоило прогрессу разойтись с планом, и (б)
    повторная отправка получала НОВЫЙ номер, проходила мимо lessons_natural_key
    и создавала ещё один урок. Три отправки подряд дали три урока.
    """

    GROUP = '__spa_test_group__ пн 10:00'
    STUDENT = '__spa_test_student__'

    def _submit(self, account_id: int, payload: dict):
        return _client('teacher', account_id).post('/api/submitLesson', payload, format='json')

    def _payload(self, date='2026-06-10', **extra):
        return {
            'group': self.GROUP,
            'date': date,
            'students': [{'name': self.STUDENT, 'present': True}],
            **extra,
        }

    def _reset(self, membership_id, group_id):
        with connection.cursor() as cur:
            cur.execute('DELETE FROM planned_lessons WHERE group_id = %s', [group_id])
            cur.execute(
                'UPDATE group_memberships SET lessons_done = 0 WHERE id = %s',
                [membership_id],
            )

    def test_number_comes_from_position_not_progress(
        self, teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Позиция №5 при lessons_done=0 → урок получает номер 5, а не 1.

        Прежний расчёт дал бы 1 (0 + step). Это и есть суть перехода: позиция
        курса — источник правды по номеру.
        """
        teacher_id, _ = teacher_fixture
        token = f'acct:{account_fixture}'
        planned_id = _make_position(group_fixture, teacher_id, 5, 5, '2026-06-10')
        lesson_id = None
        try:
            resp = self._submit(account_fixture, self._payload(plannedLessonId=planned_id))
            assert resp.status_code == 200
            assert resp.json()['success'] is True
            assert resp.json()['lessonNumber'] == 5

            lesson_id = _get_lesson_id(group_fixture, token)
            assert _lesson_number(lesson_id) == 5.0
            # Факт закреплён именно за названной позицией.
            assert _position_row(planned_id) == (lesson_id, 'done')
        finally:
            if lesson_id:
                _cleanup_lesson(lesson_id)
            self._reset(membership_fixture, group_fixture)

    def test_position_resolved_by_date_without_explicit_id(
        self, teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Без plannedLessonId (вход «Мои уроки») позиция ищется по дате.

        Именно этот путь закрывает инцидент ПГ215: занятие календаря там не
        передаётся, но позиция на дату существовала.
        """
        teacher_id, _ = teacher_fixture
        token = f'acct:{account_fixture}'
        planned_id = _make_position(group_fixture, teacher_id, 7, 7, '2026-06-10')
        lesson_id = None
        try:
            resp = self._submit(account_fixture, self._payload())
            assert resp.status_code == 200
            assert resp.json()['lessonNumber'] == 7

            lesson_id = _get_lesson_id(group_fixture, token)
            assert _position_row(planned_id) == (lesson_id, 'done')
        finally:
            if lesson_id:
                _cleanup_lesson(lesson_id)
            self._reset(membership_fixture, group_fixture)

    def test_duplicate_submit_rejected_with_409(
        self, teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Повторная отправка того же занятия → 409, второго урока НЕТ.

        Регрессия на ПГ215: три отправки подряд создавали три урока.
        """
        teacher_id, _ = teacher_fixture
        token = f'acct:{account_fixture}'
        planned_id = _make_position(group_fixture, teacher_id, 1, 1, '2026-06-10')
        lesson_id = None
        try:
            first = self._submit(account_fixture, self._payload(plannedLessonId=planned_id))
            assert first.status_code == 200
            assert first.json()['success'] is True
            lesson_id = _get_lesson_id(group_fixture, token)

            second = self._submit(account_fixture, self._payload(plannedLessonId=planned_id))
            assert second.status_code == 409
            body = second.json()
            assert body['code'] == 'lesson_already_recorded'
            assert 'уже записан' in body['error']

            # Главное: второго урока не появилось, счётчик не двинулся дважды.
            assert _count_lessons(group_fixture, token) == 1
        finally:
            if lesson_id:
                _cleanup_lesson(lesson_id)
            self._reset(membership_fixture, group_fixture)

    def test_duplicate_rejected_also_without_explicit_id(
        self, teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Повтор ловится и на резолве по дате — путь «Моих уроков».

        Занятая позиция обязана давать 409, а НЕ уходить на фолбэк-расчёт:
        иначе повтор снова создал бы урок со следующим номером.
        """
        teacher_id, _ = teacher_fixture
        token = f'acct:{account_fixture}'
        _make_position(group_fixture, teacher_id, 1, 1, '2026-06-10')
        lesson_id = None
        try:
            assert self._submit(account_fixture, self._payload()).status_code == 200
            lesson_id = _get_lesson_id(group_fixture, token)

            second = self._submit(account_fixture, self._payload())
            assert second.status_code == 409
            assert _count_lessons(group_fixture, token) == 1
        finally:
            if lesson_id:
                _cleanup_lesson(lesson_id)
            self._reset(membership_fixture, group_fixture)

    def test_rescheduled_position_keeps_its_number_at_new_date(
        self, teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Перенос: позиция уезжает на новую дату ВМЕСТЕ с номером.

        Проверяем настоящим reschedule_lesson (не сымитированным состоянием) —
        если семантика переноса когда-нибудь изменится и seq/lesson_number
        перестанут сохраняться, тест упадёт здесь, а не в проде.
        """
        from apps.scheduling import repository as scheduling_repository

        teacher_id, _ = teacher_fixture
        token = f'acct:{account_fixture}'
        planned_id = _make_position(group_fixture, teacher_id, 3, 3, '2026-06-03')
        lesson_id = None
        try:
            scheduling_repository.reschedule_lesson(
                group_fixture, planned_id,
                new_date=datetime.date(2026, 6, 10), new_time=None, new_teacher_id=None,
            )
            resp = self._submit(account_fixture, self._payload(date='2026-06-10'))
            assert resp.status_code == 200
            # Номер — исходный (3), несмотря на другую дату и lessons_done=0.
            assert resp.json()['lessonNumber'] == 3

            lesson_id = _get_lesson_id(group_fixture, token)
            assert _position_row(planned_id) == (lesson_id, 'done')
        finally:
            if lesson_id:
                _cleanup_lesson(lesson_id)
            self._reset(membership_fixture, group_fixture)

    def test_multislot_same_day_disambiguated_by_id(
        self, teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Два занятия группы в один день: явный id выбирает нужную позицию.

        По дате они неразличимы — ради этого случая фронт и присылает id.
        """
        teacher_id, _ = teacher_fixture
        token = f'acct:{account_fixture}'
        first_pos = _make_position(group_fixture, teacher_id, 1, 1, '2026-06-10', '10:00')
        second_pos = _make_position(group_fixture, teacher_id, 2, 2, '2026-06-10', '18:00')
        lesson_id = None
        try:
            resp = self._submit(account_fixture, self._payload(plannedLessonId=second_pos))
            assert resp.status_code == 200
            assert resp.json()['lessonNumber'] == 2

            lesson_id = _get_lesson_id(group_fixture, token)
            assert _position_row(second_pos) == (lesson_id, 'done')
            # Первое занятие дня осталось нетронутым.
            assert _position_row(first_pos) == (None, 'pending')
        finally:
            if lesson_id:
                _cleanup_lesson(lesson_id)
            self._reset(membership_fixture, group_fixture)

    def test_foreign_position_id_does_not_grant_access(
        self, teacher_fixture, account_fixture,
        group_fixture, half_group_fixture, student_fixture, membership_fixture,
    ):
        """Позиция ЧУЖОЙ группы по id не захватывается.

        Иначе преподаватель, подставив чужой plannedLessonId, записал бы урок на
        позицию другой группы. Ожидаем: чужая позиция не тронута, запись идёт по
        своей группе (позиции на дату нет → прежний расчёт номера).
        """
        teacher_id, _ = teacher_fixture
        token = f'acct:{account_fixture}'
        foreign_pos = _make_position(half_group_fixture, teacher_id, 4, 4, '2026-06-10')
        lesson_id = None
        try:
            resp = self._submit(account_fixture, self._payload(plannedLessonId=foreign_pos))
            assert resp.status_code == 200
            assert resp.json()['success'] is True
            # Номер посчитан фолбэком по прогрессу (1), а не взят из чужой позиции (4).
            assert resp.json()['lessonNumber'] == 1
            assert _position_row(foreign_pos) == (None, 'pending')

            lesson_id = _get_lesson_id(group_fixture, token)
        finally:
            if lesson_id:
                _cleanup_lesson(lesson_id)
            with connection.cursor() as cur:
                cur.execute(
                    'DELETE FROM planned_lessons WHERE group_id IN (%s, %s)',
                    [group_fixture, half_group_fixture],
                )
                cur.execute(
                    'UPDATE group_memberships SET lessons_done = 0 WHERE id = %s',
                    [membership_fixture],
                )

    def test_half_lesson_number_from_position(
        self, teacher_fixture, account_fixture,
        half_group_fixture, student_fixture, half_membership_fixture,
    ):
        """Half-lesson: дробный номер позиции (2.5) переносится в урок как есть."""
        teacher_id, _ = teacher_fixture
        token = f'acct:{account_fixture}'
        planned_id = _make_position(half_group_fixture, teacher_id, 5, 2.5, '2026-06-10', '11:00')
        lesson_id = None
        try:
            resp = self._submit(account_fixture, {
                'group': '__spa_half_group__ 45 минут вт 11:00',
                'date': '2026-06-10',
                'students': [{'name': self.STUDENT, 'present': True}],
                'plannedLessonId': planned_id,
            })
            assert resp.status_code == 200
            assert resp.json()['lessonNumber'] == 2.5

            lesson_id = _get_lesson_id(half_group_fixture, token)
            assert _lesson_number(lesson_id) == 2.5
        finally:
            if lesson_id:
                _cleanup_lesson(lesson_id)
            self._reset(half_membership_fixture, half_group_fixture)

    def test_group_without_plan_falls_back_to_progress(
        self, teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Группа без плана (8 таких на проде) — прежнее поведение сохраняется."""
        token = f'acct:{account_fixture}'
        lesson_id = None
        try:
            resp = self._submit(account_fixture, self._payload())
            assert resp.status_code == 200
            assert resp.json()['success'] is True
            assert resp.json()['lessonNumber'] == 1

            lesson_id = _get_lesson_id(group_fixture, token)
        finally:
            if lesson_id:
                _cleanup_lesson(lesson_id)
            self._reset(membership_fixture, group_fixture)


# ---------------------------------------------------------------------------
# Удалённые legacy-эндпоинты
# ---------------------------------------------------------------------------

class TestRemovedLegacyEndpoints:
    """
    GET /api/report и /api/schedule (+ их /refresh-редиректы) удалены 2026-09-30.

    Порты routes/teacher.js, у которых не осталось потребителей: календарь давно на
    /api/calendar. При этом оба отдавали любому преподавателю группы ВСЕЙ школы —
    /api/schedule ещё и с датой рождения и остатком оплаченных уроков каждого ученика
    (аудит ПДн 2026-09-30). Тест держит их удалёнными: вернуть такой маршрут
    «для совместимости» = вернуть утечку.
    """

    @pytest.mark.parametrize('url', [
        '/api/report', '/api/report/refresh', '/api/schedule', '/api/schedule/refresh',
    ])
    def test_endpoint_is_gone(self, teacher_fixture, account_fixture, url):
        assert _client('teacher', account_fixture).get(url).status_code == 404


# ---------------------------------------------------------------------------
# refreshData
# ---------------------------------------------------------------------------

class TestRefreshData:

    def test_refresh_data_success(self, teacher_fixture, account_fixture):
        """POST /api/refreshData → {success:true}."""
        resp = _client('teacher', account_fixture).post(
            '/api/refreshData', {}, format='json'
        )
        assert resp.status_code == 200
        assert resp.json() == {'success': True}


# ---------------------------------------------------------------------------
# GET /api/lessons — история «Мои уроки»
# ---------------------------------------------------------------------------

class TestMyLessons:

    def test_no_cookie_401(self):
        assert _client(None).get('/api/lessons').status_code == 401

    def test_manager_403(self, manager_client):
        assert manager_client.get('/api/lessons').status_code == 403

    def test_envelope_shape(self, teacher_fixture, account_fixture):
        """GET /api/lessons → {rows, total, page, page_size}."""
        resp = _client('teacher', account_fixture).get('/api/lessons')
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body['rows'], list)
        assert 'total' in body and 'page' in body and 'page_size' in body

    def test_returns_own_submitted_lesson(
        self,
        teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """После submitLesson урок появляется в /api/lessons со скоупом по учителю."""
        group_name = '__spa_test_group__ пн 10:00'
        token = f'acct:{account_fixture}'

        submit = _client('teacher', account_fixture).post('/api/submitLesson', {
            'group': group_name, 'date': '2026-06-10',
            'students': [{'name': '__spa_test_student__', 'present': True}],
        }, format='json')
        assert submit.status_code == 200 and submit.json()['success'] is True

        lesson_id = _get_lesson_id(group_fixture, token)
        try:
            resp = _client('teacher', account_fixture).get('/api/lessons')
            assert resp.status_code == 200
            rows = resp.json()['rows']
            mine = [r for r in rows if r['group'] == group_name and r['date'] == '2026-06-10']
            assert len(mine) >= 1
            row = mine[0]
            assert row['presentCount'] == 1
            assert row['totalCount'] == 1
            assert row['payment'] is not None
            assert row['isSubstitution'] is False
            # Точный источник предмета: ключи присутствуют (значение может быть None,
            # если у тестовой группы нет направления/цвета).
            assert 'direction' in row
            assert 'directionColor' in row
        finally:
            _cleanup_lesson(lesson_id)
            with connection.cursor() as cur:
                cur.execute(
                    'UPDATE group_memberships SET lessons_done = 0 WHERE id = %s',
                    [membership_fixture],
                )


# ---------------------------------------------------------------------------
# GET /api/group-directions — карта групп→направление (точный источник предмета)
# ---------------------------------------------------------------------------

class TestGroupDirections:

    def test_no_cookie_401(self):
        assert _client(None).get('/api/group-directions').status_code == 401

    def test_manager_403(self, manager_client):
        assert manager_client.get('/api/group-directions').status_code == 403

    def test_returns_groups_map(
        self, teacher_fixture, account_fixture, group_fixture, student_fixture, membership_fixture
    ):
        """teacher → {groups: {<name>: {direction, color, isIndividual}}}."""
        resp = _client('teacher', account_fixture).get('/api/group-directions')
        assert resp.status_code == 200
        groups = resp.json()['groups']
        assert isinstance(groups, dict)
        entry = groups.get('__spa_test_group__ пн 10:00')
        assert entry is not None
        assert 'direction' in entry and 'color' in entry and 'isIndividual' in entry
        # Ф4: продолжительность + длина курса для half-lesson/лимита без regex
        assert entry['lessonDurationMinutes'] == 60
        assert entry['totalLessons'] == 8

    def test_group_own_lessons_total_overrides_direction(
        self, teacher_fixture, account_fixture, group_fixture, student_fixture, membership_fixture
    ):
        """
        groups.lessons_total задан (группа-остаток курса) → API отдаёт длину
        ИМЕННО ГРУППЫ, а не длину курса направления (иначе преподаватель
        группы-остатка увидит лимит «X / 8» вместо «X / 2»).
        """
        with connection.cursor() as cur:
            cur.execute('UPDATE groups SET lessons_total = 2 WHERE id = %s', [group_fixture])

        resp = _client('teacher', account_fixture).get('/api/group-directions')
        assert resp.status_code == 200
        entry = resp.json()['groups'].get('__spa_test_group__ пн 10:00')
        assert entry is not None
        assert entry['totalLessons'] == 2

        # Регресс покрыт test_returns_groups_map выше: без lessons_total (NULL)
        # та же группа отдаёт длину направления (8).


# ---------------------------------------------------------------------------
# GET /api/group-progress — матрица посещаемости группы (страница группы)
# ---------------------------------------------------------------------------

_GROUP_NAME = '__spa_test_group__ пн 10:00'


class TestGroupProgress:

    @staticmethod
    def _url(name: str = _GROUP_NAME) -> str:
        from urllib.parse import quote
        return f'/api/group-progress?group={quote(name)}'

    def test_no_cookie_401(self):
        assert _client(None).get(self._url()).status_code == 401

    def test_manager_403(self, manager_client):
        assert manager_client.get(self._url()).status_code == 403

    def test_missing_param_400(self, teacher_fixture, account_fixture):
        assert _client('teacher', account_fixture).get('/api/group-progress').status_code == 400

    def test_unknown_group_404(self, teacher_fixture, account_fixture):
        assert _client('teacher', account_fixture).get(
            self._url('__nonexistent_group__'),
        ).status_code == 404

    def test_owner_200_contract(
        self, teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Владелец группы → 200, контракт admin-прогресса (students/slots/…)."""
        resp = _client('teacher', account_fixture).get(self._url())
        assert resp.status_code == 200
        body = resp.json()
        assert 'students' in body and 'slots' in body
        assert 'total_slots' in body and 'held_slots' in body
        names = [s['name'] for s in body['students']]
        assert '__spa_test_student__' in names

    def test_unpaid_skip_on_not_held_slot_is_blue(
        self, teacher_fixture, account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Пометка «неоплачиваемый пропуск» на ЕЩЁ НЕ ПРОВЕДЁННОМ слоте видна и
        здесь: teacher SPA рисует ту же матрицу тем же GroupProgressView, а
        сервер отдаёт её из общей apps.groups.services.get_group_progress.

        Регрессия та же, что в apps/groups/tests/test_progress_api.py: раньше
        матрица читала только lesson_attendance.unpaid_skip, поэтому пропуск на
        непроведённом уроке не отображался и «дорисовывался» по мере занятий.
        """
        with connection.cursor() as cur:
            for num in (1, 2, 3):
                cur.execute(
                    'INSERT INTO lesson_skips (group_id, student_id, lesson_number, created_at) '
                    'VALUES (%s,%s,%s,NOW())', [group_fixture, student_fixture, num])
        try:
            resp = _client('teacher', account_fixture).get(self._url())
            assert resp.status_code == 200
            row = next(s for s in resp.json()['students']
                       if s['student_id'] == student_fixture)
            # Уроков в группе нет вовсе — все три слота плановые, но помечены.
            assert row['unpaid_skip'][:3] == [True, True, True]
            assert row['cells'][:3] == [False, False, False]
            # Неоплачиваемый пропуск — не обязательство посещения.
            assert row['held'] == 0 and row['present'] == 0
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM lesson_skips WHERE group_id = %s', [group_fixture])

    def test_foreign_teacher_403(
        self, teacher_fixture, account_fixture,
        sub_teacher_fixture, sub_account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Чужая группа без назначенных занятий → 403."""
        assert _client('teacher', sub_account_fixture).get(self._url()).status_code == 403

    def test_assigned_substitute_200(
        self, teacher_fixture, account_fixture,
        sub_teacher_fixture, sub_account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """Заменщик с назначенным плановым занятием группы → 200."""
        sub_id, _ = sub_teacher_fixture
        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
                'scheduled_time, teacher_id, status, created_at, updated_at) '
                "VALUES (%s, 1, 1, '2026-06-10', '10:00', %s, 'pending', NOW(), NOW()) RETURNING id",
                [group_fixture, sub_id],
            )
            planned_id = cur.fetchone()[0]
        try:
            assert _client('teacher', sub_account_fixture).get(self._url()).status_code == 200
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM planned_lessons WHERE id = %s', [planned_id])

    def test_one_off_substitute_200(
        self, teacher_fixture, account_fixture,
        sub_teacher_fixture, sub_account_fixture,
        group_fixture, student_fixture, membership_fixture,
    ):
        """
        Разовая замена на дату (substitute_teacher_id, преподаватель занятия прежний)
        тоже открывает страницу группы. Раньше проверка смотрела только teacher_id:
        заменяющий видел занятие в календаре и мог записать урок, а «Карточка группы»
        из того же меню отдавала 403.
        """
        owner_id, _ = teacher_fixture
        sub_id, _ = sub_teacher_fixture
        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
                'scheduled_time, teacher_id, substitute_teacher_id, status, created_at, updated_at) '
                "VALUES (%s, 1, 1, '2026-06-10', '10:00', %s, %s, 'pending', NOW(), NOW()) "
                'RETURNING id',
                [group_fixture, owner_id, sub_id],
            )
            planned_id = cur.fetchone()[0]
        try:
            assert _client('teacher', sub_account_fixture).get(self._url()).status_code == 200
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM planned_lessons WHERE id = %s', [planned_id])
