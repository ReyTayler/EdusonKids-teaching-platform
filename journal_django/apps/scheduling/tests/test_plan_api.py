"""
API-тесты admin-операций плана (planned_lessons, шаг 4):
  GET  /api/admin/groups/<pk>/plan
  POST /api/admin/groups/<pk>/plan/generate
  POST /api/admin/groups/<pk>/plan/<lid>/reschedule
  POST /api/admin/groups/<pk>/plan/permanent-change
  POST /api/admin/groups/<pk>/plan/<lid>/cancel

Покрытие: RBAC (401/403), CSRF (session-auth без токена → 403), генерация плана,
разовый перенос (+moved_from_date), перенос навсегда (пересчёт хвоста + версия
слота), отмена со сдвигом (не трогает 'done'), конфликт переноса
'done' (409, не 500), отсутствие записей в security_audit_log (журнал ИБ).

Права: IsManagerOrAdmin. Аутентификация — JWT (root conftest клиенты).
managed-схема eduson_test; чистим прямым DELETE в FK-безопасном порядке.
"""
from __future__ import annotations

import datetime

import pytest
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import connection
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

pytestmark = pytest.mark.django_db


@pytest.fixture
def plan_group(db):
    """
    Группа со стартом 2026-06-01 (Пн), слотом Пн 10:00, direction.total_lessons=8,
    длительностью 60 мин + второй преподаватель (для смены препода).
    Возвращает dict с id. План НЕ генерируется — тесты дёргают эндпоинт generate.
    """
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name, active) VALUES ('__plan_A__', true) RETURNING id")
        teacher_a = cur.fetchone()[0]
        cur.execute("INSERT INTO teachers (name, active) VALUES ('__plan_B__', true) RETURNING id")
        teacher_b = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO directions (name,total_lessons,color,active) "
            "VALUES ('__plan_dir__',8,'#4F59F9',true) RETURNING id"
        )
        direction_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name,direction_id,teacher_id,is_individual,lesson_duration_minutes,"
            "group_start_date,active,lesson_number_offset) "
            "VALUES ('__plan_g__',%s,%s,false,60,'2026-06-01',true,0) RETURNING id",
            [direction_id, teacher_a],
        )
        group_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_schedule_slots (group_id,day_of_week,start_time,effective_from) "
            "VALUES (%s,1,'10:00','2026-06-01')",  # day_of_week=1 → понедельник (Вс=0)
            [group_id],
        )

    yield {
        'group_id': group_id, 'teacher_a': teacher_a, 'teacher_b': teacher_b,
        'direction_id': direction_id,
    }

    with connection.cursor() as cur:
        cur.execute('DELETE FROM security_audit_log WHERE target_id = %s', [group_id])
        # Начисления за отмены создаёт сама операция отмены; FK держит группу.
        cur.execute('DELETE FROM payroll_cancellations WHERE group_id = %s', [group_id])
        # Записи об отменах ссылаются на группу и учётку менеджера (её удаляет
        # фикстура клиента сырым DELETE) — убираем до отложенной сверки FK.
        cur.execute('DELETE FROM lesson_cancellations WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM planned_lessons WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM lessons WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM group_schedule_slots WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM groups WHERE id = %s', [group_id])
        cur.execute('DELETE FROM directions WHERE id = %s', [direction_id])
        cur.execute('DELETE FROM teachers WHERE id IN (%s,%s)', [teacher_a, teacher_b])


# --- helpers --------------------------------------------------------------

def _generate(client, gid):
    return client.post(f'/api/admin/groups/{gid}/plan/generate', {}, format='json')


def _get_plan(client, gid):
    return client.get(f'/api/admin/groups/{gid}/plan').json()


def _by_seq(plan):
    return {r['seq']: r for r in plan if r['seq'] is not None}


def _cancel_body(row, **over):
    """Валидное тело отмены (с 2026-10-23 обязательно): дата занятия, как её
    видит клиент, + сторона и «Другая причина»."""
    return {'lesson_date': row['scheduled_date'], 'side': 'teacher',
            'other_text': 'тест', **over}


# ---------------------------------------------------------------------------
# 1. RBAC
# ---------------------------------------------------------------------------

class TestRBAC:
    def _endpoints(self, gid):
        return [
            ('get', f'/api/admin/groups/{gid}/plan'),
            ('post', f'/api/admin/groups/{gid}/plan/generate'),
            ('post', f'/api/admin/groups/{gid}/plan/1/reschedule'),
            ('post', f'/api/admin/groups/{gid}/plan/permanent-change'),
            ('post', f'/api/admin/groups/{gid}/plan/change-teacher-permanent'),
            ('post', f'/api/admin/groups/{gid}/plan/1/change-teacher'),
            ('post', f'/api/admin/groups/{gid}/plan/1/cancel'),
        ]

    def test_anon_401(self, anon_client, plan_group):
        for method, url in self._endpoints(plan_group['group_id']):
            resp = getattr(anon_client, method)(url, {}, format='json')
            assert resp.status_code == 401, f'{method} {url} → {resp.status_code}'

    def test_teacher_403(self, teacher_client, plan_group):
        for method, url in self._endpoints(plan_group['group_id']):
            resp = getattr(teacher_client, method)(url, {}, format='json')
            assert resp.status_code == 403, f'{method} {url} → {resp.status_code}'


