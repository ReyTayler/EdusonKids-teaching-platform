"""
Очеловечивание событий журнала изменений для модалки деталей.

Задача слоя — превратить сырое событие pghistory (англ. имена полей, ISO-даты,
булевы true/false, id связанных строк) в готовый к показу обычному пользователю
объект:

    human = {
        'title':   'Оплата 3 600 ₽ — Иван Петров',   # шапка секции
        'text':    'Оплата 3 600 ₽: Иван Петров',      # фраза-предложение
        'changes': [{'label': 'сумма', 'old': None, 'new': '3 600 ₽'}, ...],
    }

Механизм — интроспекция Django (`model._meta.concrete_fields`), а не только
словари по именам: значение форматируется ПО ТИПУ поля (Boolean → «да»/«нет»,
Date → «ДД.ММ.ГГГГ», Decimal → без хвостовых нулей, FK → имя связанной записи
через Lookups). Поверх интроспекции — точечные словари для денег, дня недели и
статуса планового занятия.

`text` берётся из summary.describe_event — одна формулировка на фразу-предложение
(она же используется в ленте), чтобы не расходиться.

N+1 запрещён: Lookups (имена групп/учеников/преподавателей/направлений) строится
один раз на операцию в repository; здесь работаем только со словарями.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from apps.changelog import registry, summary

# Денежные поля → «3 600 ₽» (разделитель тысяч — узкий неразрывный пробел, копейки
# только если они есть). `amount` СЮДА НЕ входит: у Discount это доля 0..1, не рубли.
MONEY_FIELDS = {'total_amount', 'unit_price', 'price', 'subscription_price', 'penalty',
                # Зарплата и ставки «Мотивации». cancel_value / value сюда НЕ входят:
                # в зависимости от режима это проценты, а не рубли.
                'payment', 'calc_payment', 'calc_penalty',
                'group_rate', 'individual_rate', 'penalty_amount'}

# day_of_week: конвенция проекта Вс=0 (JS getDay), проверена на реальных данных.
DAY_OF_WEEK_RU = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб']


# Технический шум: в insert/delete-снапшоте и в update прятать эти поля.
_NOISE_FIELDS = {'created_at', 'updated_at', 'sheet_row'}

# FK, уже вынесенные в title сущности → не дублировать их в списке изменений
# insert/delete (в update оставляем: смена FK там осмысленна — напр. смена препода).
_TITLE_FK_FIELDS = {
    'planned_lesson': {'group_id'},
    'lesson':         {'group_id'},
    'schedule_slot':  {'group_id'},
    'payment':        {'student_id'},
    'membership':     {'student_id', 'group_id'},
    'attendance':     {'student_id', 'lesson_id'},
    'lesson_feedback': {'group_id'},
    'personal_rate':   {'teacher_id', 'group_id'},
    'personal_rate_term': {'teacher_id', 'group_id'},
    'cancellation_pay': {'teacher_id', 'group_id'},
    'pay_rate_direction': {'direction_id'},
    # reason_id — ссылка на справочник; в снимке записи уже есть reason_text
    # (название на момент отмены), а «причина: #4» читателю ничего не говорит.
    'lesson_cancellation': {'group_id', 'reason_id'},
}


# ---------------------------------------------------------------------------
# Форматирование значений
# ---------------------------------------------------------------------------

# Деньги и даты форматирует summary (описания ленты) — здесь те же функции.
_money = summary.money
_fmt_date = summary.fmt_date


def _fmt_datetime(value: Any) -> str:
    """'2026-07-09T12:00:00+00:00' → '09.07.2026 12:00'."""
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})', str(value))
    return (f'{m.group(3)}.{m.group(2)}.{m.group(1)} {m.group(4)}:{m.group(5)}'
            if m else str(value))


def _day_of_week(value: Any) -> str:
    i = summary._as_int(value)
    return DAY_OF_WEEK_RU[i] if i is not None and 0 <= i < 7 else str(value)


def _field_by_attname(model, attname: str):
    for f in model._meta.concrete_fields:
        if f.attname == attname:
            return f
    return None


def _fk_name(field, value: Any, lk: summary.Lookups) -> str:
    """Имя связанной записи через Lookups; если имени нет — «#id»."""
    iv = summary._as_int(value)
    bucket = summary._FK_BUCKET.get(
        field.related_model._meta.label if field.related_model else '')
    if bucket and iv is not None:
        name = getattr(lk, bucket).get(iv)
        if name:
            return name
    return f'#{value}'


