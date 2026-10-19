"""
Оркестрация раздела «База знаний».

Сохранение документа — единственное место, где сходятся три вещи: валидация
контента, запись документа и пересборка использований картинок. Всё в одной
транзакции: либо документ и его картинки согласованы, либо не меняется ничего.
"""
from __future__ import annotations

from typing import Optional

from django.db import transaction
from django.utils import timezone

from apps.knowledge import content as content_module
from apps.knowledge import repository
from apps.knowledge.models import KnowledgeDocument


class DocumentNotFound(Exception):
    """Документ не найден или недоступен — вьюха отдаёт 404."""


class SectionNotFound(Exception):
    """Раздел не найден, удалён или неактивен — вьюха отдаёт 404."""


class StaleDocument(Exception):
    """
    Документ изменили, пока его правили в этой вкладке — вьюха отдаёт 409.

    С автосохранением это перестало быть теорией: две открытые вкладки шлют
    правки каждую секунду, и без проверки та, что сохранила позже, молча
    затирала бы чужую работу.
    """

    def __init__(self, updated_at):
        super().__init__('stale')
        self.updated_at = updated_at


@transaction.atomic
def update_document(document_id: int, data: dict, *, account_id: int) -> dict:
    """
    Обновить документ. Бросает DocumentNotFound или content.ContentError.

    Порядок важен: сначала проверяем контент (в том числе что все картинки
    существуют), только потом пишем. Иначе в БД попал бы документ со ссылкой на
    несуществующую картинку.
    """
    document = repository.get_document_for_write(document_id)
    if document is None:
        raise DocumentNotFound()

    # Оптимистическая блокировка: клиент присылает отметку времени той версии,
    # которую правит. Разошлась — значит документ успели изменить в другом
    # месте, и молча затирать чужую правку нельзя.
    base = data.get('base_updated_at')
    if base is not None and not _same_moment(document.updated_at, base):
        raise StaleDocument(document.updated_at)

    # Пишем ТОЛЬКО изменённые поля. Полный UPDATE записывал бы и те, что были
    # прочитаны в начале транзакции: если между чтением и записью кто-то нажал
    # «Опубликовать», сохранение текста откатило бы публикацию обратно в
    # черновик — документ молча пропал бы у читателей.
    changed: list[str] = []
    image_ids: set[int] = set()
    file_ids: set[int] = set()
    images_changed = False
    files_changed = False

    new_content = data.get('content')
    if new_content is not None:
        image_ids = content_module.collect_image_ids(new_content)
        file_ids = content_module.collect_file_ids(new_content)
        content_module.validate_content(
            new_content,
            allowed_image_ids=repository.existing_image_ids(image_ids),
            allowed_file_ids=repository.existing_file_ids(file_ids),
        )
        # Наборы вложений сравниваем ДО перезаписи содержимого. Таблицы
        # использований ведёт только этот код, поэтому прежнее содержимое —
        # достоверный слепок их состояния, и сверка обходится без единого
        # запроса. Без неё каждое автосохранение (а при непрерывном наборе это
        # раз в пять секунд) впустую перебирало связи документа, хотя картинки
        # и файлы в тексте не меняются почти никогда.
        images_changed = image_ids != content_module.collect_image_ids(document.content)
        files_changed = file_ids != content_module.collect_file_ids(document.content)
        document.content = new_content
        document.plain_text = content_module.extract_plain_text(new_content)
        changed += ['content', 'plain_text']

    if 'title' in data:
        document.title = data['title']
        changed.append('title')
    if 'section_id' in data:
        # Раздел проверяем так же, как при создании документа: без этого
        # несуществующий id даёт нарушение внешнего ключа и 500 вместо 404,
        # а мягко удалённый раздел принимается молча — документ оседает в
        # папке, которой больше нет ни в одном списке.
        if repository.get_active_section(data['section_id']) is None:
            raise SectionNotFound()
        document.section_id = data['section_id']
        changed.append('section_id')
    if 'reader_roles' in data:
        document.reader_roles = data['reader_roles']
        changed.append('reader_roles')

    document.updated_by_id = account_id
    changed += ['updated_by', 'updated_at']
    document.save(update_fields=changed)

    if images_changed:
        repository.sync_image_usages(document.id, image_ids)
    if files_changed:
        repository.sync_file_usages(document.id, file_ids)

    return _serialize(document)


def _same_moment(stored, from_client) -> bool:
    """
    Сравнить отметки времени с точностью до миллисекунды.

    Точное равенство здесь не работает: наружу время уходит в формате
    JS-toISOString (apps/core/renderers.py), то есть с миллисекундами, а в
    PostgreSQL лежат микросекунды. Клиент физически не может вернуть то, чего
    не получал, и любое сохранение считалось бы конфликтом.
    """
    return int(stored.timestamp() * 1000) == int(from_client.timestamp() * 1000)


@transaction.atomic
def duplicate_document(document_id: int, *, role: str, account_id: int) -> dict:
    """
    Копия документа — черновик рядом с оригиналом.

    Копируется содержимое и права на чтение, но не статус: копия обязана
    начинаться с черновика, иначе полуготовый дубль сразу уезжает читателям.
    """
    source = repository.get_document(role, document_id)
    if source is None:
        raise DocumentNotFound()

    copy = repository.create_document(
        section_id=source['section_id'],
        title=f"{source['title']} (копия)",
        account_id=account_id,
    )
    copy.content = source['content']
    # Плоский текст пересобираем из содержимого, а не копируем из ответа:
    # наружу он больше не отдаётся (см. repository.get_document). В базе он
    # нужен по-прежнему — из него режется фрагмент для списка и строится
    # поисковый вектор, так что пустым его оставлять нельзя.
    copy.plain_text = content_module.extract_plain_text(source['content'])
    copy.reader_roles = source['reader_roles']
    copy.save(update_fields=['content', 'plain_text', 'reader_roles'])
    repository.sync_image_usages(
        copy.id, content_module.collect_image_ids(source['content']),
    )
    repository.sync_file_usages(
        copy.id, content_module.collect_file_ids(source['content']),
    )
    return _serialize(copy)


@transaction.atomic
def set_published(document_id: int, published: bool, *, account_id: int) -> dict:
    document = repository.get_document_for_write(document_id)
    if document is None:
        raise DocumentNotFound()
    if published:
        document.status = KnowledgeDocument.Status.PUBLISHED
        document.published_at = timezone.now()
    else:
        document.status = KnowledgeDocument.Status.DRAFT
        document.published_at = None
    document.updated_by_id = account_id
    document.save()
    return _serialize(document)


def create_document(*, section_id: int, title: str, account_id: int) -> dict:
    # Та же проверка, что и при переносе документа: раздел должен существовать
    # и быть активным. Проверка живёт здесь, а не во вьюхе, чтобы оба пути —
    # создание и перенос — не могли разойтись.
    if repository.get_active_section(section_id) is None:
        raise SectionNotFound()
    document = repository.create_document(
        section_id=section_id, title=title, account_id=account_id,
    )
    return _serialize(document)


def get_document(role: str, document_id: int) -> Optional[dict]:
    return repository.get_document(role, document_id)


def _serialize(document: KnowledgeDocument) -> dict:
    return {
        'id': document.id,
        'section_id': document.section_id,
        'title': document.title,
        'content': document.content,
        'status': document.status,
        'reader_roles': document.reader_roles,
        'position': document.position,
        'published_at': document.published_at,
        'updated_at': document.updated_at,
    }
