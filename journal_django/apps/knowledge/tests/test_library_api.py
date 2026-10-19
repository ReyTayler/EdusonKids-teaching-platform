"""
Тесты «библиотечной» части раздела: поиск, избранное, архив, дубликаты и
история версий.

Отдельный модуль от test_documents_api.py: тот про жизненный цикл документа
(создание, правка, публикация, права), этот — про то, как документ находят и
что с ним делают снаружи текста.
"""
from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

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


def _doc(admin_client, section, title, text='', *, published=False, roles=None):
    doc = admin_client.post(
        DOCS, {'section_id': section['id'], 'title': title}, format='json',
    ).json()
    patch = {'content': {'type': 'doc', 'content': [
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': text}]},
    ]}}
    if roles is not None:
        patch['reader_roles'] = roles
    admin_client.patch(f"{DOCS}/{doc['id']}", patch, format='json')
    if published:
        admin_client.post(f"{DOCS}/{doc['id']}/publish", {}, format='json')
    return doc


def _titles(response) -> list[str]:
    return [row['title'] for row in response.json()['rows']]


# ---------------------------------------------------------------------------
# Поиск
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_search_finds_by_body_text(admin_client, section):
    _doc(admin_client, section, '__test_kb_методика', 'Как проводить первый урок')
    _doc(admin_client, section, '__test_kb_продажи', 'Скрипт звонка родителю')

    found = admin_client.get(f'{DOCS}?q=урок')
    assert _titles(found) == ['__test_kb_методика']


@pytest.mark.django_db
def test_search_finds_by_title(admin_client, section):
    _doc(admin_client, section, '__test_kb_регламент отпусков', 'текст')
    _doc(admin_client, section, '__test_kb_прочее', 'текст')

    found = admin_client.get(f'{DOCS}?q=отпуск')
    assert _titles(found) == ['__test_kb_регламент отпусков']


@pytest.mark.django_db
def test_search_matches_word_forms(admin_client, section):
    """Русская морфология: «урокам» обязано находиться по «урок»."""
    _doc(admin_client, section, '__test_kb_а', 'Подготовка к урокам занимает время')

    assert len(admin_client.get(f'{DOCS}?q=урок').json()['rows']) == 1


@pytest.mark.django_db
def test_search_survives_operator_soup(admin_client, section):
    """
    Строка из поля поиска уходит в websearch_to_tsquery как есть — он не
    падает ни на кавычках, ни на минусах, ни на скобках.
    """
    _doc(admin_client, section, '__test_kb_а', 'текст')
    assert admin_client.get(f'{DOCS}?q=((( -"').status_code == 200


@pytest.mark.django_db
def test_search_respects_visibility(admin_client, teacher_client, section):
    """Поиск не должен находить то, чего роль не видит в списке."""
    _doc(admin_client, section, '__test_kb_черновик', 'секретный урок')
    _doc(admin_client, section, '__test_kb_общий', 'открытый урок',
         published=True, roles=['teacher'])

    assert _titles(teacher_client.get(f'{DOCS}?q=урок')) == ['__test_kb_общий']


# ---------------------------------------------------------------------------
# Избранное
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_favorite_roundtrip(admin_client, section):
    doc = _doc(admin_client, section, '__test_kb_док', 'текст')

    assert admin_client.post(f"{DOCS}/{doc['id']}/favorite", {}, format='json').json() == {
        'is_favorite': True,
    }
    assert _titles(admin_client.get(f'{DOCS}?scope=favorites')) == ['__test_kb_док']
    assert admin_client.get(f"{DOCS}/{doc['id']}").json()['is_favorite'] is True

    admin_client.delete(f"{DOCS}/{doc['id']}/favorite")
    assert admin_client.get(f'{DOCS}?scope=favorites').json()['rows'] == []


@pytest.mark.django_db
def test_favorite_is_personal(admin_client, superadmin_client, section):
    doc = _doc(admin_client, section, '__test_kb_док', 'текст')
    admin_client.post(f"{DOCS}/{doc['id']}/favorite", {}, format='json')

    assert superadmin_client.get(f'{DOCS}?scope=favorites').json()['rows'] == []


@pytest.mark.django_db
def test_favorite_hidden_document_is_404(teacher_client, admin_client, section):
    doc = _doc(admin_client, section, '__test_kb_док', 'текст')
    assert teacher_client.post(
        f"{DOCS}/{doc['id']}/favorite", {}, format='json',
    ).status_code == 404


