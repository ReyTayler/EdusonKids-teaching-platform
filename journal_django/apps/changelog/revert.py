"""
Откат операции журнала изменений по pghistory-контексту.

Алгоритм (спека §6):
  1. Собрать события контекста из конкретных event-моделей (типизированные поля —
     сравнение значений без JSON-представлений).
  2. Конфликт-детекция ДО любых записей: текущее состояние каждой строки должно
     совпадать со снапшотом её последнего события в контексте (для delete —
     строка должна отсутствовать). Расхождение → RevertConflict.
  3. Применение в FK-безопасном порядке:
       a) undo-insert: удалить вставленные строки (дети раньше родителей);
       b) undo-delete: вставить удалённые строки (родители раньше детей);
       c) undo-update: вернуть строке состояние ДО контекста (снапшот события,
          предшествующего первому update контекста).
  4. Поправить sequences после вставок с явным PK.

Строки идентифицируются registry.identity (у lesson_attendance составной
реальный PK — pgh_obj_id там не уникален per-row).

Всё в одной transaction.atomic + pghistory.context(operation='changelog.revert')
— сам откат попадает в журнал как новая операция. Повторный откат уже
откаченной операции и откат самого отката запрещены (RevertForbidden).
"""
from __future__ import annotations

import pghistory
from django.db import connection, transaction
from pghistory.models import Context

from apps.changelog import registry


class RevertError(Exception):
    """База ошибок отката (в т.ч. «операция не найдена»)."""


class RevertForbidden(RevertError):
    """Контекст содержит неоткатываемые модели (accounts) или пуст."""


class RevertConflict(RevertError):
    """Данные изменились после операции — откат отклонён."""

    def __init__(self, conflicts: list[dict]):
        self.conflicts = conflicts
        super().__init__(f'{len(conflicts)} конфликт(ов)')


def _tracked_attnames(event_model, model) -> list[str]:
    """attname-поля модели, представленные и в event-модели (без pgh_*)."""
    event_names = {f.name for f in event_model._meta.get_fields()}
    return [
        f.attname for f in model._meta.concrete_fields
        if f.name in event_names or f.attname in event_names
    ]


def _identity_filter(model_label: str, ev) -> dict:
    """Kwargs для однозначного выбора строки по registry.identity."""
    identity = registry.TRACKED[model_label].identity
    return {attname: getattr(ev, attname) for attname in identity}


def _row_key(model_label: str, ev) -> tuple:
    identity = registry.TRACKED[model_label].identity
    return (model_label, tuple(getattr(ev, attname) for attname in identity))


def _load_events(context_id) -> list[tuple[str, object]]:
    """[(model_label, event_instance), ...] всех событий контекста."""
    result = []
    for model_label in registry.TRACKED:
        event_model = registry.event_model(model_label)
        for ev in event_model.objects.filter(pgh_context_id=context_id):
            result.append((model_label, ev))
    return result


def _check_conflicts(last_events: list[tuple[str, object]]) -> list[dict]:
    """Сверка текущего состояния строк с последним снапшотом контекста."""
    conflicts = []
    for model_label, ev in last_events:
        model = registry.tracked_model(model_label)
        event_model = registry.event_model(model_label)
        attnames = _tracked_attnames(event_model, model)
        ident = _identity_filter(model_label, ev)
        current = model.objects.filter(**ident).first()

        entity = registry.TRACKED[model_label].entity
        if ev.pgh_label == 'delete':
            if current is not None:
                conflicts.append({'model': model_label, 'entity': entity,
                                  'obj_id': ev.pgh_obj_id, 'reason': 'row_exists'})
        else:  # insert / update — строка должна существовать и совпадать
            if current is None:
                conflicts.append({'model': model_label, 'entity': entity,
                                  'obj_id': ev.pgh_obj_id, 'reason': 'row_missing'})
                continue
            changed = [a for a in attnames
                       if getattr(current, a) != getattr(ev, a)]
            if changed:
                conflicts.append({'model': model_label, 'entity': entity,
                                  'obj_id': ev.pgh_obj_id,
                                  'reason': 'changed_later', 'fields': changed})
    return conflicts


