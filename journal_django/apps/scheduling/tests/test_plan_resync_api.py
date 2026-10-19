"""
API «починки плана группы»:
  GET  /api/admin/groups/<pk>/plan/health   — проверки здоровья + предпросмотр
  POST /api/admin/groups/<pk>/plan/resync   — применить починку

Покрытие: RBAC (только superadmin), read-only характер health, границы отказа
(проверки слоя 3, факты без позиции своего номера, разошедшийся expected),
идемпотентность, перестановка двух фактов местами (unbind→rebind, без 23505),
гашение moved_from_date, половинный шаг (45 мин) и след в журнале изменений.

managed-схема eduson_test; чистим прямым DELETE в FK-безопасном порядке.
"""
from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

pytestmark = pytest.mark.django_db

_TOKEN = '__resync_api__'


# --- фикстуры и хелперы ----------------------------------------------------

@pytest.fixture
def resync_env(db):
    """Преподаватель + направление (курс 8 уроков) + фабрика групп.

    План и факты каждый тест раскладывает сам: сценарии здесь — про РАССОГЛАСОВАНИЕ,
    а его генератор плана по построению не создаёт.
    """
    groups: list[int] = []
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name, active) VALUES ('__resync_t__', true) RETURNING id")
        teacher_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO directions (name,total_lessons,color,active) "
            "VALUES ('__resync_dir__',8,'#4F59F9',true) RETURNING id")
        direction_id = cur.fetchone()[0]

    def make_group(duration: int = 60) -> int:
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO groups (name,direction_id,teacher_id,is_individual,"
                "lesson_duration_minutes,group_start_date,active,lesson_number_offset) "
                "VALUES ('__resync_g__',%s,%s,false,%s,'2026-06-01',true,0) RETURNING id",
                [direction_id, teacher_id, duration])
            gid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO group_schedule_slots (group_id,day_of_week,start_time,effective_from) "
                "VALUES (%s,1,'10:00','2026-06-01')", [gid])
        groups.append(gid)
        return gid

    yield {'teacher_id': teacher_id, 'direction_id': direction_id, 'make_group': make_group}

    with connection.cursor() as cur:
        for gid in groups:
            cur.execute('DELETE FROM planned_lessons WHERE group_id=%s', [gid])
            cur.execute('DELETE FROM lessons WHERE group_id=%s', [gid])
            cur.execute('DELETE FROM group_schedule_slots WHERE group_id=%s', [gid])
            cur.execute('DELETE FROM groups WHERE id=%s', [gid])
        cur.execute('DELETE FROM directions WHERE id=%s', [direction_id])
        cur.execute('DELETE FROM teachers WHERE id=%s', [teacher_id])


def _lesson(group_id: int, teacher_id: int, date: str, number, duration: int = 60) -> int:
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, '
            'lesson_duration_minutes, lesson_type, submitted_at, submitted_by_token) '
            "VALUES (%s,%s,%s,%s,%s,'regular',NOW(),%s) RETURNING id",
            [group_id, teacher_id, date, number, duration, _TOKEN])
        return cur.fetchone()[0]


def _position(group_id: int, teacher_id: int, seq: int, number, date: str,
              fact_id=None, status=None, moved_from=None) -> int:
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
            'scheduled_time, teacher_id, status, fact_lesson_id, moved_from_date, '
            'created_at, updated_at) '
            "VALUES (%s,%s,%s,%s,'10:00',%s,%s,%s,%s,NOW(),NOW()) RETURNING id",
            [group_id, seq, number, date, teacher_id,
             status or ('done' if fact_id else 'pending'), fact_id, moved_from])
        return cur.fetchone()[0]


def _row(position_id: int) -> dict:
    with connection.cursor() as cur:
        cur.execute(
            'SELECT fact_lesson_id, status, scheduled_date, moved_from_date '
            'FROM planned_lessons WHERE id=%s', [position_id])
        fact_id, status, date, moved_from = cur.fetchone()
    return {'fact_lesson_id': fact_id, 'status': status,
            'scheduled_date': str(date), 'moved_from_date': moved_from}


