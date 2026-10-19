"""
Матрица видимости документов: роль × статус × reader_roles.

Читается так: admin и superadmin видят всё; manager и teacher — только
опубликованное, где их роль явно указана. Недоступный документ отдаётся как
404, а не 403.
"""
from __future__ import annotations

import pytest

from apps.knowledge.tests.conftest import cleanup_kb

DOCS = '/api/admin/knowledge/documents'
SECTIONS = '/api/admin/knowledge/sections'


@pytest.fixture(autouse=True)
def _clean():
    cleanup_kb()
    yield
    cleanup_kb()


@pytest.fixture
def section(admin_client):
    return admin_client.post(
        SECTIONS, {'title': '__test_kb_раздел'}, format='json',
    ).json()


def _make_document(admin_client, section, *, title, roles, published):
    doc = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': title}, format='json',
    ).json()
    admin_client.patch(f"{DOCS}/{doc['id']}", {'reader_roles': roles}, format='json')
    if published:
        admin_client.post(f"{DOCS}/{doc['id']}/publish", {}, format='json')
    return doc


# --- detail ----------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize(
    'roles,published,manager_expects,teacher_expects',
    [
        (['manager'], True, 200, 404),          # опубликован, только менеджерам
        (['teacher'], True, 404, 200),          # опубликован, только преподавателям
        (['manager', 'teacher'], True, 200, 200),
        (['manager'], False, 404, 404),         # черновик не виден никому, кроме админов
        ([], True, 404, 404),                   # опубликован, но роли не выданы
    ],
)
def test_visibility_matrix(
    admin_client, manager_client, teacher_client, section,
    roles, published, manager_expects, teacher_expects,
):
    doc = _make_document(
        admin_client, section, title='__test_kb_матрица', roles=roles, published=published,
    )
    url = f"{DOCS}/{doc['id']}"
    assert manager_client.get(url).status_code == manager_expects
    assert teacher_client.get(url).status_code == teacher_expects
    # admin и superadmin видят документ при любых настройках
    assert admin_client.get(url).status_code == 200


@pytest.mark.django_db
def test_superadmin_sees_draft(superadmin_client, admin_client, section):
    doc = _make_document(
        admin_client, section, title='__test_kb_черновик', roles=[], published=False,
    )
    assert superadmin_client.get(f"{DOCS}/{doc['id']}").status_code == 200


# --- list ------------------------------------------------------------------

@pytest.mark.django_db
def test_list_hides_invisible_documents(admin_client, manager_client, section):
    _make_document(
        admin_client, section, title='__test_kb_видимый',
        roles=['manager'], published=True,
    )
    _make_document(
        admin_client, section, title='__test_kb_скрытый',
        roles=['teacher'], published=True,
    )
    rows = manager_client.get(f"{DOCS}?section_id={section['id']}").json()['rows']
    titles = [r['title'] for r in rows]
    assert titles == ['__test_kb_видимый']


@pytest.mark.django_db
def test_admin_list_includes_drafts(admin_client, section):
    _make_document(
        admin_client, section, title='__test_kb_черновик', roles=[], published=False,
    )
    rows = admin_client.get(f"{DOCS}?section_id={section['id']}").json()['rows']
    assert [r['title'] for r in rows] == ['__test_kb_черновик']


# --- запись ----------------------------------------------------------------

@pytest.mark.django_db
def test_teacher_cannot_write(teacher_client, admin_client, section):
    doc = _make_document(
        admin_client, section, title='__test_kb_д', roles=['teacher'], published=True,
    )
    assert teacher_client.patch(
        f"{DOCS}/{doc['id']}", {'title': 'взлом'}, format='json',
    ).status_code == 403
    assert teacher_client.delete(f"{DOCS}/{doc['id']}").status_code == 403
    assert teacher_client.post(
        f"{DOCS}/{doc['id']}/publish", {}, format='json',
    ).status_code == 403


@pytest.mark.django_db
def test_anonymous_gets_401(anon_client, admin_client, section):
    doc = _make_document(
        admin_client, section, title='__test_kb_д', roles=['teacher'], published=True,
    )
    assert anon_client.get(f"{DOCS}/{doc['id']}").status_code == 401
