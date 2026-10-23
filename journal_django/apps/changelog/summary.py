"""
Человекочитаемые описания операций журнала изменений.

Вход: ключ операции + события контекста (pgh_data/pgh_diff из Events) +
bulk-словари имён (группы/ученики/преподаватели/уроки/стадии — собираются
repository одним IN-запросом на тип, без N+1).

Принцип (по образцу журнала старой системы Eduson, legacy/client AuditPage):
описание отвечает «что сделали, с чем и как изменилось» — «Перенос ПИ1001 №5:
09.07.2026 12:00 → 10.07.2026 12:00», «Выключена персональная ставка Иванов —
ПИ1033», а не «Персональная ставка #10: создание (+ ещё изменений: 25)».
Мотивация сотрудников конфиденциальна: ленту видит и admin, поэтому описания
операций зарплаты и ставок — без сумм (MOTIVATION_ENTITIES); суммы — только
в describe_event для модалки деталей, которую отдаёт лишь superadmin.
Специфичные шаблоны — по ключу операции (_BUILDERS) и по типу записи
(describe_event); остальное — generic-фолбэк с подписями полей и значениями
«было → стало». Описание НИКОГДА не пустое.
"""
from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

# Статусы планового занятия (apps/scheduling/occurrences.py) → русский.
STATUS_RU = {
    'pending':   'запланирован',
    'overdue':   'просрочен',
    'done':      'проведён',
    'cancelled': 'отменён',
    'moved':     'перенесён',
}

_LABEL_RU = {'insert': 'создание', 'update': 'изменение', 'delete': 'удаление'}

_NARROW = ' '  # NARROW NO-BREAK SPACE — разделитель тысяч и перед знаком ₽

# Сколько изменённых полей расписывать «было → стало»; остальные — счётчиком.
_DIFF_LIMIT = 3

# Имена уроков (группа №N, дата) нужны только операциям над одним-двумя
# уроками (ручная правка начисления, правка посещаемости). Пересчёт ставок
# трогает тысячи начислений — тянуть их уроки ради описания незачем.
_LESSON_LOOKUP_LIMIT = 5

# Поля, значения которых в описание ленты не выносим: персональные данные и
# длинные тексты. В строке ленты — только подпись поля, значения — в модалке.
_VALUE_HIDDEN_FIELDS = {
    'full_name', 'phone', 'email', 'telegram', 'birth_date', 'crm_link', 'platform_id',
    'parent1_name', 'parent1_phone', 'parent1_email', 'parent1_telegram',
    'parent2_name', 'parent2_phone', 'parent2_email',
    'vk_chat', 'note', 'comment', 'description', 'body', 'rule_note', 'record_url',
}

# Технический шум: в описании не упоминаем вовсе.
_NOISE_FIELDS = {'id', 'created_at', 'updated_at', 'sheet_row', 'token_version',
                 'failed_login_count', 'locked_until', 'last_login', 'stage_entered_at',
                 'calc_payment', 'calc_penalty', 'rule_note'}


# ---------------------------------------------------------------------------
# Форматирование
# ---------------------------------------------------------------------------

def _fmt_time(t: Any) -> str:
    """'12:00:00' → '12:00'."""
    s = str(t or '')
    return s[:5] if len(s) >= 5 else s


def _fmt_num(n: Any) -> str:
    """20.0 → '20', 20.5 → '20.5'."""
    if n is None:
        return '?'
    try:
        f = float(n)
        return str(int(f)) if f == int(f) else str(f)
    except (TypeError, ValueError):
        return str(n)


def fmt_date(value: Any) -> str:
    """'2026-07-09' → '09.07.2026'."""
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', str(value))
    return f'{m.group(3)}.{m.group(2)}.{m.group(1)}' if m else str(value)


def money(value: Any) -> str:
    """3600 → «3 600 ₽», 3600.50 → «3 600,50 ₽» (узкий пробел, копейки при наличии)."""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    neg = d < 0
    d = -d if neg else d
    int_part = int(d)
    grouped = f'{int_part:,}'.replace(',', _NARROW)
    if d == int_part:
        body = grouped
    else:
        cents = f'{d:.2f}'.split('.')[1]
        body = f'{grouped},{cents}'
    sign = '−' if neg else ''  # MINUS SIGN
    return f'{sign}{body}{_NARROW}₽'


def _fmt_dt(date: Any, time: Any) -> str:
    """Дата и время занятия: '09.07.2026 12:00'."""
    d = fmt_date(date) if date else ''
    return f'{d} {_fmt_time(time)}'.strip()


def _count(n: int) -> str:
    """2115 → '2 115'."""
    return f'{n:,}'.replace(',', _NARROW)


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


class Lookups:
    """Имена сущностей по id (наполняется repository bulk-запросами)."""

    def __init__(self, groups=None, students=None, teachers=None, directions=None,
                 lessons=None, renewal_stages=None):
        self.groups: dict[int, str] = groups or {}
        self.students: dict[int, str] = students or {}
        self.teachers: dict[int, str] = teachers or {}
        self.directions: dict[int, str] = directions or {}
        # lesson_id → «ПИ1001 №5 (09.07.2026)» (repository._lesson_titles).
        self.lessons: dict[int, str] = lessons or {}
        self.renewal_stages: dict[int, str] = renewal_stages or {}

    def group(self, gid) -> str:
        return self.groups.get(_as_int(gid)) or f'группа #{gid}'

    def student(self, sid) -> str:
        return self.students.get(_as_int(sid)) or f'ученик #{sid}'

    def teacher(self, tid) -> str:
        return self.teachers.get(_as_int(tid)) or f'преп. #{tid}'

    def direction(self, did) -> str:
        return self.directions.get(_as_int(did)) or f'направление #{did}'

    def lesson(self, lid) -> str:
        return self.lessons.get(_as_int(lid)) or f'урок #{lid}'

    def stage(self, sid) -> str:
        if sid is None:
            return '—'
        return self.renewal_stages.get(_as_int(sid)) or f'стадия #{sid}'


def lesson_title(group_name: str, lesson_number: Any, lesson_date: Any) -> str:
    """Подпись урока для Lookups.lessons: «ПИ1001 №5 (09.07.2026)»."""
    return f'{group_name} №{_fmt_num(lesson_number)} ({fmt_date(lesson_date)})'


# Связанная модель pghistory (related_model._meta.label) → атрибут-словарь Lookups.
# Используется humanize._fk_name для показа имени вместо id у любого FK-поля.
_FK_BUCKET = {
    'groups.Group': 'groups',
    'students.Student': 'students',
    'teachers.Teacher': 'teachers',
    'directions.Direction': 'directions',
    'lessons.Lesson': 'lessons',
    'renewals.RenewalStage': 'renewal_stages',
}

# attname FK-полей → атрибут-словарь Lookups (для сбора id, которые надо разрешить
# в имена: и из снапшота data, и из обеих сторон diff). stage_id здесь нет: это имя
# носят и сделка продления, и карточка задачи — стадии собираются по сущности.
_FK_ATTNAME_BUCKET = {
    'group_id': 'groups',
    'student_id': 'students',
    'teacher_id': 'teachers',
    'substitute_teacher_id': 'teachers',
    'direction_id': 'directions',
}

# Сущность → поля со стадией продления.
_STAGE_FIELDS = {
    'renewal_deal': ('stage_id',),
    'renewal_activity': ('from_stage_id', 'to_stage_id'),
}

