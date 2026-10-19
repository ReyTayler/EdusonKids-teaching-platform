"""
E2E тесты для /api/admin/registry/* (DRF APIClient, реальная eduson_test БД).

Реестр агрегирует всю базу — точные суммы данными-зависимы, поэтому здесь:
auth/RBAC, форма ответа и ТИПЫ, валидация query-параметров, конверт пагинации.
Логика границ сигналов/сортировки — в test_registry_service.py (детерминированно).
"""
from __future__ import annotations

import pytest
from django.contrib.auth.hashers import make_password
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Account

pytestmark = pytest.mark.django_db

SUMMARY = '/api/admin/registry/summary'
STUDENTS = '/api/admin/registry/students'


@pytest.fixture(autouse=True)
def _clear_registry_cache():
    """Каждый тест видит свежую сводку: LocMemCache живёт весь прогон, иначе
    закэшированная между тестами сводка дала бы ложную стабильность."""
    from django.core.cache import cache

    from apps.dashboard.registry_service import SUMMARY_CACHE_KEY
    cache.delete(SUMMARY_CACHE_KEY)
    yield
    cache.delete(SUMMARY_CACHE_KEY)

_ROLE_EMAILS = {
    'admin': '__reg_admin__@example.com',
    'manager': '__reg_manager__@example.com',
    'teacher': '__reg_teacher__@example.com',
}


@pytest.fixture(scope='session')
def django_db_setup():
    pass


def _get_or_create_account(role: str) -> 'Account':
    email = _ROLE_EMAILS[role]
    try:
        return Account.objects.get(email=email)
    except Account.DoesNotExist:
        from django.db import connection as _conn
        with _conn.cursor() as cur:
            teacher_id = None
            if role == 'teacher':
                cur.execute("INSERT INTO teachers (name) VALUES ('__reg_teacher__') RETURNING id")
                teacher_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO accounts (email, password, role, teacher_id, is_active, is_staff, is_superuser, first_name, last_name, date_joined, token_version) "
                "VALUES (%s, %s, %s, %s, true, false, false, '', '', NOW(), 0) RETURNING id",
                [email, make_password('testpass123'), role, teacher_id],
            )
            acc_id = cur.fetchone()[0]
        return Account.objects.get(pk=acc_id)


def _client(role: str | None) -> APIClient:
    c = APIClient()
    if role is not None:
        account = _get_or_create_account(role)
        refresh = RefreshToken.for_user(account)
        refresh['token_version'] = account.token_version
        c.cookies['access'] = str(refresh.access_token)
    return c


# ---------------------------------------------------------------------------
# Auth / RBAC
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('url', [SUMMARY, STUDENTS])
def test_requires_auth(url):
    assert _client(None).get(url).status_code == 401


@pytest.mark.parametrize('url', [SUMMARY, STUDENTS])
def test_teacher_forbidden(url):
    assert _client('teacher').get(url).status_code == 403


# ---------------------------------------------------------------------------
# Summary — форма и типы
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('role', ['manager', 'admin'])
def test_summary_shape(role):
    resp = _client(role).get(SUMMARY)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {'generated_at', 'kpis', 'today_stream', 'signals'}

    assert set(body['kpis'].keys()) == {
        'active_students', 'renewal_upsell', 'idle',
        'avg_progress', 'lessons_ahead', 'cancellations',
    }
    for k, v in body['kpis'].items():
        assert isinstance(v, (int, float)), f'{k} must be number, got {type(v)}'

    assert set(body['signals'].keys()) == {'ending', 'closed', 'idle', 'no_plan'}
    for seg in body['signals'].values():
        assert set(seg.keys()) == {'count'}
        assert isinstance(seg['count'], int)

    assert isinstance(body['today_stream'], list)
    for occ in body['today_stream']:
        assert set(occ.keys()) == {
            'time', 'group_id', 'group_code', 'teacher_name', 'student_names',
            'status', 'duration_minutes',
        }
        # Длительность нужна фронту, чтобы отличить «идёт урок» от «не отмечен».
        assert isinstance(occ['duration_minutes'], int)


def test_summary_kpi_consistency():
    # renewal_upsell = ending + closed (числа из макета: 19 = 10 + 9).
    body = _client('manager').get(SUMMARY).json()
    sig = body['signals']
    assert body['kpis']['renewal_upsell'] == sig['ending']['count'] + sig['closed']['count']
    assert body['kpis']['idle'] == sig['idle']['count']


