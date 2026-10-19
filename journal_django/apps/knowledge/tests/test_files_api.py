"""
API прикреплённых файлов: загрузка, скачивание, права.

Отдельно от test_images.py: у файлов другая проверка содержимого, другие
заголовки ответа и другое правило именования — общее только хранилище.
"""
from __future__ import annotations

from urllib.parse import quote

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.knowledge.models import KnowledgeFile
from apps.knowledge.tests.conftest import cleanup_kb

FILES = '/api/admin/knowledge/files'
DOCS = '/api/admin/knowledge/documents'
SECTIONS = '/api/admin/knowledge/sections'

PDF = b'%PDF-1.7\n1 0 obj\n<<>>\nendobj\n'
EXE = b'MZ\x90\x00' + b'\x00' * 40


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


def _upload(client, tmp_path, payload=PDF, name='__test_kb_Договор оферты.pdf'):
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        return client.post(
            FILES,
            {'file': SimpleUploadedFile(name, payload, content_type='application/pdf')},
            format='multipart',
        )


# --- загрузка ---------------------------------------------------------------

@pytest.mark.django_db
def test_upload_returns_metadata(admin_client, tmp_path):
    response = _upload(admin_client, tmp_path)
    assert response.status_code == 201
    body = response.json()
    assert body['name'] == '__test_kb_Договор оферты.pdf'
    assert body['mime'] == 'application/pdf'
    assert body['byte_size'] == len(PDF)


@pytest.mark.django_db
def test_upload_rejects_wrong_content(admin_client, tmp_path):
    """Исполняемый файл, переименованный в .pdf, дальше приёма не проходит."""
    response = _upload(admin_client, tmp_path, payload=EXE)
    assert response.status_code == 415
    assert not KnowledgeFile.objects.exists()


@pytest.mark.django_db
def test_upload_rejects_unknown_extension(admin_client, tmp_path):
    response = _upload(admin_client, tmp_path, name='__test_kb_вирус.exe')
    assert response.status_code == 415


@pytest.mark.django_db
def test_upload_deduplicates_by_content(admin_client, tmp_path):
    first = _upload(admin_client, tmp_path, name='__test_kb_а.pdf').json()
    second = _upload(admin_client, tmp_path, name='__test_kb_б.pdf').json()
    assert first['id'] == second['id']
    assert KnowledgeFile.objects.count() == 1


@pytest.mark.django_db
def test_upload_forbidden_for_manager(manager_client, tmp_path):
    """Прикреплять файлы — мутация, она только для администраторов."""
    assert _upload(manager_client, tmp_path).status_code == 403


@pytest.mark.django_db
def test_upload_too_large_answers_413_with_readable_json(admin_client, tmp_path):
    """
    Отказ по размеру обязан приходить нашим JSON с русским текстом, а не голым
    статусом: этот текст фронт показывает пользователю дословно.

    Предел на время теста опущен, чтобы не гонять по сети 25 МБ.
    """
    with override_settings(KNOWLEDGE_MAX_FILE_BYTES=16):
        response = _upload(admin_client, tmp_path, payload=PDF + b'x' * 100)

    assert response.status_code == 413
    assert 'больше' in response.json()['error']
    assert not KnowledgeFile.objects.exists()


# --- скачивание -------------------------------------------------------------

@pytest.mark.django_db
def test_download_forces_attachment(admin_client, tmp_path):
    """
    Файл обязан скачиваться, а не открываться: отданный с нашего домена, он
    иначе исполнялся бы в контексте нашего сайта.
    """
    file_id = _upload(admin_client, tmp_path).json()['id']

    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path), KNOWLEDGE_X_ACCEL_PREFIX=''):
        response = admin_client.get(f'{FILES}/{file_id}')

    assert response.status_code == 200
    assert response['Content-Disposition'].startswith('attachment;')
    assert response['X-Content-Type-Options'] == 'nosniff'


@pytest.mark.django_db
def test_download_encodes_cyrillic_name(admin_client, tmp_path):
    """Кириллица в имени уходит по RFC 5987, а не сырыми байтами в заголовке."""
    file_id = _upload(admin_client, tmp_path).json()['id']

    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path), KNOWLEDGE_X_ACCEL_PREFIX=''):
        disposition = admin_client.get(f'{FILES}/{file_id}')['Content-Disposition']

    assert f"filename*=UTF-8''{quote('__test_kb_Договор оферты.pdf', safe='')}" in disposition
    # Запасное имя — только ASCII: старые клиенты читают именно его.
    assert disposition.split('"')[1].isascii()


@pytest.mark.django_db
def test_download_uses_x_accel_when_configured(admin_client, tmp_path):
    file_id = _upload(admin_client, tmp_path).json()['id']

    with override_settings(
        KNOWLEDGE_MEDIA_ROOT=str(tmp_path), KNOWLEDGE_X_ACCEL_PREFIX='/internal-media',
    ):
        response = admin_client.get(f'{FILES}/{file_id}')

    assert response['X-Accel-Redirect'].startswith('/internal-media/knowledge-files/')


@pytest.mark.django_db
def test_download_404_for_unknown_file(admin_client):
    assert admin_client.get(f'{FILES}/999999').status_code == 404


# --- права ------------------------------------------------------------------

@pytest.mark.django_db
def test_file_hidden_until_used_in_visible_document(
    admin_client, teacher_client, section, tmp_path,
):
    """
    Преподаватель видит файл только через документ, который ему доступен.
    Прямая ссылка в обход прав отдаёт 404 — существование файла тоже не
    разглашаем.
    """
    file_id = _upload(admin_client, tmp_path).json()['id']

    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path), KNOWLEDGE_X_ACCEL_PREFIX=''):
        assert teacher_client.get(f'{FILES}/{file_id}').status_code == 404

    doc = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_док'}, format='json',
    ).json()
    admin_client.patch(
        f"{DOCS}/{doc['id']}",
        {
            'content': {'type': 'doc', 'content': [
                {'type': 'knowledgeFile', 'attrs': {
                    'fileId': file_id, 'name': '__test_kb_Договор оферты.pdf',
                    'size': len(PDF), 'mime': 'application/pdf',
                }},
            ]},
            'reader_roles': ['teacher'],
        },
        format='json',
    )
    admin_client.post(f"{DOCS}/{doc['id']}/publish", {}, format='json')

    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path), KNOWLEDGE_X_ACCEL_PREFIX=''):
        assert teacher_client.get(f'{FILES}/{file_id}').status_code == 200


@pytest.mark.django_db
def test_document_rejects_unknown_file_id(admin_client, section):
    """Ссылка на несуществующий файл в документ не сохраняется."""
    doc = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': '__test_kb_док'}, format='json',
    ).json()
    response = admin_client.patch(
        f"{DOCS}/{doc['id']}",
        {'content': {'type': 'doc', 'content': [
            {'type': 'knowledgeFile', 'attrs': {'fileId': 999999}},
        ]}},
        format='json',
    )
    assert response.status_code == 400
    assert response.json()['code'] == 'invalid_content'