def format_value(model_label: Optional[str], attname: str, value: Any,
                 lk: summary.Lookups) -> str:
    """Очеловечить одно значение поля: пусто → «—», иначе по типу/имени поля."""
    if value is None or value == '':
        return '—'  # em dash

    model = None
    if model_label:
        try:
            model = registry.tracked_model(model_label)
        except Exception:  # noqa: BLE001 — модель может быть недоступна
            model = None
    field = _field_by_attname(model, attname) if model is not None else None

    # FK → имя связанной записи.
    if field is not None and field.is_relation:
        return _fk_name(field, value, lk)

    # Точечные словари поверх интроспекции.
    if attname in MONEY_FIELDS:
        return _money(value)
    if attname == 'day_of_week':
        return _day_of_week(value)
    if attname == 'status':
        return summary.STATUS_RU.get(value, str(value))

    # choices поля (если заданы) → отображаемое значение.
    if field is not None and field.choices:
        return str(dict(field.choices).get(value, value))

    # По типу поля.
    if field is not None:
        internal = field.get_internal_type()
        if internal == 'BooleanField':
            return 'да' if value else 'нет'
        if internal == 'DateField':
            return _fmt_date(value)
        if internal == 'DateTimeField':
            return _fmt_datetime(value)
        if internal == 'TimeField':
            return summary._fmt_time(value)
        if internal == 'DecimalField':
            return summary._fmt_num(value)

    return str(value)


def format_event_value(ev: dict, attname: str, value: Any, lk: summary.Lookups) -> str:
    """format_value для поля события (модель — по pgh_obj_model или сущности)."""
    return format_value(_model_label(ev), attname, value, lk)


def event_field_order(ev: dict, keys) -> list[str]:
    """Поля события в порядке объявления в модели (для описаний ленты)."""
    return _ordered_attnames(_model_label(ev), {k: None for k in keys})

# ---------------------------------------------------------------------------
# title / changes / human
# ---------------------------------------------------------------------------

def _named(entity_ru: str, name: Optional[str], obj_id: Any) -> str:
    return f'{entity_ru} «{name}»' if name else f'{entity_ru} #{obj_id}'


def humanize_title(ev: dict, lk: summary.Lookups) -> str:
    """Русское имя сущности + человеческое имя записи (шапка секции деталей)."""
    entity = ev.get('entity')
    data = ev.get('pgh_data') or {}
    obj_id = ev.get('pgh_obj_id')

    if entity == 'student':
        return f'Ученик {data.get("full_name") or f"#{obj_id}"}'
    if entity == 'teacher':
        return f'Преподаватель {data.get("name") or f"#{obj_id}"}'
    if entity == 'direction':
        return _named('Направление', data.get('name'), obj_id)
    if entity == 'discount':
        return _named('Скидка', data.get('name'), obj_id)
    if entity == 'group':
        return _named('Группа', data.get('name'), obj_id)
    if entity in ('planned_lesson', 'lesson'):
        group = lk.group(data.get('group_id'))
        head = 'Занятие' if entity == 'planned_lesson' else 'Урок'
        num = data.get('lesson_number')
        tail = f' №{summary._fmt_num(num)}' if num is not None else ''
        return f'{head} «{group}»{tail}'
    if entity == 'membership':
        return (f'Членство: {lk.student(data.get("student_id"))} '
                f'→ {lk.group(data.get("group_id"))}')
    if entity == 'attendance':
        return f'Посещаемость: {lk.student(data.get("student_id"))}'
    if entity == 'payment':
        total = data.get('total_amount')
        amount = _money(total) if total is not None else '—'
        return f'Оплата {amount} — {lk.student(data.get("student_id"))}'
    if entity == 'payroll':
        return f'Начисление {lk.teacher(data.get("teacher_id"))}'
    if entity == 'cancellation_pay':
        return (f'Начисление за отмену {lk.teacher(data.get("teacher_id"))}'
                f' — {lk.group(data.get("group_id"))}, {_fmt_date(data.get("lesson_date"))}')
    if entity == 'pay_rate_version':
        return f'Ставки школы с {_fmt_date(data.get("effective_from"))}'
    if entity == 'pay_rate_direction':
        return f'Ставки направления {lk.direction(data.get("direction_id"))}'
    if entity == 'personal_rate':
        return (f'Персональная ставка {lk.teacher(data.get("teacher_id"))}'
                f' — {lk.group(data.get("group_id"))}')
    if entity == 'personal_rate_term':
        start = data.get('valid_from')
        since = f' с {_fmt_date(start)}' if start else ''
        return (f'Период персональной ставки {lk.teacher(data.get("teacher_id"))}'
                f' — {lk.group(data.get("group_id"))}{since}')
    if entity == 'lesson_feedback':
        return f'Отзыв по уроку — {lk.group(data.get("group_id"))}, {data.get("lesson_date")}'
    if entity == 'schedule_slot':
        return f'Слот расписания {lk.group(data.get("group_id"))}'
    if entity == 'account':
        return f'Учётка {data.get("email") or f"#{obj_id}"}'
    if entity == 'cancellation_reason':
        return f'Причина отмены {summary._reason_ident(data)}'
    if entity == 'lesson_cancellation':
        when = summary._fmt_dt(data.get('lesson_date'), data.get('scheduled_time'))
        return f'Отмена урока «{lk.group(data.get("group_id"))}», {when}'

    return f'{summary._ENTITY_RU.get(entity, entity or "Запись")} #{obj_id}'