def _shifted_plan(gid: int, tid: int) -> dict:
    """Позиции 1..4; занятия №1,2,3 сидят на позициях 2,3,4 — сдвиг на единицу."""
    facts = [
        _lesson(gid, tid, '2026-06-08', 1),
        _lesson(gid, tid, '2026-06-15', 2),
        _lesson(gid, tid, '2026-06-22', 3),
    ]
    positions = [
        _position(gid, tid, 1, 1, '2026-06-01'),
        _position(gid, tid, 2, 2, '2026-06-08', facts[0]),
        _position(gid, tid, 3, 3, '2026-06-15', facts[1]),
        _position(gid, tid, 4, 4, '2026-06-22', facts[2]),
    ]
    return {'facts': facts, 'positions': positions}


def _health(client, gid):
    return client.get(f'/api/admin/groups/{gid}/plan/health')


def _resync(client, gid, expected):
    return client.post(f'/api/admin/groups/{gid}/plan/resync',
                       {'expected': expected}, format='json')


def _expected_from(health_body: dict) -> list:
    return [[c['position_id'], c['to']['fact_lesson_id'], c['to']['scheduled_date']]
            for c in health_body['resync']['changes']]


# ---------------------------------------------------------------------------
# 1. RBAC
# ---------------------------------------------------------------------------

class TestRBAC:
    def test_anon_401(self, anon_client, resync_env):
        gid = resync_env['make_group']()
        assert anon_client.get(f'/api/admin/groups/{gid}/plan/health').status_code == 401
        assert _resync(anon_client, gid, []).status_code == 401

    @pytest.mark.parametrize('client_name', ['teacher_client', 'manager_client', 'admin_client'])
    def test_non_superadmin_403(self, request, client_name, resync_env):
        """Разбор рассогласований план↔факт — только superadmin: manager и admin,
        имеющие доступ к остальным /plan/*, сюда не допускаются."""
        client = request.getfixturevalue(client_name)
        gid = resync_env['make_group']()
        assert client.get(f'/api/admin/groups/{gid}/plan/health').status_code == 403
        assert _resync(client, gid, []).status_code == 403

    def test_superadmin_200(self, superadmin_client, resync_env):
        gid = resync_env['make_group']()
        assert _health(superadmin_client, gid).status_code == 200
        assert _resync(superadmin_client, gid, []).status_code == 200

    def test_missing_group_404(self, superadmin_client):
        assert superadmin_client.get('/api/admin/groups/99999999/plan/health').status_code == 404
        assert _resync(superadmin_client, 99999999, []).status_code == 404


# ---------------------------------------------------------------------------
# 2. GET health — только чтение
# ---------------------------------------------------------------------------

class TestHealthReadOnly:
    def test_health_writes_nothing(self, superadmin_client, resync_env):
        """Контракт health-модуля — только чтение: ни одного INSERT/UPDATE/DELETE."""
        gid = resync_env['make_group']()
        plan = _shifted_plan(gid, resync_env['teacher_id'])

        with CaptureQueriesContext(connection) as ctx:
            resp = _health(superadmin_client, gid)
        assert resp.status_code == 200

        writes = [q['sql'] for q in ctx.captured_queries
                  if q['sql'].strip().lower().startswith(('insert', 'update', 'delete'))]
        assert writes == [], writes
        # состояние плана не поехало
        assert _row(plan['positions'][1])['fact_lesson_id'] == plan['facts'][0]

    def test_health_shows_findings_and_changes(self, superadmin_client, resync_env):
        gid = resync_env['make_group']()
        plan = _shifted_plan(gid, resync_env['teacher_id'])
        body = _health(superadmin_client, gid).json()

        assert body['group_id'] == gid
        # позиции 2..4 держат факты с чужими номерами — это и есть сдвиг
        assert len(body['findings']['number_mismatch']) == 3
        resync = body['resync']
        assert resync['blocked_by'] == []
        assert resync['orphan_facts'] == []
        assert len(resync['changes']) == 4
        first = resync['changes'][0]
        assert first['position_id'] == plan['positions'][0]
        assert first['to'] == {'fact_lesson_id': plan['facts'][0],
                               'scheduled_date': '2026-06-08'}
        # последняя позиция остаётся без факта — уходит вперёд
        assert [f['position_id'] for f in resync['freed']] == [plan['positions'][3]]