# ---------------------------------------------------------------------------
# 2. CSRF — мутация session-auth без X-CSRFToken отклоняется
# ---------------------------------------------------------------------------

def _csrf_jwt_client(account) -> APIClient:
    """APIClient с JWT access-cookie И включённой CSRF-проверкой."""
    refresh = RefreshToken.for_user(account)
    refresh['token_version'] = account.token_version
    client = APIClient(enforce_csrf_checks=True)
    client.cookies[settings.SIMPLE_JWT.get('AUTH_COOKIE', 'access')] = str(refresh.access_token)
    return client


@pytest.fixture
def csrf_manager():
    from apps.accounts.models import Account
    acc = Account.objects.create(
        email='__plan_csrf__@x.com', password=make_password('secret123'),
        role='manager', token_version=0, is_active=True,
    )
    yield acc
    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM security_audit_log WHERE account_id = %s OR target_id = %s',
            [acc.id, acc.id],
        )
    Account.objects.filter(pk=acc.pk).delete()


class TestCSRF:
    def test_mutation_without_csrf_token_403(self, csrf_manager, plan_group):
        """manager имеет право (IsManagerOrAdmin) → единственная причина 403 = провал CSRF."""
        client = _csrf_jwt_client(csrf_manager)
        resp = client.post(
            f'/api/admin/groups/{plan_group["group_id"]}/plan/generate', {}, format='json',
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 3. GET plan
# ---------------------------------------------------------------------------

class TestGetPlan:
    def test_missing_group_404(self, manager_client):
        assert manager_client.get('/api/admin/groups/99999999/plan').status_code == 404

    def test_generate_then_list(self, manager_client, plan_group):
        gid = plan_group['group_id']
        gen = _generate(manager_client, gid)
        assert gen.status_code == 200
        plan = gen.json()
        assert len(plan) == 8
        dates = [r['scheduled_date'] for r in plan]
        assert dates == [
            '2026-06-01', '2026-06-08', '2026-06-15', '2026-06-22',
            '2026-06-29', '2026-07-06', '2026-07-13', '2026-07-20',
        ]
        first = plan[0]
        assert first['seq'] == 1
        assert first['scheduled_time'] == '10:00'
        assert first['lesson_number'] == 1
        assert first['is_extra'] is False
        # Статус ВЫЧИСЛЯЕТСЯ на чтении (occurrences.planned_status), а не берётся
        # сырым из БД: в базе строка лежит как pending, но занятие 01.06.2026 давно
        # прошло и не заполнено — значит overdue. Раньше /plan отдавал сырое
        # значение и расходился с /api/calendar, из-за чего «Обзор» писал
        # «Запланирован» на просроченном занятии.
        assert first['status'] == 'overdue'
        assert first['teacher_id'] == plan_group['teacher_a']

        # GET plan возвращает тот же список.
        listed = _get_plan(manager_client, gid)
        assert [r['id'] for r in listed] == [r['id'] for r in plan]

    def test_generate_idempotent(self, manager_client, plan_group):
        gid = plan_group['group_id']
        _generate(manager_client, gid)
        plan2 = _generate(manager_client, gid).json()
        assert len(plan2) == 8  # повторный прогон не плодит дубли

    def test_generate_preserves_manual_reschedule(self, manager_client, plan_group):
        """Повторная генерация НЕ затирает ручные операции: уже существующие строки
        не пересчитываются (generate только досоздаёт недостающие seq)."""
        gid = plan_group['group_id']
        plan = _generate(manager_client, gid).json()
        target = _by_seq(plan)[2]  # 2026-06-08
        manager_client.post(
            f'/api/admin/groups/{gid}/plan/{target["id"]}/reschedule',
            {'new_date': '2026-06-10'}, format='json',
        )
        after = _by_seq(_generate(manager_client, gid).json())
        assert after[2]['scheduled_date'] == '2026-06-10'      # ручной перенос сохранён
        assert after[2]['moved_from_date'] == '2026-06-08'


# ---------------------------------------------------------------------------
# 4. Операции
# ---------------------------------------------------------------------------

class TestReschedule:
    def test_moves_single_row(self, manager_client, plan_group):
        gid = plan_group['group_id']
        plan = _generate(manager_client, gid).json()
        target = _by_seq(plan)[2]  # 2026-06-08
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/{target["id"]}/reschedule',
            {'new_date': '2026-06-10', 'new_time': '15:00', 'new_teacher_id': plan_group['teacher_b']},
            format='json',
        )
        assert resp.status_code == 200
        row = resp.json()
        assert row['scheduled_date'] == '2026-06-10'
        assert row['scheduled_time'] == '15:00'
        assert row['moved_from_date'] == '2026-06-08'
        assert row['teacher_id'] == plan_group['teacher_b']
        assert row['seq'] == 2  # порядок контента сохранён

        # остальные строки не тронуты
        by_seq = _by_seq(_get_plan(manager_client, gid))
        assert by_seq[1]['scheduled_date'] == '2026-06-01'
        assert by_seq[3]['scheduled_date'] == '2026-06-15'

    def test_reschedule_done_conflict(self, manager_client, plan_group):
        gid = plan_group['group_id']
        plan = _generate(manager_client, gid).json()
        target = _by_seq(plan)[3]
        with connection.cursor() as cur:
            cur.execute("UPDATE planned_lessons SET status='done' WHERE id=%s", [target['id']])
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/{target["id"]}/reschedule',
            {'new_date': '2026-06-17'}, format='json',
        )
        assert resp.status_code in (400, 409)  # не 500

    def test_missing_lesson_404(self, manager_client, plan_group):
        gid = plan_group['group_id']
        _generate(manager_client, gid)
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/99999999/reschedule',
            {'new_date': '2026-06-17'}, format='json',
        )
        assert resp.status_code == 404

    def test_unknown_field_rejected(self, manager_client, plan_group):
        gid = plan_group['group_id']
        plan = _generate(manager_client, gid).json()
        target = _by_seq(plan)[2]
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/{target["id"]}/reschedule',
            {'new_date': '2026-06-10', 'bogus': 1}, format='json',
        )
        assert resp.status_code == 400

    def test_same_date_reschedule_does_not_set_moved_from(self, manager_client, plan_group):
        """Перенос на ту же дату (правка только времени) не помечает строку
        перенесённой — moved_from_date остаётся null."""
        gid = plan_group['group_id']
        plan = _generate(manager_client, gid).json()
        target = _by_seq(plan)[2]  # 2026-06-08
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/{target["id"]}/reschedule',
            {'new_date': target['scheduled_date'], 'new_time': '15:00'}, format='json',
        )
        assert resp.status_code == 200
        assert resp.json()['moved_from_date'] is None
        assert resp.json()['scheduled_time'] == '15:00'


