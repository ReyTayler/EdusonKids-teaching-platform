"""
conftest.py тестов reports.

СОЗНАТЕЛЬНО НЕ переопределяем django_db_setup (в отличие от apps.audit и др.):
отчёт по продлениям читает renewal_deal, а персистентная eduson_test отстала по
схеме этой таблицы (легаси-колонка direction_id, см. память проекта «Stale
eduson_test DB»). Как и apps/renewals/tests — даём pytest-django создать свежую
мигрированную test_eduson_test с актуальной схемой.

renewals_fixture — фабрика: throwaway-воронка + стадии + сделка + activity-лог
переходов с явными датами (тот же приём raw-SQL, что apps/renewals/tests/conftest.py).
"""
from __future__ import annotations

import pytest
from django.db import connection


@pytest.fixture
def renewals_fixture(db):
    """Фабрики для наполнения отчёта по продлениям.

    Возвращает объект с методами:
      • pipeline() -> id          — throwaway-воронка (is_default=false);
      • stage(pipeline_id, key, label, kind, is_auto=False) -> id;
      • student(full_name) -> id;
      • account(full_name) -> id  — ответственный;
      • deal(student_id, pipeline_id, stage_id, cycle_no, assignee_id=None,
             closed=False) -> id;
      • activity(deal_id, to_stage_id, created_at, kind='stage_change') -> id.
    """
    created = {
        'activity': [], 'deal': [], 'stage': [], 'pipeline': [],
        'student': [], 'account': [],
    }

    class F:
        def pipeline(self, name='__report_test_pipe__'):
            with connection.cursor() as cur:
                cur.execute(
                    "INSERT INTO renewal_pipeline (name, is_default, created_at) "
                    "VALUES (%s, false, now()) RETURNING id", [name])
                pid = cur.fetchone()[0]
            created['pipeline'].append(pid)
            return pid

        def stage(self, pipeline_id, key, label, kind, is_auto=False, sort_order=0):
            with connection.cursor() as cur:
                cur.execute(
                    "INSERT INTO renewal_stage "
                    "(pipeline_id, key, label, kind, is_auto, sort_order) "
                    "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                    [pipeline_id, key, label, kind, is_auto, sort_order])
                sid = cur.fetchone()[0]
            created['stage'].append(sid)
            return sid

        def student(self, full_name='__report_test_student__'):
            with connection.cursor() as cur:
                cur.execute(
                    "INSERT INTO students (full_name, created_at) "
                    "VALUES (%s, now()) RETURNING id", [full_name])
                sid = cur.fetchone()[0]
            created['student'].append(sid)
            return sid

        def account(self, full_name='__report_test_manager__'):
            from django.contrib.auth.hashers import make_password
            email = f'__report_{len(created["account"])}__@test.local'
            with connection.cursor() as cur:
                cur.execute(
                    "INSERT INTO accounts "
                    "(email, password, role, is_active, is_staff, is_superuser, "
                    " first_name, last_name, full_name, token_version, date_joined) "
                    "VALUES (%s,%s,'manager',true,false,false,'','',%s,0,NOW()) "
                    "RETURNING id",
                    [email, make_password('x'), full_name])
                aid = cur.fetchone()[0]
            created['account'].append(aid)
            return aid

        def deal(self, student_id, pipeline_id, stage_id, cycle_no,
                 assignee_id=None, entered_at=None, closed_at=None, due_at=None):
            """entered_at → stage_entered_at (по умолчанию now()); closed_at →
            outcome_at (None = открытая сделка); due_at → дата созревания цикла.
            Даты — aware datetime / date для проверки реальных дат стадий в отчёте."""
            with connection.cursor() as cur:
                cur.execute(
                    "INSERT INTO renewal_deal "
                    "(student_id, cycle_no, pipeline_id, stage_id, assignee_id, "
                    " stage_entered_at, outcome_at, due_at, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s, COALESCE(%s, now()), %s, %s, now(), now()) "
                    "RETURNING id",
                    [student_id, cycle_no, pipeline_id, stage_id, assignee_id,
                     entered_at, closed_at, due_at])
                did = cur.fetchone()[0]
            created['deal'].append(did)
            return did

        def activity(self, deal_id, to_stage_id, created_at, kind='stage_change'):
            with connection.cursor() as cur:
                cur.execute(
                    "INSERT INTO renewal_activity "
                    "(deal_id, kind, to_stage_id, created_at) "
                    "VALUES (%s,%s,%s,%s) RETURNING id",
                    [deal_id, kind, to_stage_id, created_at])
                aid = cur.fetchone()[0]
            created['activity'].append(aid)
            return aid

    yield F()

    with connection.cursor() as cur:
        for aid in created['activity']:
            cur.execute('DELETE FROM renewal_activity WHERE id = %s', [aid])
        for did in created['deal']:
            cur.execute('DELETE FROM renewal_activity WHERE deal_id = %s', [did])
            cur.execute('DELETE FROM renewal_deal WHERE id = %s', [did])
        for sid in created['stage']:
            cur.execute('DELETE FROM renewal_stage WHERE id = %s', [sid])
        for pid in created['pipeline']:
            cur.execute('DELETE FROM renewal_pipeline WHERE id = %s', [pid])
        for sid in created['student']:
            cur.execute('DELETE FROM students WHERE id = %s', [sid])
        for aid in created['account']:
            cur.execute('DELETE FROM accounts WHERE id = %s', [aid])
