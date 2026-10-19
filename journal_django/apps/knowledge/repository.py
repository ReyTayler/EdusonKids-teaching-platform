"""
Единственное место доступа к данным раздела «База знаний».

Ключевая функция — visible_documents_qs: фильтр видимости по роли. Условие
уходит в SQL и попадает в GIN-индекс по reader_roles, никаких Python-циклов
по выборке.
"""
from __future__ import annotations

from typing import Optional

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import (
    BooleanField, Count, Exists, F, OuterRef, Q, QuerySet, TextField, Value,
)
from django.db.models.functions import Coalesce, Substr

from apps.knowledge.models import (
    FULL_ACCESS_ROLES,
    KnowledgeDocument,
    KnowledgeFavorite,
    KnowledgeFile,
    KnowledgeFileUsage,
    KnowledgeImage,
    KnowledgeImageUsage,
    KnowledgeSection,
)


# ---------------------------------------------------------------------------
# Разделы
# ---------------------------------------------------------------------------

def list_sections(include_inactive: bool = False, *, role: Optional[str] = None) -> list[dict]:
    """
    Разделы, при передаче role — со счётчиком видимых этой роли документов.

    Счёт делает БД одним запросом с группировкой. Раньше экран получал его
    иначе: тянул ВСЕ документы (до 500 строк, 293 КБ ответа) и считал их на
    клиенте. Числа рядом с папками — единственное, ради чего это делалось, и
    ради них же выгрузка росла линейно с базой знаний.

    Читателю (все, кроме admin/superadmin) отдаются только те разделы, где
    есть хотя бы один доступный ему документ. Иначе дерево показывало бы
    преподавателю всю структуру школы — «Финансы», «Продажи», — пусть и с
    нулём внутри: содержимое закрыто, а состав папок нет.
    """
    qs = KnowledgeSection.objects.all()
    full_access = role in FULL_ACCESS_ROLES
    # include_inactive — инструмент администратора (восстановление удалённого).
    # Читателю погашенный раздел не показываем в любом случае: его документы
    # всё равно скрыты (visible_documents_qs требует section__active), то есть
    # папка приехала бы заведомо пустой.
    if not include_inactive or (role is not None and not full_access):
        qs = qs.filter(active=True)
    if role is not None:
        # filter внутри Count, а не .filter() по queryset: последний превратил бы
        # LEFT JOIN во внутреннее соединение, и пустые разделы пропали бы из
        # списка вместо того, чтобы показать ноль.
        qs = qs.annotate(document_count=Count(
            'documents', filter=_visible_documents_condition(role), distinct=True,
        ))
        if not full_access:
            # Отсечка по уже посчитанному числу (HAVING в той же группировке),
            # а не отдельной проверкой на каждую папку: список разделов обязан
            # стоить один запрос независимо от их количества.
            qs = qs.filter(document_count__gt=0)
    fields = ['id', 'title', 'position', 'active']
    if role is not None:
        fields.append('document_count')
    return list(qs.order_by('position', 'title').values(*fields))


def _visible_documents_condition(role: str) -> Q:
    """
    Условие видимости документа для роли — как Q, пригодный внутри агрегата.

    Повторяет visible_documents_qs, но в виде выражения: там фильтруется
    queryset документов, здесь — считаются документы внутри выборки разделов.
    Логика обязана совпадать, иначе счётчик у папки разойдётся с её
    содержимым.
    """
    condition = Q(documents__active=True)
    if role in FULL_ACCESS_ROLES:
        return condition
    return condition & Q(
        documents__status=KnowledgeDocument.Status.PUBLISHED,
        documents__reader_roles__contains=[role],
    )


def count_visible_documents(role: str) -> int:
    """Сколько документов видит роль всего — число рядом с «Все документы»."""
    return visible_documents_qs(role).count()


