"""Склейка участников Eduson в учеников по связке признаков (спека §5.2).

В Eduson нет сущности «ученик»: каждая строка participants — ребёнок внутри одной
группы, и один ребёнок в двух группах — две строки. Ни один признак в одиночку
ребёнка не определяет: имя без фамилии, телефон и ссылка CRM бывают общими у
братьев и сестёр.

Две записи — один ученик, только если совпало ВСЁ:
  1. имя ребёнка (normalize.norm_name);
  2. телефон родителя (normalize.norm_phone), причём годный (phone_is_usable):
     по заглушке «79999999999» склеились бы тёзки из разных семей;
  3. возраст отличается не более чем на год или у одной записи не указан;
  4. имя родителя совпадает или у одной записи пусто.
Совпали 1–2, а 3 или 4 противоречат — не склеиваем и кладём пару в manual_review.
Почта, Telegram, ссылка CRM здесь не участвуют вовсе: подтверждать склейку они
могли бы, но решить её в одиночку не вправе, а правило 1–4 уже необходимо и
достаточно. Запись без имени или с негодным телефоном не склеивается ни с кем.

НЕСКОЛЬКО ЗАПИСЕЙ. Новая запись сравнивается с КАЖДОЙ записью уже собранного
ученика, а не с первой: при условиях «±1 год» и «пусто у одной» совместимость
не транзитивна (10 ≈ 11 и 9 ≈ 10, но 9 ≉ 11). Противоречие хотя бы с одной
записью — с этим учеником не склеиваем; на проверку уходит снимок его
participant_ids на момент сравнения плюс новая запись (записи, приклеенные к
нему позже, в этот снимок не попадают).

НЕОДНОЗНАЧНОСТЬ. Если новая запись совместима сразу с несколькими учениками под
тем же ключом (например, без возраста и без имени родителя, а ученики — 7 и
12 лет), выбрать одного значило бы угадать. Запись становится отдельным учеником
и уходит на проверку вместе со всеми подходящими кандидатами.

Неоднозначная запись сама кандидатом для следующих не становится.

ПУСТЫЕ СТРОКИ. Legacy createGroup при пустом списке участников вставляет строку,
где пусто всё (services/schedule.js:89). Это не ребёнок: такие id отдаются в
skipped_empty, чтобы шаг групп их пропустил и показал в отчёте импорта.

Порядок детерминирован: участники обрабатываются по возрастанию id, ученики идут
в порядке своего первого участника. Записи группируются по ключу (имя, телефон),
сравнение — только внутри ключа, без прохода по всем участникам.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apps.eduson_import.normalize import norm_name, norm_phone, phone_is_usable

REASON_AGE = 'возраст'
REASON_PARENT = 'имя родителя'
REASON_AMBIGUOUS = 'неоднозначно: подходит нескольким ученикам'

# Поля ребёнка и родителя: если пусты все — строка пустая (amo_url и notes не в счёт,
# legacy подставляет в пустую строку ссылку CRM группы).
_PERSON_FIELDS = ('child_name', 'child_tg', 'child_phone', 'child_email',
                  'parent_name', 'parent_phone', 'parent_tg', 'parent_email')


@dataclass
class ResolvedStudent:
    """Будущий Student: participant_ids по возрастанию, rows — исходные строки в том же порядке."""
    participant_ids: list[int] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ManualReview:
    participant_ids: list[int]
    reason: str


@dataclass
class Resolution:
    """students — в т.ч. ученики без имени ребёнка (строка только с возрастом или
    контактом): шаг создания учеников обязан это учитывать. skipped_empty — id
    полностью пустых строк participants."""
    students: list[ResolvedStudent]
    manual_review: list[ManualReview]
    skipped_empty: list[int] = field(default_factory=list)


def _age(value) -> int | None:
    # SQLite не держит тип колонки: возраст мог лечь строкой или пустой строкой
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_empty(row: dict) -> bool:
    return _age(row.get('age')) is None and not any(str(row.get(f) or '').strip() for f in _PERSON_FIELDS)


def _conflicts(a: dict, b: dict) -> set[str]:
    reasons = set()
    age_a, age_b = _age(a.get('age')), _age(b.get('age'))
    if age_a is not None and age_b is not None and abs(age_a - age_b) > 1:
        reasons.add(REASON_AGE)
    parent_a, parent_b = norm_name(a.get('parent_name')), norm_name(b.get('parent_name'))
    if parent_a and parent_b and parent_a != parent_b:
        reasons.add(REASON_PARENT)
    return reasons


def _reason_text(reasons: set[str]) -> str:
    return ', '.join(r for r in (REASON_AGE, REASON_PARENT) if r in reasons)


def _merge_key(row: dict) -> tuple[str, str] | None:
    name, phone = norm_name(row.get('child_name')), norm_phone(row.get('parent_phone'))
    return (name, phone) if name and phone_is_usable(phone) else None


def resolve_students(participants: list[dict]) -> Resolution:
    students: list[ResolvedStudent] = []
    review: list[ManualReview] = []
    skipped: list[int] = []
    by_key: dict[tuple[str, str], list[ResolvedStudent]] = {}

    for row in sorted(participants, key=lambda r: r['id']):
        if _is_empty(row):
            skipped.append(row['id'])
            continue
        key = _merge_key(row)
        compatible: list[ResolvedStudent] = []
        for candidate in by_key.get(key, ()) if key else ():
            reasons = set().union(*(_conflicts(row, other) for other in candidate.rows))
            if reasons:
                review.append(ManualReview(candidate.participant_ids + [row['id']], _reason_text(reasons)))
            else:
                compatible.append(candidate)

        if len(compatible) == 1:
            target = compatible[0]
        else:
            if len(compatible) > 1:
                ids = sorted(i for c in compatible for i in c.participant_ids)
                review.append(ManualReview(ids + [row['id']], REASON_AMBIGUOUS))
            target = ResolvedStudent()
            students.append(target)
            # Неоднозначную запись в кандидаты не берём: иначе каждая следующая
            # запись настоящего ребёнка совпала бы и с ней и тоже стала бы спорной.
            if key and not compatible:
                by_key.setdefault(key, []).append(target)
        target.participant_ids.append(row['id'])
        target.rows.append(row)

    return Resolution(students, review, skipped)