def _previous_event(model_label: str, ev):
    """Предыдущее событие той же строки (для undo-update)."""
    event_model = registry.event_model(model_label)
    return (event_model.objects
            .filter(pgh_id__lt=ev.pgh_id, **_identity_filter(model_label, ev))
            .order_by('-pgh_id').first())


def _nullable_unique_attnames(model) -> set[str]:
    """attname полей модели с unique=True И null=True.

    Единственные поля, которые безопасно на миг занулить: NOT NULL они не
    нарушат (null=True), а уникальность — ровно та причина, по которой их
    вообще приходится занулять. Нужно для восстановления состояния, когда
    операция внутри одного контекста ПОМЕНЯЛА такое значение местами между
    двумя строками (напр. planned_lessons.fact_lesson_id при plan.resync,
    переставляющем факты между позициями) — прямая запись целевых значений
    «в лоб» упёрлась бы в 23505, т.к. на момент записи первой строки второе
    значение ещё занято её текущим (дореверсным) состоянием.

    Покрыт ТОЛЬКО undo-update. Восстановление удалённых строк (undo-delete) идёт
    раньше и сразу финальными значениями: если удалённая строка держала такое же
    nullable-unique значение, занятое сейчас одной из восстанавливаемых, будет
    тот же 23505. Сегодня недостижимо — ни одна revertable-операция не удаляет
    строку и не переставляет её уникальное значение в одном контексте.
    """
    return {
        f.attname for f in model._meta.concrete_fields
        if getattr(f, 'unique', False) and f.null
    }


def _fix_sequence(model) -> None:
    """После INSERT с явным PK сдвинуть sequence, иначе будущие вставки упадут."""
    table = model._meta.db_table
    pk_col = model._meta.pk.column
    with connection.cursor() as cur:
        cur.execute('SELECT pg_get_serial_sequence(%s, %s)', [table, pk_col])
        seq = cur.fetchone()[0]
        if seq is None:  # текстовый/составной PK — sequence нет
            return
        cur.execute(
            f'SELECT setval(%s, GREATEST((SELECT COALESCE(MAX("{pk_col}"), 1) '
            f'FROM "{table}"), 1))',
            [seq],
        )