def get_section(section_id: int) -> Optional[dict]:
    return KnowledgeSection.objects.filter(id=section_id).values(
        'id', 'title', 'position', 'active',
    ).first()


def get_visible_section(role: str, section_id: int) -> Optional[dict]:
    """
    Раздел, если роли его вообще положено видеть. Иначе None — вьюха отдаст 404.

    Правило то же, что в list_sections: читателю раздел существует, только
    пока внутри есть хотя бы один доступный ему документ. Без этой проверки
    ручка по id обходила бы дерево и сообщала название любой папки школы
    любому сотруднику.

    Условие берётся у visible_documents_qs напрямую, а не повторяется здесь
    третьим списком: разойдись они — и раздел был бы доступен по ссылке
    ровно тогда, когда его нет в списке.
    """
    if role in FULL_ACCESS_ROLES:
        return get_section(section_id)
    return (
        KnowledgeSection.objects
        .filter(id=section_id, active=True)
        .filter(Exists(visible_documents_qs(role).filter(section_id=OuterRef('pk'))))
        .values('id', 'title', 'position', 'active')
        .first()
    )


def get_active_section(section_id) -> Optional[dict]:
    """
    Живой раздел или None. Отдельно от get_section: класть документы можно
    только в активный раздел, иначе они оседают в папке, которой больше нет ни
    в одном списке — найти их потом можно лишь через «Все документы».

    section_id приходит из запроса, поэтому нечисловое значение здесь же
    превращается в None, а не в исключение уровня БД.
    """
    try:
        section_id = int(section_id)
    except (TypeError, ValueError):
        return None
    return KnowledgeSection.objects.filter(id=section_id, active=True).values(
        'id', 'title', 'position', 'active',
    ).first()


def create_section(title: str) -> dict:
    last = KnowledgeSection.objects.order_by('-position').values_list(
        'position', flat=True,
    ).first()
    obj = KnowledgeSection.objects.create(
        title=title, position=(last or 0) + 1,
    )
    return get_section(obj.id)


def update_section(section_id: int, data: dict) -> Optional[dict]:
    obj = KnowledgeSection.objects.filter(id=section_id).first()
    if obj is None:
        return None
    if 'title' in data:
        obj.title = data['title']
    if 'active' in data:
        obj.active = data['active']
    obj.save()
    return get_section(section_id)


def count_active_documents(section_id: int) -> int:
    """Активные документы раздела — проверка перед удалением (409)."""
    return KnowledgeDocument.objects.filter(
        section_id=section_id, active=True,
    ).count()


def soft_delete_section(section_id: int) -> bool:
    return KnowledgeSection.objects.filter(id=section_id).update(active=False) > 0


def reorder_sections(order: list[int]) -> None:
    for position, section_id in enumerate(order, start=1):
        KnowledgeSection.objects.filter(id=section_id).update(position=position)


# ---------------------------------------------------------------------------
# Документы
# ---------------------------------------------------------------------------

def visible_documents_qs(role: str) -> QuerySet:
    """
    Документы, доступные роли.

    admin/superadmin видят всё, включая черновики и документы с пустым
    reader_roles: иначе документ теряется, стоит снять все галочки.
    Остальные — только опубликованное, где их роль явно указана.
    """
    # section__active — не формальность: раздел можно погасить (PATCH active),
    # и без этого условия его документы остались бы доступны по прямой ссылке и
    # в списке «Все документы», хотя папки уже нет ни в одном перечне.
    qs = KnowledgeDocument.objects.filter(active=True, section__active=True)
    if role in FULL_ACCESS_ROLES:
        return qs
    return qs.filter(
        status=KnowledgeDocument.Status.PUBLISHED,
        reader_roles__contains=[role],
    )


# Поля списка: content намеренно не выбирается — незачем тянуть мегабайты jsonb.
_LIST_FIELDS = (
    'id', 'section_id', 'title', 'status', 'reader_roles',
    'position', 'published_at', 'updated_at',
)


