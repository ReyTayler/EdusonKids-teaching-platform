"""Чтение: фильтры, просрочка, отсутствие N+1."""
import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.taskboard import repository, services
from apps.taskboard.models import Task


@pytest.mark.django_db
def test_filter_only_open_excludes_closed(board):
    b, stages = board
    open_task = services.create_task(board_id=b.id, title='Открытая', author_id=None)
    closed = services.create_task(board_id=b.id, title='Закрытая', author_id=None)
    services.move_task(closed, to_stage_id=stages['done'].id,
                       resolution='done', author_id=None)

    ids = [t['id'] for t in repository.list_tasks({'board_id': b.id, 'only_open': True})]
    assert open_task.id in ids
    assert closed.id not in ids


@pytest.mark.django_db
def test_overdue_flag_is_derived(board):
    b, _ = board
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    task = services.create_task(
        board_id=b.id, title='Просроченная', author_id=None, due_date=yesterday)
    row = next(t for t in repository.list_tasks({'board_id': b.id}) if t['id'] == task.id)
    assert row['is_overdue'] is True


@pytest.mark.django_db
def test_closed_task_is_never_overdue(board):
    b, stages = board
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    task = services.create_task(
        board_id=b.id, title='Закрытая просрочка', author_id=None, due_date=yesterday)
    services.move_task(task, to_stage_id=stages['done'].id,
                       resolution='done', author_id=None)
    row = next(t for t in repository.list_tasks({'board_id': b.id}) if t['id'] == task.id)
    assert row['is_overdue'] is False


@pytest.mark.django_db
def test_list_does_not_grow_queries_with_rows(board):
    """N+1-страж: 10 задач стоят столько же запросов, сколько 2."""
    b, _ = board
    for i in range(2):
        services.create_task(board_id=b.id, title=f'Задача {i}', author_id=None)
    with CaptureQueriesContext(connection) as few:
        repository.list_tasks({'board_id': b.id})

    for i in range(2, 10):
        services.create_task(board_id=b.id, title=f'Задача {i}', author_id=None)
    with CaptureQueriesContext(connection) as many:
        repository.list_tasks({'board_id': b.id})

    assert len(many) == len(few)


@pytest.mark.django_db
def test_week_returns_only_dated_tasks_in_range(board):
    b, _ = board
    today = datetime.date.today()
    dated = services.create_task(
        board_id=b.id, title='С датой', author_id=None, due_date=today)
    undated = services.create_task(board_id=b.id, title='Без даты', author_id=None)

    ids = [t['id'] for t in repository.list_week(date_from=today, date_to=today)]
    assert dated.id in ids
    assert undated.id not in ids


@pytest.mark.django_db
def test_get_task_returns_same_shape_as_list(board):
    """Одна карточка приходит тем же форматом, что и строка списка."""
    b, _ = board
    task = services.create_task(board_id=b.id, title='Одна', author_id=None)
    row = repository.get_task(task.id)
    from_list = next(t for t in repository.list_tasks({'board_id': b.id})
                     if t['id'] == task.id)
    assert row == from_list


@pytest.mark.django_db
def test_get_task_returns_none_for_missing(db):
    assert repository.get_task(99999999) is None


@pytest.mark.django_db
def test_activity_is_ordered_oldest_first(board):
    b, stages = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    services.add_comment(task, body='Первый', author_id=None)
    services.move_task(task, to_stage_id=stages['work'].id,
                       resolution=None, author_id=None)

    kinds = [e['kind'] for e in repository.list_activity(task.id)]
    assert kinds == ['system', 'comment', 'stage_change']


