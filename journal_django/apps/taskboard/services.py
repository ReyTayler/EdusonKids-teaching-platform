"""
Мутации раздела «Задачи». ВСЕ изменения проходят через этот модуль — вьюхи
не трогают модели напрямую.

Это же точка расширения под будущую автогенерацию задач по событиям: правило
вызовет create_task(), и переписывать создание не придётся.
"""
from __future__ import annotations

from datetime import date

from django.db import transaction
from django.utils import timezone
from rest_framework.serializers import ValidationError

from apps.taskboard.models import Task, TaskActivity, TaskStage


def _first_stage(board_id: int, *, category: str) -> TaskStage | None:
    # sort_order не уникален в пределах воронки — вторичный ключ `id` нужен,
    # чтобы выбор первой стадии был детерминированным при равном sort_order.
    return (TaskStage.objects
            .filter(board_id=board_id, category=category)
            .order_by('sort_order', 'id')
            .first())


def _ensure_exists(model, ids, *, field: str, label: str) -> None:
    """
    Проверить, что все переданные id существуют.

    None пропускаем — это осмысленное «снять исполнителя/ученика/группу».
    Нужно потому, что FK у Django DEFERRABLE INITIALLY DEFERRED: без проверки
    несуществующий id уронил бы транзакцию на коммите, уже после ответа.
    """
    wanted = [i for i in ids if i is not None]
    if not wanted:
        return
    found = set(model.objects.filter(id__in=wanted).values_list('id', flat=True))
    missing = [i for i in wanted if i not in found]
    if missing:
        raise ValidationError({field: f'{label} не найдены: {missing}'})


def _validate_refs(*, assignee_ids=None, student_id=None, group_id=None) -> None:
    """Проверить существование всех связанных записей одним местом."""
    from apps.accounts.models import Account
    from apps.groups.models import Group
    from apps.students.models import Student

    _ensure_exists(Account, assignee_ids or [], field='assignee_ids', label='Учётки')
    _ensure_exists(Student, [student_id], field='student_id', label='Ученики')
    _ensure_exists(Group, [group_id], field='group_id', label='Группы')


@transaction.atomic
def create_task(
    *,
    board_id: int,
    title: str,
    author_id: int | None,
    stage_id: int | None = None,
    description: str | None = None,
    assignee_ids: list[int] | None = None,
    student_id: int | None = None,
    group_id: int | None = None,
    due_date=None,
    priority: str = Task.Priority.NORMAL,
) -> Task:
    """Создать задачу. Без явной стадии кладём в первую открытую стадию воронки."""
    if stage_id is None:
        stage = _first_stage(board_id, category=TaskStage.Category.OPEN)
        if stage is None:
            raise ValidationError({'board_id': 'В воронке нет ни одной открытой стадии'})
    else:
        # Стадия ОБЯЗАНА принадлежать той же воронке: составным внешним ключом
        # это не выражается, и без проверки карточка окажется в чужой колонке.
        stage = TaskStage.objects.filter(id=stage_id, board_id=board_id).first()
        if stage is None:
            raise ValidationError({'stage_id': 'Стадия не принадлежит этой воронке'})
        # Задача рождается только открытой: closed_at/resolution заполняет
        # исключительно move_task/complete_task. Явная закрытая стадия здесь
        # дала бы «закрытую» карточку с пустыми полями закрытия — тот же
        # разрыв инварианта, что уже перекрыт для смены категории стадии.
        if stage.category == TaskStage.Category.CLOSED:
            raise ValidationError(
                {'stage_id': 'Нельзя создать задачу сразу в закрытой стадии'})

    _validate_refs(
        assignee_ids=assignee_ids, student_id=student_id, group_id=group_id,
    )

    task = Task.objects.create(
        board_id=board_id,
        stage=stage,
        title=title,
        description=description,
        created_by_id=author_id,
        student_id=student_id,
        group_id=group_id,
        due_date=due_date,
        priority=priority,
    )
    if assignee_ids:
        task.assignees.set(assignee_ids)

    TaskActivity.objects.create(
        task=task, kind=TaskActivity.Kind.SYSTEM, author_id=author_id,
        text='Задача создана', meta={'stage_id': stage.id},
    )
    return task