# Длина фрагмента для карточки в списке. Обрезка идёт в SQL (Substr), а не в
# Python: иначе ради 160 символов пришлось бы вытащить plain_text целиком по
# всем документам сразу.
EXCERPT_CHARS = 160


def list_documents(
    role: str,
    section_id: Optional[int] = None,
    *,
    account_id: Optional[int] = None,
    query: str = '',
    status: str = '',
    scope: str = 'all',
) -> QuerySet:
    """
    Список документов с фильтрами экрана.

    scope: all | favorites | archive. «Архив» — не фильтр поверх общего списка,
    а другой источник строк, поэтому и порядок сортировки у него свой: по
    времени удаления.
    """
    qs = visible_documents_qs(role) if scope != 'archive' else archived_documents_qs(role)
    if section_id is not None:
        qs = qs.filter(section_id=section_id)
    if status:
        qs = qs.filter(status=status)

    qs = _annotate_list(qs, account_id)

    if scope == 'favorites':
        qs = qs.filter(favorites__account_id=account_id)

    if query:
        qs = search(qs, query)
    elif scope == 'archive':
        qs = qs.order_by('-updated_at')
    else:
        qs = qs.order_by('section_id', 'position', 'id')

    return qs.values(*_LIST_FIELDS, 'excerpt', 'is_favorite', 'author_name')


def _annotate_list(qs: QuerySet, account_id: Optional[int]) -> QuerySet:
    """Фрагмент текста, автор и признак избранного — то, что рисует строка."""
    favorite = (
        Exists(KnowledgeFavorite.objects.filter(
            document_id=OuterRef('pk'), account_id=account_id,
        ))
        if account_id is not None
        else Value(False, output_field=BooleanField())
    )
    return qs.annotate(
        excerpt=Substr('plain_text', 1, EXCERPT_CHARS),
        is_favorite=favorite,
        # У сотрудников без full_name (заведены только с почтой) показываем
        # почту: пустая колонка «Автор» выглядит как потеря данных.
        # output_field обязателен: full_name — CharField, email — EmailField,
        # и Django отказывается угадывать тип выражения из двух разных.
        author_name=Coalesce(
            'created_by__full_name', 'created_by__email', Value(''),
            output_field=TextField(),
        ),
    )


def archived_documents_qs(role: str) -> QuerySet:
    """
    Удалённые документы. Видны только тем, кто видит всё: восстановление —
    действие администратора, а для читателя удалённого документа не существует.
    """
    if role not in FULL_ACCESS_ROLES:
        return KnowledgeDocument.objects.none()
    return KnowledgeDocument.objects.filter(active=False)


def search(qs: QuerySet, query: str) -> QuerySet:
    """
    Полнотекстовый поиск по названию и тексту.

    websearch_to_tsquery, а не plainto_tsquery: он понимает кавычки и минус
    («первый урок» -пробный) и никогда не падает на кривом вводе — то есть
    строку из поля поиска можно отдавать ему как есть.

    Совпадение ищется по генерируемой колонке search_tsv, под которой лежит GIN
    (см. models.py). Ранжирование — SearchRank; при равном ранге свежие выше.
    """
    tsquery = SearchQuery(query, config='russian', search_type='websearch')
    return (
        qs.filter(search_tsv=tsquery)
        .annotate(rank=SearchRank(F('search_tsv'), tsquery))
        .order_by('-rank', '-updated_at')
    )


def get_document(role: str, document_id: int) -> Optional[dict]:
    """Документ с контентом или None, если роли он не виден (вьюха отдаст 404)."""
    return (
        visible_documents_qs(role)
        .filter(id=document_id)
        .annotate(author_name=Coalesce(
            'created_by__full_name', 'created_by__email', Value(''),
            output_field=TextField(),
        ))
        # plain_text намеренно не выбирается: он дублировал бы содержимое,
        # которое уже едет рядом. Единственным его потребителем было время
        # чтения статьи — снято по решению пользователя 2026-08-12.
        .values(*_LIST_FIELDS, 'content', 'created_at', 'author_name')
        .first()
    )