def _model_label(ev: dict) -> Optional[str]:
    return ev.get('pgh_obj_model') or registry.model_label_for_entity(
        ev.get('entity') or '')


def _pk_attname(model_label: Optional[str]) -> Optional[str]:
    if not model_label:
        return None
    try:
        return registry.tracked_model(model_label)._meta.pk.attname
    except Exception:  # noqa: BLE001
        return None


def _ordered_attnames(model_label: Optional[str], data: dict) -> list[str]:
    """attname'ы в порядке объявления полей модели + хвост неизвестных ключей."""
    ordered: list[str] = []
    if model_label:
        try:
            model = registry.tracked_model(model_label)
            for f in model._meta.concrete_fields:
                if f.attname in data:
                    ordered.append(f.attname)
        except Exception:  # noqa: BLE001
            pass
    for key in data:
        if key not in ordered:
            ordered.append(key)
    return ordered


def humanize_changes(ev: dict, lk: summary.Lookups) -> list[dict]:
    """
    Полностью очеловеченные строки «Поле / Было / Стало».

    update — из pgh_diff (old и new очеловечены); insert — из pgh_data
    ({old: None, new: значение}); delete — зеркально. Для insert/delete скрываем
    шум: null-поля, created_at/updated_at, sheet_row, id/pk и FK, уже
    вынесенные в title.
    """
    model_label = _model_label(ev)
    label = ev['pgh_label']
    pk = _pk_attname(model_label)

    if label == 'update':
        diff = ev.get('pgh_diff') or {}
        hidden = set(_NOISE_FIELDS) | {'id'}
        if pk:
            hidden.add(pk)
        changes = []
        for attname in sorted(diff.keys()):
            if attname in hidden:
                continue
            old, new = diff[attname]
            changes.append({
                'label': summary.FIELD_RU.get(attname, attname),
                'old': format_value(model_label, attname, old, lk),
                'new': format_value(model_label, attname, new, lk),
            })
        return changes

    # insert / delete — снапшот значений строки.
    data = ev.get('pgh_data') or {}
    hidden = set(_NOISE_FIELDS) | {'id'} | _TITLE_FK_FIELDS.get(ev.get('entity'), set())
    if pk:
        hidden.add(pk)
    changes = []
    for attname in _ordered_attnames(model_label, data):
        if attname in hidden:
            continue
        value = data.get(attname)
        if value is None or value == '':
            continue
        formatted = format_value(model_label, attname, value, lk)
        if label == 'insert':
            changes.append({'label': summary.FIELD_RU.get(attname, attname),
                            'old': None, 'new': formatted})
        else:  # delete
            changes.append({'label': summary.FIELD_RU.get(attname, attname),
                            'old': formatted, 'new': None})
    return changes


def humanize_event(ev: dict, lk: summary.Lookups) -> dict:
    """{'title', 'text', 'changes'} для одного события (модалка деталей)."""
    return {
        'title': humanize_title(ev, lk),
        'text': summary.describe_event(ev, lk),
        'changes': humanize_changes(ev, lk),
    }


# ---------------------------------------------------------------------------
# Причина неоткатываемости (для верхнего уровня деталей операции)
# ---------------------------------------------------------------------------

# Неоткатываемые сущности (registry: revertable=False) — во множественном числе
# для причины «затрагивает неоткатываемые данные (…)».
FORBIDDEN_RU = {
    'account': 'учётки',
    'notification_settings': 'настройки уведомлений',
    'telegram_recipient': 'получатели в Telegram',
    'pay_rate_version': 'ставки школы',
    'pay_rate_direction': 'ставки направлений',
    'personal_rate': 'персональные ставки',
    'personal_rate_term': 'периоды персональных ставок',
    'cancellation_reason': 'причины отмен',
    'lesson_cancellation': 'записи об отменах уроков',
}


def not_revertable_reason(*, has_events: bool, has_forbidden: bool,
                          is_revert: bool, reverted: bool,
                          forbidden_entities=()) -> Optional[str]:
    """
    Причина, почему операция не откатывается, либо None (если откат доступен).

    Возвращается придаточным-фрагментом: фронт показывает его в шаблоне
    «Эта операция не может быть отменена: {reason}.» (без точки в самом тексте).
    Смысл согласован с RevertForbidden-сообщениями revert.py.
    forbidden_entities — сущности, из-за которых откат запрещён (по порядку
    появления в операции); без них — прежняя подпись про учётки.
    """
    if not has_events:
        return 'она не содержит изменений данных'
    if has_forbidden:
        names = list(dict.fromkeys(FORBIDDEN_RU.get(e, e) for e in forbidden_entities))
        return f'она затрагивает неоткатываемые данные ({", ".join(names) or "учётки"})'
    if is_revert:
        return 'операции отката не откатываются'
    if reverted:
        return 'она уже откачена'
    return None