class TestChangeTeacher:
    def test_changes_only_teacher(self, manager_client, plan_group):
        gid = plan_group['group_id']
        plan = _generate(manager_client, gid).json()
        target = _by_seq(plan)[2]  # 2026-06-08
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/{target["id"]}/change-teacher',
            {'new_teacher_id': plan_group['teacher_b']}, format='json',
        )
        assert resp.status_code == 200
        row = resp.json()
        assert row['teacher_id'] == plan_group['teacher_b']
        assert row['scheduled_date'] == '2026-06-08'   # дата не тронута
        assert row['moved_from_date'] is None           # НЕ помечен перенесённым
        assert row['seq'] == 2

    def test_change_teacher_done_conflict(self, manager_client, plan_group):
        gid = plan_group['group_id']
        plan = _generate(manager_client, gid).json()
        target = _by_seq(plan)[3]
        with connection.cursor() as cur:
            cur.execute("UPDATE planned_lessons SET status='done' WHERE id=%s", [target['id']])
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/{target["id"]}/change-teacher',
            {'new_teacher_id': plan_group['teacher_b']}, format='json',
        )
        assert resp.status_code in (400, 409)

    def test_missing_lesson_404(self, manager_client, plan_group):
        gid = plan_group['group_id']
        _generate(manager_client, gid)
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/99999999/change-teacher',
            {'new_teacher_id': plan_group['teacher_b']}, format='json',
        )
        assert resp.status_code == 404