def create_document(*, section_id: int, title: str, account_id: int) -> KnowledgeDocument:
    last = KnowledgeDocument.objects.filter(section_id=section_id).order_by(
        '-position',
    ).values_list('position', flat=True).first()
    return KnowledgeDocument.objects.create(
        section_id=section_id,
        title=title,
        content={'type': 'doc', 'content': []},
        plain_text='',
        status=KnowledgeDocument.Status.DRAFT,
        reader_roles=[],
        position=(last or 0) + 1,
        created_by_id=account_id,
        updated_by_id=account_id,
    )


def get_document_for_write(document_id: int) -> Optional[KnowledgeDocument]:
    """Модель документа для мутаций. Доступ уже проверен permission-классом."""
    return KnowledgeDocument.objects.filter(id=document_id, active=True).first()


def soft_delete_document(document_id: int) -> bool:
    return KnowledgeDocument.objects.filter(id=document_id).update(active=False) > 0


def reorder_documents(section_id: int, order: list[int]) -> None:
    for position, document_id in enumerate(order, start=1):
        KnowledgeDocument.objects.filter(
            id=document_id, section_id=section_id,
        ).update(position=position)


def restore_document(document_id: int) -> bool:
    return KnowledgeDocument.objects.filter(id=document_id, active=False).update(
        active=True,
    ) > 0


# ---------------------------------------------------------------------------
# Избранное
# ---------------------------------------------------------------------------

def is_favorite(document_id: int, account_id: int) -> bool:
    return KnowledgeFavorite.objects.filter(
        document_id=document_id, account_id=account_id,
    ).exists()


def set_favorite(document_id: int, account_id: int, value: bool) -> None:
    if value:
        KnowledgeFavorite.objects.get_or_create(
            document_id=document_id, account_id=account_id,
        )
    else:
        KnowledgeFavorite.objects.filter(
            document_id=document_id, account_id=account_id,
        ).delete()


# ---------------------------------------------------------------------------
# Картинки и использования
# ---------------------------------------------------------------------------

def get_image_by_sha256(sha256: str) -> Optional[KnowledgeImage]:
    return KnowledgeImage.objects.filter(sha256=sha256).first()


def get_image(image_id: int) -> Optional[KnowledgeImage]:
    return KnowledgeImage.objects.filter(id=image_id).first()


def existing_image_ids(image_ids) -> set[int]:
    if not image_ids:
        return set()
    return set(KnowledgeImage.objects.filter(
        id__in=list(image_ids),
    ).values_list('id', flat=True))


def sync_image_usages(document_id: int, image_ids: set[int]) -> None:
    """Привести набор использований документа к переданному."""
    KnowledgeImageUsage.objects.filter(document_id=document_id).exclude(
        image_id__in=list(image_ids) or [0],
    ).delete()
    existing = set(KnowledgeImageUsage.objects.filter(
        document_id=document_id,
    ).values_list('image_id', flat=True))
    KnowledgeImageUsage.objects.bulk_create(
        [
            KnowledgeImageUsage(document_id=document_id, image_id=image_id)
            for image_id in image_ids - existing
        ],
        ignore_conflicts=True,
    )


def image_visible_to(role: str, image_id: int) -> bool:
    """
    Картинка видна, если хотя бы один документ, где она используется, доступен
    этой роли. Без этой проверки прямая ссылка на файл обходит ролевую модель.

    Исключение для admin/superadmin: им картинка доступна всегда, даже пока не
    используется нигде. Иначе только что вставленный в редактор скриншот не
    показывался бы до сохранения документа — связь «картинка ↔ документ»
    появляется только при записи, а увидеть вставленное нужно сразу. Это не
    ослабляет модель: те же роли и так видят все документы, а значит и все
    картинки в них.
    """
    if role in FULL_ACCESS_ROLES:
        return True
    return visible_documents_qs(role).filter(
        image_usages__image_id=image_id,
    ).exists()