# Сущности, у которых lesson_id стоит показать «группа №N (дата)».
_LESSON_ENTITIES = {'payroll', 'attendance', 'lesson_feedback'}


def _as_int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _add_ids(bucket: set, data: dict, diff: dict, attname: str) -> None:
    v = _as_int(data.get(attname))
    if v is not None:
        bucket.add(v)
    # обе стороны diff: описание показывает и старое, и новое имя
    for side in diff.get(attname) or ():
        sv = _as_int(side)
        if sv is not None:
            bucket.add(sv)


def collect_lookup_ids(events: list[dict]) -> dict[str, set]:
    """Какие имена понадобятся summary/humanize для этой пачки событий."""
    ids: dict[str, set] = {b: set() for b in _FK_ATTNAME_BUCKET.values()}
    ids['lessons'] = set()
    ids['renewal_stages'] = set()

    lesson_events_per_ctx = Counter(
        ev.get('pgh_context_id') for ev in events if ev.get('entity') in _LESSON_ENTITIES)
    for ev in events:
        data = ev.get('pgh_data') or {}
        diff = ev.get('pgh_diff') or {}
        for attname, bucket in _FK_ATTNAME_BUCKET.items():
            _add_ids(ids[bucket], data, diff, attname)
        for attname in _STAGE_FIELDS.get(ev.get('entity'), ()):
            _add_ids(ids['renewal_stages'], data, diff, attname)
        if (ev.get('entity') in _LESSON_ENTITIES
                and lesson_events_per_ctx[ev.get('pgh_context_id')] <= _LESSON_LOOKUP_LIMIT):
            _add_ids(ids['lessons'], data, diff, 'lesson_id')
    return ids


# ---------------------------------------------------------------------------
# Подписи сущностей и полей
# ---------------------------------------------------------------------------

_ENTITY_RU = {
    'direction': 'Направление', 'teacher': 'Преподаватель', 'student': 'Ученик',
    'discount': 'Скидка', 'account': 'Учётка',
    'group': 'Группа', 'schedule_slot': 'Слот расписания', 'membership': 'Членство',
    'planned_lesson': 'Плановое занятие', 'lesson': 'Урок',
    'attendance': 'Посещаемость', 'payment': 'Оплата', 'payroll': 'Начисление',
    'lesson_feedback': 'Отзыв по уроку',
    'pay_rate_version': 'Ставки школы', 'pay_rate_direction': 'Ставки направления',
    'personal_rate': 'Персональная ставка', 'cancellation_pay': 'Начисление за отмену',
    'personal_rate_term': 'Период персональной ставки',
    'renewal_pipeline': 'Воронка продлений', 'renewal_stage': 'Стадия продлений',
    'renewal_deal': 'Сделка продления', 'renewal_activity': 'Запись ленты продления',
    'task_board': 'Доска задач', 'task_stage': 'Этап задач', 'task': 'Задача',
    'task_activity': 'Запись ленты задачи',
    'notification_settings': 'Настройки уведомлений',
    'telegram_recipient': 'Получатель в Telegram',
    'absence_resolution': 'Пропуск', 'lesson_skip': 'Неоплачиваемый пропуск',
}

# Сущность → (как сказать о создании, как — об удалении): «Новая группа «ПИ1001»».
_CREATED_DELETED_RU = {
    'direction': ('Новое направление', 'Удалено направление'),
    'teacher': ('Новый преподаватель', 'Удалён преподаватель'),
    'student': ('Новый ученик', 'Удалён ученик'),
    'discount': ('Новая скидка', 'Удалена скидка'),
    'account': ('Новая учётка', 'Удалена учётка'),
    'group': ('Новая группа', 'Удалена группа'),
    'renewal_pipeline': ('Новая воронка продлений', 'Удалена воронка продлений'),
    'renewal_stage': ('Новая стадия продлений', 'Удалена стадия продлений'),
    'task_board': ('Новая доска задач', 'Удалена доска задач'),
    'task_stage': ('Новый этап задач', 'Удалён этап задач'),
    'task': ('Новая задача', 'Удалена задача'),
}

# Сущность → «сколько их» для хвоста описания: «(также: занятий плана — 48)».
_ENTITY_MANY_RU = {
    'planned_lesson': 'занятий плана', 'payroll': 'начислений',
    'cancellation_pay': 'оплат отмен', 'schedule_slot': 'слотов расписания',
    'membership': 'членств', 'attendance': 'отметок посещаемости', 'lesson': 'уроков',
    'lesson_feedback': 'отзывов', 'group': 'групп', 'student': 'учеников',
    'account': 'учёток', 'direction': 'направлений', 'teacher': 'преподавателей',
    'payment': 'оплат', 'renewal_deal': 'сделок продления',
    'renewal_activity': 'записей ленты продлений', 'task_activity': 'записей ленты задач',
    'personal_rate_term': 'периодов ставки', 'pay_rate_direction': 'ставок направлений',
    'absence_resolution': 'пропусков', 'lesson_skip': 'неоплачиваемых пропусков',
}