class TestSubstituteTeacher:
    def test_change_teacher_sets_substitute_not_content(self, manager_client, plan_group):
        """Разовая замена пишется в substitute_teacher, teacher (контент) не тронут."""
        gid = plan_group['group_id']
        plan = _generate(manager_client, gid).json()
        target = _by_seq(plan)[3]
        sub = plan_group['teacher_b']
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/{target["id"]}/change-teacher',
            {'new_teacher_id': sub}, format='json',
        )
        assert resp.status_code == 200
        with connection.cursor() as cur:
            cur.execute(
                "SELECT teacher_id, substitute_teacher_id FROM planned_lessons WHERE id=%s",
                [target['id']],
            )
            content_id, substitute_id = cur.fetchone()
        assert substitute_id == sub          # замена в новой колонке
        assert content_id != sub             # контент-преподаватель не тронут

    def test_substitute_dropped_when_cancel_moves_row(self, manager_client, plan_group):
        """Баг №1: замена НЕ едет с контентом. Ставим замену на урок, отменяем его —
        сдвинутая строка теряет замену (замена осталась на отменённой дате)."""
        gid = plan_group['group_id']
        plan = _generate(manager_client, gid).json()
        by_seq = _by_seq(plan)
        target = by_seq[3]
        sub = plan_group['teacher_b']
        manager_client.post(
            f'/api/admin/groups/{gid}/plan/{target["id"]}/change-teacher',
            {'new_teacher_id': sub}, format='json',
        )
        manager_client.post(
            f'/api/admin/groups/{gid}/plan/{target["id"]}/cancel', _cancel_body(target), format='json',
        )
        with connection.cursor() as cur:
            cur.execute(
                "SELECT substitute_teacher_id FROM planned_lessons WHERE id=%s",
                [target['id']],
            )
            (substitute_id,) = cur.fetchone()
        assert substitute_id is None   # замена сброшена при переезде строки

    def test_cancel_marker_carries_substitute_teacher(self, manager_client, plan_group):
        """Отмена замещённого занятия: зачёркнутый маркер несёт ЗАМЕСТИТЕЛЯ (он
        реально должен был вести эту дату), а не преподавателя контента."""
        gid = plan_group['group_id']
        plan = _generate(manager_client, gid).json()
        target = _by_seq(plan)[3]
        sub = plan_group['teacher_b']
        marker_date = target['scheduled_date']
        manager_client.post(
            f'/api/admin/groups/{gid}/plan/{target["id"]}/change-teacher',
            {'new_teacher_id': sub}, format='json',
        )
        after = manager_client.post(
            f'/api/admin/groups/{gid}/plan/{target["id"]}/cancel', _cancel_body(target), format='json',
        ).json()
        marker = next(r for r in after
                      if r['status'] == 'cancelled' and r['scheduled_date'] == marker_date)
        # teacher_id маркера (эффективный) = заместитель.
        assert marker['teacher_id'] == sub

    def _set_substitute(self, manager_client, gid, lesson_id, sub):
        manager_client.post(
            f'/api/admin/groups/{gid}/plan/{lesson_id}/change-teacher',
            {'new_teacher_id': sub}, format='json',
        )

    def _substitute_of(self, lesson_id):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT substitute_teacher_id FROM planned_lessons WHERE id=%s", [lesson_id])
            (substitute_id,) = cur.fetchone()
        return substitute_id

    def test_substitute_dropped_when_reschedule_moves_row(self, manager_client, plan_group):
        """Разовый перенос на другую дату сбрасывает замену (свойство даты)."""
        gid = plan_group['group_id']
        target = _by_seq(_generate(manager_client, gid).json())[3]   # 2026-06-15
        self._set_substitute(manager_client, gid, target['id'], plan_group['teacher_b'])
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/{target["id"]}/reschedule',
            {'new_date': '2026-06-18'}, format='json',
        )
        assert resp.status_code == 200
        assert self._substitute_of(target['id']) is None

    def test_substitute_dropped_when_permanent_change_moves_row(self, manager_client, plan_group):
        """Перенос навсегда на другой день недели сбрасывает замену у переехавших строк."""
        gid = plan_group['group_id']
        target = _by_seq(_generate(manager_client, gid).json())[3]   # 2026-06-15 (Пн)
        self._set_substitute(manager_client, gid, target['id'], plan_group['teacher_b'])
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/permanent-change',
            {'from_seq': 3, 'effective_from': '2026-06-15',
             'new_slots': [{'day_of_week': 3, 'start_time': '14:00'}]}, format='json',
        )
        assert resp.status_code == 200
        assert self._substitute_of(target['id']) is None


class TestChangeTeacherPermanent:
    def test_sets_teacher_on_tail_only(self, manager_client, plan_group):
        gid = plan_group['group_id']
        plan = _generate(manager_client, gid).json()
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/change-teacher-permanent',
            {'from_seq': 5, 'new_teacher_id': plan_group['teacher_b']}, format='json',
        )
        assert resp.status_code == 200
        by_seq = _by_seq(resp.json())
        assert by_seq[4]['teacher_id'] == plan_group['teacher_a']   # голова не тронута
        assert by_seq[5]['teacher_id'] == plan_group['teacher_b']
        assert by_seq[8]['teacher_id'] == plan_group['teacher_b']
        # даты/дни не изменились
        assert by_seq[5]['scheduled_date'] == '2026-06-29'
        assert by_seq[5]['scheduled_time'] == '10:00'
        # новый преподаватель хвоста становится преподавателем группы по умолчанию
        with connection.cursor() as cur:
            cur.execute('SELECT teacher_id FROM groups WHERE id = %s', [gid])
            assert cur.fetchone()[0] == plan_group['teacher_b']

    def test_missing_group_404(self, manager_client):
        resp = manager_client.post(
            '/api/admin/groups/99999999/plan/change-teacher-permanent',
            {'from_seq': 1, 'new_teacher_id': 1}, format='json',
        )
        assert resp.status_code == 404


