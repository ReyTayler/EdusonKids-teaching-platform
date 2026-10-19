"""
ChangelogRepository — доступ к pghistory_context + event-таблицам.

Лента: пагинация по Context (сортировка по created_at DESC), затем один
агрегатный проход по Events ТОЛЬКО для контекстов страницы — без UNION
по всей истории (производительность, спека §7).
"""
from __future__ import annotations

from typing import Any, Optional

from django.db.models import Exists, OuterRef
from pghistory.models import Context, Events

from apps.changelog import humanize, labels, registry, summary as summary_mod


def _operation_of(metadata: dict) -> str:
    return metadata.get('operation') or labels.resolve_operation(
        metadata.get('method', ''), metadata.get('url', ''),
    )


def _actor_of(metadata: dict) -> Optional[dict]:
    if 'account_id' not in metadata:
        return None
    return {
        'account_id': metadata['account_id'],
        'email': metadata.get('email'),
        'name': metadata.get('name') or metadata.get('email'),
        'role': metadata.get('role'),
    }


def _build_lookups(events: list[dict]) -> summary_mod.Lookups:
    """Bulk-имена для summary: по одному IN-запросу на тип, без N+1."""
    from apps.directions.models import Direction
    from apps.groups.models import Group
    from apps.students.models import Student
    from apps.teachers.models import Teacher

    ids = summary_mod.collect_lookup_ids(events)
    return summary_mod.Lookups(
        groups=dict(Group.objects.filter(id__in=ids['groups'])
                    .values_list('id', 'name')) if ids['groups'] else {},
        students=dict(Student.objects.filter(id__in=ids['students'])
                      .values_list('id', 'full_name')) if ids['students'] else {},
        teachers=dict(Teacher.objects.filter(id__in=ids['teachers'])
                      .values_list('id', 'name')) if ids['teachers'] else {},
        directions=dict(Direction.objects.filter(id__in=ids['directions'])
                        .values_list('id', 'name')) if ids['directions'] else {},
    )


def _reverted_context_ids(ctx_ids: list) -> set[str]:
    """Какие из контекстов уже откатывались (metadata.revert_of у revert-операций)."""
    if not ctx_ids:
        return set()
    wanted = [str(cid) for cid in ctx_ids]
    return set(
        Context.objects.filter(metadata__revert_of__in=wanted)
        .values_list('metadata__revert_of', flat=True)
    )


def _apply_filters(qs, filters: dict[str, Any]):
    actor = filters.get('actor')
    if actor not in (None, ''):
        qs = qs.filter(metadata__email__icontains=str(actor))

    operation = filters.get('operation')
    if operation not in (None, ''):
        if operation == 'changelog.revert':
            qs = qs.filter(metadata__operation='changelog.revert')
        else:
            rule = labels.rule_for_operation(str(operation))
            if rule is None:
                return qs.none()
            method, pattern = rule
            qs = qs.filter(metadata__method=method,
                           metadata__url__regex=pattern.pattern)

    date_from = filters.get('date_from')
    if date_from not in (None, ''):
        qs = qs.filter(created_at__date__gte=date_from)
    date_to = filters.get('date_to')
    if date_to not in (None, ''):
        qs = qs.filter(created_at__date__lte=date_to)

    entity = filters.get('entity')
    if entity not in (None, ''):
        model_label = registry.model_label_for_entity(str(entity))
        if model_label is None:
            return qs.none()
        event_qs = registry.event_model(model_label).objects.filter(
            pgh_context_id=OuterRef('pk'),
        )
        entity_id = filters.get('entity_id')
        if entity_id not in (None, ''):
            event_qs = event_qs.filter(pgh_obj_id=entity_id)
        qs = qs.filter(Exists(event_qs))

    return qs