# Русские подписи полей для описаний и модалки деталей. Ключи — реальные
# attname трекаемых моделей (registry.py); неизвестные поля показываем как есть.
FIELD_RU = {
    'name': 'название', 'full_name': 'ФИО', 'email': 'email', 'phone': 'телефон',
    'username': 'логин', 'active': 'активность', 'color': 'цвет',
    'sheet_row': 'строка листа', 'is_individual': 'индивидуальное',
    'birth_date': 'дата рождения',
    'crm_link': 'ссылка в CRM', 'telegram': 'Telegram',
    'parent1_name': 'родитель 1', 'parent1_phone': 'телефон родителя 1',
    'parent1_email': 'email родителя 1', 'parent1_telegram': 'Telegram родителя 1',
    'parent2_name': 'родитель 2', 'parent2_phone': 'телефон родителя 2',
    'parent2_email': 'email родителя 2',
    'platform_id': 'ID платформы', 'manager_id': 'менеджер', 'assignee_id': 'ответственный менеджер',
    'vk_chat': 'VK-чат', 'note': 'комментарий', 'comment': 'комментарий',
    'record_url': 'ссылка на запись',
    'direction': 'направление', 'direction_id': 'направление',
    'teacher': 'преподаватель', 'teacher_id': 'преподаватель',
    'substitute_teacher_id': 'замена',
    'original_teacher': 'исходный преподаватель',
    'group': 'группа', 'group_id': 'группа',
    'student': 'ученик', 'student_id': 'ученик',
    'price': 'цена', 'subscription_price': 'цена абонемента',
    'unit_price': 'цена за единицу', 'total_amount': 'сумма', 'amount': 'сумма',
    'subscriptions_count': 'кол-во абонементов', 'penalty': 'штраф',
    'paid_at': 'дата оплаты',
    'parent_payment_id': 'доплата к оплате', 'subscription_index': 'номер абонемента',
    'duration': 'длительность', 'lesson_duration_minutes': 'длительность',
    'day_of_week': 'день недели', 'start_time': 'время начала',
    'effective_from': 'действует с', 'effective_to': 'действует по',
    'scheduled_date': 'плановая дата', 'scheduled_time': 'плановое время',
    'moved_from_date': 'перенесено с', 'moved_to_date': 'перенесено на',
    'status': 'статус', 'lesson_number': 'номер занятия', 'seq': 'порядковый номер',
    'lesson_date': 'дата урока', 'lesson_type': 'тип урока',
    'lessons_done': 'проведено занятий', 'lessons_per_week': 'занятий в неделю',
    'total_lessons': 'всего занятий', 'remaining': 'осталось',
    'lessons_total': 'уроков в группе',
    'lesson_number_offset': 'сдвиг нумерации уроков',
    'present': 'присутствие', 'present_count': 'присутствовало',
    'total_students': 'всего учеников', 'start_date': 'дата начала',
    'group_start_date': 'дата старта группы',
    'frozen_until_month': 'заморозка до месяца',
    # Подписей enrollment_status/frozen_from/frozen_until здесь намеренно НЕТ:
    # students/0016 удалила эти колонки и из теневой таблицы students_studentevent,
    # поэтому построчный дифф по ним больше не встречается. Сами исторические
    # события заморозки/отказа из ленты не исчезли — их операция резолвится по
    # сохранённому url (LEGACY-правила в labels.py), теряется только дифф полей.
    # Ссылка на урок (lessons.id): payroll и отзыв по уроку. У посещаемости
    # lesson_id скрыт как часть заголовка (humanize._TITLE_FK_FIELDS), у
    # резолюций пропусков поля называются missed_lesson_id/fact_lesson_id.
    'lesson_id': 'урок',
    # Отзыв по уроку (apps.feedback)
    'took_place': 'урок состоялся', 'understanding': 'понимание', 'interest': 'интерес',
    'tech_problems': 'тех. проблемы', 'submitted_at': 'отправлено',
    'created_at': 'создано', 'updated_at': 'обновлено',
    # Зарплата и «Мотивация» (apps.payroll)
    'payment': 'начислено', 'calc_payment': 'расчётная сумма',
    'calc_penalty': 'расчётный штраф', 'is_manual': 'правка вручную',
    'rule_note': 'как посчитано',
    'group_rate': 'ставка группового', 'individual_rate': 'ставка индивидуального',
    'cancel_mode': 'оплата отмены', 'cancel_value': 'размер оплаты отмены',
    'penalty_enabled': 'штраф включён', 'penalty_mode': 'штраф начисляется',
    'penalty_amount': 'размер штрафа',
    'version_id': 'версия ставок', 'planned_lesson_id': 'занятие',
    'lesson_from': 'с урока', 'lesson_to': 'по урок', 'kind': 'вид', 'value': 'значение',
    'valid_from': 'действует с', 'valid_until': 'действует до (не включительно)',
    'is_active': 'включена', 'deleted_at': 'удалена', 'rate_id': 'персональная ставка',
    'created_by_id': 'кто создал',
    # Учётки
    'role': 'роль', 'twofa_method': 'способ 2FA', 'twofa_enabled': '2FA включена',
    'twofa_confirmed_at': '2FA подтверждена',
    # Продления и задачи
    'stage_id': 'стадия', 'cycle_no': 'цикл', 'due_at': 'срок', 'outcome_at': 'дата итога',
    'reason_code': 'причина', 'title': 'название', 'due_date': 'срок',
    'priority': 'приоритет', 'resolution': 'итог', 'closed_at': 'закрыта',
    'label': 'название',
}


def _fields_ru(keys) -> str:
    """Список изменённых полей русскими подписями (неизвестные — как есть)."""
    return ', '.join(FIELD_RU.get(k, k) for k in sorted(keys)) or '—'


def _value(ev: dict, attname: str, value: Any, lk: Lookups) -> str:
    # humanize импортирует этот модуль на уровне модуля — обратный импорт
    # только здесь, иначе цикл. Форматирование по типу поля живёт там одно.
    from apps.changelog import humanize
    return humanize.format_event_value(ev, attname, value, lk)


def _diff_text(ev: dict, lk: Lookups, *, skip=(), limit: int = _DIFF_LIMIT) -> str:
    """«преподаватель: Иванов → Петров; длительность: 60 → 90» по pgh_diff события.

    Персональные данные и длинные тексты — только подписью поля; после `limit`
    полей — счётчик остальных."""
    from apps.changelog import humanize
    diff = ev.get('pgh_diff') or {}
    keys = [k for k in humanize.event_field_order(ev, diff)
            if k not in _NOISE_FIELDS and k not in skip]
    if not keys:
        return 'без видимых изменений'
    parts = []
    for attname in keys[:limit]:
        label = FIELD_RU.get(attname, attname)
        if attname in _VALUE_HIDDEN_FIELDS:
            parts.append(label)
            continue
        old, new = diff[attname]
        parts.append(f'{label}: {_value(ev, attname, old, lk)} → {_value(ev, attname, new, lk)}')
    rest = len(keys) - limit
    if rest > 0:
        parts.append(f'ещё {rest} {_plural(rest, "поле", "поля", "полей")}')
    return '; '.join(parts)


def _generic_name(data: dict) -> Optional[str]:
    for key in ('name', 'full_name', 'title', 'label', 'email', 'username'):
        if data.get(key):
            return str(data[key])
    return None


def _ident(ev: dict) -> str:
    name = _generic_name(ev.get('pgh_data') or {})
    return f'«{name}»' if name else f'#{ev.get("pgh_obj_id")}'


def _describe_generic(ev: dict, entity_label: str, lk: Optional[Lookups] = None) -> str:
    """Фолбэк-описание события: имя записи + «поле: было → стало»."""
    lk = lk or Lookups()
    entity = ev.get('entity')
    diff = ev.get('pgh_diff') or {}
    ident = _ident(ev)
    label = ev['pgh_label']
    if label == 'update':
        if diff.get('active') == [True, False]:
            return f'{entity_label} {ident}: в архив'
        if diff.get('active') == [False, True]:
            return f'{entity_label} {ident}: восстановление'
        return f'{entity_label} {ident}: {_diff_text(ev, lk)}'
    verbs = _CREATED_DELETED_RU.get(entity)
    if verbs:
        return f'{verbs[0] if label == "insert" else verbs[1]} {ident}'
    return f'{entity_label} {ident}: {_LABEL_RU[label]}'


def _entity_label(entity: Optional[str]) -> str:
    return _ENTITY_RU.get(entity, entity or 'Запись')


# ---------------------------------------------------------------------------
# Шаблоны по типам записей
# ---------------------------------------------------------------------------

_DAY_RU = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб']  # Вс=0, как day_of_week


def _slot_text(data: dict) -> str:
    """«Пн 17:00»."""
    i = _as_int(data.get('day_of_week'))
    day = _DAY_RU[i] if i is not None and 0 <= i < 7 else '?'
    return f'{day} {_fmt_time(data.get("start_time"))}'


def _planned_lesson_phrase(ev: dict, lk: Lookups) -> Optional[str]:
    data = ev.get('pgh_data') or {}
    diff = ev.get('pgh_diff') or {}
    group = lk.group(data.get('group_id'))
    num = _fmt_num(data.get('lesson_number'))

    if ev['pgh_label'] == 'insert':
        return f'Доп. занятие {group}: {_fmt_dt(data.get("scheduled_date"), data.get("scheduled_time"))}' \
            if data.get('seq') is None else None  # курсовые insert'ы описывает plan.generate

    if ev['pgh_label'] == 'update':
        if 'scheduled_date' in diff or 'scheduled_time' in diff:
            old_d, new_d = diff.get('scheduled_date', [data.get('scheduled_date')] * 2)
            old_t, new_t = diff.get('scheduled_time', [data.get('scheduled_time')] * 2)
            return (f'Перенос {group} №{num}: '
                    f'{_fmt_dt(old_d, old_t)} → {_fmt_dt(new_d, new_t)}')
        when = _fmt_dt(data.get('scheduled_date'), data.get('scheduled_time'))
        if 'substitute_teacher_id' in diff:
            old_s, new_s = diff['substitute_teacher_id']
            if new_s is None:
                return f'Снята замена {group} №{num} ({when}): {lk.teacher(old_s)}'
            return (f'Замена {group} №{num} ({when}): '
                    f'{lk.teacher(old_s or data.get("teacher_id"))} → {lk.teacher(new_s)}')
        if 'teacher_id' in diff:
            old_t, new_t = diff['teacher_id']
            return (f'Смена преподавателя {group} №{num}: '
                    f'{lk.teacher(old_t)} → {lk.teacher(new_t)}')
        if 'status' in diff:
            old_s, new_s = diff['status']
            return (f'Статус {group} №{num}: '
                    f'{STATUS_RU.get(old_s, old_s)} → {STATUS_RU.get(new_s, new_s)}')
    return None


