"""E2E-тесты /api/admin/knowledge/documents."""
from __future__ import annotations

import pytest
from django.db import connection

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


def _titles(response) -> list[str]:
    """Названия из страницы списка — форма ответа у проекта {rows, total, ...}."""
    return [row['title'] for row in response.json()['rows']]


def _doc_content(text: str) -> dict:
    return {'type': 'doc', 'content': [
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': text}]},
    ]}


# --- создание и правка -----------------------------------------------------

@pytest.mark.django_db
def test_create_returns_draft(admin_client, section):
    resp = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_регламент'},
        format='json',
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body['status'] == 'draft'
    assert body['reader_roles'] == []


@pytest.mark.django_db
def test_manager_cannot_create(manager_client, section):
    resp = manager_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_x'}, format='json',
    )
    assert resp.status_code == 403


@pytest.mark.django_db
def test_patch_saves_content_and_plain_text(admin_client, section):
    doc = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_d'}, format='json',
    ).json()
    resp = admin_client.patch(
        f"{DOCS}/{doc['id']}", {'content': _doc_content('Текст документа')},
        format='json',
    )
    assert resp.status_code == 200
    assert resp.json()['content']['type'] == 'doc'

    with connection.cursor() as cur:
        cur.execute('SELECT plain_text FROM knowledge_documents WHERE id = %s', [doc['id']])
        assert cur.fetchone()[0] == 'Текст документа'


@pytest.mark.django_db
def test_patch_rejects_unknown_node(admin_client, section):
    doc = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_d'}, format='json',
    ).json()
    bad = {'type': 'doc', 'content': [{'type': 'script', 'content': []}]}
    resp = admin_client.patch(f"{DOCS}/{doc['id']}", {'content': bad}, format='json')
    assert resp.status_code == 400
    assert resp.json()['code'] == 'invalid_content'


@pytest.mark.django_db
def test_patch_sets_reader_roles(admin_client, section):
    doc = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_d'}, format='json',
    ).json()
    resp = admin_client.patch(
        f"{DOCS}/{doc['id']}", {'reader_roles': ['manager', 'teacher']}, format='json',
    )
    assert resp.status_code == 200
    assert resp.json()['reader_roles'] == ['manager', 'teacher']


# --- публикация ------------------------------------------------------------

@pytest.mark.django_db
def test_publish_sets_status_and_timestamp(admin_client, section):
    doc = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_d'}, format='json',
    ).json()
    resp = admin_client.post(f"{DOCS}/{doc['id']}/publish", {}, format='json')
    assert resp.status_code == 200
    body = resp.json()
    assert body['status'] == 'published'
    assert body['published_at'] is not None


@pytest.mark.django_db
def test_unpublish_returns_to_draft(admin_client, section):
    doc = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_d'}, format='json',
    ).json()
    admin_client.post(f"{DOCS}/{doc['id']}/publish", {}, format='json')
    resp = admin_client.post(f"{DOCS}/{doc['id']}/unpublish", {}, format='json')
    assert resp.status_code == 200
    assert resp.json()['status'] == 'draft'


# --- удаление и список -----------------------------------------------------

@pytest.mark.django_db
def test_delete_is_soft(admin_client, section):
    doc = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_d'}, format='json',
    ).json()
    assert admin_client.delete(f"{DOCS}/{doc['id']}").status_code == 204
    assert admin_client.get(f"{DOCS}/{doc['id']}").status_code == 404
    with connection.cursor() as cur:
        cur.execute('SELECT active FROM knowledge_documents WHERE id = %s', [doc['id']])
        assert cur.fetchone()[0] is False


@pytest.mark.django_db
def test_list_is_paginated_and_without_content(admin_client, section):
    admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_d'}, format='json',
    )
    body = admin_client.get(f"{DOCS}?section_id={section['id']}").json()
    assert set(body) >= {'rows', 'total', 'page', 'page_size'}
    assert 'content' not in body['rows'][0]


# ---------------------------------------------------------------------------
# Переименование и перенос
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_rename_keeps_content(admin_client, section):
    """
    Переименование не должно трогать текст: форма шлёт одно поле, и содержимое
    в запросе не участвует вовсе.
    """
    doc = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_старое'}, format='json',
    ).json()
    admin_client.patch(
        f"{DOCS}/{doc['id']}", {'content': _doc_content('текст на месте')}, format='json',
    )

    renamed = admin_client.patch(
        f"{DOCS}/{doc['id']}", {'title': '__test_kb_новое'}, format='json',
    )
    assert renamed.status_code == 200
    assert renamed.json()['title'] == '__test_kb_новое'
    assert 'текст на месте' in str(renamed.json()['content'])


