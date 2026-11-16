"""
Обязательность ссылки на запись урока — ЕДИНСТВЕННОЕ место расчёта.

обязательна = правило XOR (группа в исключениях ИЛИ преподаватель в исключениях).
Преподаватель — тот, кто записывает урок (заменяющий при замене).
Спека: docs/specs/2026-11-16-eduson-record-url-rule-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from django.db import transaction

from apps.lessons.models import RecordUrlRule, RecordUrlRuleGroup, RecordUrlRuleTeacher

# Строка правила — singleton с фиксированным pk (см. RecordUrlRule.load).
_RULE_PK = 1


@dataclass(frozen=True)
class RuleSnapshot:
    required: bool
    teacher_ids: frozenset[int]
    group_ids: frozenset[int]

    def is_required(self, *, group_id: int, teacher_id: Optional[int]) -> bool:
        # Совпадение и по группе, и по преподавателю — ОДНО исключение (OR), а не
        # два, гасящих друг друга: иначе добавление группы в исключения тихо
        # возвращало бы требование преподавателю, уже стоящему в исключениях.
        excepted = group_id in self.group_ids or (
            teacher_id is not None and teacher_id in self.teacher_ids)
        return self.required != excepted


def load_rule() -> RuleSnapshot:
    """Снимок правила: три коротких запроса, читать один раз на запрос/ответ.

    Только читает: зовётся на горячем пути (запись урока, выдача групп
    преподавателю), где get_or_create означал бы лишнюю запись и событие журнала.
    Строки ещё нет — действует правило по умолчанию (ссылка обязательна)."""
    required = (RecordUrlRule.objects.filter(pk=_RULE_PK)
                .values_list('required', flat=True).first())
    if required is None:
        return RuleSnapshot(True, frozenset(), frozenset())
    return RuleSnapshot(
        required=required,
        teacher_ids=frozenset(
            RecordUrlRuleTeacher.objects.filter(rule_id=_RULE_PK)
            .values_list('teacher_id', flat=True)),
        group_ids=frozenset(
            RecordUrlRuleGroup.objects.filter(rule_id=_RULE_PK)
            .values_list('group_id', flat=True)),
    )


def _diff_links(model, field: str, wanted: set[int]) -> tuple[set[int], set[int]]:
    """(что удалить, что добавить) — по разнице с текущим набором. Неизменённые
    ссылки не трогаем: delete+insert того же исключения засорял бы журнал."""
    current = set(model.objects.filter(rule_id=_RULE_PK).values_list(field, flat=True))
    return current - wanted, wanted - current


def _apply_links(model, rule: RecordUrlRule, field: str,
                 removed: set[int], added: set[int]) -> None:
    if removed:
        model.objects.filter(rule=rule, **{f'{field}__in': removed}).delete()
    if added:
        # bulk_create сигналов не шлёт, но pghistory пишет события триггерами БД —
        # вставка в журнал попадает.
        model.objects.bulk_create([model(rule=rule, **{field: i}) for i in sorted(added)])


def replace_rule(*, required: bool, teacher_ids: Iterable[int], group_ids: Iterable[int],
                 updated_by: Optional[str]) -> RecordUrlRule:
    """Заменить правило и оба набора исключений целиком, одной транзакцией.

    Существование id проверяет сериализатор; здесь — только запись. Строка правила
    блокируется (select_for_update), чтобы два одновременных сохранения не
    перемешали наборы. Если по существу ничего не изменилось — не пишем ничего:
    ни ссылок, ни save() (иначе auto_now и updated_by дали бы пустое событие
    журнала «изменено правило» без изменений)."""
    RecordUrlRule.load()
    with transaction.atomic():
        rule = RecordUrlRule.objects.select_for_update().get(pk=_RULE_PK)
        t_removed, t_added = _diff_links(RecordUrlRuleTeacher, 'teacher_id', set(teacher_ids))
        g_removed, g_added = _diff_links(RecordUrlRuleGroup, 'group_id', set(group_ids))
        if rule.required == required and not (t_removed or t_added or g_removed or g_added):
            return rule
        _apply_links(RecordUrlRuleTeacher, rule, 'teacher_id', t_removed, t_added)
        _apply_links(RecordUrlRuleGroup, rule, 'group_id', g_removed, g_added)
        # save() и при неизменном required: updated_at/updated_by — подпись
        # «кто менял исключения» на странице настроек.
        rule.required = required
        rule.updated_by = updated_by
        rule.save()
    return rule


def rule_payload() -> dict:
    """Ответ GET/PUT API: исключения с именами, отсортированы по имени (тай-брейк — id)."""
    rule = RecordUrlRule.load()
    teachers = (RecordUrlRuleTeacher.objects.filter(rule=rule)
                .order_by('teacher__name', 'teacher_id').values('teacher_id', 'teacher__name'))
    groups = (RecordUrlRuleGroup.objects.filter(rule=rule)
              .order_by('group__name', 'group_id').values('group_id', 'group__name'))
    return {
        'required': rule.required,
        'teachers': [{'id': t['teacher_id'], 'name': t['teacher__name']} for t in teachers],
        'groups': [{'id': g['group_id'], 'name': g['group__name']} for g in groups],
        'updated_at': rule.updated_at,
        'updated_by': rule.updated_by,
    }