_KIND_TEXT = {
    'fixed': lambda v: f'{money(v)} вместо ставки',
    'plus_fixed': lambda v: f'+{money(v)} к ставке',
    'plus_percent': lambda v: f'+{_fmt_num(v)} % к ставке',
}


def _term_text(data: dict) -> str:
    """Параметры периода персональной ставки: «700 ₽ вместо ставки, уроки 5–10»."""
    kind = _KIND_TEXT.get(data.get('kind'))
    value = kind(data.get('value')) if kind else f'{data.get("kind")} {_fmt_num(data.get("value"))}'
    lf, lt = data.get('lesson_from'), data.get('lesson_to')
    if lf is None and lt is None:
        lessons = 'все уроки'
    elif lf == lt:
        lessons = f'урок {_fmt_num(lf)}'
    else:
        lessons = f'уроки {_fmt_num(lf)}–{_fmt_num(lt)}'
    return f'{value}, {lessons}'


def _rate_pair(teacher_id, group_id, lk: Lookups) -> str:
    return f'{lk.teacher(teacher_id)} — {lk.group(group_id)}'


def _cancel_text(data: dict) -> str:
    v = data.get('cancel_value')
    return f'{_fmt_num(v)} %' if data.get('cancel_mode') == 'percent' else money(v)


def _rates_text(data: dict) -> str:
    """Ставки версии: «групповой 1 000 ₽, индивидуальный 600 ₽, отмена 50 %»."""
    parts = [f'групповой {money(data.get("group_rate"))}',
             f'индивидуальный {money(data.get("individual_rate"))}',
             f'отмена {_cancel_text(data)}']
    if data.get('penalty_enabled'):
        per = 'за ученика' if data.get('penalty_mode') == 'per_student' else 'за урок'
        parts.append(f'штраф за поздний отчёт {money(data.get("penalty_amount"))} {per}')
    return ', '.join(parts)


def _direction_rates_text(data: dict) -> str:
    parts = []
    if data.get('group_rate') is not None:
        parts.append(f'групповой {money(data["group_rate"])}')
    if data.get('individual_rate') is not None:
        parts.append(f'индивидуальный {money(data["individual_rate"])}')
    return ', '.join(parts) or 'как по умолчанию'


def _payment_diff(ev: dict) -> str:
    diff = ev.get('pgh_diff') or {}
    data = ev.get('pgh_data') or {}
    if 'payment' in diff:
        old, new = diff['payment']
        text = f'{money(old)} → {money(new)}'
    else:
        text = money(data.get('payment'))
    if 'penalty' in diff:
        old, new = diff['penalty']
        text += f', штраф {money(old)} → {money(new)}'
    return text