# ---------------------------------------------------------------------------
# Архив
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_deleted_document_moves_to_archive_and_back(admin_client, section):
    doc = _doc(admin_client, section, '__test_kb_док', 'текст')
    admin_client.delete(f"{DOCS}/{doc['id']}")

    assert _titles(admin_client.get(f'{DOCS}?scope=archive')) == ['__test_kb_док']
    assert admin_client.get(DOCS).json()['rows'] == []

    restored = admin_client.post(f"{DOCS}/{doc['id']}/restore", {}, format='json')
    assert restored.status_code == 200
    assert _titles(admin_client.get(DOCS)) == ['__test_kb_док']
    assert admin_client.get(f'{DOCS}?scope=archive').json()['rows'] == []


@pytest.mark.django_db
def test_archive_is_closed_for_teacher(teacher_client, admin_client, section):
    doc = _doc(admin_client, section, '__test_kb_док', 'текст',
               published=True, roles=['teacher'])
    admin_client.delete(f"{DOCS}/{doc['id']}")

    assert teacher_client.get(f'{DOCS}?scope=archive').json()['rows'] == []


@pytest.mark.django_db
def test_unknown_scope_is_400(admin_client):
    assert admin_client.get(f'{DOCS}?scope=выдумка').status_code == 400


# ---------------------------------------------------------------------------
# Дубликат
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_duplicate_copies_content_as_draft(admin_client, section):
    doc = _doc(admin_client, section, '__test_kb_док', 'исходный текст',
               published=True, roles=['teacher'])

    copy = admin_client.post(f"{DOCS}/{doc['id']}/duplicate", {}, format='json')
    assert copy.status_code == 201
    body = copy.json()
    assert body['title'] == '__test_kb_док (копия)'
    assert body['status'] == 'draft'      # копия не уезжает читателям сама
    assert body['reader_roles'] == ['teacher']
    assert body['content'] == admin_client.get(
        f"{DOCS}/{doc['id']}",
    ).json()['content']


# ---------------------------------------------------------------------------
# Стоимость сохранения
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_save_without_image_changes_touches_no_usage_tables(
    admin_client, section, django_assert_num_queries,
):
    """
    Автосохранение не должно трогать связи «документ ↔ картинки», пока набор
    картинок в тексте не изменился.

    Раньше их перебирали на КАЖДОЕ сохранение: удаляли лишние и выбирали
    имеющиеся — два запроса впустую, потому что картинки в тексте не меняются
    почти никогда, а сохранение с потолком ожидания приходит раз в пять секунд.
    Теперь наборы сверяются в памяти, до перезаписи содержимого.
    """
    doc = _doc(admin_client, section, '__test_kb_док', 'начальный текст')
    body = {'content': {'type': 'doc', 'content': [
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'правка'}]},
    ]}}
    admin_client.patch(f"{DOCS}/{doc['id']}", body, format='json')   # прогрев

    body['content']['content'][0]['content'][0]['text'] = 'ещё правка'
    # Ровно шесть: две проверки аккаунта (аутентификация по cookie), открытие и
    # закрытие точки сохранения вокруг @transaction.atomic, выборка документа и
    # его UPDATE. Ни одного запроса к таблице использований — картинок в тексте
    # нет ни до, ни после, и сверка наборов идёт в памяти.
    with django_assert_num_queries(6):
        response = admin_client.patch(f"{DOCS}/{doc['id']}", body, format='json')
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Одновременная правка
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_stale_save_is_rejected(admin_client, superadmin_client, section):
    """
    Вкладка, которая начала правку раньше, не должна затирать чужое
    сохранение. С автосохранением это ежедневная ситуация, а не редкость.
    """
    doc = _doc(admin_client, section, '__test_kb_док', 'исходное')
    base = admin_client.get(f"{DOCS}/{doc['id']}").json()['updated_at']

    # Часы Windows тикают ~раз в 15 мс: без явного сдвига чужая правка может
    # получить ту же отметку времени, что и создание документа, — тогда база
    # совпадёт с текущей версией и конфликт не распознается. Сдвигаем время
    # чужой правки явно, чтобы тест не зависел от разрешения часов.
    later = timezone.now() + timedelta(seconds=1)
    with mock.patch('django.utils.timezone.now', return_value=later):
        superadmin_client.patch(
            f"{DOCS}/{doc['id']}",
            {'content': {'type': 'doc', 'content': [
                {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'чужая правка'}]},
            ]}},
            format='json',
        )

    stale = admin_client.patch(
        f"{DOCS}/{doc['id']}",
        {'base_updated_at': base,
         'content': {'type': 'doc', 'content': [
             {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'моя правка'}]},
         ]}},
        format='json',
    )
    assert stale.status_code == 409
    assert stale.json()['code'] == 'stale'
    assert 'чужая правка' in str(admin_client.get(f"{DOCS}/{doc['id']}").json()['content'])