@transaction.atomic
def move_task(
    task: Task, *, to_stage_id: int, resolution: str | None, author_id: int | None,
) -> Task:
    """
    Перенести задачу в стадию той же воронки.

    Задача закрыта ⇔ стадия имеет category='closed'. Переход в закрытую стадию
    ТРЕБУЕТ результата; возврат в открытую результат и дату закрытия обнуляет.

    Работаем не с переданным объектом, а с перечитанной под блокировкой строкой:
    на канбан-доске двое штатно тянут одну карточку, и у второго объект в памяти
    устаревший — иначе `closed_at or now` затрёт исходную дату закрытия.
    """
    locked = Task.objects.select_for_update().filter(id=task.id).first()
    if locked is None:
        raise ValidationError({'task': 'Задача не найдена'})

    target = TaskStage.objects.filter(id=to_stage_id, board_id=locked.board_id).first()
    if target is None:
        raise ValidationError({'to_stage_id': 'Стадия не принадлежит воронке задачи'})

    from_stage_id = locked.stage_id
    now = timezone.now()

    if target.category == TaskStage.Category.CLOSED:
        if not resolution:
            raise ValidationError({'resolution': 'Укажите результат при закрытии задачи'})
        # Сервис — точка входа и для будущей автогенерации задач, мимо сериализатора.
        # Без этой проверки произвольная строка доезжает до CHECK в БД и даёт 500.
        if resolution not in Task.Resolution.values:
            raise ValidationError({'resolution': 'Неизвестный результат'})
        locked.resolution = resolution
        locked.closed_at = locked.closed_at or now
    else:
        locked.resolution = None
        locked.closed_at = None

    fields = ['resolution', 'closed_at', 'updated_at']
    if target.id != from_stage_id:
        # Промах мышью на ту же колонку не сбрасывает «сколько висит в стадии».
        # Смена результата внутри той же закрытой стадии при этом разрешена.
        locked.stage = target
        locked.stage_entered_at = now
        fields += ['stage', 'stage_entered_at']
    locked.save(update_fields=fields)

    TaskActivity.objects.create(
        task=locked, kind=TaskActivity.Kind.STAGE_CHANGE, author_id=author_id,
        meta={'from_stage_id': from_stage_id, 'to_stage_id': target.id,
              'resolution': locked.resolution},
    )
    return locked


@transaction.atomic
def complete_task(task: Task, *, resolution: str, author_id: int | None) -> Task:
    """
    Кнопка «Выполнено» — не флаг, а ДЕЙСТВИЕ: перенос в первую закрытую стадию
    воронки. Для пользователя один клик из любой колонки; в данных остаётся
    единственный источник истины — стадия.
    """
    target = _first_stage(task.board_id, category=TaskStage.Category.CLOSED)
    if target is None:
        raise ValidationError({'board_id': 'В воронке нет ни одной закрытой стадии'})
    return move_task(task, to_stage_id=target.id, resolution=resolution, author_id=author_id)


# Поля, которые можно менять через update_task. Стадия, результат и дата
# закрытия сюда НЕ входят намеренно — они меняются только move_task/complete_task,
# иначе появится «полузакрытая» карточка мимо правил.
EDITABLE_FIELDS = frozenset({
    'title', 'description', 'student_id', 'group_id', 'due_date', 'priority',
})


def _jsonable(value):
    """`meta` уезжает в jsonb — дата обязана стать строкой."""
    return value.isoformat() if isinstance(value, date) else value


