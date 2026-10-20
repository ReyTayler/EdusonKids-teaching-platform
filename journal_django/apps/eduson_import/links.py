"""Чтение и запись таблицы соответствий EdusonLink (см. models.py)."""
from __future__ import annotations

from apps.eduson_import.context import ImportContext
from apps.eduson_import.models import EdusonLink

Kind = EdusonLink.Kind


def load_links(kind: str) -> dict[str, int]:
    """source_id → object_id для одного вида записей."""
    return dict(EdusonLink.objects.filter(kind=kind).values_list('source_id', 'object_id'))


def has_links() -> bool:
    """Связи групп есть — значит, база собрана импортом, который их пишет."""
    return EdusonLink.objects.filter(kind=Kind.GROUP).exists()


def save_links(ctx: ImportContext) -> None:
    """Upsert соответствий из словарей контекста прогона: новые добавляются, у
    существующих обновляется object_id (участник мог перейти к другому ученику).
    Связи записей, которых в этом прогоне нет (группа удалена в старой системе),
    остаются: по ним мягкое обновление узнаёт, что группу надо убрать в архив."""
    pairs = (
        (Kind.DIRECTION, ctx.direction_by_course),
        (Kind.TEACHER, ctx.teacher_by_eduson),
        (Kind.ACCOUNT, ctx.account_by_user),
        (Kind.GROUP, ctx.group_by_eduson),
        (Kind.PARTICIPANT, ctx.student_by_participant),
    )
    rows = [EdusonLink(kind=kind, source_id=str(source), object_id=obj)
            for kind, mapping in pairs for source, obj in mapping.items()]
    EdusonLink.objects.bulk_create(rows, batch_size=1000, update_conflicts=True,
                                   unique_fields=['kind', 'source_id'], update_fields=['object_id'])