@pytest.mark.django_db
def test_move_to_another_section(admin_client, section):
    other = admin_client.post(
        SECTIONS, {'title': '__test_kb_второй раздел'}, format='json',
    ).json()
    doc = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_док'}, format='json',
    ).json()

    moved = admin_client.patch(
        f"{DOCS}/{doc['id']}", {'section_id': other['id']}, format='json',
    )
    assert moved.status_code == 200
    assert moved.json()['section_id'] == other['id']

    # Документ обязан пропасть из прежнего раздела и появиться в новом —
    # иначе перенос виден только в свойствах, а в списке нет.
    assert '__test_kb_док' not in _titles(admin_client.get(f"{DOCS}?section_id={section['id']}"))
    assert '__test_kb_док' in _titles(admin_client.get(f"{DOCS}?section_id={other['id']}"))


@pytest.mark.django_db
def test_rename_and_move_in_one_request(admin_client, section):
    """Окно свойств меняет оба поля разом — сервер обязан принять их вместе."""
    other = admin_client.post(
        SECTIONS, {'title': '__test_kb_второй раздел'}, format='json',
    ).json()
    doc = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_док'}, format='json',
    ).json()

    body = admin_client.patch(
        f"{DOCS}/{doc['id']}",
        {'title': '__test_kb_переименован', 'section_id': other['id']},
        format='json',
    ).json()
    assert (body['title'], body['section_id']) == ('__test_kb_переименован', other['id'])


@pytest.mark.django_db
def test_move_to_inactive_section_returns_404(admin_client, section):
    """
    Погашенный раздел не должен принимать документы: он не показывается ни в
    одном перечне, и перенесённый туда документ пропал бы из виду.
    """
    other = admin_client.post(
        SECTIONS, {'title': '__test_kb_погашенный'}, format='json',
    ).json()
    admin_client.patch(f"{SECTIONS}/{other['id']}", {'active': False}, format='json')
    doc = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_док'}, format='json',
    ).json()

    resp = admin_client.patch(
        f"{DOCS}/{doc['id']}", {'section_id': other['id']}, format='json',
    )
    assert resp.status_code == 404


@pytest.mark.django_db
def test_blank_title_rejected(admin_client, section):
    doc = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_док'}, format='json',
    ).json()
    resp = admin_client.patch(f"{DOCS}/{doc['id']}", {'title': '   '}, format='json')
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Кривой ввод не должен давать 500, погашенный раздел не должен прятать документы
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_patch_to_missing_section_returns_404(admin_client, section):
    doc = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_d'}, format='json',
    ).json()
    # Раньше давало нарушение внешнего ключа и 500.
    resp = admin_client.patch(f"{DOCS}/{doc['id']}", {'section_id': 10 ** 9}, format='json')
    assert resp.status_code == 404


@pytest.mark.django_db
def test_create_in_inactive_section_returns_404(admin_client, section):
    admin_client.patch(f"{SECTIONS}/{section['id']}", {'active': False}, format='json')
    resp = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_d'}, format='json',
    )
    assert resp.status_code == 404


@pytest.mark.django_db
def test_deactivating_section_with_documents_returns_409(admin_client, section):
    admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_d'}, format='json',
    )
    # Погасить раздел — то же, что удалить: документы иначе остаются доступны,
    # а папки уже нет ни в одном списке.
    resp = admin_client.patch(f"{SECTIONS}/{section['id']}", {'active': False}, format='json')
    assert resp.status_code == 409
    assert resp.json()['error'] == 'has_documents'


@pytest.mark.django_db
def test_non_numeric_section_filter_returns_400(admin_client):
    assert admin_client.get(f'{DOCS}?section_id=abc').status_code == 400


@pytest.mark.django_db
def test_blank_title_rejected(admin_client, section):
    resp = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '   '}, format='json',
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_deleted_document_disappears_from_list(admin_client, section):
    doc = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_del'}, format='json',
    ).json()
    admin_client.delete(f"{DOCS}/{doc['id']}")
    rows = admin_client.get(f"{DOCS}?section_id={section['id']}").json()['rows']
    assert [r['id'] for r in rows] == []


@pytest.mark.django_db
def test_manager_cannot_mutate_document(manager_client, admin_client, section):
    doc = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_d'}, format='json',
    ).json()
    assert manager_client.patch(
        f"{DOCS}/{doc['id']}", {'title': 'взлом'}, format='json',
    ).status_code == 403
    assert manager_client.delete(f"{DOCS}/{doc['id']}").status_code == 403
    assert manager_client.post(
        f"{DOCS}/{doc['id']}/publish", {}, format='json',
    ).status_code == 403


@pytest.mark.django_db
def test_teacher_sees_only_permitted_documents_in_list(teacher_client, admin_client, section):
    allowed = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_видно'}, format='json',
    ).json()
    admin_client.patch(f"{DOCS}/{allowed['id']}", {'reader_roles': ['teacher']}, format='json')
    admin_client.post(f"{DOCS}/{allowed['id']}/publish", {}, format='json')
    admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_скрыто'}, format='json',
    )
    rows = teacher_client.get(f"{DOCS}?section_id={section['id']}").json()['rows']
    assert [r['title'] for r in rows] == ['__test_kb_видно']