class TestPermanentChange:
    def test_recomputes_tail_and_versions_slot(self, manager_client, plan_group):
        gid = plan_group['group_id']
        _generate(manager_client, gid)
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/permanent-change',
            {'from_seq': 5, 'effective_from': '2026-06-29',
             'new_slots': [{'day_of_week': 3, 'start_time': '14:00'}]},
            format='json',
        )
        assert resp.status_code == 200
        by_seq = _by_seq(resp.json())
        # голова (seq<5) не тронута
        assert by_seq[4]['scheduled_date'] == '2026-06-22'
        assert by_seq[4]['scheduled_time'] == '10:00'
        # хвост на среду 14:00, от effective_from=2026-06-29 (Пн той же недели)
        assert by_seq[5]['scheduled_date'] == '2026-07-01'  # среда недели 2026-06-29
        assert by_seq[5]['scheduled_time'] == '14:00'
        assert by_seq[6]['scheduled_date'] == '2026-07-08'
        assert by_seq[8]['scheduled_date'] == '2026-07-22'

        # слот версионирован от КЛИЕНТСКОЙ effective_from (2026-06-29), а не от
        # новой даты первой строки хвоста (07-01): старый Пн 10:00 закрыт день
        # перед effective_from (06-28), новый Ср 14:00 открыт ровно с 06-29.
        sched = manager_client.get(f'/api/admin/groups/{gid}/schedule').json()
        slots = {(s['day_of_week'], s['start_time']): s for s in sched['slots']}
        assert slots[(1, '10:00')]['effective_to'] == '2026-06-28'
        assert slots[(3, '14:00')]['effective_from'] == '2026-06-29'
        assert slots[(3, '14:00')]['effective_to'] is None
        # new_teacher_id не передавали — преподаватель группы по умолчанию не тронут
        with connection.cursor() as cur:
            cur.execute('SELECT teacher_id FROM groups WHERE id = %s', [gid])
            assert cur.fetchone()[0] == plan_group['teacher_a']

    def test_updates_group_default_teacher_when_provided(self, manager_client, plan_group):
        gid = plan_group['group_id']
        _generate(manager_client, gid)
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/permanent-change',
            {'from_seq': 5, 'effective_from': '2026-06-29',
             'new_slots': [{'day_of_week': 3, 'start_time': '14:00'}],
             'new_teacher_id': plan_group['teacher_b']},
            format='json',
        )
        assert resp.status_code == 200
        by_seq = _by_seq(resp.json())
        assert by_seq[5]['teacher_id'] == plan_group['teacher_b']
        with connection.cursor() as cur:
            cur.execute('SELECT teacher_id FROM groups WHERE id = %s', [gid])
            assert cur.fetchone()[0] == plan_group['teacher_b']

    def test_effective_from_required(self, manager_client, plan_group):
        """effective_from теперь ОБЯЗАТЕЛЕН (единственный контракт) — без него 400."""
        gid = plan_group['group_id']
        _generate(manager_client, gid)
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/permanent-change',
            {'from_seq': 5, 'new_slots': [{'day_of_week': 3, 'start_time': '14:00'}]},
            format='json',
        )
        assert resp.status_code == 400

    def test_multi_slot_group_regenerates_tail(self, manager_client, plan_group):
        """Мультислот (Пн 10:00 + Ср 12:00): «изменить расписание» с seq=5,
        effective_from=2026-06-15, набором new_slots (Вт 17:00 + Чт 20:00)
        чисто перегенерирует хвост по новому набору, версионирует ОБА старых
        слота и открывает оба новых. Голова не тронута."""
        gid = plan_group['group_id']
        # второй открытый слот (Ср=3 12:00) ДО генерации → мультислотовая группа
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO group_schedule_slots (group_id,day_of_week,start_time,effective_from) "
                "VALUES (%s,3,'12:00','2026-06-01')",
                [gid],
            )
        _generate(manager_client, gid)
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/permanent-change',
            {'from_seq': 5, 'effective_from': '2026-06-15', 'new_slots': [
                {'day_of_week': 2, 'start_time': '17:00'},   # Вт
                {'day_of_week': 4, 'start_time': '20:00'},   # Чт
            ]},
            format='json',
        )
        assert resp.status_code == 200, resp.content
        by_seq = _by_seq(resp.json())
        # голова (seq<5) не тронута: seq4 = Ср 2026-06-10 12:00
        assert (by_seq[4]['scheduled_date'], by_seq[4]['scheduled_time']) == ('2026-06-10', '12:00')
        # хвост разложен по Вт 17:00 / Чт 20:00 непрерывно от effective_from (2026-06-15)
        assert (by_seq[5]['scheduled_date'], by_seq[5]['scheduled_time']) == ('2026-06-16', '17:00')
        assert (by_seq[6]['scheduled_date'], by_seq[6]['scheduled_time']) == ('2026-06-18', '20:00')
        assert (by_seq[7]['scheduled_date'], by_seq[7]['scheduled_time']) == ('2026-06-23', '17:00')
        assert (by_seq[8]['scheduled_date'], by_seq[8]['scheduled_time']) == ('2026-06-25', '20:00')
        # НЕ должно быть двух курсовых строк на одну дату (constraint это не ловит)
        tail_dates = [by_seq[i]['scheduled_date'] for i in (5, 6, 7, 8)]
        assert len(set(tail_dates)) == 4
        # слоты: оба старых закрыты (eff_to = effective_from-1 = 2026-06-14), оба новых открыты
        sched = manager_client.get(f'/api/admin/groups/{gid}/schedule').json()
        slots = {(s['day_of_week'], s['start_time']): s for s in sched['slots']}
        assert slots[(1, '10:00')]['effective_to'] == '2026-06-14'
        assert slots[(3, '12:00')]['effective_to'] == '2026-06-14'
        assert slots[(2, '17:00')]['effective_from'] == '2026-06-15'
        assert slots[(2, '17:00')]['effective_to'] is None
        assert slots[(4, '20:00')]['effective_from'] == '2026-06-15'
        assert slots[(4, '20:00')]['effective_to'] is None
        # lessons_per_week синхронизирован с числом новых слотов
        with connection.cursor() as cur:
            cur.execute('SELECT lessons_per_week FROM groups WHERE id = %s', [gid])
            assert cur.fetchone()[0] == 2

    def test_expand_single_to_multi_slot(self, manager_client, plan_group):
        """Одно-слотовая группа (Пн 10:00) расширяется до двух слотов
        (Пн 10:00 + Ср 12:00) с seq=5 — раньше это было невозможно."""
        gid = plan_group['group_id']
        _generate(manager_client, gid)  # 8 понедельников
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/permanent-change',
            {'from_seq': 5, 'effective_from': '2026-06-29', 'new_slots': [
                {'day_of_week': 1, 'start_time': '10:00'},   # Пн (оставляем)
                {'day_of_week': 3, 'start_time': '12:00'},   # + Ср
            ]},
            format='json',
        )
        assert resp.status_code == 200, resp.content
        by_seq = _by_seq(resp.json())
        # голова не тронута: seq4 = Пн 2026-06-22 10:00
        assert (by_seq[4]['scheduled_date'], by_seq[4]['scheduled_time']) == ('2026-06-22', '10:00')
        # хвост с seq5 от 2026-06-29 по Пн+Ср
        assert (by_seq[5]['scheduled_date'], by_seq[5]['scheduled_time']) == ('2026-06-29', '10:00')
        assert (by_seq[6]['scheduled_date'], by_seq[6]['scheduled_time']) == ('2026-07-01', '12:00')
        assert (by_seq[7]['scheduled_date'], by_seq[7]['scheduled_time']) == ('2026-07-06', '10:00')
        assert (by_seq[8]['scheduled_date'], by_seq[8]['scheduled_time']) == ('2026-07-08', '12:00')
        with connection.cursor() as cur:
            cur.execute('SELECT lessons_per_week FROM groups WHERE id = %s', [gid])
            assert cur.fetchone()[0] == 2

    def test_unknown_field_rejected(self, manager_client, plan_group):
        """Легаси-поле new_day_of_week больше не существует — StrictSerializer
        отклоняет его как неизвестное, даже если валидный new_slots тоже передан."""
        gid = plan_group['group_id']
        _generate(manager_client, gid)
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/permanent-change',
            {'from_seq': 5, 'effective_from': '2026-06-29', 'new_day_of_week': 3,
             'new_slots': [{'day_of_week': 3, 'start_time': '14:00'}]},
            format='json',
        )
        assert resp.status_code == 400

    def test_new_slots_required(self, manager_client, plan_group):
        """new_slots — единственный способ задать целевое расписание; без него 400."""
        gid = plan_group['group_id']
        _generate(manager_client, gid)
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/permanent-change',
            {'from_seq': 5, 'effective_from': '2026-06-29'}, format='json',
        )
        assert resp.status_code == 400

    def test_missing_group_404(self, manager_client):
        resp = manager_client.post(
            '/api/admin/groups/99999999/plan/permanent-change',
            {'from_seq': 1, 'effective_from': '2026-01-01',
             'new_slots': [{'day_of_week': 1, 'start_time': '10:00'}]},
            format='json',
        )
        assert resp.status_code == 404

    def test_preview_true_returns_affected_without_writing(self, manager_client, plan_group):
        """preview: true — вернуть {'affected': [...]} БЕЗ записи (план и слоты
        остаются прежними). Ставим замену на строку хвоста (substitution), затем
        шлём тот же payload что и для реальной мутации, но с preview=true."""
        gid = plan_group['group_id']
        plan = _generate(manager_client, gid).json()
        target = _by_seq(plan)[5]   # 2026-06-29 (Пн), попадает в хвост from_seq=5
        sub = plan_group['teacher_b']
        manager_client.post(
            f'/api/admin/groups/{gid}/plan/{target["id"]}/change-teacher',
            {'new_teacher_id': sub}, format='json',
        )
        plan_before = _get_plan(manager_client, gid)

        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/permanent-change',
            {'preview': True, 'from_seq': 5, 'effective_from': '2026-06-29',
             'new_slots': [{'day_of_week': 3, 'start_time': '14:00'}]},
            format='json',
        )
        assert resp.status_code == 200
        body = resp.json()
        assert 'affected' in body
        affected = body['affected']
        assert len(affected) >= 1
        assert any(a['kind'] == 'substitution' for a in affected)

        # ничего не записано: план идентичен состоянию до preview-запроса
        plan_after = _get_plan(manager_client, gid)
        assert plan_after == plan_before
        # слот-версии тоже не тронуты (никакого versioning при preview)
        sched = manager_client.get(f'/api/admin/groups/{gid}/schedule').json()
        slots = {(s['day_of_week'], s['start_time']): s for s in sched['slots']}
        assert (3, '14:00') not in slots
        assert slots[(1, '10:00')]['effective_to'] is None

    def test_preview_missing_group_404(self, manager_client):
        """Превью для несуществующей группы — 404 (не тихое [])."""
        resp = manager_client.post(
            '/api/admin/groups/99999999/plan/permanent-change',
            {'preview': True, 'from_seq': 1, 'effective_from': '2026-01-01',
             'new_slots': [{'day_of_week': 1, 'start_time': '10:00'}]},
            format='json',
        )
        assert resp.status_code == 404


