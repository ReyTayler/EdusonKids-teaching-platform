"""
Unit/integration тесты для GroupsRepository.

Используют реальную БД (managed=False, продовая).
Все созданные строки удаляются в teardown.

Тестируют:
  - list_groups: форма ответа {rows, total, page, page_size}
  - list_groups: фильтрация по active
  - list_groups: сортировка
  - get_group: существующий ID → dict с slots
  - get_group: несуществующий ID → None
  - create_group: создаёт группу + слоты, RETURNING * работает
  - update_group: COALESCE-обновление, перезапись слотов
  - soft_delete_group: active=false, повторный вызов → False
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal

import pytest
from django.db import connection

from apps.core.tests.minimal_school import ensure_minimal_school

from apps.groups import repository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cleanup_group(group_id: int) -> None:
    """Прямой DELETE — как Nest e2e after() через пул."""
    with connection.cursor() as cur:
        # форк Eduson: тест раньше пропускался из-за пустой БД. FK слотов на группу
        # без ON DELETE CASCADE в БД (каскад у модели — только ORM), поэтому сырой
        # DELETE группы оставлял слоты, и проверка ограничений в конце теста падала.
        cur.execute('DELETE FROM group_schedule_slots WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM groups WHERE id = %s', [group_id])


def _insert_lessons(group_id: int, teacher_id: int, lesson_types,
                    duration_minutes: int = 90) -> None:
    """Вставляет по одному занятию каждого переданного типа (прямой INSERT)."""
    with connection.cursor() as cur:
        for i, lesson_type in enumerate(lesson_types):
            cur.execute(
                "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number,"
                " lesson_duration_minutes, lesson_type, submitted_at, submitted_by_token)"
                " VALUES (%s, %s, %s, %s, %s, %s, now(), '__test__')",
                [group_id, teacher_id, f'2026-01-{i + 1:02d}', i + 1,
                 duration_minutes, lesson_type],
            )


def _delete_lessons(group_ids: list[int]) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM lessons WHERE group_id = ANY(%s)', [group_ids])


def _get_valid_direction_id() -> int:
    """Направление минимальной школы."""
    return ensure_minimal_school()['direction_id']


def _get_valid_teacher_id() -> int:
    """Преподаватель минимальной школы."""
    return ensure_minimal_school()['teacher_id']


@contextmanager
def _own_direction_and_teacher():
    """Собственные направление и преподаватель на время теста.

    Проверкам счётчика «Пройдено» нужны справочники, не разделяемые ни с кем
    (у направления минимальной школы уже есть свой урок), поэтому создаём свои
    строки и удаляем в finally.
    """
    from django.utils import timezone
    from apps.directions.models import Direction
    from apps.teachers.models import Teacher

    direction = Direction.objects.create(name='__test_lh_direction__', total_lessons=36)
    teacher = Teacher.objects.create(name='__test_lh_teacher__', created_at=timezone.now())
    try:
        yield direction.id, teacher.id
    finally:
        Teacher.objects.filter(id=teacher.id).delete()
        Direction.objects.filter(id=direction.id).delete()


def _make_group_data(**overrides) -> dict:
    return {
        'name': '__test_repo_group__',
        'direction_id': _get_valid_direction_id(),
        'teacher_id': _get_valid_teacher_id(),
        'is_individual': False,
        'lesson_duration_minutes': 90,
        'lessons_per_week': 2,
        'group_start_date': None,
        'vk_chat': None,
        'slots': [],
        **overrides,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestListGroups:
    """Тесты list_groups()."""

    def test_returns_correct_shape(self):
        result = repository.list_groups()
        assert 'rows' in result
        assert 'total' in result
        assert 'page' in result
        assert 'page_size' in result

    def test_page_and_page_size_defaults(self):
        result = repository.list_groups()
        assert result['page'] == 1
        assert result['page_size'] == 50

    def test_total_is_int(self):
        result = repository.list_groups()
        assert isinstance(result['total'], int)

    def test_rows_is_list(self):
        result = repository.list_groups()
        assert isinstance(result['rows'], list)

    def test_filter_active_true(self):
        result = repository.list_groups(filters={'active': 'true'})
        for row in result['rows']:
            assert row['active'] is True

    def test_filter_active_false(self):
        result = repository.list_groups(filters={'active': 'false'})
        for row in result['rows']:
            assert row['active'] is False

    def test_default_hides_archived_include_inactive_shows(self):
        """Основной список по умолчанию — только активные (архив скрыт);
        include_inactive=True возвращает и архивные; явный filter[active]=false —
        только архивные."""
        prefix = '__test_archdefault__'
        active = repository.create_group(_make_group_data(name=prefix + 'active'))
        archived = repository.create_group(_make_group_data(name=prefix + 'arch'))
        repository.soft_delete_group(archived['id'])
        try:
            default = repository.list_groups(filters={'name': prefix}, page_size=100)
            names = {r['name'] for r in default['rows']}
            assert prefix + 'active' in names          # активная видна
            assert prefix + 'arch' not in names        # архивная скрыта по умолчанию
            assert all(r['active'] for r in default['rows'])

            inc = repository.list_groups(
                filters={'name': prefix}, include_inactive=True, page_size=100)
            names_inc = {r['name'] for r in inc['rows']}
            assert {prefix + 'active', prefix + 'arch'} <= names_inc  # обе

            arch_only = repository.list_groups(
                filters={'name': prefix, 'active': 'false'}, page_size=100)
            assert {r['name'] for r in arch_only['rows']} == {prefix + 'arch'}
        finally:
            _cleanup_group(active['id'])
            _cleanup_group(archived['id'])

    def test_members_count_counts_only_active(self):
        """members_count («Состав группы») = число АКТИВНЫХ членств группы;
        неактивные (выбывшие) не считаются."""
        grp = repository.create_group(_make_group_data(name='__test_members_count__'))
        student_ids = []
        try:
            with connection.cursor() as cur:
                for i, active in enumerate((True, True, False)):
                    cur.execute(
                        "INSERT INTO students (full_name) "
                        "VALUES (%s) RETURNING id", [f'__mc_student_{i}__'])
                    sid = cur.fetchone()[0]
                    student_ids.append(sid)
                    cur.execute(
                        "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                        "VALUES (%s, %s, 0, %s)", [grp['id'], sid, active])

            rows = repository.list_groups(filters={'name': '__test_members_count__'})['rows']
            row = next(r for r in rows if r['id'] == grp['id'])
            assert row['members_count'] == 2  # два активных, выбывший не в счёт
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [grp['id']])
                if student_ids:
                    cur.execute('DELETE FROM students WHERE id = ANY(%s)', [student_ids])
            _cleanup_group(grp['id'])

    def test_lessons_done_counts_only_course_lessons(self):
        """lessons_done («Пройдено») = уроки курса, пройденные группой.

        Считаются только курсовые типы; доп.урок (extra) и сгорание пропуска
        (burned) идут сверх сетки курса и в счётчик не попадают.
        """
        with _own_direction_and_teacher() as (direction_id, teacher_id):
            grp = repository.create_group(_make_group_data(
                name='__test_lessons_done__',
                direction_id=direction_id, teacher_id=teacher_id))
            empty = repository.create_group(_make_group_data(
                name='__test_lessons_done_empty__',
                direction_id=direction_id, teacher_id=teacher_id))
            try:
                _insert_lessons(grp['id'], teacher_id, (
                    'regular', 'substitution', 'reschedule', 'extra', 'burned'))

                rows = repository.list_groups(
                    filters={'name': '__test_lessons_done'}, page_size=100)['rows']
                by_id = {r['id']: r for r in rows}
                # regular + substitution + reschedule; extra и burned не в счёт
                assert by_id[grp['id']]['lessons_done'] == Decimal('3.0')
                # без занятий — 0, а не None: Coalesce вокруг подзапроса
                assert by_id[empty['id']]['lessons_done'] == Decimal('0.0')
                # соседний счётчик не поехал от второй аннотации
                assert by_id[grp['id']]['members_count'] == 0
            finally:
                _delete_lessons([grp['id'], empty['id']])
                _cleanup_group(grp['id'])
                _cleanup_group(empty['id'])

    def test_lessons_done_counts_45min_as_half(self):
        """Единица — УРОКИ курса, не занятия: у 45-минутной группы 3 занятия = 1.5 урока.

        Та же единица, что у lessons_total / directions.total_lessons, — иначе
        колонку нельзя было бы читать рядом с длиной курса.
        """
        with _own_direction_and_teacher() as (direction_id, teacher_id):
            grp = repository.create_group(_make_group_data(
                name='__test_lessons_done_45__', lesson_duration_minutes=45,
                direction_id=direction_id, teacher_id=teacher_id))
            try:
                _insert_lessons(grp['id'], teacher_id,
                                ('regular', 'regular', 'regular'), duration_minutes=45)

                rows = repository.list_groups(
                    filters={'name': '__test_lessons_done_45__'}, page_size=100)['rows']
                row = next(r for r in rows if r['id'] == grp['id'])
                assert row['lessons_done'] == Decimal('1.5')
                # Scale зафиксирован Cast'ом: '1.5', а не '1.50'/'2'
                assert str(row['lessons_done']) == '1.5'
            finally:
                _delete_lessons([grp['id']])
                _cleanup_group(grp['id'])

    def test_sort_by_lessons_done(self):
        """Сортировка по lessons_done работает в обе стороны — и в уроках, а не в занятиях."""
        with _own_direction_and_teacher() as (direction_id, teacher_id):
            # У 45-минутной группы занятий БОЛЬШЕ, а уроков курса — меньше:
            # сортировка обязана идти по урокам (1.5 < 2), не по числу занятий (3 > 2).
            few = repository.create_group(_make_group_data(
                name='__test_ld_sort_few__', lesson_duration_minutes=45,
                direction_id=direction_id, teacher_id=teacher_id))
            many = repository.create_group(_make_group_data(
                name='__test_ld_sort_many__',
                direction_id=direction_id, teacher_id=teacher_id))
            try:
                _insert_lessons(few['id'], teacher_id,
                                ('regular', 'regular', 'regular'), duration_minutes=45)
                _insert_lessons(many['id'], teacher_id, ('regular', 'regular'))

                asc = repository.list_groups(
                    filters={'name': '__test_ld_sort_'}, sort_by='lessons_done',
                    sort_dir='asc', page_size=100)['rows']
                assert [r['id'] for r in asc] == [few['id'], many['id']]

                desc = repository.list_groups(
                    filters={'name': '__test_ld_sort_'}, sort_by='lessons_done',
                    sort_dir='desc', page_size=100)['rows']
                assert [r['id'] for r in desc] == [many['id'], few['id']]
            finally:
                _delete_lessons([few['id'], many['id']])
                _cleanup_group(few['id'])
                _cleanup_group(many['id'])

    def test_page_size_respected(self):
        result = repository.list_groups(page=1, page_size=2)
        assert result['page_size'] == 2
        assert len(result['rows']) <= 2

    def test_sort_by_name_asc(self):
        result = repository.list_groups(sort_by='name', sort_dir='asc', page_size=10)
        names = [r['name'] for r in result['rows']]
        assert names == sorted(names)

    def test_sort_by_name_desc(self):
        """Убеждаемся что sort_dir=desc принимается без ошибок.

        Точный порядок Cyrillic-имён в Python sorted() не совпадает с PostgreSQL
        (разные collation), поэтому проверяем только статус запроса.
        """
        result = repository.list_groups(sort_by='name', sort_dir='desc', page_size=10)
        assert isinstance(result['rows'], list)
        assert result['page_size'] == 10

    def test_rows_have_direction_name(self):
        """Список включает direction_name из JOIN."""
        result = repository.list_groups(page_size=5)
        if result['rows']:
            assert 'direction_name' in result['rows'][0]

    def test_rows_have_teacher_name(self):
        """Список включает teacher_name из JOIN."""
        result = repository.list_groups(page_size=5)
        if result['rows']:
            assert 'teacher_name' in result['rows'][0]

    def test_rows_have_slots(self):
        """Каждая строка содержит поле slots (list)."""
        result = repository.list_groups(page_size=5)
        if result['rows']:
            assert isinstance(result['rows'][0]['slots'], list)


@pytest.mark.django_db
class TestGetGroup:
    """Тесты get_group()."""

    def test_nonexistent_returns_none(self):
        result = repository.get_group(999_999_999)
        assert result is None

    def test_existing_returns_dict_with_slots(self):
        data = _make_group_data(name='__test_get_group__')
        group = repository.create_group(data)
        group_id = group['id']
        try:
            result = repository.get_group(group_id)
            assert result is not None
            assert result['id'] == group_id
            assert result['name'] == '__test_get_group__'
            assert 'slots' in result
            assert isinstance(result['slots'], list)
        finally:
            _cleanup_group(group_id)

    def test_slots_populated_correctly(self):
        data = _make_group_data(
            name='__test_get_group_slots__',
            slots=[
                {'day_of_week': 1, 'start_time': '10:00'},
                {'day_of_week': 3, 'start_time': '14:30'},
            ],
        )
        group = repository.create_group(data)
        group_id = group['id']
        try:
            result = repository.get_group(group_id)
            assert len(result['slots']) == 2
            days = [s['day_of_week'] for s in result['slots']]
            assert sorted(days) == [1, 3]
        finally:
            _cleanup_group(group_id)


@pytest.mark.django_db
class TestCreateGroup:
    """Тесты create_group()."""

    def test_create_returns_dict(self):
        data = _make_group_data(name='__test_create_group__')
        group = repository.create_group(data)
        try:
            assert isinstance(group, dict)
            assert 'id' in group
            assert group['name'] == '__test_create_group__'
        finally:
            _cleanup_group(group['id'])

    def test_created_group_in_db(self):
        data = _make_group_data(name='__test_create_db__')
        group = repository.create_group(data)
        group_id = group['id']
        try:
            fetched = repository.get_group(group_id)
            assert fetched is not None
            assert fetched['id'] == group_id
        finally:
            _cleanup_group(group_id)

    def test_creates_with_slots(self):
        data = _make_group_data(
            name='__test_create_slots__',
            slots=[{'day_of_week': 0, 'start_time': '09:00'}],
        )
        group = repository.create_group(data)
        group_id = group['id']
        try:
            fetched = repository.get_group(group_id)
            assert len(fetched['slots']) == 1
            assert fetched['slots'][0]['day_of_week'] == 0
        finally:
            _cleanup_group(group_id)

    def test_active_default_true(self):
        data = _make_group_data(name='__test_create_active__')
        group = repository.create_group(data)
        try:
            assert group['active'] is True
        finally:
            _cleanup_group(group['id'])


@pytest.mark.django_db
class TestUpdateGroup:
    """Тесты update_group()."""

    def test_update_nonexistent_returns_none(self):
        result = repository.update_group(999_999_999, {'name': 'ghost'})
        assert result is None

    def test_update_name(self):
        data = _make_group_data(name='__test_upd_before__')
        group = repository.create_group(data)
        group_id = group['id']
        try:
            updated = repository.update_group(group_id, {'name': '__test_upd_after__'})
            assert updated['name'] == '__test_upd_after__'
        finally:
            _cleanup_group(group_id)

    def test_update_ignores_slots(self):
        """update_group НЕ трогает расписание: слоты меняются только через
        apply_schedule_change (версионный путь). Присланные в update slots
        игнорируются — иначе стёрлась бы версионная история (Blocker-фикс)."""
        data = _make_group_data(
            name='__test_upd_slots__',
            slots=[{'day_of_week': 2, 'start_time': '11:00'}],
        )
        group = repository.create_group(data)
        group_id = group['id']
        try:
            repository.update_group(
                group_id,
                {'slots': [
                    {'day_of_week': 4, 'start_time': '15:00'},
                    {'day_of_week': 5, 'start_time': '16:00'},
                ]},
            )
            fetched = repository.get_group(group_id)
            days = sorted(s['day_of_week'] for s in fetched['slots'])
            assert days == [2]  # исходный слот не тронут
        finally:
            _cleanup_group(group_id)

    def test_patch_active_false(self):
        data = _make_group_data(name='__test_upd_active__')
        group = repository.create_group(data)
        group_id = group['id']
        try:
            updated = repository.update_group(group_id, {'active': False})
            assert updated['active'] is False
        finally:
            _cleanup_group(group_id)

    def test_update_ignores_immutable_fields(self):
        """Направление, преподаватель, длительность неизменны после создания;
        УЖЕ заданную дату начала тоже нельзя изменить. update_group игнорирует эти
        поля (даже невалидные значения не применяются, иначе был бы FK-сбой на
        save). Имя/чат ВК остаются изменяемыми."""
        data = _make_group_data(
            name='__test_upd_immutable__', lesson_duration_minutes=90,
            group_start_date='2026-01-01')
        group = repository.create_group(data)
        gid = group['id']
        orig_dir, orig_teacher = group['direction_id'], group['teacher_id']
        try:
            updated = repository.update_group(gid, {
                'name': '__test_upd_immutable_new__',
                'direction_id': 999_999_999,          # невалидный: применился бы → FK-сбой
                'teacher_id': 999_999_999,
                'lesson_duration_minutes': 45,
                'group_start_date': '2030-01-01',
            })
            assert updated['name'] == '__test_upd_immutable_new__'     # имя меняется
            assert updated['direction_id'] == orig_dir                 # направление закреплено
            assert updated['teacher_id'] == orig_teacher               # преподаватель закреплён
            assert updated['lesson_duration_minutes'] == 90            # длительность закреплена
            assert str(updated['group_start_date']) == '2026-01-01'    # заданную дату не меняем
        finally:
            _cleanup_group(gid)

    def test_update_allows_first_start_date_set(self):
        """Первичная установка даты начала (было NULL) разрешена — завершает
        настройку группы (и триггерит автоген плана в services). Уже заданную —
        нельзя (см. test_update_ignores_immutable_fields)."""
        data = _make_group_data(name='__test_upd_firststart__', group_start_date=None)
        group = repository.create_group(data)
        gid = group['id']
        try:
            updated = repository.update_group(gid, {'group_start_date': '2026-09-01'})
            assert str(updated['group_start_date']) == '2026-09-01'
        finally:
            _cleanup_group(gid)


@pytest.mark.django_db
class TestSoftDeleteGroup:
    """Тесты soft_delete_group()."""

    def test_soft_delete_existing(self):
        data = _make_group_data(name='__test_softdel__')
        group = repository.create_group(data)
        group_id = group['id']
        try:
            result = repository.soft_delete_group(group_id)
            assert result is True
            fetched = repository.get_group(group_id)
            assert fetched['active'] is False
        finally:
            _cleanup_group(group_id)

    def test_soft_delete_nonexistent_returns_false(self):
        result = repository.soft_delete_group(999_999_999)
        assert result is False