# ---------------------------------------------------------------------------
# Students — конверт пагинации, форма строки, валидация
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('role', ['manager', 'admin'])
def test_students_envelope(role):
    resp = _client(role).get(STUDENTS, {'page_size': 5})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {'rows', 'total', 'page', 'page_size'}
    assert body['page'] == 1
    assert body['page_size'] == 5
    assert len(body['rows']) <= 5
    for r in body['rows']:
        assert set(r.keys()) == {
            'student_id', 'student_name', 'codes', 'teacher_names', 'balance',
            'attended', 'planned', 'progress_pct', 'last_lesson_date',
            'next_lesson_date', 'status',
        }
        assert isinstance(r['codes'], list)
        assert isinstance(r['balance'], (int, float))
        assert r['status'] in {'closed', 'ending', 'idle', 'no_plan', 'ok'}


def test_students_total_matches_active_students_kpi():
    # total активного списка (segment=all) == KPI active_students.
    active = _client('manager').get(SUMMARY).json()['kpis']['active_students']
    total = _client('manager').get(STUDENTS, {'page_size': 1}).json()['total']
    assert total == active


def test_students_segment_narrows_result():
    total_all = _client('manager').get(STUDENTS).json()['total']
    total_closed = _client('manager').get(STUDENTS, {'segment': 'closed'}).json()['total']
    assert total_closed <= total_all


@pytest.mark.parametrize('params', [
    {'segment': 'bogus'},
    {'sort_by': 'bogus'},
    {'sort_dir': 'sideways'},
    {'no_group': 'maybe'},
])
def test_students_invalid_params_400(params):
    assert _client('manager').get(STUDENTS, params).status_code == 400


# ── Режим «Без группы» (no_group=1) ──────────────────────────────────────────

def test_students_no_group_same_row_shape():
    """Колонки те же — фронт рисует ту же таблицу, договор ответа не меняется."""
    body = _client('manager').get(STUDENTS, {'no_group': 1, 'page_size': 5}).json()
    for r in body['rows']:
        assert set(r.keys()) == {
            'student_id', 'student_name', 'codes', 'teacher_names', 'balance',
            'attended', 'planned', 'progress_pct', 'last_lesson_date',
            'next_lesson_date', 'status',
        }
        assert r['status'] in {'closed', 'ending', 'idle', 'no_plan', 'ok'}
        # Без активного членства взяться коду группы и преподавателю неоткуда.
        assert r['codes'] == []
        assert r['teacher_names'] == []


def test_students_no_group_partitions_the_school():
    """
    total обычного списка + total «Без группы» == всего учеников в базе.
    Данными-независимый инвариант: ученик не может ни задвоиться, ни пропасть.
    """
    from apps.students.models import Student

    c = _client('manager')
    with_group = c.get(STUDENTS, {'page_size': 1}).json()['total']
    without = c.get(STUDENTS, {'no_group': 1, 'page_size': 1}).json()['total']
    assert with_group + without == Student.objects.count()


def test_students_no_group_default_is_off():
    """Без параметра — прежнее поведение (флаг по умолчанию выключен)."""
    c = _client('manager')
    assert c.get(STUDENTS, {'page_size': 1}).json()['total'] == \
        c.get(STUDENTS, {'no_group': 0, 'page_size': 1}).json()['total']


# ── Инварианты БД-пагинации/сортировки (вариант B), не зависят от данных ──

def test_students_sorted_by_balance_asc_is_nondecreasing():
    body = _client('manager').get(
        STUDENTS, {'sort_by': 'balance', 'sort_dir': 'asc', 'page_size': 100},
    ).json()
    balances = [r['balance'] for r in body['rows']]
    assert balances == sorted(balances)


def test_students_sorted_by_balance_desc_is_nonincreasing():
    body = _client('manager').get(
        STUDENTS, {'sort_by': 'balance', 'sort_dir': 'desc', 'page_size': 100},
    ).json()
    balances = [r['balance'] for r in body['rows']]
    assert balances == sorted(balances, reverse=True)


def test_students_closed_segment_rows_have_nonpositive_balance():
    body = _client('manager').get(STUDENTS, {'segment': 'closed', 'page_size': 100}).json()
    for r in body['rows']:
        assert r['balance'] <= 0


def test_students_ending_segment_rows_have_balance_in_range():
    body = _client('manager').get(STUDENTS, {'segment': 'ending', 'page_size': 100}).json()
    for r in body['rows']:
        assert 0 < r['balance'] <= 2


def test_students_pagination_disjoint_pages():
    c = _client('manager')
    p1 = c.get(STUDENTS, {'page': 1, 'page_size': 5, 'sort_by': 'name'}).json()
    if p1['total'] <= 5:
        return  # в eduson_test мало активных учеников — 2-й страницы нет
    p2 = c.get(STUDENTS, {'page': 2, 'page_size': 5, 'sort_by': 'name'}).json()
    ids1 = {r['student_id'] for r in p1['rows']}
    ids2 = {r['student_id'] for r in p2['rows']}
    assert ids1.isdisjoint(ids2)  # страницы не пересекаются