@pytest.mark.django_db
def test_save_with_fresh_base_passes(admin_client, section):
    doc = _doc(admin_client, section, '__test_kb_док', 'исходное')
    base = admin_client.get(f"{DOCS}/{doc['id']}").json()['updated_at']

    ok = admin_client.patch(
        f"{DOCS}/{doc['id']}",
        {'base_updated_at': base,
         'content': {'type': 'doc', 'content': [
             {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'моя правка'}]},
         ]}},
        format='json',
    )
    assert ok.status_code == 200



# ---------------------------------------------------------------------------
# Счётчики разделов
# ---------------------------------------------------------------------------

def _sections(client) -> dict:
    body = client.get(SECTIONS).json()
    return {s['title']: s['document_count'] for s in body['sections']}


@pytest.mark.django_db
def test_section_counts_replace_full_download(admin_client, section):
    """
    Числа рядом с папками приходят вместе со списком разделов.

    Раньше экран ради них выгружал все документы целиком; теперь считает БД, и
    ответ не растёт вместе с базой знаний.
    """
    _doc(admin_client, section, '__test_kb_один', 'текст')
    _doc(admin_client, section, '__test_kb_два', 'текст')

    body = admin_client.get(SECTIONS).json()
    counts = {s['title']: s['document_count'] for s in body['sections']}
    assert counts['__test_kb_раздел'] == 2
    assert body['total'] >= 2


@pytest.mark.django_db
def test_empty_section_shows_zero(admin_client, section):
    """Пустая папка обязана остаться в списке с нулём, а не исчезнуть."""
    assert _sections(admin_client)['__test_kb_раздел'] == 0


@pytest.mark.django_db
def test_counts_respect_visibility(admin_client, teacher_client, section):
    """Счётчик обязан совпадать с тем, что роль реально увидит внутри папки."""
    _doc(admin_client, section, '__test_kb_черновик', 'текст')
    _doc(admin_client, section, '__test_kb_общий', 'текст',
         published=True, roles=['teacher'])
    _doc(admin_client, section, '__test_kb_чужой', 'текст',
         published=True, roles=['manager'])

    assert _sections(admin_client)['__test_kb_раздел'] == 3
    assert _sections(teacher_client)['__test_kb_раздел'] == 1

    visible = teacher_client.get(f"{DOCS}?section_id={section['id']}").json()
    assert visible['total'] == 1


@pytest.mark.django_db
def test_deleted_document_leaves_the_count(admin_client, section):
    doc = _doc(admin_client, section, '__test_kb_док', 'текст')
    assert _sections(admin_client)['__test_kb_раздел'] == 1

    admin_client.delete(f"{DOCS}/{doc['id']}")
    assert _sections(admin_client)['__test_kb_раздел'] == 0


@pytest.mark.django_db
def test_sections_cost_one_query_regardless_of_count(admin_client, section, django_assert_num_queries):
    """
    Стоимость списка разделов не должна расти с числом папок: счёт идёт
    группировкой в БД, а не запросом на папку.
    """
    for i in range(5):
        admin_client.post(SECTIONS, {'title': f'__test_kb_раздел {i}'}, format='json')
    admin_client.get(SECTIONS)          # прогрев: аутентификация и т.п.

    # Две проверки аккаунта (аутентификация по cookie), один запрос разделов со
    # счётчиками через LEFT JOIN + GROUP BY и один общий COUNT. Ни одно из
    # четырёх чисел не зависит от количества папок или документов.
    with django_assert_num_queries(4):
        admin_client.get(SECTIONS)