def describe_event(ev: dict, lk: Lookups) -> str:
    """
    Человекочитаемое описание ОДНОГО события (для модалки деталей).

    ev — dict со структурой ev_rows из repository.get_operation: pgh_label /
    pgh_data / pgh_diff / pgh_obj_id + entity. Никогда не возвращает пустую
    строку: незнакомое событие описывает generic-фолбэком.
    """
    entity = ev.get('entity')
    label = ev['pgh_label']
    data = ev.get('pgh_data') or {}
    diff = ev.get('pgh_diff') or {}

    # --- Плановое занятие ---
    if entity == 'planned_lesson':
        phrase = _planned_lesson_phrase(ev, lk)
        if phrase:
            return phrase
        group = lk.group(data.get('group_id'))
        num = _fmt_num(data.get('lesson_number'))
        when = _fmt_dt(data.get('scheduled_date'), data.get('scheduled_time'))
        if label == 'insert':  # курсовой insert (seq не None)
            return f'Занятие {group} №{num}: {when}'
        if label == 'delete':
            return f'Удалено занятие {group} №{num}: {when}'
        return f'Занятие {group} №{num} ({when}): {_diff_text(ev, lk)}'

    # --- Урок (факт) ---
    if entity == 'lesson':
        group = lk.group(data.get('group_id'))
        num = _fmt_num(data.get('lesson_number'))
        date = fmt_date(data.get('lesson_date'))
        if label == 'insert':
            return f'Проведён урок {group} №{num} ({date})'
        if label == 'delete':
            return f'Удалён урок {group} №{num} ({date})'
        return f'Урок {group} №{num} ({date}): {_diff_text(ev, lk)}'

    # --- Посещаемость ---
    if entity == 'attendance':
        student = lk.student(data.get('student_id'))
        if label == 'delete':
            return f'Посещаемость удалена: {student}'
        if label == 'insert':
            state = 'был' if data.get('present') else 'не был'
            return f'Отмечен: {student} — {state}'
        present = diff.get('present')
        if present == [False, True]:
            state = 'был'
        elif present == [True, False]:
            state = 'не был'
        else:
            state = 'был' if data.get('present') else 'не был'
        return f'{student}: {state}'

    # --- Оплата ---
    if entity == 'payment':
        student = lk.student(data.get('student_id'))
        is_refund = data.get('kind') == 'refund'
        amt_raw = data.get('total_amount')
        amount = _fmt_num(abs(float(amt_raw)) if (is_refund and amt_raw is not None) else amt_raw)
        if label == 'delete':
            return (f'Отменён возврат {amount} ₽: {student}' if is_refund
                    else f'Удалена оплата {amount} ₽: {student}')
        if label == 'insert':
            if is_refund:
                lc = _fmt_num(abs(float(data.get('lessons_count') or 0)))
                return f'Возврат {amount} ₽ ({lc} уроков): {student}'
            lc = data.get('lessons_count')
            tag = '' if (lc is None or float(lc) % 4 == 0) else f' ({_fmt_num(lc)} уроков)'
            return f'Оплата {amount} ₽{tag}: {student}'
        return f'Оплата {student}: {_diff_text(ev, lk)}'

    # --- Членство ---
    if entity == 'membership':
        student = lk.student(data.get('student_id'))
        group = lk.group(data.get('group_id'))
        if label == 'insert':
            return f'Зачисление: {student} → {group}'
        if label == 'delete':
            return f'Отчисление: {student} из {group}'
        return f'Членство {student} в {group}: {_diff_text(ev, lk)}'

    # --- Слот расписания ---
    if entity == 'schedule_slot':
        group = lk.group(data.get('group_id'))
        if label == 'insert':
            return f'Расписание {group}: добавлено {_slot_text(data)}'
        if label == 'delete':
            return f'Расписание {group}: убрано {_slot_text(data)}'
        return f'Расписание {group} ({_slot_text(data)}): {_diff_text(ev, lk)}'

    # --- Начисление за урок ---
    if entity == 'payroll':
        who = f'{lk.teacher(data.get("teacher_id"))} за {lk.lesson(data.get("lesson_id"))}'
        if label == 'insert':
            return f'Начисление {who}: {money(data.get("payment"))}'
        if label == 'delete':
            return f'Удалено начисление {who}'
        manual = ' (вручную)' if data.get('is_manual') else ''
        return f'Начисление {who}: {_payment_diff(ev)}{manual}'

    # --- Начисление за отмену ---
    if entity == 'cancellation_pay':
        who = (f'{lk.teacher(data.get("teacher_id"))} — {lk.group(data.get("group_id"))}, '
               f'{_fmt_dt(data.get("lesson_date"), data.get("scheduled_time"))}')
        if label == 'insert':
            return f'Оплата отмены {who}: {money(data.get("payment"))}'
        if label == 'delete':
            return f'Удалена оплата отмены {who}'
        manual = ' (вручную)' if data.get('is_manual') else ''
        return f'Оплата отмены {who}: {_payment_diff(ev)}{manual}'

    # --- Ставки школы ---
    if entity == 'pay_rate_version':
        since = fmt_date(data.get('effective_from'))
        if label == 'insert':
            return f'Ставки школы с {since}: {_rates_text(data)}'
        if label == 'delete':
            return f'Удалены ставки школы с {since}'
        return f'Ставки школы с {since}: {_diff_text(ev, lk)}'

    if entity == 'pay_rate_direction':
        direction = lk.direction(data.get('direction_id'))
        if label == 'insert':
            return f'Особые ставки {direction}: {_direction_rates_text(data)}'
        if label == 'delete':
            return f'Убраны особые ставки {direction}'
        return f'Особые ставки {direction}: {_diff_text(ev, lk)}'

    # --- Персональная ставка ---
    if entity == 'personal_rate':
        pair = _rate_pair(data.get('teacher_id'), data.get('group_id'), lk)
        if label == 'insert':
            return f'Новая персональная ставка {pair}'
        if label == 'delete':
            return f'Удалена персональная ставка {pair}'
        if diff.get('is_active') == [True, False]:
            return f'Выключена персональная ставка {pair}'
        if diff.get('is_active') == [False, True]:
            return f'Включена персональная ставка {pair}'
        if 'deleted_at' in diff and diff['deleted_at'][1]:
            return f'Удалена персональная ставка {pair}'
        return f'Персональная ставка {pair}: {_diff_text(ev, lk)}'

    if entity == 'personal_rate_term':
        pair = _rate_pair(data.get('teacher_id'), data.get('group_id'), lk)
        since = f' с {fmt_date(data["valid_from"])}' if data.get('valid_from') else ''
        if label == 'insert':
            return f'Персональная ставка {pair}{since}: {_term_text(data)}'
        if label == 'delete':
            return f'Удалён период персональной ставки {pair}: {_term_text(data)}'
        if set(diff) == {'valid_until'}:
            old, new = diff['valid_until']
            if new:
                return f'Персональная ставка {pair} ({_term_text(data)}) действует до {fmt_date(new)}'
            return f'Персональная ставка {pair} ({_term_text(data)}) снова действует без срока'
        return f'Персональная ставка {pair}{since}: {_diff_text(ev, lk)}'

    # --- Сделка продления ---
    if entity == 'renewal_deal':
        who = f'{lk.student(data.get("student_id"))} (цикл {data.get("cycle_no")})'
        if label == 'insert':
            return f'Новая сделка продления {who}: {lk.stage(data.get("stage_id"))}'
        if label == 'delete':
            return f'Удалена сделка продления {who}'
        if 'stage_id' in diff:
            old, new = diff['stage_id']
            return f'Продление {who}: {lk.stage(old)} → {lk.stage(new)}'
        return f'Продление {who}: {_diff_text(ev, lk)}'

    # --- Всё остальное: имя записи + «поле: было → стало» ---
    return _describe_generic(ev, _entity_label(entity), lk)


# ---------------------------------------------------------------------------
# Описание операции целиком
# ---------------------------------------------------------------------------

def _first(by_entity: dict, entity: str, label: Optional[str] = None) -> Optional[dict]:
    for ev in by_entity.get(entity, ()):
        if label is None or ev['pgh_label'] == label:
            return ev
    return None


def _recalc_tail(by_entity: dict) -> str:
    """«; начисления пересчитаны» — побочный пересчёт зарплат операции (без цифр:
    мотивация конфиденциальна, см. MOTIVATION_ENTITIES)."""
    if by_entity.get('payroll') or by_entity.get('cancellation_pay'):
        return '; начисления пересчитаны'
    return ''


def _others_tail(by_entity: dict, skip=()) -> str:
    """«(также: занятий плана — 48, начислений — 3)» — что ещё задела операция."""
    parts = [f'{_ENTITY_MANY_RU.get(e, _entity_label(e).lower())} — {_count(len(evs))}'
             for e, evs in by_entity.items()
             if e not in skip and e not in MOTIVATION_ENTITIES and evs]
    return f' (также: {", ".join(parts)})' if parts else ''


def _inserted_counts(by_entity: dict, order) -> str:
    parts = []
    for entity in order:
        n = sum(1 for ev in by_entity.get(entity, ()) if ev['pgh_label'] == 'insert')
        if n:
            parts.append(f'{_ENTITY_MANY_RU.get(entity, entity)} — {_count(n)}')
    return ', '.join(parts)


# --- Группы и расписание ---

def _sum_group_create(op, by_entity, lk):
    ev = _first(by_entity, 'group', 'insert')
    if ev is None:
        return None
    data = ev['pgh_data'] or {}
    kind = 'индивидуальная' if data.get('is_individual') else 'групповая'
    text = (f'Новая группа «{data.get("name")}»: {lk.direction(data.get("direction_id"))}, '
            f'{lk.teacher(data.get("teacher_id"))}, {kind}')
    slots = [_slot_text(e['pgh_data'] or {}) for e in by_entity.get('schedule_slot', ())
             if e['pgh_label'] == 'insert']
    if slots:
        text += f', {", ".join(slots)}'
    planned = sum(1 for e in by_entity.get('planned_lesson', ()) if e['pgh_label'] == 'insert')
    if planned:
        text += f'; план — {_count(planned)} {_plural(planned, "занятие", "занятия", "занятий")}'
    return text


def _sum_group_update(op, by_entity, lk):
    ev = _first(by_entity, 'group', 'update')
    if ev is None:
        return None
    if (ev.get('pgh_diff') or {}).get('active') == [True, False]:
        return f'Группа {_ident(ev)} в архив'
    return (f'Изменена группа {_ident(ev)}: {_diff_text(ev, lk)}'
            + _others_tail(by_entity, skip=('group',)))


def _sum_group_delete(op, by_entity, lk):
    ev = _first(by_entity, 'group')
    if ev is None:
        return None
    if ev['pgh_label'] == 'delete':
        return f'Удалена группа {_ident(ev)}' + _others_tail(by_entity, skip=('group',))
    return f'Группа {_ident(ev)} в архив' + _others_tail(by_entity, skip=('group',))