def revert_context(context_id) -> dict:
    """Откатить операцию целиком. Возвращает сводку; бросает Revert*-исключения."""
    ctx = Context.objects.filter(pk=context_id).values('metadata').first()
    if ctx is None:
        raise RevertError('Операция не найдена.')

    metadata = ctx['metadata'] or {}
    # Сам откат откатывать нельзя — иначе бесконечный redo-цикл.
    if metadata.get('operation') == 'changelog.revert':
        raise RevertForbidden('Откат операции отката недоступен.')
    # Повторный откат уже откаченной операции запрещён (проверка на уровне кода,
    # не только UI). Предикат metadata__revert_of инкапсулирован в repository —
    # единый источник правды для guard и для UI-признака reverted.
    from apps.changelog.repository import _reverted_context_ids
    if _reverted_context_ids([context_id]):
        raise RevertForbidden('Операция уже откачена.')

    events = _load_events(context_id)
    if not events:
        raise RevertForbidden('Операция не содержит изменений данных.')

    forbidden = sorted({
        ml for ml, _ in events if not registry.TRACKED[ml].revertable
    })
    if forbidden:
        raise RevertForbidden(
            'Откат недоступен: операция затрагивает ' + ', '.join(forbidden),
        )

    inserts = [(ml, ev) for ml, ev in events if ev.pgh_label == 'insert']
    updates = [(ml, ev) for ml, ev in events if ev.pgh_label == 'update']
    deletes = [(ml, ev) for ml, ev in events if ev.pgh_label == 'delete']

    # Конфликты сверяем по ПОСЛЕДНЕМУ событию каждой строки в контексте.
    last_by_row: dict[tuple, tuple] = {}
    for ml, ev in events:
        key = _row_key(ml, ev)
        if key not in last_by_row or ev.pgh_id > last_by_row[key][1].pgh_id:
            last_by_row[key] = (ml, ev)
    conflicts = _check_conflicts(list(last_by_row.values()))
    if conflicts:
        raise RevertConflict(conflicts)

    # undo-update: у строки может быть несколько update в контексте —
    # восстанавливаем к состоянию ДО первого из них.
    first_update_by_row: dict[tuple, tuple] = {}
    for ml, ev in updates:
        key = _row_key(ml, ev)
        if key not in first_update_by_row or ev.pgh_id < first_update_by_row[key][1].pgh_id:
            first_update_by_row[key] = (ml, ev)

    with transaction.atomic(), pghistory.context(
        operation='changelog.revert', revert_of=str(context_id),
    ):
        # a) удалить вставленное: дети раньше родителей
        for ml, ev in sorted(inserts, key=lambda p: -registry.TRACKED[p[0]].topo):
            model = registry.tracked_model(ml)
            model.objects.filter(**_identity_filter(ml, ev)).delete()

        # b) вернуть удалённое: родители раньше детей
        touched_models = set()
        for ml, ev in sorted(deletes, key=lambda p: registry.TRACKED[p[0]].topo):
            model = registry.tracked_model(ml)
            event_model = registry.event_model(ml)
            attnames = _tracked_attnames(event_model, model)
            model(**{a: getattr(ev, a) for a in attnames}).save(force_insert=True)
            touched_models.add(model)

        # c) вернуть обновлённое к состоянию до контекста
        to_restore = []  # (ml, ev, prev, attnames, identity, model)
        for ml, ev in first_update_by_row.values():
            # Строка могла быть вставлена И обновлена в этом же контексте —
            # тогда undo-insert уже удалил её, восстанавливать нечего.
            if _row_key(ml, ev) in {_row_key(iml, iev) for iml, iev in inserts}:
                continue
            model = registry.tracked_model(ml)
            event_model = registry.event_model(ml)
            prev = _previous_event(ml, ev)
            if prev is None:
                raise RevertConflict([{'model': ml,
                                       'entity': registry.TRACKED[ml].entity,
                                       'obj_id': ev.pgh_obj_id,
                                       'reason': 'no_previous_state'}])
            attnames = _tracked_attnames(event_model, model)
            identity = set(registry.TRACKED[ml].identity) | {model._meta.pk.attname}
            to_restore.append((ml, ev, prev, attnames, identity, model))

        # Проход c1: занулить nullable-unique поля у ВСЕХ восстанавливаемых строк
        # затронутых моделей — прежде чем что-либо проставлять целевые значения.
        # Иначе перестановка уникального значения между двумя строками (см.
        # docstring _nullable_unique_attnames) упёрлась бы в 23505 на первой же
        # строке прохода c2: её целевое значение ещё «занято» второй строкой,
        # которая пока не тронута.
        nullable_unique_cache: dict[type, set[str]] = {}
        for ml, ev, prev, attnames, identity, model in to_restore:
            if model not in nullable_unique_cache:
                nullable_unique_cache[model] = _nullable_unique_attnames(model)
            null_fields = nullable_unique_cache[model] & (set(attnames) - identity)
            # Только те, у кого значение реально меняется на не-NULL: строка,
            # которая и так придёт к NULL (или уже в нём), лишнего UPDATE не
            # стоит — это удвоило бы записи и события pghistory на КАЖДОМ
            # откате плана, а не только на редкой перестановке.
            null_fields = {a for a in null_fields if getattr(prev, a, None) is not None}
            if null_fields:
                model.objects.filter(**_identity_filter(ml, ev)).update(
                    **{a: None for a in null_fields},
                )

        # Проход c2: полное восстановление к состоянию до контекста.
        for ml, ev, prev, attnames, identity, model in to_restore:
            model.objects.filter(**_identity_filter(ml, ev)).update(
                **{a: getattr(prev, a) for a in attnames if a not in identity},
            )

        for model in touched_models:
            _fix_sequence(model)

    return {
        'reverted_events': len(events),
        'inserts_undone': len(inserts),
        'deletes_undone': len(deletes),
        'updates_undone': len(first_update_by_row),
    }