def get_operation(context_id) -> Optional[dict]:
    """Детали операции: контекст + события с diff. None, если контекста нет."""
    ctx = Context.objects.filter(pk=context_id).values('id', 'created_at', 'metadata').first()
    if ctx is None:
        return None
    meta = ctx['metadata'] or {}

    events = []
    revertable = True
    ev_rows = list(
        Events.objects.filter(pgh_context_id=context_id)
        .order_by('pgh_created_at', 'pgh_id')
        .values('pgh_obj_model', 'pgh_obj_id', 'pgh_label',
                'pgh_data', 'pgh_diff')
    )
    has_forbidden = False
    if not ev_rows:
        revertable = False
    # Один набор bulk-имён на всю операцию — и для summary, и для описаний событий.
    lookups = _build_lookups(ev_rows)
    for ev in ev_rows:
        cfg = registry.TRACKED.get(ev['pgh_obj_model'])
        if cfg is None or not cfg.revertable:
            revertable = False
            has_forbidden = True
        ev['entity'] = registry.entity_of(ev['pgh_obj_model']) or ev['pgh_obj_model']
        human = humanize.humanize_event(ev, lookups)
        events.append({
            'model': ev['pgh_obj_model'],
            'entity': ev['entity'],
            'obj_id': ev['pgh_obj_id'],
            'label': ev['pgh_label'],
            'data': ev['pgh_data'],
            'diff': ev['pgh_diff'],
            'description': human['text'],
            'human': human,
        })

    operation = _operation_of(meta)
    is_revert = operation == 'changelog.revert'
    if is_revert:
        revertable = False  # откат отката запрещён (см. revert.revert_context)
    reverted = str(ctx['id']) in _reverted_context_ids([ctx['id']])
    return {
        'id': str(ctx['id']),
        'occurred_at': ctx['created_at'],
        'actor': _actor_of(meta),
        'operation': operation,
        'summary': summary_mod.build_summary(operation, ev_rows, lookups),
        'url': meta.get('url'),
        'method': meta.get('method'),
        'revertable': revertable and not reverted,
        'reverted': reverted,
        'not_revertable_reason': humanize.not_revertable_reason(
            has_events=bool(ev_rows), has_forbidden=has_forbidden,
            is_revert=is_revert, reverted=reverted,
        ),
        'events': events,
    }


def list_operations(page: int, page_size: int, filters: dict) -> dict:
    qs = _apply_filters(Context.objects.all(), filters).order_by('-created_at', '-id')

    total = qs.count()
    offset = max(0, (page - 1) * page_size)
    contexts = list(qs[offset:offset + page_size].values('id', 'created_at', 'metadata'))
    ctx_ids = [c['id'] for c in contexts]

    # События страницы одним проходом по Events (CTE только по нужным uuid);
    # data/diff нужны генератору описаний (summary_mod).
    per_ctx_events: dict[object, list[dict]] = {cid: [] for cid in ctx_ids}
    all_events: list[dict] = []
    if ctx_ids:
        ev_rows = (Events.objects.filter(pgh_context_id__in=ctx_ids)
                   .order_by('pgh_created_at', 'pgh_id')
                   .values('pgh_context_id', 'pgh_obj_model', 'pgh_obj_id',
                           'pgh_label', 'pgh_data', 'pgh_diff'))
        for ev in ev_rows:
            ev['entity'] = registry.entity_of(ev['pgh_obj_model']) or ev['pgh_obj_model']
            per_ctx_events[ev['pgh_context_id']].append(ev)
            all_events.append(ev)

    lookups = _build_lookups(all_events)
    reverted_ids = _reverted_context_ids(ctx_ids)

    rows = []
    for ctx in contexts:
        meta = ctx['metadata'] or {}
        events = per_ctx_events.get(ctx['id'], [])

        buckets: dict[str, dict[str, int]] = {}
        revertable = bool(events)
        for ev in events:
            cfg = registry.TRACKED.get(ev['pgh_obj_model'])
            if cfg is None or not cfg.revertable:
                revertable = False
            bucket = buckets.setdefault(
                ev['pgh_obj_model'], {'insert': 0, 'update': 0, 'delete': 0})
            if ev['pgh_label'] in bucket:
                bucket[ev['pgh_label']] += 1
        entities = [
            {
                'entity': registry.entity_of(model_label) or model_label,
                'inserts': counts['insert'],
                'updates': counts['update'],
                'deletes': counts['delete'],
            }
            for model_label, counts in sorted(buckets.items())
        ]

        operation = _operation_of(meta)
        if operation == 'changelog.revert':
            revertable = False  # откат отката запрещён (см. revert.revert_context)
        reverted = str(ctx['id']) in reverted_ids
        rows.append({
            'id': str(ctx['id']),
            'occurred_at': ctx['created_at'],
            'actor': _actor_of(meta),
            'operation': operation,
            'summary': summary_mod.build_summary(operation, events, lookups),
            'url': meta.get('url'),
            'method': meta.get('method'),
            'entities': entities,
            'events_total': len(events),
            'revertable': revertable and not reverted,
            'reverted': reverted,
        })

    return {'rows': rows, 'total': total, 'page': page, 'page_size': page_size}