def _sum_schedule_change(op, by_entity, lk):
    slots = by_entity.get('schedule_slot', [])
    planned = by_entity.get('planned_lesson', [])
    anchor = (slots or planned or by_entity.get('group') or [None])[0]
    if anchor is None:
        return None
    data = anchor.get('pgh_data') or {}
    group = lk.group(data.get('group_id') if anchor['entity'] != 'group' else anchor.get('pgh_obj_id'))
    old = [_slot_text(e['pgh_data'] or {}) for e in slots if e['pgh_label'] == 'delete']
    new = [_slot_text(e['pgh_data'] or {}) for e in slots if e['pgh_label'] == 'insert']
    if old or new:
        text = f'Изменено расписание {group}: {", ".join(old) or "—"} → {", ".join(new) or "—"}'
    else:
        text = f'Изменено расписание {group}'
    if planned:
        positions = len({e.get('pgh_obj_id') for e in planned})
        text += f' (занятий плана изменено — {_count(positions)})'
    return text


def _sum_change_teacher_permanent(op, by_entity, lk):
    ev = _first(by_entity, 'group', 'update')
    if ev is None or 'teacher_id' not in (ev.get('pgh_diff') or {}):
        return None
    old, new = ev['pgh_diff']['teacher_id']
    text = f'Смена преподавателя группы {_ident(ev)}: {lk.teacher(old)} → {lk.teacher(new)}'
    planned = by_entity.get('planned_lesson', [])
    if planned:
        first = min((e['pgh_data'] or {}).get('scheduled_date') or '' for e in planned)
        text += (f' с {fmt_date(first)} (занятий — '
                 f'{_count(len({e.get("pgh_obj_id") for e in planned}))})')
    return text


def _sum_plan_generate(op, by_entity, lk):
    planned = by_entity.get('planned_lesson', [])
    if not planned:
        return None
    inserts = sum(1 for e in planned if e['pgh_label'] == 'insert')
    group = lk.group((planned[0].get('pgh_data') or {}).get('group_id'))
    return f'План {group}: создано занятий — {_count(inserts)}'


def _sum_plan_resync(op, by_entity, lk):
    planned = by_entity.get('planned_lesson', [])
    if not planned:
        return None
    # Считаем ПОЗИЦИИ, а не события: починка пишет строку дважды (сначала
    # снимает привязку, потом ставит новую — fact_lesson уникален), и len(planned)
    # завысил бы счёт вдвое. Без этой ветки сработал бы фолбэк и описал бы
    # пакетную починку фразой первой позиции — ложное описание денежно
    # значимой операции.
    group = lk.group((planned[0].get('pgh_data') or {}).get('group_id'))
    positions = len({e.get('pgh_obj_id') for e in planned})
    return f'Починка плана {group}: позиций изменено — {positions}'


def _sum_plan_cancel(op, by_entity, lk):
    for ev in by_entity.get('planned_lesson', ()):
        if 'status' in (ev.get('pgh_diff') or {}):
            data = ev.get('pgh_data') or {}
            text = (f'Отмена занятия {lk.group(data.get("group_id"))} '
                    f'№{_fmt_num(data.get("lesson_number"))} '
                    f'({_fmt_dt(data.get("scheduled_date"), data.get("scheduled_time"))})')
            if _first(by_entity, 'cancellation_pay', 'insert') is not None:
                text += '; начислена оплата за отмену'  # сумма конфиденциальна
            return text
    return None


# --- Уроки ---

def _sum_lesson(op, by_entity, lk):
    ev = (by_entity.get('lesson') or [None])[0]
    if ev is None:
        return None
    data = ev.get('pgh_data') or {}
    group = lk.group(data.get('group_id'))
    num = _fmt_num(data.get('lesson_number'))
    date = fmt_date(data.get('lesson_date'))
    att = by_entity.get('attendance', [])
    if ev['pgh_label'] == 'insert':
        present = sum(1 for a in att if (a.get('pgh_data') or {}).get('present'))
        text = f'Проведён урок {group} №{num} ({date})'
        if att:
            text += f': были {present} из {len(att)}'
        return text
    if ev['pgh_label'] == 'delete':
        return f'Удалён урок {group} №{num} ({date})'
    return (f'Изменён урок {group} №{num} ({date}): {_diff_text(ev, lk)}'
            + _others_tail(by_entity, skip=('lesson',)))


def _sum_attendance_update(op, by_entity, lk):
    att = by_entity.get('attendance', [])
    if not att:
        return None
    data = att[0].get('pgh_data') or {}
    student = lk.student(data.get('student_id'))
    present = (att[0].get('pgh_diff') or {}).get('present')
    arrow = ' → был' if present == [False, True] else (' → не был' if present == [True, False] else '')
    where = f' {lk.lesson(data.get("lesson_id"))}' if _as_int(data.get('lesson_id')) in lk.lessons else ''
    return f'Посещаемость{where}: {student}{arrow}'


# --- Членства ---

def _sum_membership(op, by_entity, lk):
    memberships = by_entity.get('membership', [])
    if not memberships:
        return None
    data = memberships[0].get('pgh_data') or {}
    student = lk.student(data.get('student_id'))
    group = lk.group(data.get('group_id'))
    if op == 'membership.create':
        return f'Зачисление: {student} → {group}'
    if op == 'membership.delete':
        return f'Отчисление: {student} из {group}'
    if op in ('membership.transfer', 'membership.place') and len(memberships) >= 2:
        # >= 2, не == 2: Фаза 1b (place_student_in_group._seed_transfer_continuation)
        # может дописать ещё одну строку-событие (UPDATE lessons_done на ТОЙ ЖЕ новой
        # membership сразу после её создания) — это тот же «новый» ряд, просто с ещё
        # одним снимком; берём ПОСЛЕДНИЙ такой (актуальное состояние), не первый.
        old_ev = next(
            (e for e in memberships if (e.get('pgh_diff') or {}).get('active') == [True, False]),
            None,
        )
        new_ev = next((e for e in reversed(memberships) if e is not old_ev), None)
        if old_ev is not None and new_ev is not None:
            new_data = new_ev.get('pgh_data') or {}
            to_group = lk.group(new_data.get('group_id'))
            from_group = lk.group((old_ev.get('pgh_data') or {}).get('group_id'))
            return f'Перевод: {student} из {from_group} в {to_group}'
    if op == 'membership.place':
        # Одно событие: запись с нуля или повторная запись/перевод из
        # неактивной группы (источник уже неактивен, потому события два не набралось).
        if data.get('transferred_from_id'):
            return f'Запись с историей: {student} → {group}'
        return f'Запись в группу: {student} → {group}'
    return describe_event(memberships[0], lk)


# --- Оплаты ---

def _sum_payment(op, by_entity, lk):
    payments = by_entity.get('payment', [])
    if not payments:
        return None
    data = payments[0].get('pgh_data') or {}
    student = lk.student(data.get('student_id'))
    is_refund = data.get('kind') == 'refund'
    if is_refund:
        # Возврат — по строке на каждое направление остатка (refund_student),
        # поэтому в сводке операции показываем сумму по всем строкам.
        amount = _fmt_num(sum(
            abs(float((e.get('pgh_data') or {}).get('total_amount') or 0))
            for e in payments
            if (e.get('pgh_data') or {}).get('kind') == 'refund'
        ))
    else:
        amount = _fmt_num(data.get('total_amount'))
    if payments[0]['pgh_label'] == 'delete':
        verb = 'Отменён возврат' if is_refund else 'Удалена оплата'
    else:
        verb = 'Возврат' if is_refund else 'Оплата'
    return f'{verb} {amount} ₽: {student}'