def orphan_image_ids(older_than) -> list[int]:
    """
    Картинки без единого использования в ЖИВЫХ документах, старше указанного
    момента.

    Считать использования без оглядки на active нельзя: удаление документа
    мягкое, строки использований при нём остаются, и картинки удалённых
    документов не собирались бы никогда — занимали бы диск вечно.
    """
    return list(
        KnowledgeImage.objects
        .annotate(usage_count=Count('usages', filter=Q(usages__document__active=True)))
        .filter(usage_count=0, created_at__lt=older_than)
        .values_list('id', flat=True)
    )


def pending_image_ids(limit: int = 100) -> list[int]:
    return list(
        KnowledgeImage.objects
        .filter(optimize_state=KnowledgeImage.OptimizeState.PENDING)
        .order_by('id')
        .values_list('id', flat=True)[:limit]
    )


def ready_images_in_formats(mimes) -> list[tuple[int, str, str]]:
    """(id, путь оригинала, путь варианта) готовых картинок указанных форматов."""
    return list(
        KnowledgeImage.objects
        .filter(optimize_state=KnowledgeImage.OptimizeState.READY, mime__in=list(mimes))
        .order_by('id')
        .values_list('id', 'original_path', 'optimized_path')
    )


# ---------------------------------------------------------------------------
# Прикреплённые файлы и использования
# ---------------------------------------------------------------------------
# Устройство повторяет картинки намеренно: правила видимости и уборки у них
# одни и те же, и расхождение здесь означало бы, что файл виден там, где
# картинка уже нет. Объединять в один набор функций мешает разная модель —
# см. комментарий у KnowledgeFile.

def get_file_by_sha256(sha256: str) -> Optional[KnowledgeFile]:
    return KnowledgeFile.objects.filter(sha256=sha256).first()


def get_file(file_id: int) -> Optional[KnowledgeFile]:
    return KnowledgeFile.objects.filter(id=file_id).first()


def existing_file_ids(file_ids) -> set[int]:
    if not file_ids:
        return set()
    return set(KnowledgeFile.objects.filter(
        id__in=list(file_ids),
    ).values_list('id', flat=True))


def sync_file_usages(document_id: int, file_ids: set[int]) -> None:
    """Привести набор использований документа к переданному."""
    KnowledgeFileUsage.objects.filter(document_id=document_id).exclude(
        file_id__in=list(file_ids) or [0],
    ).delete()
    existing = set(KnowledgeFileUsage.objects.filter(
        document_id=document_id,
    ).values_list('file_id', flat=True))
    KnowledgeFileUsage.objects.bulk_create(
        [
            KnowledgeFileUsage(document_id=document_id, file_id=file_id)
            for file_id in file_ids - existing
        ],
        ignore_conflicts=True,
    )


def file_visible_to(role: str, file_id: int) -> bool:
    """
    Файл доступен, если доступен хотя бы один документ, где он прикреплён.

    Исключение для admin/superadmin — по той же причине, что у картинок: связь
    «файл ↔ документ» появляется только при сохранении, а скачать только что
    загруженный файл автор должен уметь сразу.
    """
    if role in FULL_ACCESS_ROLES:
        return True
    return visible_documents_qs(role).filter(
        file_usages__file_id=file_id,
    ).exists()


def orphan_file_ids(older_than) -> list[int]:
    """Файлы без единого использования в ЖИВЫХ документах, старше момента."""
    return list(
        KnowledgeFile.objects
        .annotate(usage_count=Count('usages', filter=Q(usages__document__active=True)))
        .filter(usage_count=0, created_at__lt=older_than)
        .values_list('id', flat=True)
    )
