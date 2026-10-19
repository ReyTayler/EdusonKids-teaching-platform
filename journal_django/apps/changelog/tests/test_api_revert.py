"""POST /api/admin/changelog/<uuid>/revert — только admin/superadmin, аудит в security_audit_log."""
from __future__ import annotations

import pghistory
import pytest
from django.utils import timezone

from apps.audit.models import SecurityAuditLog

pytestmark = pytest.mark.django_db


def _last_op_id(client):
    return client.get('/api/admin/changelog?page_size=1').json()['rows'][0]['id']


def _create_direction(client):
    # Запись направлений — только superadmin (ReadStaffWriteSuperAdmin), поэтому
    # генератор события в этих тестах — superadmin_client; сам revert
    # проверяется отдельным клиентом (admin/manager/teacher/superadmin).
    resp = client.post('/api/admin/directions', {
        'name': '__chg_api_rev__',
    }, format='json')
    assert resp.status_code in (200, 201)


def test_revert_endpoint_success(admin_client, superadmin_client):
    """Admin откатывает операцию, сгенерированную superadmin'ом — разрешено."""
    _create_direction(superadmin_client)
    op_id = _last_op_id(admin_client)
    resp = admin_client.post(f'/api/admin/changelog/{op_id}/revert')
    assert resp.status_code == 200, resp.content
    assert resp.json()['reverted_events'] == 1
    # Откат — доменное действие: в журнал ИБ он не пишется (фиксируется самим
    # «Журналом изменений» операцией 'changelog.revert' в metadata.operation).
    assert not SecurityAuditLog.objects.filter(event='changelog_revert').exists()


def test_revert_endpoint_superadmin_allowed(superadmin_client):
    """Superadmin откатывает собственную операцию — разрешено."""
    _create_direction(superadmin_client)
    op_id = _last_op_id(superadmin_client)
    resp = superadmin_client.post(f'/api/admin/changelog/{op_id}/revert')
    assert resp.status_code == 200, resp.content


def test_revert_endpoint_conflict_409(admin_client, superadmin_client):
    from apps.directions.models import Direction
    _create_direction(superadmin_client)
    op_id = _last_op_id(admin_client)
    # Позднее изменение — свой контекст (в проде это другой запрос)
    with pghistory.context(url='/t2', method='PATCH'):
        Direction.objects.filter(name='__chg_api_rev__').update(name='__chg_api_rev2__')
    resp = admin_client.post(f'/api/admin/changelog/{op_id}/revert')
    assert resp.status_code == 409
    assert resp.json()['details']['conflicts']


def test_revert_endpoint_rbac(admin_client, manager_client, teacher_client, superadmin_client):
    """Manager/teacher — 403; admin — разрешено (не 403)."""
    _create_direction(superadmin_client)
    op_id = _last_op_id(admin_client)
    assert manager_client.post(f'/api/admin/changelog/{op_id}/revert').status_code == 403
    assert teacher_client.post(f'/api/admin/changelog/{op_id}/revert').status_code == 403
    resp = admin_client.post(f'/api/admin/changelog/{op_id}/revert')
    assert resp.status_code != 403


def test_revert_refund_removes_all_direction_rows(admin_client):
    """Возврат пишет строку на каждое направление — откат обязан снять их все разом.

    Строки создаются в одном запросе → один pghistory-контекст → одна операция.
    """
    from apps.directions.models import Direction
    from apps.finances.repository import balance_for_student
    from apps.payments.models import Payment
    from apps.students.models import Student

    student = Student.objects.create(full_name='__rev_refund_student__',
                                     created_at=timezone.now())
    dir_a = Direction.objects.create(name='__rev_refund_a__', total_lessons=8)
    dir_b = Direction.objects.create(name='__rev_refund_b__', total_lessons=8)
    for direction in (dir_a, dir_b):
        resp = admin_client.post('/api/admin/payments', {
            'student_id': student.id, 'direction_id': direction.id,
            'lessons_count': 4, 'total_amount': '4000.00', 'paid_at': '2026-01-01',
        }, format='json')
        assert resp.status_code == 201, resp.content

    refund = admin_client.post(f'/api/admin/students/{student.id}/refund', {}, format='json')
    assert refund.status_code == 201, refund.content
    assert len(refund.json()['refunds']) == 2
    assert balance_for_student(student.id) == 0

    # Не _last_op_id: внутри одного теста все контексты создаются в одной
    # транзакции, поэтому created_at у них совпадает и порядок ленты решает uuid.
    op_id = next(
        r['id'] for r in admin_client.get('/api/admin/changelog?page_size=10').json()['rows']
        if r['operation'] == 'payment.refund'
    )
    resp = admin_client.post(f'/api/admin/changelog/{op_id}/revert')

    assert resp.status_code == 200, resp.content
    assert resp.json()['inserts_undone'] == 2
    assert not Payment.objects.filter(student=student, kind='refund').exists()
    assert balance_for_student(student.id) == 8


def test_revert_endpoint_double_revert_400(superadmin_client):
    """Повторный откат той же операции через API → 400."""
    _create_direction(superadmin_client)
    op_id = _last_op_id(superadmin_client)
    assert superadmin_client.post(f'/api/admin/changelog/{op_id}/revert').status_code == 200
    resp = superadmin_client.post(f'/api/admin/changelog/{op_id}/revert')
    assert resp.status_code == 400
    assert resp.json()['error']


def test_revert_endpoint_revert_of_revert_400(superadmin_client):
    """Откат самой revert-операции через API → 400."""
    _create_direction(superadmin_client)
    op_id = _last_op_id(superadmin_client)
    assert superadmin_client.post(f'/api/admin/changelog/{op_id}/revert').status_code == 200
    # revert-операция теперь на вершине ленты
    revert_op_id = next(
        r['id'] for r in superadmin_client.get('/api/admin/changelog?page_size=5').json()['rows']
        if r['operation'] == 'changelog.revert'
    )
    resp = superadmin_client.post(f'/api/admin/changelog/{revert_op_id}/revert')
    assert resp.status_code == 400
    assert resp.json()['error']


def test_revert_endpoint_404(admin_client):
    resp = admin_client.post(
        '/api/admin/changelog/00000000-0000-0000-0000-000000000000/revert')
    assert resp.status_code == 404