@transaction.atomic
def update_task(task: Task, *, author_id: int | None, fields: dict) -> Task:
    """Изменить разрешённые поля карточки. Незнакомые ключи молча игнорируются."""
    changed: list[str] = []

    # Сервис — точка входа и мимо сериализатора (будущая автогенерация задач),
    # поэтому кривой приоритет обязан давать ошибку валидации, а не CHECK из БД.
    if 'priority' in fields and fields['priority'] not in Task.Priority.values:
        raise ValidationError({'priority': 'Неизвестный приоритет'})

    _validate_refs(
        student_id=fields.get('student_id'),
        group_id=fields.get('group_id'),
    )

    # Прежние значения нужны отдельным словарём: после setattr исходное уже
    # не достать, а лента обязана показать «было → стало».
    before: dict[str, object] = {}
    for key, value in fields.items():
        if key not in EDITABLE_FIELDS:
            continue
        current = getattr(task, key)
        if current == value:
            continue
        before[key] = current
        setattr(task, key, value)
        changed.append(key)

    if not changed:
        return task

    task.save(update_fields=[*changed, 'updated_at'])

    # Остальные правки — системными записями. В meta лежат сырые значения (id и
    # даты), а не готовый русский текст: собранный сейчас текст переврал бы
    # историю после переименования типа или ученика. Подписи рисует фронт.
    for key in changed:
        TaskActivity.objects.create(
            task=task, kind=TaskActivity.Kind.SYSTEM, author_id=author_id,
            meta={'field': key,
                  'from': _jsonable(before[key]),
                  'to': _jsonable(getattr(task, key))},
        )
    return task


@transaction.atomic
def add_comment(task: Task, *, body: str, author_id: int | None) -> TaskActivity:
    return TaskActivity.objects.create(
        task=task, kind=TaskActivity.Kind.COMMENT, author_id=author_id, text=body)


@transaction.atomic
def set_assignees(task: Task, *, assignee_ids: list[int], author_id: int | None) -> Task:
    """
    Заменить набор исполнителей.

    Отдельным сервисом, а не полем в update_task: набор живёт в промежуточной
    таблице, и присвоить его через setattr нельзя. Промежуточную таблицу
    pghistory не трекает (модель автогенерируемая), поэтому изменение фиксируем
    в ленте вручную — тем же приёмом, что и смену меток.

    Прежний набор читаем из БД, а не из переданного объекта: он может быть
    устаревшим (двое правят одну карточку), и лента записала бы неверное «было».
    """
    from apps.accounts.models import Account

    _ensure_exists(Account, assignee_ids or [], field='assignee_ids', label='Учётки')

    before = sorted(Task.objects.filter(id=task.id)
                    .values_list('assignees__id', flat=True))
    before = [i for i in before if i is not None]
    after = sorted(set(assignee_ids or []))
    if before == after:
        return task

    task.assignees.set(after)
    TaskActivity.objects.create(
        task=task, kind=TaskActivity.Kind.ASSIGN, author_id=author_id,
        meta={'from_assignee_ids': before, 'to_assignee_ids': after},
    )
    return task


def _is_last_of_category(stage: TaskStage, category: str) -> bool:
    """Останется ли воронка без стадий этой категории, если убрать данную."""
    return not (TaskStage.objects
                .filter(board_id=stage.board_id, category=category)
                .exclude(id=stage.id)
                .exists())


def stage_delete_blocker(stage: TaskStage) -> str | None:
    """
    Почему стадию нельзя удалить, или None если можно.

    'has_tasks'              — на стадии висят задачи (FK RESTRICT);
    'last_stage_of_category' — это последняя открытая или последняя закрытая
                               стадия воронки, без неё воронка сломается.
    """
    if Task.objects.filter(stage=stage).exists():
        return 'has_tasks'
    if _is_last_of_category(stage, stage.category):
        return 'last_stage_of_category'
    return None


def stage_category_change_blocker(stage: TaskStage, new_category: str) -> str | None:
    """
    Почему нельзя сменить категорию стадии, или None если можно.

    'has_tasks' — на стадии лежат карточки. Смена категории перенесла бы их в
        закрытую колонку с пустыми closed_at/resolution (или наоборот), то есть
        развела бы два определения «закрыта»: доктрину (категория стадии) и
        данные (closed_at), на которые смотрят фильтры, просрочка и индексы.
        CHECK в БД это не ловит — он связывает closed_at и resolution между
        собой, но не со стадией. Штатный путь: завести новую стадию и перенести
        карточки через move_task, который поля закрытия проставит корректно.
    'last_stage_of_category' — стадия последняя в своей категории, без неё
        воронка останется, например, без закрытых стадий.
    """
    if new_category == stage.category:
        return None
    if Task.objects.filter(stage=stage).exists():
        return 'has_tasks'
    if _is_last_of_category(stage, stage.category):
        return 'last_stage_of_category'
    return None