@pytest.mark.django_db
def test_filter_by_student(board):
    """Задачи ученика — основа блока на его странице."""
    from django.db import connection as conn

    b, _ = board
    with conn.cursor() as cur:
        cur.execute("INSERT INTO students (full_name, created_at) "
                    "VALUES ('__tb_repo_student__', now()) RETURNING id")
        student_id = cur.fetchone()[0]
    try:
        mine = services.create_task(
            board_id=b.id, title='По ученику', author_id=None, student_id=student_id)
        services.create_task(board_id=b.id, title='Без ученика', author_id=None)

        ids = [t['id'] for t in repository.list_tasks({'student_id': student_id})]
        assert ids == [mine.id]
    finally:
        from apps.taskboard.models import Task
        Task.objects.filter(student_id=student_id).delete()
        with conn.cursor() as cur:
            cur.execute('DELETE FROM students WHERE id = %s', [student_id])


@pytest.mark.django_db
def test_row_includes_assignee_and_author_names(board, admin_account_id):
    """Имя человека берётся из учётки — поле называется full_name, не name."""
    from apps.accounts.models import Account

    Account.objects.filter(id=admin_account_id).update(full_name='Пётр Куратор')
    b, _ = board
    task = services.create_task(
        board_id=b.id, title='Х', author_id=admin_account_id,
        assignee_ids=[admin_account_id])

    row = repository.get_task(task.id)
    assert [a['full_name'] for a in row['assignees']] == ['Пётр Куратор']

    entry = repository.list_activity(task.id)[0]
    assert entry['author_name'] == 'Пётр Куратор'


@pytest.mark.django_db
def test_tasks_queryset_allows_server_side_slicing(board):
    """
    Пагинация обязана резать в БД, а не в Python: репозиторий отдаёт queryset,
    вьюха применяет к нему LIMIT/OFFSET и превращает в строки только страницу.
    """
    from django.db.models import QuerySet

    b, _ = board
    for i in range(5):
        services.create_task(board_id=b.id, title=f'Т{i}', author_id=None)

    qs = repository.tasks_queryset({'board_id': b.id})
    assert isinstance(qs, QuerySet)

    page = list(qs[:2])
    assert len(page) == 2

    rows = repository.rows(page)
    assert len(rows) == 2
    assert set(rows[0].keys()) == set(repository.get_task(page[0].id).keys())


@pytest.mark.django_db
def test_search_by_hash_id(board):
    """`#124` ищет по номеру задачи, а не по тексту заголовка."""
    b, _ = board
    target = services.create_task(board_id=b.id, title='Первая', author_id=None)
    services.create_task(board_id=b.id, title='Вторая', author_id=None)

    ids = [t['id'] for t in repository.list_tasks({'board_id': b.id, 'q': f'#{target.id}'})]
    assert ids == [target.id]


@pytest.mark.django_db
def test_search_by_bare_number(board):
    """Номер без решётки работает так же — люди её не набирают."""
    b, _ = board
    target = services.create_task(board_id=b.id, title='Первая', author_id=None)

    ids = [t['id'] for t in repository.list_tasks({'board_id': b.id, 'q': str(target.id)})]
    assert ids == [target.id]


@pytest.mark.django_db
def test_search_by_student_name(board):
    from django.utils import timezone

    from apps.students.models import Student

    b, _ = board
    student = Student.objects.create(
        full_name='__tb_Образцов Пётр__', created_at=timezone.now())
    try:
        target = services.create_task(
            board_id=b.id, title='Позвонить', author_id=None, student_id=student.id)
        services.create_task(board_id=b.id, title='Другая', author_id=None)

        ids = [t['id'] for t in repository.list_tasks({'board_id': b.id, 'q': 'Образцов'})]
        assert ids == [target.id]
    finally:
        Task.objects.filter(student=student).update(student=None)
        student.delete()