class TestCancel:
    def test_cancel_pins_done_row_and_moves_anchor_to_course_end(self, manager_client, plan_group):
        """Новая модель отмены: done — неподвижный пин (дата/seq не трогаются,
        нумерация pending продолжается от него); прочие курсовые строки СОХРАНЯЮТ
        свои даты (никакого relay), только seq пересчитывается по дате; отменённая
        строка уезжает на первый свободный слот ПОСЛЕ текущей последней курсовой
        даты — становится новым последним занятием курса."""
        gid = plan_group['group_id']
        plan = _generate(manager_client, gid).json()
        by_seq = _by_seq(plan)
        done_row = by_seq[1]       # 2026-06-01 — уже проведён (история)
        anchor = by_seq[3]         # 2026-06-15 — отменяем
        with connection.cursor() as cur:
            cur.execute("UPDATE planned_lessons SET status='done' WHERE id=%s", [done_row['id']])

        resp = manager_client.post(f'/api/admin/groups/{gid}/plan/{anchor["id"]}/cancel', _cancel_body(anchor), format='json')
        assert resp.status_code == 200
        after_list = resp.json()
        after = _by_seq(after_list)

        assert after[1]['scheduled_date'] == '2026-06-01'   # done — неподвижный пин
        assert after[1]['status'] == 'done'
        # Прочие курсовые сохранили ДАТЫ (никакого сдвига), только seq сместился
        # (продолжение нумерации от done: seq2..7 вместо 2..8, т.к. одна строка ушла в конец).
        assert after[2]['scheduled_date'] == '2026-06-08'    # был seq2
        assert after[3]['scheduled_date'] == '2026-06-22'    # был seq4
        assert after[4]['scheduled_date'] == '2026-06-29'    # был seq5
        assert after[7]['scheduled_date'] == '2026-07-20'    # был seq8 — прежний конец курса
        # Отменённая строка (была seq3, 06-15) — новый конец курса, сразу после 07-20.
        assert after[8]['scheduled_date'] == '2026-07-27'
        moved = next(r for r in after_list if r['id'] == anchor['id'])
        assert moved['seq'] == 8
        assert moved['scheduled_date'] == '2026-07-27'
        # Маркер отмены на исходной дате.
        marker = next(r for r in after_list if r['status'] == 'cancelled')
        assert marker['scheduled_date'] == '2026-06-15'

    def test_double_cancel_is_contiguous_no_gap(self, manager_client, plan_group):
        """Две отмены (сначала поздняя, затем ранняя) НЕ создают ПУСТЫХ недель:
        весь календарь (курсовые строки + маркеры отмен вместе) идёт непрерывно
        по неделям. Курс суммарно продлён на 2 недели — по неделе за каждую отмену.
        Отменённая неделя не «пустая»: она занята маркером, а не потеряна."""
        gid = plan_group['group_id']
        plan = _generate(manager_client, gid).json()
        by_seq = _by_seq(plan)
        # Курс: seq1..8 по понедельникам с 06-01.
        manager_client.post(f'/api/admin/groups/{gid}/plan/{by_seq[3]["id"]}/cancel', _cancel_body(by_seq[3]), format='json')
        after = manager_client.post(
            f'/api/admin/groups/{gid}/plan/{by_seq[1]["id"]}/cancel', _cancel_body(by_seq[1]), format='json',
        ).json()
        # Полный календарь: курсовые (pending/overdue) + маркеры отмен, по дате.
        occupied = sorted(r['scheduled_date'] for r in after if r['status'] != 'done')
        # Непрерывность недель: между соседними занятыми датами ровно 7 дней —
        # ни одной пустой недели (отменённые недели заняты маркерами).
        for i in range(1, len(occupied)):
            d0 = datetime.date.fromisoformat(occupied[i - 1])
            d1 = datetime.date.fromisoformat(occupied[i])
            assert (d1 - d0).days == 7, f'пустая неделя между {occupied[i-1]} и {occupied[i]}'
        # Два маркера отмены присутствуют; курс продлён на 2 недели.
        markers = [r for r in after if r['status'] == 'cancelled']
        assert len(markers) == 2

    def test_cancel_creates_cancelled_marker(self, manager_client, plan_group):
        """Отмена вставляет НЕ-курсовой маркер status='cancelled' на исходную дату
        (календарь показывает зачёркнутое занятие); сам урок уезжает в конец курса."""
        gid = plan_group['group_id']
        plan = _generate(manager_client, gid).json()
        anchor = _by_seq(plan)[3]            # 2026-06-15
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/{anchor["id"]}/cancel', _cancel_body(anchor), format='json',
        )
        assert resp.status_code == 200
        markers = [r for r in resp.json() if r['status'] == 'cancelled']
        assert len(markers) == 1
        m = markers[0]
        assert m['scheduled_date'] == anchor['scheduled_date']   # на исходной дате
        assert m['scheduled_time'] == anchor['scheduled_time']
        assert m['seq'] is None
        assert m['is_extra'] is True

    def test_missing_lesson_404(self, manager_client, plan_group):
        gid = plan_group['group_id']
        _generate(manager_client, gid)
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/99999999/cancel', _cancel_body({'scheduled_date': '2026-06-01'}), format='json',
        )
        assert resp.status_code == 404

    def test_second_cancel_does_not_move_prior_marker(self, manager_client, plan_group):
        """Повторная отмена не двигает прежний маркер 'cancelled' — пересчёт хвоста
        касается только курсовых pending/overdue строк, пины отмен неподвижны."""
        gid = plan_group['group_id']
        plan = _generate(manager_client, gid).json()
        by_seq = _by_seq(plan)
        # первая отмена: seq=5 (2026-06-29) → маркер cancelled на 06-29
        manager_client.post(f'/api/admin/groups/{gid}/plan/{by_seq[5]["id"]}/cancel', _cancel_body(by_seq[5]), format='json')
        # вторая отмена более раннего seq=3 (2026-06-15)
        after = manager_client.post(
            f'/api/admin/groups/{gid}/plan/{by_seq[3]["id"]}/cancel', _cancel_body(by_seq[3]), format='json',
        ).json()
        markers = [r for r in after if r['status'] == 'cancelled']
        assert any(m['scheduled_date'] == '2026-06-29' for m in markers)  # прежний маркер на месте
        assert len(markers) == 2                                          # оба маркера присутствуют

    def test_cancel_marker_row_rejected(self, manager_client, plan_group):
        """Отмена по не-курсовой строке (маркер отмены, seq=NULL) → 400, план не тронут."""
        gid = plan_group['group_id']
        plan = _generate(manager_client, gid).json()
        anchor = _by_seq(plan)[3]
        after = manager_client.post(
            f'/api/admin/groups/{gid}/plan/{anchor["id"]}/cancel', _cancel_body(anchor), format='json',
        ).json()
        marker = next(r for r in after if r['status'] == 'cancelled')
        before = _get_plan(manager_client, gid)
        resp = manager_client.post(
            f'/api/admin/groups/{gid}/plan/{marker["id"]}/cancel', _cancel_body(marker), format='json',
        )
        assert resp.status_code == 400
        after2 = _get_plan(manager_client, gid)
        assert [(r['id'], r['scheduled_date']) for r in after2] == \
               [(r['id'], r['scheduled_date']) for r in before]