# ---------------------------------------------------------------------------
# 3. Границы отказа (409)
# ---------------------------------------------------------------------------

class TestBlocked:
    def test_duplicate_dates_blocks(self, superadmin_client, resync_env):
        """Два занятия на одну дату — граница слоя 3: раскладка не определена."""
        gid = resync_env['make_group']()
        tid = resync_env['teacher_id']
        f1 = _lesson(gid, tid, '2026-06-08', 1)
        f2 = _lesson(gid, tid, '2026-06-08', 2)
        _position(gid, tid, 1, 1, '2026-06-08', f1)
        _position(gid, tid, 2, 2, '2026-06-08', f2)

        body = _health(superadmin_client, gid).json()
        assert body['resync']['blocked_by'] == ['duplicate_dates']
        # план починки, который сервер применять откажется, не показываем
        assert body['resync']['changes'] is None

        resp = _resync(superadmin_client, gid, [])
        assert resp.status_code == 409
        assert resp.json()['blocked_by'] == ['duplicate_dates']

    def test_fact_without_position_blocks(self, superadmin_client, resync_env):
        """Занятие, не привязанное НИ К ОДНОЙ позиции — тоже граница слоя 3."""
        gid = resync_env['make_group']()
        tid = resync_env['teacher_id']
        _lesson(gid, tid, '2026-06-08', 1)
        _position(gid, tid, 1, 1, '2026-06-01')

        assert _health(superadmin_client, gid).json()['resync']['blocked_by'] == \
            ['fact_without_position']
        resp = _resync(superadmin_client, gid, [])
        assert resp.status_code == 409

    def test_orphan_fact_blocks(self, superadmin_client, resync_env):
        """ДРУГОЙ предикат: занятие привязано (значит слой 3 молчит), но позиции
        С ЕГО НОМЕРОМ в плане нет — чинить наполовину нельзя."""
        gid = resync_env['make_group']()
        tid = resync_env['teacher_id']
        stray = _lesson(gid, tid, '2026-06-08', 5)
        _position(gid, tid, 1, 1, '2026-06-01')
        _position(gid, tid, 2, 2, '2026-06-08', stray)

        body = _health(superadmin_client, gid).json()
        assert body['resync']['blocked_by'] == []          # слой 3 чист
        assert body['resync']['changes'] is None
        assert [f['lesson_id'] for f in body['resync']['orphan_facts']] == [stray]
        assert body['resync']['orphan_facts'][0]['reason'] == 'no_position'

        resp = _resync(superadmin_client, gid, [])
        assert resp.status_code == 409
        assert [f['lesson_id'] for f in resp.json()['orphan_facts']] == [stray]

    def test_orphan_fact_locked_position_reason(self, superadmin_client, resync_env):
        """ТРЕТИЙ случай, отличный от 'no_position': факт привязан к позиции,
        которую починка не вправе трогать (status='moved') — health.fact_without_
        position молчит (факт СВЯЗАН с чем-то), но резолвер обязан отказать с
        отдельной причиной 'locked_position', а не тихо считать номер свободным."""
        gid = resync_env['make_group']()
        tid = resync_env['teacher_id']
        stray = _lesson(gid, tid, '2026-06-08', 1)
        _position(gid, tid, 1, 1, '2026-06-01', fact_id=stray, status='moved')

        body = _health(superadmin_client, gid).json()
        assert body['resync']['blocked_by'] == []          # слой 3 (health) чист
        assert body['resync']['changes'] is None
        assert [f['lesson_id'] for f in body['resync']['orphan_facts']] == [stray]
        assert body['resync']['orphan_facts'][0]['reason'] == 'locked_position'

        resp = _resync(superadmin_client, gid, [])
        assert resp.status_code == 409
        assert [f['lesson_id'] for f in resp.json()['orphan_facts']] == [stray]

    def test_stale_expected_conflicts(self, superadmin_client, resync_env):
        """expected из устаревшего предпросмотра → 409, ничего не записано."""
        gid = resync_env['make_group']()
        plan = _shifted_plan(gid, resync_env['teacher_id'])
        stale = [[plan['positions'][0], plan['facts'][0], '2026-06-08']]  # только часть диффа

        resp = _resync(superadmin_client, gid, stale)
        assert resp.status_code == 409
        assert 'обновите предпросмотр' in resp.json()['error']
        # позиции не тронуты
        assert _row(plan['positions'][0])['fact_lesson_id'] is None
        assert _row(plan['positions'][1])['fact_lesson_id'] == plan['facts'][0]

    def test_calendar_invalid_date_in_expected_is_not_500(self, superadmin_client, resync_env):
        """`_ISO_DATE_RE` проверяет только ФОРМУ (\\d{4}-\\d{2}-\\d{2}), не календарную
        валидность — '2026-13-40' проходит сериализатор. Раз диффу есть что чинить,
        сверка строк не совпадёт с реально посчитанным диффом → 409 (обновите
        предпросмотр), а не 500: сервер никогда не парсит ЧУЖУЮ дату в date()."""
        gid = resync_env['make_group']()
        plan = _shifted_plan(gid, resync_env['teacher_id'])
        garbage = [[plan['positions'][0], plan['facts'][0], '2026-13-40']]

        resp = _resync(superadmin_client, gid, garbage)
        assert resp.status_code == 409, resp.content
        assert _row(plan['positions'][0])['fact_lesson_id'] is None

    def test_malformed_expected_400(self, superadmin_client, resync_env):
        gid = resync_env['make_group']()
        resp = superadmin_client.post(f'/api/admin/groups/{gid}/plan/resync',
                                      {'expected': [[1, 2]]}, format='json')
        assert resp.status_code == 400

    @pytest.mark.parametrize('body', [
        {'expected': [[1, 2, 3]]},                        # дата не строка
        {'expected': [[1, None, '08-06-2026']]},           # неверный формат даты
        {'expected': [[0, None, '2026-06-01']]},           # position_id < 1
        {'expected': [[-1, None, '2026-06-01']]},           # отрицательный position_id
        {'expected': [['1', None, '2026-06-01']]},         # position_id строкой
        {'expected': [[True, None, '2026-06-01']]},        # bool — не целое (isinstance-ловушка)
        {'expected': [[1, 0, '2026-06-01']]},               # fact_lesson_id < 1
        {'expected': [[1, -5, '2026-06-01']]},              # отрицательный fact_lesson_id
        {'expected': [[1, True, '2026-06-01']]},            # fact_lesson_id — bool
        {'expected': [[1, None, '2026-06-01'], [1, None, '2026-06-02']]},  # дубль position_id
        {'expected': 'not-a-list'},                         # expected не список
        {},                                                  # expected отсутствует
        {'expected': [], 'unknown_field': 1},               # неизвестное поле (StrictSerializer)
    ])
    def test_malformed_expected_variants_400(self, superadmin_client, resync_env, body):
        """Мусорный вход резолвится в 400 (валидация сериализатора), не в 500."""
        gid = resync_env['make_group']()
        resp = superadmin_client.post(f'/api/admin/groups/{gid}/plan/resync',
                                      body, format='json')
        assert resp.status_code == 400, resp.content