@pytest.mark.django_db
def test_search_by_assignee_name(board, admin_account_id):
    from apps.accounts.models import Account

    # Имя исполнителя в карточке берётся из full_name — фикстура заполняет
    # только first_name/last_name, поэтому задаём его явно.
    Account.objects.filter(id=admin_account_id).update(full_name='Тестовый админ')

    b, _ = board
    target = services.create_task(
        board_id=b.id, title='Позвонить', author_id=None, assignee_ids=[admin_account_id])
    services.create_task(board_id=b.id, title='Другая', author_id=None)

    ids = [t['id'] for t in repository.list_tasks({'board_id': b.id, 'q': 'Тестовый'})]
    assert ids == [target.id]






@pytest.mark.django_db
def test_due_today(board):
    b, _ = board
    today = datetime.date.today()
    target = services.create_task(
        board_id=b.id, title='Сегодня', author_id=None, due_date=today)
    services.create_task(
        board_id=b.id, title='Завтра', author_id=None,
        due_date=today + datetime.timedelta(days=1))

    ids = [t['id'] for t in repository.list_tasks({'board_id': b.id, 'due': 'today'})]
    assert ids == [target.id]


@pytest.mark.django_db
def test_due_week_covers_monday_to_sunday(board):
    b, _ = board
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    inside = services.create_task(
        board_id=b.id, title='В неделе', author_id=None, due_date=monday)
    outside = services.create_task(
        board_id=b.id, title='Следующая', author_id=None,
        due_date=monday + datetime.timedelta(days=7))

    ids = [t['id'] for t in repository.list_tasks({'board_id': b.id, 'due': 'week'})]
    assert inside.id in ids
    assert outside.id not in ids


@pytest.mark.django_db
def test_due_overdue_excludes_closed(board):
    b, stages = board
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    open_task = services.create_task(
        board_id=b.id, title='Висит', author_id=None, due_date=yesterday)
    closed = services.create_task(
        board_id=b.id, title='Закрыта', author_id=None, due_date=yesterday)
    services.move_task(closed, to_stage_id=stages['done'].id,
                       resolution='done', author_id=None)

    ids = [t['id'] for t in repository.list_tasks({'board_id': b.id, 'due': 'overdue'})]
    assert ids == [open_task.id]


@pytest.mark.django_db
def test_due_none_finds_tasks_without_date(board):
    b, _ = board
    without = services.create_task(board_id=b.id, title='Без срока', author_id=None)
    services.create_task(
        board_id=b.id, title='Со сроком', author_id=None,
        due_date=datetime.date.today())

    ids = [t['id'] for t in repository.list_tasks({'board_id': b.id, 'due': 'none'})]
    assert ids == [without.id]


@pytest.mark.django_db
def test_comments_count_in_row(board):
    b, _ = board
    task = services.create_task(board_id=b.id, title='С комментариями', author_id=None)
    services.add_comment(task, body='Первый', author_id=None)
    services.add_comment(task, body='Второй', author_id=None)

    row = repository.get_task(task.id)
    assert row['comments_count'] == 2


@pytest.mark.django_db
def test_comments_count_ignores_system_entries(board):
    """Системные записи и смены стадии — не комментарии."""
    b, stages = board
    task = services.create_task(board_id=b.id, title='Без комментариев', author_id=None)
    services.move_task(task, to_stage_id=stages['work'].id,
                       resolution=None, author_id=None)

    row = repository.get_task(task.id)
    assert row['comments_count'] == 0


@pytest.mark.django_db
def test_comments_count_does_not_add_queries(board):
    """Счётчик не должен стоить запроса на карточку."""
    b, _ = board
    for i in range(2):
        t = services.create_task(board_id=b.id, title=f'Задача {i}', author_id=None)
        services.add_comment(t, body='Комментарий', author_id=None)
    with CaptureQueriesContext(connection) as few:
        repository.list_tasks({'board_id': b.id})

    for i in range(2, 10):
        t = services.create_task(board_id=b.id, title=f'Задача {i}', author_id=None)
        services.add_comment(t, body='Комментарий', author_id=None)
    with CaptureQueriesContext(connection) as many:
        repository.list_tasks({'board_id': b.id})

    assert len(many) == len(few)
