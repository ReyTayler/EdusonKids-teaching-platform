"""Перенос между стадиями и правила закрытия."""
import pytest
from rest_framework.serializers import ValidationError

from apps.taskboard import services
from apps.taskboard.models import TaskActivity


@pytest.mark.django_db
def test_move_between_open_stages_keeps_task_open(board):
    b, stages = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    moved = services.move_task(task, to_stage_id=stages['work'].id,
                               resolution=None, author_id=None)
    assert moved.stage_id == stages['work'].id
    assert moved.closed_at is None
    assert moved.resolution is None


@pytest.mark.django_db
def test_move_to_closed_stage_requires_resolution(board):
    b, stages = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    with pytest.raises(ValidationError):
        services.move_task(task, to_stage_id=stages['done'].id,
                           resolution=None, author_id=None)


@pytest.mark.django_db
def test_move_to_closed_stage_sets_closed_at(board):
    b, stages = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    closed = services.move_task(task, to_stage_id=stages['done'].id,
                                resolution='done', author_id=None)
    assert closed.closed_at is not None
    assert closed.resolution == 'done'


@pytest.mark.django_db
def test_reopening_clears_resolution(board):
    b, stages = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    services.move_task(task, to_stage_id=stages['done'].id, resolution='done', author_id=None)
    reopened = services.move_task(task, to_stage_id=stages['work'].id,
                                  resolution=None, author_id=None)
    assert reopened.closed_at is None
    assert reopened.resolution is None


@pytest.mark.django_db
def test_move_rejects_stage_from_another_board(board):
    from apps.taskboard.models import TaskBoard, TaskStage

    b, _ = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    other = TaskBoard.objects.create(name='__tb_move_other__')
    alien = TaskStage.objects.create(board=other, label='Чужая', sort_order=0, category='open')
    try:
        with pytest.raises(ValidationError):
            services.move_task(task, to_stage_id=alien.id, resolution=None, author_id=None)
    finally:
        alien.delete()
        other.delete()


@pytest.mark.django_db
def test_complete_moves_to_first_closed_stage(board):
    b, stages = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    done = services.complete_task(task, resolution='done', author_id=None)
    assert done.stage_id == stages['done'].id
    assert done.resolution == 'done'


@pytest.mark.django_db
def test_move_writes_stage_change_activity(board):
    b, stages = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    services.move_task(task, to_stage_id=stages['work'].id, resolution=None, author_id=None)
    kinds = list(TaskActivity.objects.filter(task=task).values_list('kind', flat=True))
    assert 'stage_change' in kinds


@pytest.mark.django_db
def test_move_between_closed_stages_keeps_first_closed_at(board):
    """Переклассификация исхода не меняет дату закрытия: закрыли один раз."""
    from apps.taskboard.models import Task, TaskStage

    b, stages = board
    cancelled = TaskStage.objects.create(
        board=b, label='Отменено', sort_order=3, category='closed')
    try:
        task = services.create_task(board_id=b.id, title='Х', author_id=None)
        services.move_task(task, to_stage_id=stages['done'].id,
                           resolution='done', author_id=None)
        first_closed_at = Task.objects.get(id=task.id).closed_at

        services.move_task(task, to_stage_id=cancelled.id,
                           resolution='cancelled', author_id=None)
        after = Task.objects.get(id=task.id)
        assert after.closed_at == first_closed_at
        assert after.resolution == 'cancelled'
    finally:
        Task.objects.filter(board=b).delete()
        cancelled.delete()


@pytest.mark.django_db
def test_stale_task_object_does_not_reset_closed_at(board):
    """
    Двое админов тянут одну карточку. Второй держит объект, загруженный ДО
    закрытия, поэтому в памяти closed_at=None. Сервис обязан опираться на
    состояние в БД, а не на устаревший объект, иначе дата закрытия уедет.
    """
    from apps.taskboard.models import Task, TaskStage

    b, stages = board
    cancelled = TaskStage.objects.create(
        board=b, label='Отменено', sort_order=3, category='closed')
    try:
        task = services.create_task(board_id=b.id, title='Х', author_id=None)
        stale = Task.objects.get(id=task.id)  # снимок ДО закрытия

        services.move_task(task, to_stage_id=stages['done'].id,
                           resolution='done', author_id=None)
        first_closed_at = Task.objects.get(id=task.id).closed_at

        services.move_task(stale, to_stage_id=cancelled.id,
                           resolution='cancelled', author_id=None)
        assert Task.objects.get(id=task.id).closed_at == first_closed_at
    finally:
        Task.objects.filter(board=b).delete()
        cancelled.delete()


@pytest.mark.django_db
def test_move_rejects_unknown_resolution(board):
    """Произвольная строка результата — ошибка валидации, а не IntegrityError."""
    b, stages = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    with pytest.raises(ValidationError):
        services.move_task(task, to_stage_id=stages['done'].id,
                           resolution='почти готово', author_id=None)


@pytest.mark.django_db
def test_complete_rejects_board_without_closed_stage(db):
    """«Выполнено» на воронке без закрытых стадий — ошибка валидации."""
    from apps.taskboard.models import Task, TaskBoard, TaskStage

    b2 = TaskBoard.objects.create(name='__tb_no_closed__')
    TaskStage.objects.create(board=b2, label='Новая', sort_order=0, category='open')
    try:
        task = services.create_task(board_id=b2.id, title='Х', author_id=None)
        with pytest.raises(ValidationError):
            services.complete_task(task, resolution='done', author_id=None)
    finally:
        Task.objects.filter(board=b2).delete()
        TaskStage.objects.filter(board=b2).delete()
        b2.delete()


@pytest.mark.django_db
def test_complete_picks_lowest_sort_order_deterministically(board):
    """
    При одинаковом sort_order у двух закрытых стадий выбор обязан быть
    предсказуемым (вторичная сортировка по id), а не случайным от прогона к прогону.
    """
    from apps.taskboard.models import Task, TaskStage

    b, stages = board
    twin = TaskStage.objects.create(
        board=b, label='Готово-2', sort_order=stages['done'].sort_order, category='closed')
    try:
        chosen = set()
        for i in range(3):
            task = services.create_task(board_id=b.id, title=f'Х{i}', author_id=None)
            chosen.add(services.complete_task(task, resolution='done', author_id=None).stage_id)
        assert chosen == {min(stages['done'].id, twin.id)}
    finally:
        Task.objects.filter(board=b).delete()
        twin.delete()


@pytest.mark.django_db
def test_move_to_same_stage_keeps_stage_entered_at(board):
    """
    Промах мышью на ту же колонку не должен сбрасывать «сколько висит в стадии».
    """
    from apps.taskboard.models import Task

    b, stages = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    before = Task.objects.get(id=task.id).stage_entered_at

    services.move_task(task, to_stage_id=stages['new'].id,
                       resolution=None, author_id=None)
    assert Task.objects.get(id=task.id).stage_entered_at == before


@pytest.mark.django_db
def test_move_activity_meta_records_stage_ids(board):
    """Лента фиксирует, откуда и куда переехала карточка."""
    b, stages = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    services.move_task(task, to_stage_id=stages['work'].id, resolution=None, author_id=None)
    entry = TaskActivity.objects.filter(task=task, kind='stage_change').get()
    assert entry.meta['from_stage_id'] == stages['new'].id
    assert entry.meta['to_stage_id'] == stages['work'].id
