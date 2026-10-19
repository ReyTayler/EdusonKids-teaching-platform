"""
Чтение раздела «Задачи». Возвращает списки dict — сериализаторы на чтении не
используются (паттерн apps/renewals).

Признак просрочки выводится здесь, а не хранится: due_date < сегодня и задача
не закрыта.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from django.db.models import Count, IntegerField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce

from apps.core.utils.dates import msk_today
from apps.taskboard.models import Task, TaskActivity

# Связи, без которых каждая карточка давала бы лишние запросы.
_RELATED = ('board', 'stage', 'created_by', 'student', 'group')


def _comments_count_subquery():
    """
    Счётчик комментариев ПОДЗАПРОСОМ, а не аннотацией с JOIN.

    JOIN потребовал бы GROUP BY по всем колонкам семи присоединённых таблиц из
    select_related — на VPS с двумя ядрами это заметно дороже подзапроса,
    который ложится на готовый индекс task_activity_task_idx.
    """
    return Subquery(
        TaskActivity.objects
        .filter(task_id=OuterRef('id'), kind=TaskActivity.Kind.COMMENT)
        .values('task_id')
        .annotate(n=Count('id'))
        .values('n'),
        output_field=IntegerField(),
    )


def _base_queryset():
    return (Task.objects
            .select_related(*_RELATED)
            .prefetch_related('assignees')
            .annotate(comments_count=Coalesce(_comments_count_subquery(), 0)))


def _row(task: Task, *, today: date) -> dict:
    return {
        'id': task.id,
        'board_id': task.board_id,
        'stage_id': task.stage_id,
        'stage_label': task.stage.label,
        'stage_category': task.stage.category,
        'stage_color': task.stage.color,
        'title': task.title,
        'description': task.description,
        # Исполнителей может быть несколько: карточка рисует их аватарами в ряд.
        'assignees': [
            {'id': a.id, 'full_name': a.full_name}
            for a in sorted(task.assignees.all(), key=lambda a: (a.full_name or '', a.id))
        ],
        'created_by_id': task.created_by_id,
        'created_by_name': task.created_by.full_name if task.created_by else None,
        'student_id': task.student_id,
        'student_name': task.student.full_name if task.student else None,
        'group_id': task.group_id,
        'group_name': task.group.name if task.group else None,
        'comments_count': getattr(task, 'comments_count', 0) or 0,
        'due_date': task.due_date.isoformat() if task.due_date else None,
        'priority': task.priority,
        'resolution': task.resolution,
        'is_closed': task.closed_at is not None,
        'is_overdue': (
            task.closed_at is None
            and task.due_date is not None
            and task.due_date < today
        ),
        'closed_at': task.closed_at.isoformat() if task.closed_at else None,
        'stage_entered_at': task.stage_entered_at.isoformat(),
        'updated_at': task.updated_at.isoformat(),
        'created_at': task.created_at.isoformat(),
    }


# Номер задачи в строке поиска: «#124» и «124». До 18 цифр — длиннее числа не
# влезает в bigint, и Postgres ответил бы ошибкой вместо пустой выдачи.
_TASK_ID_RE = re.compile(r'^#?(\d{1,18})$')


def _search_q(term: str, *, prefix: str = '') -> Q:
    """
    Строка поиска. Похоже на номер — ищем по номеру: «#124» в разделе значит
    конкретную задачу, а не заголовки, где встретилось «124».

    Иначе — OR по заголовку, ученику, исполнителю и метке. Join по меткам
    размножает строки, поэтому вызывающий обязан спросить _needs_distinct().
    """
    match = _TASK_ID_RE.match(term.strip())
    if match:
        return Q(**{f'{prefix}id': int(match.group(1))})
    return (
        Q(**{f'{prefix}title__icontains': term})
        | Q(**{f'{prefix}student__full_name__icontains': term})
        | Q(**{f'{prefix}assignees__full_name__icontains': term})
    )


def _due_q(value: str, *, prefix: str = '') -> Q:
    """
    Значения селектора «Срок». `overdue` повторяет отдельный булев фильтр
    `overdue` — тот остался ради существующих вызовов (блок задач ученика),
    здесь он нужен как одно из значений единого селектора.
    """
    today = date.fromisoformat(msk_today())
    if value == 'today':
        return Q(**{f'{prefix}due_date': today})
    if value == 'week':
        monday = today - timedelta(days=today.weekday())
        return Q(**{f'{prefix}due_date__gte': monday,
                    f'{prefix}due_date__lte': monday + timedelta(days=6)})
    if value == 'overdue':
        return Q(**{f'{prefix}closed_at__isnull': True,
                    f'{prefix}due_date__lt': today})
    if value == 'none':
        return Q(**{f'{prefix}due_date__isnull': True})
    return Q()


def _needs_distinct(params: dict) -> bool:
    """
    Нужен ли DISTINCT. Только текстовый поиск join'ит метки и может выдать одну
    задачу дважды; поиск по номеру и остальные фильтры — нет. Вешать DISTINCT
    всегда дорого: он ложится на каждый запрос доски.
    """
    term = (params.get('q') or '').strip()
    return bool(term) and not _TASK_ID_RE.match(term)


def _filters_q(params: dict, *, prefix: str = '') -> Q:
    """
    Условия фильтра одним объектом Q — чтобы их можно было применить и к
    выборке карточек, и к агрегату счётчиков колонок. Две копии этой логики
    неизбежно разъехались бы, и счётчик в шапке колонки врал бы относительно
    списка под ней.

    `prefix` — путь до полей Task от модели, на которой строится запрос:
    пустая строка для queryset задач, `tasks__` для аннотации на TaskStage.
    """
    def f(field: str) -> str:
        return f'{prefix}{field}'

    q = Q()
    if params.get('board_id'):
        q &= Q(**{f('board_id'): params['board_id']})
    if params.get('stage_id'):
        q &= Q(**{f('stage_id'): params['stage_id']})
    if params.get('assignee_id'):
        # «Среди исполнителей есть этот», а не «единственный исполнитель — этот».
        # Имя параметра оставлено прежним: его шлют сегмент «Мои» и фильтр.
        q &= Q(**{f('assignees__id'): params['assignee_id']})
    if params.get('student_id'):
        q &= Q(**{f('student_id'): params['student_id']})
    if params.get('group_id'):
        q &= Q(**{f('group_id'): params['group_id']})
    if params.get('priority'):
        q &= Q(**{f('priority'): params['priority']})
    if params.get('only_open'):
        # Буквально closed_at IS NULL — иначе не подхватится частичный индекс.
        q &= Q(**{f('closed_at__isnull'): True})
    if params.get('overdue'):
        q &= Q(**{f('closed_at__isnull'): True,
                   f('due_date__lt'): date.fromisoformat(msk_today())})
    if params.get('due'):
        q &= _due_q(params['due'], prefix=prefix)
    if params.get('q'):
        q &= _search_q(params['q'], prefix=prefix)
    return q


def _apply_filters(qs, params: dict):
    qs = qs.filter(_filters_q(params))
    return qs.distinct() if _needs_distinct(params) else qs


def tasks_queryset(params: dict):
    """
    Отфильтрованный и отсортированный queryset — для СЕРВЕРНОЙ пагинации.

    Вьюха накладывает на него LIMIT/OFFSET и превращает в строки только
    страницу: иначе воронка с тысячей задач читалась бы целиком ради 20 карточек.
    """
    return _apply_filters(_base_queryset(), params).order_by('due_date', '-created_at')


def rows(tasks, *, today: date | None = None) -> list[dict]:
    """Превратить набор задач (обычно одну страницу) в строки выдачи."""
    today = today or date.fromisoformat(msk_today())
    return [_row(t, today=today) for t in tasks]


def list_tasks(params: dict) -> list[dict]:
    """
    Полный список без пагинации — для мест, где выборка заведомо мала
    (блок задач на странице ученика, колонка доски). Для списков произвольного
    размера вьюха обязана идти через tasks_queryset + rows.
    """
    return rows(tasks_queryset(params))


def get_task(task_id: int) -> dict | None:
    """Одна карточка тем же форматом, что и строка списка."""
    today = date.fromisoformat(msk_today())
    task = _base_queryset().filter(id=task_id).first()
    return _row(task, today=today) if task else None


def list_column(stage_id: int) -> list[dict]:
    """Карточки одной колонки доски."""
    return list_tasks({'stage_id': stage_id})


def list_week(*, date_from: date, date_to: date, params: dict | None = None) -> list[dict]:
    """
    Задачи со сроком в диапазоне (границы включительно). Без срока — не попадают.

    Принимает те же фильтры, что и список: фильтр-бар на странице общий для
    доски и недельного вида, иначе переключение вида молча показывало бы задачи
    всех воронок.
    """
    today = date.fromisoformat(msk_today())
    qs = _apply_filters(_base_queryset(), params or {})
    qs = (qs.filter(due_date__gte=date_from, due_date__lte=date_to)
            .order_by('due_date', '-created_at'))
    return [_row(t, today=today) for t in qs]


def activity_row(entry: TaskActivity) -> dict:
    """Одна запись ленты тем же форматом, что и строка списка."""
    return {
        'id': entry.id,
        'kind': entry.kind,
        'author_id': entry.author_id,
        'author_name': entry.author.full_name if entry.author else None,
        'text': entry.text,
        'meta': entry.meta,
        'created_at': entry.created_at.isoformat(),
    }


def list_activity(task_id: int) -> list[dict]:
    """Лента карточки, старые записи сверху."""
    entries = (TaskActivity.objects
               .select_related('author')
               .filter(task_id=task_id)
               .order_by('created_at', 'id'))
    return [activity_row(e) for e in entries]


def column_counts(board_id: int, params: dict | None = None) -> list[dict]:
    """
    Колонки доски со счётчиками — ОДИН лёгкий агрегат.

    Карточки сюда НЕ входят: доска не грузится одним запросом, иначе воронка
    с тысячей закрытых задач кладёт страницу.

    Фильтры — те же, что и у списка карточек (фильтр-бар общий), иначе
    счётчик в шапке колонки расходится со списком под ней. Аннотация Count
    считается от TaskStage, поэтому условия идут с префиксом `tasks__`.
    """
    from django.db.models import Count

    from apps.taskboard.models import TaskStage

    stages = (TaskStage.objects
              .filter(board_id=board_id)
              .annotate(task_count=Count(
                  'tasks',
                  filter=_filters_q(params or {}, prefix='tasks__'),
                  distinct=_needs_distinct(params or {}),
              ))
              .order_by('sort_order', 'id'))
    return [{
        'stage_id': s.id,
        'label': s.label,
        'color': s.color,
        'category': s.category,
        'sort_order': s.sort_order,
        'count': s.task_count,
    } for s in stages]