# --- Зарплата и «Мотивация» ---
#
# КОНФИДЕНЦИАЛЬНО (решение владельца 2026-10-23): суммы, ставки, проценты и
# штрафы мотивации видит только superadmin. Ленту журнала читает и admin,
# поэтому описания ленты здесь — формальные, без единой цифры мотивации:
# «Изменена персональная ставка Иванов — ПИ1001». Значения «было/стало» есть
# только в деталях операции (модалка), а её отдаёт лишь IsSuperAdmin.

# Сущности мотивации: ни в описании ленты, ни в его хвосте их значения не выводим.
MOTIVATION_ENTITIES = frozenset({
    'payroll', 'cancellation_pay', 'pay_rate_version', 'pay_rate_direction',
    'personal_rate', 'personal_rate_term',
})


def _sum_payroll_edit(op, by_entity, lk):
    ev = _first(by_entity, 'payroll', 'update')
    if ev is None:
        return None
    data = ev.get('pgh_data') or {}
    who = f'{lk.teacher(data.get("teacher_id"))} за {lk.lesson(data.get("lesson_id"))}'
    if op == 'payroll.reset':
        return f'Сброшена ручная правка начисления: {who}'
    return f'Начисление изменено вручную: {who}'


def _sum_cancellation_edit(op, by_entity, lk):
    ev = _first(by_entity, 'cancellation_pay', 'update')
    if ev is None:
        return None
    data = ev.get('pgh_data') or {}
    who = (f'{lk.teacher(data.get("teacher_id"))} — {lk.group(data.get("group_id"))}, '
           f'{_fmt_dt(data.get("lesson_date"), data.get("scheduled_time"))}')
    if op == 'payroll.cancellation_reset':
        return f'Сброшена ручная правка оплаты отмены: {who}'
    return f'Оплата отмены изменена вручную: {who}'


def _sum_rate_version(op, by_entity, lk):
    version = _first(by_entity, 'pay_rate_version')
    tail = _recalc_tail(by_entity)
    if version is None:
        return f'Изменены ставки школы{tail}' if by_entity.get('pay_rate_direction') else None
    since = fmt_date((version.get('pgh_data') or {}).get('effective_from'))
    if op == 'payroll.rate_version_delete' or version['pgh_label'] == 'delete':
        return f'Удалены ставки школы с {since}{tail}'
    if version['pgh_label'] == 'insert':
        return f'Новые ставки школы с {since}{tail}'
    return f'Изменены ставки школы с {since}{tail}'


def _sum_personal_rate(op, by_entity, lk):
    rate = _first(by_entity, 'personal_rate')
    anchor = rate or _first(by_entity, 'personal_rate_term')
    if anchor is None:
        return None
    data = anchor.get('pgh_data') or {}
    pair = _rate_pair(data.get('teacher_id'), data.get('group_id'), lk)
    verb = {
        'payroll.personal_rate_create': 'Новая персональная ставка',
        'payroll.personal_rate_deactivate': 'Выключена персональная ставка',
        'payroll.personal_rate_activate': 'Включена персональная ставка',
        'payroll.personal_rate_delete': 'Удалена персональная ставка',
    }.get(op, 'Изменена персональная ставка')
    return f'{verb} {pair}{_recalc_tail(by_entity)}'


def _sum_recalculate(op, by_entity, lk):
    return 'Пересчёт зарплат'


def _motivation_phrase(ev: dict, lk: Lookups) -> str:
    """Формальное описание события мотивации для ленты — без сумм и ставок."""
    entity = ev.get('entity')
    data = ev.get('pgh_data') or {}
    verb = {'insert': 'создание', 'update': 'изменение', 'delete': 'удаление'}[ev['pgh_label']]
    if entity == 'payroll':
        return (f'Начисление {lk.teacher(data.get("teacher_id"))} '
                f'за {lk.lesson(data.get("lesson_id"))}: {verb}')
    if entity == 'cancellation_pay':
        return (f'Оплата отмены {lk.teacher(data.get("teacher_id"))} — '
                f'{lk.group(data.get("group_id"))}: {verb}')
    if entity in ('personal_rate', 'personal_rate_term'):
        return (f'Персональная ставка {_rate_pair(data.get("teacher_id"), data.get("group_id"), lk)}: '
                f'{verb}')
    return f'Ставки школы: {verb}'


# --- Перенос и обновление данных Eduson ---

_IMPORT_ORDER = ('direction', 'teacher', 'account', 'student', 'group', 'membership',
                 'schedule_slot', 'planned_lesson', 'lesson', 'attendance',
                 'lesson_feedback', 'renewal_deal')


def _sum_eduson_import(op, by_entity, lk):
    counts = _inserted_counts(by_entity, _IMPORT_ORDER)
    return f'Перенос данных из старой системы: {counts or "без изменений"}'


def _sum_eduson_sync(op, by_entity, lk):
    """Обновление с прода: по группам — что поменялось, по остальным — счётчики."""
    per_group: dict[int, list[str]] = {}
    names: dict[int, str] = {}
    for ev in by_entity.get('group', ()):
        gid = _as_int(ev.get('pgh_obj_id'))
        names[gid] = (ev.get('pgh_data') or {}).get('name') or lk.group(gid)
        if ev['pgh_label'] == 'insert':
            per_group.setdefault(gid, []).append('новая группа')
        elif ev['pgh_label'] == 'update':
            per_group.setdefault(gid, []).append(_diff_text(ev, lk))
    slot_changes: dict[int, tuple[list, list]] = {}
    for ev in by_entity.get('schedule_slot', ()):
        data = ev.get('pgh_data') or {}
        old, new = slot_changes.setdefault(_as_int(data.get('group_id')), ([], []))
        (old if ev['pgh_label'] == 'delete' else new).append(_slot_text(data))
    for gid, (old, new) in slot_changes.items():
        if sorted(old) == sorted(new):
            continue  # слоты пересозданы без изменений
        per_group.setdefault(gid, []).append(
            f'расписание {", ".join(old) or "—"} → {", ".join(new) or "—"}')

    parts = [f'{names.get(gid) or lk.group(gid)} — {", ".join(items)}'
             for gid, items in per_group.items()]
    shown = parts[:3]
    if len(parts) > 3:
        rest = len(parts) - 3
        shown.append(f'и ещё {rest} {_plural(rest, "группа", "группы", "групп")}')
    for entity in ('student', 'membership'):
        evs = by_entity.get(entity, ())
        added = sum(1 for e in evs if e['pgh_label'] == 'insert')
        changed = len(evs) - added
        if added:
            shown.append(f'новых {_ENTITY_MANY_RU[entity]} — {_count(added)}')
        if changed:
            shown.append(f'изменено {_ENTITY_MANY_RU[entity]} — {_count(changed)}')
    return f'Обновление данных с прода: {"; ".join(shown) or "без изменений"}'


# --- Учётки ---

_TWOFA_METHOD_RU = {'totp': 'приложение', 'email': 'код на почту'}

_ACCOUNT_PHRASES = {
    'account.reset_password': 'Сброс пароля',
    'account.reset_2fa': 'Сброс 2FA',
    'account.twofa_disable': 'Выключена 2FA',
    'account.invite_create': 'Приглашение',
    'account.invite_revoke': 'Отозвано приглашение',
    'account.invite_accept': 'Учётка активирована',
}