# ---------------------------------------------------------------------------
# 4. Применение
# ---------------------------------------------------------------------------

class TestApply:
    def test_empty_group_no_positions_no_facts(self, superadmin_client, resync_env):
        """Группа без единой позиции плана и без фактов — законный вход, не 500."""
        gid = resync_env['make_group']()

        health_body = _health(superadmin_client, gid).json()
        assert health_body['findings'] == {}
        assert health_body['resync'] == {
            'blocked_by': [], 'changes': [], 'orphan_facts': [], 'freed': []}

        resp = _resync(superadmin_client, gid, [])
        assert resp.status_code == 200, resp.content
        assert resp.json() == {'applied': 0, 'freed_count': 0, 'plan': []}

    def test_aligns_numbers_dates_and_frees_tail(self, superadmin_client, resync_env):
        gid = resync_env['make_group']()
        plan = _shifted_plan(gid, resync_env['teacher_id'])
        expected = _expected_from(_health(superadmin_client, gid).json())

        resp = _resync(superadmin_client, gid, expected)
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert (body['applied'], body['freed_count']) == (4, 1)
        assert isinstance(body['plan'], list)

        p1, p2, p3, p4 = plan['positions']
        f1, f2, f3 = plan['facts']
        assert _row(p1) | {'moved_from_date': None} == {
            'fact_lesson_id': f1, 'status': 'done',
            'scheduled_date': '2026-06-08', 'moved_from_date': None}
        assert _row(p2)['fact_lesson_id'] == f2
        assert _row(p3)['fact_lesson_id'] == f3
        assert (_row(p4)['fact_lesson_id'], _row(p4)['status']) == (None, 'pending')

    def test_repeat_is_idempotent(self, superadmin_client, resync_env):
        """Повторный клик, когда чинить нечего → 200 applied=0, а не 409."""
        gid = resync_env['make_group']()
        _shifted_plan(gid, resync_env['teacher_id'])
        expected = _expected_from(_health(superadmin_client, gid).json())
        assert _resync(superadmin_client, gid, expected).json()['applied'] == 4

        after = _health(superadmin_client, gid).json()
        assert after['resync']['changes'] == []
        assert 'number_mismatch' not in after['findings']

        assert _resync(superadmin_client, gid, []).json()['applied'] == 0
        # и даже с устаревшим expected: чинить нечего — это не конфликт
        assert _resync(superadmin_client, gid, expected).json()['applied'] == 0

    def test_swapped_facts_do_not_violate_unique(self, superadmin_client, resync_env):
        """Перестановка двух фактов местами: одношаговая запись упёрлась бы в
        уникальность fact_lesson (23505) — спасает проход unbind→rebind."""
        gid = resync_env['make_group']()
        tid = resync_env['teacher_id']
        f1 = _lesson(gid, tid, '2026-06-01', 1)
        f2 = _lesson(gid, tid, '2026-06-08', 2)
        p1 = _position(gid, tid, 1, 1, '2026-06-08', f2)   # факт №2 на позиции №1
        p2 = _position(gid, tid, 2, 2, '2026-06-01', f1)   # факт №1 на позиции №2

        expected = _expected_from(_health(superadmin_client, gid).json())
        resp = _resync(superadmin_client, gid, expected)
        assert resp.status_code == 200, resp.content
        assert _row(p1) == {'fact_lesson_id': f1, 'status': 'done',
                            'scheduled_date': '2026-06-01', 'moved_from_date': None}
        assert _row(p2)['fact_lesson_id'] == f2
        assert _row(p2)['scheduled_date'] == '2026-06-08'

    def test_moved_from_date_cleared(self, superadmin_client, resync_env):
        """Позиция, получившая дату от факта, теряет метку разового переноса:
        та описывала плановое движение, а дата пришла от занятия."""
        gid = resync_env['make_group']()
        tid = resync_env['teacher_id']
        fact = _lesson(gid, tid, '2026-06-10', 1)
        pid = _position(gid, tid, 1, 1, '2026-06-01', moved_from='2026-05-25')
        _position(gid, tid, 2, 2, '2026-06-15', fact)   # факт сидит не на своём номере

        expected = _expected_from(_health(superadmin_client, gid).json())
        assert _resync(superadmin_client, gid, expected).status_code == 200
        assert _row(pid) == {'fact_lesson_id': fact, 'status': 'done',
                             'scheduled_date': '2026-06-10', 'moved_from_date': None}

    def test_moved_position_is_not_touched(self, superadmin_client, resync_env):
        """Позиция в статусе moved починке не подчиняется (как _MUTABLE_STATUSES
        в permanent_change): её состояние — отдельное решение человека."""
        gid = resync_env['make_group']()
        tid = resync_env['teacher_id']
        fact = _lesson(gid, tid, '2026-06-08', 2)
        moved = _position(gid, tid, 1, 1, '2026-06-01', status='moved')
        holder = _position(gid, tid, 2, 2, '2026-06-08', fact)

        body = _health(superadmin_client, gid).json()
        assert body['resync']['changes'] == []          # чинить нечего
        assert _resync(superadmin_client, gid, []).json()['applied'] == 0
        assert _row(moved)['status'] == 'moved'
        assert _row(holder)['fact_lesson_id'] == fact

    def test_half_lesson_group(self, superadmin_client, resync_env):
        """45 мин → шаг 0.5: сопоставление по номеру идёт по значению (Decimal),
        а не по строке ('1.0' != '1')."""
        gid = resync_env['make_group'](duration=45)
        tid = resync_env['teacher_id']
        facts = [
            _lesson(gid, tid, '2026-06-08', '0.5', duration=45),
            _lesson(gid, tid, '2026-06-15', '1.0', duration=45),
        ]
        positions = [
            _position(gid, tid, 1, '0.5', '2026-06-01'),
            _position(gid, tid, 2, '1.0', '2026-06-08', facts[0]),
            _position(gid, tid, 3, '1.5', '2026-06-15', facts[1]),
        ]
        body = _health(superadmin_client, gid).json()
        assert body['resync']['changes'][0]['lesson_number'] == 0.5

        expected = _expected_from(body)
        assert _resync(superadmin_client, gid, expected).status_code == 200
        assert _row(positions[0]) == {'fact_lesson_id': facts[0], 'status': 'done',
                                      'scheduled_date': '2026-06-08', 'moved_from_date': None}
        assert _row(positions[1])['fact_lesson_id'] == facts[1]
        assert _row(positions[2])['fact_lesson_id'] is None


