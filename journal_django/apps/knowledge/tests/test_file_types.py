"""
Проверка содержимого прикрепляемого файла.

Проверяется именно то, ради чего заведён двойной барьер: расширение и сигнатура
должны совпасть ОБА. Тесты работают без базы и без HTTP — это чистые функции.
"""
from __future__ import annotations

import io

import pytest
from django.test import override_settings

from apps.knowledge import file_types, storage

PDF = b'%PDF-1.7\n1 0 obj\n<<>>\nendobj\n'
ZIP = b'PK\x03\x04' + b'\x00' * 30
OLE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 24
EXE = b'MZ\x90\x00' + b'\x00' * 40


def _store(tmp_path, payload: bytes, name: str):
    """Принять файл через общий слой — как это делает вьюха загрузки."""
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        return storage.store_upload(
            io.BytesIO(payload),
            prefix='knowledge-files',
            max_bytes=25 * 1024 * 1024,
            probe=file_types.probe_by_name(name),
            original_name=name,
        )


# --- расширение ------------------------------------------------------------

def test_accepts_pdf(tmp_path):
    blob = _store(tmp_path, PDF, 'Договор оферты.pdf')
    assert blob.mime == 'application/pdf'
    assert (tmp_path / blob.relative_path).exists()


def test_rejects_unknown_extension(tmp_path):
    with pytest.raises(storage.UploadRejected, match='расширением'):
        _store(tmp_path, PDF, 'файл.xyz')


def test_rejects_file_without_extension(tmp_path):
    with pytest.raises(storage.UploadRejected, match='расширением'):
        _store(tmp_path, PDF, 'README')


@pytest.mark.parametrize('name', ['макрос.docm', 'книга.xlsm', 'показ.pptm'])
def test_rejects_macro_enabled_office(tmp_path, name):
    """Документы Office с макросами — исполняемый код в обёртке документа."""
    with pytest.raises(storage.UploadRejected, match='расширением'):
        _store(tmp_path, ZIP, name)


@pytest.mark.parametrize('name', ['вирус.exe', 'скрипт.js', 'страница.html', 'запуск.bat'])
def test_rejects_executables_and_markup(tmp_path, name):
    with pytest.raises(storage.UploadRejected, match='расширением'):
        _store(tmp_path, EXE, name)


# --- сигнатура -------------------------------------------------------------

def test_rejects_executable_renamed_to_pdf(tmp_path):
    """Главный случай, ради которого сигнатура и нужна."""
    with pytest.raises(storage.UploadRejected, match='не похоже'):
        _store(tmp_path, EXE, 'договор.pdf')


def test_rejects_signature_not_at_the_start(tmp_path):
    """
    Сигнатуру ищем в начале файла, а не где угодно: иначе подошёл бы любой
    файл, где «%PDF-» встретилось в середине.
    """
    with pytest.raises(storage.UploadRejected, match='не похоже'):
        _store(tmp_path, b'\x00' * 64 + PDF, 'договор.pdf')


def test_ooxml_accepted_by_zip_signature(tmp_path):
    """docx, xlsx, pptx и OpenDocument — это zip-архивы."""
    for name in ('методичка.docx', 'таблица.xlsx', 'показ.pptx', 'текст.odt'):
        assert _store(tmp_path, ZIP, name).byte_size == len(ZIP)


def test_old_office_accepted_by_ole_signature(tmp_path):
    assert _store(tmp_path, OLE, 'старый.doc').mime == 'application/msword'


def test_text_accepted_without_signature(tmp_path):
    """У обычного текста опознавательных байтов не бывает."""
    blob = _store(tmp_path, 'имя;класс\nИван;5А\n'.encode('utf-8'), 'список.csv')
    assert blob.mime == 'text/csv'


# --- общий слой ------------------------------------------------------------

def test_rejects_empty_file(tmp_path):
    with pytest.raises(storage.UploadRejected, match='пустой'):
        _store(tmp_path, b'', 'пусто.pdf')


def test_rejects_oversized(tmp_path):
    with override_settings(KNOWLEDGE_MEDIA_ROOT=str(tmp_path)):
        with pytest.raises(storage.UploadTooLarge):
            storage.store_upload(
                io.BytesIO(PDF + b'\x00' * 2048),
                prefix='knowledge-files',
                max_bytes=1024,
                probe=file_types.probe_by_name('большой.pdf'),
            )


def test_same_content_stored_once(tmp_path):
    """Адресация содержимым: два одинаковых файла — один файл на диске."""
    first = _store(tmp_path, PDF, 'первый.pdf')
    second = _store(tmp_path, PDF, 'второй.pdf')
    assert first.sha256 == second.sha256
    assert first.relative_path == second.relative_path


# --- имя файла -------------------------------------------------------------

def test_name_keeps_cyrillic_and_spaces():
    assert file_types.check_name('Договор оферты 2026.pdf') == 'Договор оферты 2026.pdf'


def test_name_drops_path():
    """Некоторые браузеры шлют полный путь; из него собирается выход из каталога."""
    assert file_types.check_name(r'C:\Users\ilya\..\..\секрет.pdf') == 'секрет.pdf'
    assert file_types.check_name('/etc/passwd.txt') == 'passwd.txt'


def test_name_drops_control_characters():
    """Перевод строки в имени — это подстановка заголовков при скачивании."""
    assert file_types.check_name('файл\r\nX-Injected: 1.pdf') == 'файлX-Injected: 1.pdf'


def test_name_is_capped():
    long_name = 'я' * 400 + '.pdf'
    assert len(file_types.check_name(long_name)) == file_types.MAX_NAME_CHARS


def test_empty_name_rejected():
    with pytest.raises(storage.UploadRejected, match='нет имени'):
        file_types.check_name('   ')