# ---------------------------------------------------------------------------
# Видимость самих разделов
# ---------------------------------------------------------------------------
# Счётчик выше отвечает на вопрос «сколько документов роль увидит внутри»,
# этот блок — на предшествующий ему «покажем ли роли саму папку». Правило
# одно: читатель видит раздел тогда и только тогда, когда внутри есть хотя бы
# один доступный ему документ. Иначе преподаватель видел бы в дереве всю
# структуру школы — «Финансы», «Продажи» — пусть и без содержимого.

@pytest.mark.django_db
def test_section_hidden_when_role_sees_nothing_inside(admin_client, teacher_client, section):
    _doc(admin_client, section, '__test_kb_чужой', 'текст',
         published=True, roles=['manager'])

    assert '__test_kb_раздел' not in _sections(teacher_client)
    assert '__test_kb_раздел' in _sections(admin_client)


@pytest.mark.django_db
def test_section_appears_once_role_gets_a_document(admin_client, teacher_client, section):
    """Публикация на роль обязана вернуть папку в дерево этой роли."""
    assert '__test_kb_раздел' not in _sections(teacher_client)

    _doc(admin_client, section, '__test_kb_общий', 'текст',
         published=True, roles=['teacher'])

    assert _sections(teacher_client)['__test_kb_раздел'] == 1


@pytest.mark.django_db
def test_draft_alone_does_not_reveal_section(admin_client, teacher_client, section):
    """
    Черновик, адресованный роли, — ещё не документ для неё.

    Правило видимости раздела обязано совпадать с правилом видимости
    документа, иначе папка выдаёт факт подготовки материала до публикации.
    """
    _doc(admin_client, section, '__test_kb_черновик', 'текст', roles=['teacher'])

    assert '__test_kb_раздел' not in _sections(teacher_client)


@pytest.mark.django_db
def test_manager_hidden_from_teacher_sections_too(admin_client, manager_client, section):
    """Правило общее для всех читателей, а не персональное для преподавателя."""
    _doc(admin_client, section, '__test_kb_учительский', 'текст',
         published=True, roles=['teacher'])

    assert '__test_kb_раздел' not in _sections(manager_client)


@pytest.mark.django_db
def test_empty_section_stays_for_admin(admin_client, section):
    """
    Обратная сторона правила: у администратора пустая папка обязана остаться.

    Иначе только что созданный раздел исчезал бы из дерева до первого
    документа — то есть положить документ было бы некуда.
    """
    assert _sections(admin_client)['__test_kb_раздел'] == 0


@pytest.mark.django_db
def test_hidden_section_is_not_reachable_by_id(admin_client, teacher_client, section):
    """
    Скрытый раздел не должен отдаваться и по прямому адресу.

    Список — про удобство, а вот ручка по id — про доступ: без проверки роли
    она сообщала бы название любой папки школы любому сотруднику.
    """
    _doc(admin_client, section, '__test_kb_чужой', 'текст',
         published=True, roles=['manager'])
    assert teacher_client.get(f"{SECTIONS}/{section['id']}").status_code == 404

    _doc(admin_client, section, '__test_kb_общий', 'текст',
         published=True, roles=['teacher'])
    assert teacher_client.get(f"{SECTIONS}/{section['id']}").status_code == 200


@pytest.mark.django_db
def test_admin_reaches_empty_section_by_id(admin_client, section):
    assert admin_client.get(f"{SECTIONS}/{section['id']}").status_code == 200


@pytest.mark.django_db
def test_sections_still_cost_one_query_for_teacher(
    admin_client, teacher_client, section, django_assert_num_queries,
):
    """
    Отсечка пустых папок обязана остаться частью той же группировки.

    Проверка «есть ли внутри доступное» напрашивается запросом на папку —
    и именно так список разделов снова стал бы расти по стоимости вместе с
    базой знаний.
    """
    for i in range(5):
        created = admin_client.post(
            SECTIONS, {'title': f'__test_kb_раздел {i}'}, format='json',
        ).json()
        _doc(admin_client, created, f'__test_kb_док {i}', 'текст',
             published=True, roles=['teacher'])
    teacher_client.get(SECTIONS)        # прогрев: аутентификация и т.п.

    # Пять, а не четыре как у администратора: у преподавателя аутентификация
    # дочитывает его карточку из teachers. К разделам это не относится — их
    # по-прежнему ровно один запрос, отсечка пустых уехала в HAVING той же
    # группировки, плюс общий COUNT для «Все документы».
    with django_assert_num_queries(5):
        teacher_client.get(SECTIONS)