def _sum_account(op, by_entity, lk):
    ev = _first(by_entity, 'account')
    if ev is None:
        return None
    data = ev.get('pgh_data') or {}
    email = data.get('email') or f'#{ev.get("pgh_obj_id")}'
    if op in ('account.twofa_setup', 'account.twofa_enable'):
        method = _TWOFA_METHOD_RU.get(data.get('twofa_method'), data.get('twofa_method') or '?')
        verb = 'Начата настройка 2FA' if op == 'account.twofa_setup' else 'Включена 2FA'
        return f'{verb} ({method}): {email}'
    if op in _ACCOUNT_PHRASES:
        return f'{_ACCOUNT_PHRASES[op]}: {email}'
    if ev['pgh_label'] == 'insert':
        role = _value(ev, 'role', data.get('role'), lk)
        return f'Новая учётка {email} ({role.lower()})'
    if ev['pgh_label'] == 'delete':
        return f'Удалена учётка {email}'
    diff = ev.get('pgh_diff') or {}
    if diff.get('is_active') == [True, False]:
        return f'Учётка {email} выключена'
    if diff.get('is_active') == [False, True]:
        return f'Учётка {email} включена'
    return f'Изменена учётка {email}: {_diff_text(ev, lk)}'


# --- Ученики ---

def _sum_student(op, by_entity, lk):
    ev = _first(by_entity, 'student')
    if ev is None:
        return None
    name = (ev.get('pgh_data') or {}).get('full_name') or f'#{ev.get("pgh_obj_id")}'
    if ev['pgh_label'] == 'insert':
        return f'Новый ученик {name}'
    if ev['pgh_label'] == 'delete':
        return f'Удалён ученик {name}'
    if op == 'student.manager_update':
        return f'Сменён менеджер ученика {name}'
    return f'Изменена карточка ученика {name}: {_diff_text(ev, lk)}'


# --- Продления ---

_RENEWAL_VERBS = {
    'renewal.reopen': 'переоткрыто',
    'renewal.unfreeze': 'возвращено в работу',
}


def _sum_renewal(op, by_entity, lk):
    ev = _first(by_entity, 'renewal_deal')
    if ev is None:
        if op == 'renewal.comment':
            act = _first(by_entity, 'renewal_activity')
            return 'Комментарий к продлению' if act is not None else None
        return None
    data = ev.get('pgh_data') or {}
    diff = ev.get('pgh_diff') or {}
    who = f'{lk.student(data.get("student_id"))} (цикл {data.get("cycle_no")})'
    if ev['pgh_label'] == 'insert':
        return f'Новая сделка продления {who}: {lk.stage(data.get("stage_id"))}'
    if op == 'renewal.comment':
        return f'Комментарий к продлению {who}'
    verb = _RENEWAL_VERBS.get(op)
    if 'stage_id' in diff:
        old, new = diff['stage_id']
        moved = f'{lk.stage(old)} → {lk.stage(new)}'
        return f'Продление {who} {verb}: {moved}' if verb else f'Продление {who}: {moved}'
    return f'Продление {who}: {_diff_text(ev, lk)}'


# --- Задачи ---

def _sum_task(op, by_entity, lk):
    ev = _first(by_entity, 'task')
    if ev is None:
        return None
    title = _ident(ev)
    if op == 'task.complete':
        return f'Задача {title} закрыта'
    if op == 'task.comment':
        return f'Комментарий к задаче {title}'
    if op == 'task.move':
        return f'Задача {title} перемещена на другой этап'
    return describe_event(ev, lk)


_BUILDERS: dict[str, Callable[[str, dict, Lookups], Optional[str]]] = {
    'group.create': _sum_group_create,
    'group.update': _sum_group_update,
    'group.delete': _sum_group_delete,
    'group.schedule_change': _sum_schedule_change,
    'plan.permanent_change': _sum_schedule_change,
    'plan.change_teacher_permanent': _sum_change_teacher_permanent,
    'plan.generate': _sum_plan_generate,
    'plan.resync': _sum_plan_resync,
    'plan.cancel': _sum_plan_cancel,
    'lesson.submit': _sum_lesson,
    'lesson.create': _sum_lesson,
    'lesson.update': _sum_lesson,
    'lesson.delete': _sum_lesson,
    'lesson.attendance_update': _sum_attendance_update,
    'payroll.update': _sum_payroll_edit,
    'payroll.reset': _sum_payroll_edit,
    'payroll.cancellation_update': _sum_cancellation_edit,
    'payroll.cancellation_reset': _sum_cancellation_edit,
    'payroll.rate_version_create': _sum_rate_version,
    'payroll.rate_version_update': _sum_rate_version,
    'payroll.rate_version_delete': _sum_rate_version,
    'payroll.personal_rate_create': _sum_personal_rate,
    'payroll.personal_rate_update': _sum_personal_rate,
    'payroll.personal_rate_delete': _sum_personal_rate,
    'payroll.personal_rate_deactivate': _sum_personal_rate,
    'payroll.personal_rate_activate': _sum_personal_rate,
    'payroll.recalculate': _sum_recalculate,
    'eduson.import': _sum_eduson_import,
    'eduson.sync': _sum_eduson_sync,
    'student.create': _sum_student,
    'student.update': _sum_student,
    'student.manager_update': _sum_student,
}
for _op in ('membership.create', 'membership.delete', 'membership.transfer',
            'membership.place', 'membership.update'):
    _BUILDERS[_op] = _sum_membership
for _op in ('payment.create', 'payment.delete', 'payment.refund'):
    _BUILDERS[_op] = _sum_payment
for _op in ('account.create', 'account.update', 'account.delete', 'account.reset_password',
            'account.reset_2fa', 'account.invite_create', 'account.invite_revoke',
            'account.invite_accept', 'account.twofa_setup', 'account.twofa_enable',
            'account.twofa_disable'):
    _BUILDERS[_op] = _sum_account
for _op in ('renewal.create', 'renewal.move', 'renewal.reopen', 'renewal.unfreeze',
            'renewal.comment', 'renewal.outcome_date_update', 'renewal.update'):
    _BUILDERS[_op] = _sum_renewal
for _op in ('task.create', 'task.update', 'task.delete', 'task.move', 'task.complete',
            'task.comment'):
    _BUILDERS[_op] = _sum_task


def build_summary(operation: str, events: list[dict], lk: Lookups) -> str:
    """Описание операции по её событиям. events — dict'ы из Events.values()."""
    if not events:
        return 'Без изменений данных'

    by_entity: dict[str, list[dict]] = {}
    for ev in events:
        by_entity.setdefault(ev['entity'], []).append(ev)

    builder = _BUILDERS.get(operation)
    if builder is not None:
        phrase = builder(operation, by_entity, lk)
        if phrase:
            return phrase

    # --- Операция без своего шаблона: узнаём её по самым значимым записям ---
    for ev in by_entity.get('planned_lesson', ()):
        # перенос/отмена/смена препода/статус — описываем самым значимым событием
        phrase = _planned_lesson_phrase(ev, lk)
        if phrase:
            return phrase
    for fallback in (_sum_lesson, _sum_payment):
        phrase = fallback(operation, by_entity, lk)
        if phrase:
            return phrase

    # --- Откат ---
    if operation == 'changelog.revert':
        return f'Откат операции: отменено изменений — {len(events)}'

    # --- Generic: первое событие + что ещё задела операция ---
    ev = events[0]
    if ev['entity'] in MOTIVATION_ENTITIES:
        # describe_event показывает суммы — он для модалки суперадмина, не для ленты.
        return _motivation_phrase(ev, lk)
    return (describe_event(ev, lk)
            + _others_tail({e: [x for x in evs if x is not ev] for e, evs in by_entity.items()}))