# ---------------------------------------------------------------------------
# 5. Журнал изменений
# ---------------------------------------------------------------------------

class TestChangelog:
    def test_operation_lands_in_changelog(self, superadmin_client, resync_env):
        """Именная операция plan.resync с описанием по числу ПОЗИЦИЙ (а не событий:
        каждая позиция пишется дважды — unbind, затем rebind)."""
        gid = resync_env['make_group']()
        _shifted_plan(gid, resync_env['teacher_id'])
        expected = _expected_from(_health(superadmin_client, gid).json())
        assert _resync(superadmin_client, gid, expected).status_code == 200

        rows = superadmin_client.get('/api/admin/changelog?page_size=5').json()['rows']
        row = next(r for r in rows if r['operation'] == 'plan.resync')
        assert row['url'] == f'/api/admin/groups/{gid}/plan/resync'
        assert row['summary'].startswith('Починка плана')
        assert row['summary'].endswith('позиций изменено — 4')

    def test_resync_is_reverted_as_one_operation(self, superadmin_client, resync_env):
        """Вся починка — ОДНА транзакция/один pghistory-контекст: откатывается
        одним POST /revert и возвращает исходное расхождение целиком — включая
        позиции, которые сама починка внутри себя писала дважды (unbind→rebind,
        перестановка двух фактов местами)."""
        gid = resync_env['make_group']()
        tid = resync_env['teacher_id']
        f1 = _lesson(gid, tid, '2026-06-01', 1)
        f2 = _lesson(gid, tid, '2026-06-08', 2)
        p1 = _position(gid, tid, 1, 1, '2026-06-08', f2)   # факт №2 на позиции №1
        p2 = _position(gid, tid, 2, 2, '2026-06-01', f1)   # факт №1 на позиции №2

        expected = _expected_from(_health(superadmin_client, gid).json())
        resp = _resync(superadmin_client, gid, expected)
        assert resp.status_code == 200, resp.content
        assert resp.json()['applied'] == 2
        # починка применилась — состояние поехало относительно исходного
        assert _row(p1)['fact_lesson_id'] == f1
        assert _row(p2)['fact_lesson_id'] == f2

        rows = superadmin_client.get('/api/admin/changelog?page_size=5').json()['rows']
        op_id = next(r for r in rows if r['operation'] == 'plan.resync')['id']

        revert_resp = superadmin_client.post(f'/api/admin/changelog/{op_id}/revert')
        assert revert_resp.status_code == 200, revert_resp.content

        # исходное («перепутанное») состояние восстановлено целиком одной операцией
        assert _row(p1) == {'fact_lesson_id': f2, 'status': 'done',
                            'scheduled_date': '2026-06-08', 'moved_from_date': None}
        assert _row(p2) == {'fact_lesson_id': f1, 'status': 'done',
                            'scheduled_date': '2026-06-01', 'moved_from_date': None}