# ---------------------------------------------------------------------------
# 5. Журнал ИБ — мутации плана в него НЕ пишутся
# ---------------------------------------------------------------------------

class TestAudit:
    def test_plan_mutations_do_not_touch_security_log(self, manager_client, plan_group):
        """security_audit_log — журнал событий БЕЗОПАСНОСТИ (вход/2FA/учётки).
        Операции над планом — доменные, их место в «Журнале изменений»
        (pghistory, правила plan.* в apps/changelog/labels.py). Раньше каждая
        мутация плана писала сюда plan_* и топила события ИБ в шуме."""
        from apps.audit.models import SecurityAuditLog

        gid = plan_group['group_id']
        before = SecurityAuditLog.objects.count()

        plan = _generate(manager_client, gid).json()
        target = _by_seq(plan)[2]
        manager_client.post(
            f'/api/admin/groups/{gid}/plan/{target["id"]}/reschedule',
            {'new_date': '2026-06-10'}, format='json',
        )
        manager_client.post(
            f'/api/admin/groups/{gid}/plan/permanent-change',
            {'from_seq': 5, 'effective_from': '2026-06-29',
             'new_slots': [{'day_of_week': 3, 'start_time': '14:00'}]},
            format='json',
        )
        # Строку выше переносили — отменяем по её актуальной дате.
        current = next(r for r in _get_plan(manager_client, gid) if r['id'] == target['id'])
        resp = manager_client.post(f'/api/admin/groups/{gid}/plan/{target["id"]}/cancel',
                                   _cancel_body(current), format='json')
        assert resp.status_code == 200, resp.content

        assert SecurityAuditLog.objects.count() == before
        # Скоуп по target_id: eduson_test общая и переживает прогоны, глобальный
        # фильтр мог бы поймать строку от старого запуска и дать ложное падение.
        assert not SecurityAuditLog.objects.filter(
            event__startswith='plan_', target_id=gid).exists()
