"""
PayrollRepository — единственное место доступа к данным раздела payroll.

ORM-порт services/repo/payroll.js (раздел 09).

Паритет с node-pg:
  • payment/penalty/sum_* (numeric) → Decimal → строка с масштабом (renderer).
  • lessons_count = COUNT(*) → строка (node-pg bigint = строка; в оригинале ::text).
  • список — payroll_entries: уроки + отмены одним UNION ALL; пагинирует вьюха
    (StandardPagination), sort_by проверяет она же по SORTABLE (мусор → 400).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from django.db import connection, transaction
from django.db.models import (
    Count, DecimalField, F, IntegerField, Sum, TextField, TimeField, Value,
)
from django.db.models.functions import Coalesce

from apps.core.utils.orm import dictrow, dictrows

from .models import CancellationPay, Payroll


# Поля строки payroll (p.* / RETURNING *), в порядке схемы.
_PAYROLL_FIELDS = (
    'id', 'lesson_id', 'teacher_id', 'total_students', 'present_count',
    'payment', 'penalty', 'is_manual', 'calc_payment', 'calc_penalty', 'rule_note',
)

class CancellationPayConflict(Exception):
    """Привязка отмены к маркеру сменилась во время операции — 409."""


# Поля строки начисления за отмену в ответах PATCH/reset.
_CANCEL_FIELDS = (
    'id', 'planned_lesson_id', 'teacher_id', 'group_id', 'lesson_date', 'scheduled_time',
    'payment', 'calc_payment', 'is_manual', 'rule_note',
)

# Whitelist sort_by → колонка объединённой выборки (алиасы e_* — см. _entry_columns).
# Мусор в sort_by отсекает вьюха (400); тай-брейк — (вид, id): id уникален
# только внутри вида строки.
SORTABLE: dict[str, str] = {
    'lesson_date':   'e_date',
    'lesson_number': 'e_number',
    'group_name':    'e_group_name',
    'teacher_name':  'e_teacher_name',
    'payment':       'e_payment',
    'penalty':       'e_penalty',
    'kind':          'e_kind',
}

DEFAULT_SORT_BY = 'lesson_date'
DEFAULT_SORT_DIR = 'desc'

KIND_LESSON = 'lesson'
KIND_CANCELLATION = 'cancellation'

_ZERO = Value(Decimal('0'), output_field=DecimalField(max_digits=20, decimal_places=2))
_MONEY = DecimalField(max_digits=10, decimal_places=2)


def _null(field):
    return Value(None, output_field=field)


def _entry_columns(kind: str) -> dict:
    """
    Колонки строки списка для обеих половин UNION ALL — одни имена в одном
    порядке: UNION сопоставляет колонки по позиции. Все колонки — аннотации
    (e_*), не поля моделей: Django ставит поля модели в SELECT раньше аннотаций,
    и смешанный набор разъехался бы между половинами.
    """
    if kind == KIND_LESSON:
        return {
            'e_kind': Value(KIND_LESSON, output_field=TextField()),
            'e_id': F('id'),
            'e_lesson_id': F('lesson_id'),
            'e_teacher_id': F('teacher_id'),
            'e_total': F('total_students'),
            'e_present': F('present_count'),
            'e_payment': F('payment'),
            'e_penalty': F('penalty'),
            'e_date': F('lesson__lesson_date'),
            'e_time': _null(TimeField()),
            'e_number': F('lesson__lesson_number'),
            'e_group_id': F('lesson__group_id'),
            'e_group_name': F('lesson__group__name'),
            'e_teacher_name': F('teacher__name'),
            'e_is_manual': F('is_manual'),
            'e_calc_payment': F('calc_payment'),
            'e_calc_penalty': F('calc_penalty'),
            'e_rule_note': F('rule_note'),
        }
    return {
        'e_kind': Value(KIND_CANCELLATION, output_field=TextField()),
        'e_id': F('id'),
        'e_lesson_id': _null(IntegerField()),
        'e_teacher_id': F('teacher_id'),
        'e_total': _null(IntegerField()),
        'e_present': _null(IntegerField()),
        'e_payment': F('payment'),
        # Штрафа у отмены нет (§1.3) — 0, чтобы «к выплате» считалось одинаково.
        'e_penalty': Value(Decimal('0.00'), output_field=_MONEY),
        'e_date': F('lesson_date'),
        'e_time': F('scheduled_time'),
        'e_number': _null(DecimalField(max_digits=5, decimal_places=1)),
        'e_group_id': F('group_id'),
        'e_group_name': F('group__name'),
        'e_teacher_name': F('teacher__name'),
        'e_is_manual': F('is_manual'),
        'e_calc_payment': F('calc_payment'),
        'e_calc_penalty': _null(_MONEY),
        'e_rule_note': F('rule_note'),
    }


# e_* → ключ ответа API (контракт строк уроков сохранён: id/lesson_id/... как были).
_ENTRY_KEYS = {
    'e_kind': 'kind', 'e_id': 'id', 'e_lesson_id': 'lesson_id', 'e_teacher_id': 'teacher_id',
    'e_total': 'total_students', 'e_present': 'present_count', 'e_payment': 'payment',
    'e_penalty': 'penalty', 'e_date': 'lesson_date', 'e_time': 'scheduled_time',
    'e_number': 'lesson_number', 'e_group_id': 'group_id', 'e_group_name': 'group_name',
    'e_teacher_name': 'teacher_name', 'e_is_manual': 'is_manual',
    'e_calc_payment': 'calc_payment', 'e_calc_penalty': 'calc_penalty',
    'e_rule_note': 'rule_note',
}


def _filter_part(qs, filters: dict[str, Any], *, group_field: str, date_field: str):
    """Фильтры filter[*] для одной половины объединения (у урока группа и дата —
    через lesson, у отмены — свои поля)."""
    teacher_id = filters.get('teacher_id')
    if teacher_id not in (None, ''):
        qs = qs.filter(teacher_id=int(teacher_id))

    group_id = filters.get('group_id')
    if group_id not in (None, ''):
        qs = qs.filter(**{f'{group_field}_id': int(group_id)})

    group_name = filters.get('group_name')
    if group_name not in (None, ''):
        qs = qs.filter(**{f'{group_field}__name__icontains': str(group_name)})

    teacher_name = filters.get('teacher_name')
    if teacher_name not in (None, ''):
        qs = qs.filter(teacher__name__icontains=str(teacher_name))

    for key in ('date_from', 'lesson_date_from'):
        val = filters.get(key)
        if val not in (None, ''):
            qs = qs.filter(**{f'{date_field}__gte': val})

    for key in ('date_to', 'lesson_date_to'):
        val = filters.get(key)
        if val not in (None, ''):
            qs = qs.filter(**{f'{date_field}__lte': val})

    return qs


def payroll_entries(sort_by: str = DEFAULT_SORT_BY, sort_dir: str = DEFAULT_SORT_DIR,
                    filters: Optional[dict] = None):
    """
    Начисления за уроки и за отмены одним упорядоченным queryset'ом (UNION ALL) —
    пагинатор (StandardPagination во вьюхе) считает COUNT и режет страницу по
    объединённому набору в SQL.

    filter[kind]=lesson|cancellation оставляет одну половину. sort_by — ключ
    SORTABLE (проверяет вьюха). Строки — словари с ключами e_*, в ответ API их
    переводит entry_row.
    """
    filters = filters or {}
    kind = filters.get('kind') or None

    parts = []
    if kind in (None, KIND_LESSON):
        lessons = _filter_part(Payroll.objects.all(), filters,
                               group_field='lesson__group', date_field='lesson__lesson_date')
        cols = _entry_columns(KIND_LESSON)
        parts.append(lessons.annotate(**cols).values(*cols))
    if kind in (None, KIND_CANCELLATION):
        cancels = _filter_part(CancellationPay.objects.all(), filters,
                               group_field='group', date_field='lesson_date')
        cols = _entry_columns(KIND_CANCELLATION)
        parts.append(cancels.annotate(**cols).values(*cols))
    if not parts:
        # Неизвестный вид — пустой набор той же формы (вьюха его не пропускает;
        # защита от тихой выдачи всего).
        cols = _entry_columns(KIND_LESSON)
        parts.append(Payroll.objects.none().annotate(**cols).values(*cols))

    combined = parts[0] if len(parts) == 1 else parts[0].union(parts[1], all=True)
    sort_col = F(SORTABLE.get(sort_by) or SORTABLE[DEFAULT_SORT_BY])
    # NULLS LAST в обе стороны: у отмены нет номера урока, и при desc PostgreSQL
    # по умолчанию поднял бы её наверх.
    primary = (sort_col.asc(nulls_last=True) if sort_dir == 'asc'
               else sort_col.desc(nulls_last=True))
    return combined.order_by(primary, 'e_kind', '-e_id')


def entry_row(row: dict) -> dict:
    """Строка объединённой выборки → строка ответа API."""
    return {_ENTRY_KEYS[k]: v for k, v in row.items()}


def payroll_summary(
    teacher_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list[dict]:
    """
    Сводка по учителю: COUNT уроков, SUM(payment), SUM(penalty). ORDER BY te.name.

    Диапазон — по lesson_date. Доп.урок/сгорание — отдельные Lesson (extra/burned)
    в свой месяц, считаются обычными строками payroll (спец-надбавок больше нет).
    lessons_count → строка (node-pg bigint = строка). sum_* → Decimal (renderer → строка).
    """
    base_qs = Payroll.objects.all()
    if teacher_id:
        base_qs = base_qs.filter(teacher_id=teacher_id)
    if date_from:
        base_qs = base_qs.filter(lesson__lesson_date__gte=date_from)
    if date_to:
        base_qs = base_qs.filter(lesson__lesson_date__lte=date_to)

    base_rows = (
        base_qs.values('teacher_id', teacher_name=F('teacher__name'))
        .annotate(
            lessons_count=Count('id'),
            sum_payment=Coalesce(Sum('payment'), _ZERO),
            sum_penalty=Coalesce(Sum('penalty'), _ZERO),
        )
    )

    # Отмены — второй агрегат по тому же преподавателю и периоду (дата отмены).
    cancel_qs = CancellationPay.objects.all()
    if teacher_id:
        cancel_qs = cancel_qs.filter(teacher_id=teacher_id)
    if date_from:
        cancel_qs = cancel_qs.filter(lesson_date__gte=date_from)
    if date_to:
        cancel_qs = cancel_qs.filter(lesson_date__lte=date_to)
    cancel_rows = (
        cancel_qs.values('teacher_id', teacher_name=F('teacher__name'))
        .annotate(cancellations_count=Count('id'),
                  sum_payment=Coalesce(Sum('payment'), _ZERO))
    )

    zero = Decimal('0.00')
    result_by_teacher: dict[int, dict] = {}

    def _row(tid: int, name: str) -> dict:
        return result_by_teacher.setdefault(tid, {
            'teacher_id': tid,
            'teacher_name': name,
            'lessons_count': '0',
            'cancellations_count': '0',
            'sum_lessons_payment': zero,
            'sum_cancellations_payment': zero,
            'sum_payment': zero,
            'sum_penalty': zero,
        })

    for r in base_rows:
        row = _row(r['teacher_id'], r['teacher_name'])
        row['lessons_count'] = str(r['lessons_count'])   # ::text — bigint строкой
        row['sum_lessons_payment'] = r['sum_payment']
        row['sum_penalty'] = r['sum_penalty']
    for r in cancel_rows:
        row = _row(r['teacher_id'], r['teacher_name'])
        row['cancellations_count'] = str(r['cancellations_count'])
        row['sum_cancellations_payment'] = r['sum_payment']
    # sum_payment — к начислению всего (уроки + отмены); штраф — только уроки.
    for row in result_by_teacher.values():
        row['sum_payment'] = row['sum_lessons_payment'] + row['sum_cancellations_payment']

    return sorted(result_by_teacher.values(), key=lambda r: r['teacher_name'] or '')


def teacher_month_entries(teacher_id: int, date_from: str, date_to: str) -> list[dict]:
    """
    Строки расчётного листа ОДНОГО преподавателя за период (границы включительно).

    Для кабинета преподавателя: ~30 строк в месяц, пагинация не нужна. Тянем
    сразу контекст урока (группа, направление, длительность, дата сдачи отчёта),
    чтобы расшифровка собиралась без дозапросов.

    teacher_id обязателен и приходит из JWT — фильтр по преподавателю здесь не
    опциональный, иначе вьюха отдала бы чужую зарплату.
    """
    qs = (
        Payroll.objects
        .filter(
            teacher_id=teacher_id,
            lesson__lesson_date__gte=date_from,
            lesson__lesson_date__lte=date_to,
        )
        .annotate(
            lesson_date=F('lesson__lesson_date'),
            lesson_number=F('lesson__lesson_number'),
            lesson_type=F('lesson__lesson_type'),
            duration_minutes=F('lesson__lesson_duration_minutes'),
            submitted_at=F('lesson__submitted_at'),
            group_name=F('lesson__group__name'),
            direction_name=F('lesson__group__direction__name'),
            direction_color=F('lesson__group__direction__color'),
        )
        .values(
            'id', 'lesson_id', 'total_students', 'present_count', 'payment', 'penalty',
            'is_manual', 'calc_payment', 'calc_penalty', 'rule_note',
            'lesson_date', 'lesson_number', 'lesson_type', 'duration_minutes',
            'submitted_at', 'group_name', 'direction_name', 'direction_color',
        )
        .order_by('-lesson_date', '-id')
    )
    return dictrows(qs)


def excluded_headcount(lesson_ids: list[int]) -> dict[int, dict]:
    """
    Отметки, влияющие на headcount зарплаты, по каждому уроку:
    {lesson_id: {'free': n, 'skip': n, 'attended': n}}.

    Нужно, чтобы объяснить преподавателю, почему в оплате «4 из 4», хотя в
    группе пятеро. unpaid_skip из headcount исключён всегда. is_free исключался
    только до 2026-09-28 (теперь оплачивается как обычное присутствие), а
    исторические уроки не пересчитывались — поэтому 'attended' (присутствовавшие
    без unpaid_skip) нужен, чтобы по present_count строки payroll понять,
    учтены ли бесплатные в оплате (см. services.my_payroll_month).

    Один запрос на все уроки месяца; уроки без отметок в результат не
    попадают (HAVING) — словарь остаётся компактным.
    """
    if not lesson_ids:
        return {}

    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT lesson_id,
                   COUNT(*) FILTER (WHERE is_free)     AS free_count,
                   COUNT(*) FILTER (WHERE unpaid_skip) AS skip_count,
                   COUNT(*) FILTER (WHERE present AND NOT unpaid_skip) AS attended_count
              FROM lesson_attendance
             WHERE lesson_id = ANY(%s)
             GROUP BY lesson_id
            HAVING COUNT(*) FILTER (WHERE is_free) > 0
                OR COUNT(*) FILTER (WHERE unpaid_skip) > 0
            """,
            [list(lesson_ids)],
        )
        return {
            row[0]: {'free': row[1], 'skip': row[2], 'attended': row[3]}
            for row in cur.fetchall()
        }


def update_payroll(payroll_id: int, fields: dict) -> Optional[dict]:
    """
    Частичное обновление payroll (COALESCE-семантика). Возвращает RETURNING * или None.

    total_students/present_count/payment/penalty — set если значение не None.
    Правка суммы (payment/penalty) — ручная: ставит is_manual, и пересчёт движка
    зарплат дальше обновляет у строки только calc_* (спека 2026-10-20, §1.9).
    """
    with transaction.atomic():
        # Лок строки: пересчёт (apps.payroll.recalc) держит тот же FOR UPDATE, так
        # что правка не перемешается с ним. Пишем только изменяемые поля — полный
        # save() вернул бы прочитанные до лока calc_*/rule_note/teacher поверх
        # свежего пересчёта.
        obj = Payroll.objects.select_for_update().filter(id=payroll_id).first()
        if obj is None:
            return None

        changed = []
        for name in ('total_students', 'present_count', 'payment', 'penalty'):
            if fields.get(name) is not None:
                setattr(obj, name, fields[name])
                changed.append(name)
        if 'payment' in changed or 'penalty' in changed:
            obj.is_manual = True
            changed.append('is_manual')
        if changed:
            obj.save(update_fields=changed)
    return dictrow(Payroll.objects.filter(id=payroll_id).values(*_PAYROLL_FIELDS))


def reset_payroll(payroll_id: int) -> Optional[dict]:
    """
    Сбросить ручную правку урока к расчёту (§1.9): is_manual=false + пересчёт
    урока. Возвращает строку начисления + reset + recalculated или None.

    Порядок локов — как у пересчёта: сначала урок, потом его Payroll, иначе
    встречный пересчёт этого урока (урок → Payroll) встал бы с нами в дедлок.
    """
    from apps.lessons.models import Lesson
    from apps.payroll.recalc import recalculate

    lesson_id = (Payroll.objects.filter(id=payroll_id)
                 .values_list('lesson_id', flat=True).first())
    if lesson_id is None:
        return None
    with transaction.atomic():
        list(Lesson.objects.select_for_update().filter(id=lesson_id).values_list('id'))
        obj = Payroll.objects.select_for_update().filter(id=payroll_id).first()
        if obj is None:
            return None
        was_manual = obj.is_manual
        if was_manual:
            obj.is_manual = False
            obj.save(update_fields=['is_manual'])
        stats = recalculate(lesson_ids=[obj.lesson_id])
    row = dictrow(Payroll.objects.filter(id=payroll_id).values(*_PAYROLL_FIELDS))
    # reset — снят ли флаг ручной правки (сама эта строка в пересчёт не входит:
    # recalculated считает только изменения движка, а снятый флаг — нет).
    return {**row, 'reset': was_manual, 'recalculated': stats['changed']}


def update_cancellation(cancellation_id: int, payment: Decimal) -> Optional[dict]:
    """Ручная правка суммы отмены: payment + is_manual (пересчёт её больше не
    трогает, обновляет только calc_payment). Лок строки — как у update_payroll."""
    with transaction.atomic():
        obj = (CancellationPay.objects.select_for_update()
               .filter(id=cancellation_id).first())
        if obj is None:
            return None
        obj.payment = payment
        obj.is_manual = True
        obj.save(update_fields=['payment', 'is_manual'])
    return dictrow(CancellationPay.objects.filter(id=cancellation_id).values(*_CANCEL_FIELDS))


def reset_cancellation(cancellation_id: int) -> Optional[dict]:
    """
    Сбросить ручную правку отмены. Привязана к маркеру — пересчёт маркера
    (recalculate_cancellations). Сирота (маркер удалён, прошлый месяц)
    пересчитать не из чего — возвращаем последнюю расчётную сумму,
    recalculated = 0.

    Порядок локов — «план → деньги», как у пересчёта: маркер, затем начисление.
    Маркер узнаём из начисления до лока, поэтому после лока сверяем: если
    привязка успела смениться (пересборка плана), пробуем ещё раз; не сошлось
    и со второй попытки — CancellationPayConflict (409).
    """
    from apps.payroll.recalc import recalculate_cancellations
    from apps.scheduling.models import PlannedLesson

    for _attempt in range(2):
        found = (CancellationPay.objects.filter(id=cancellation_id)
                 .values_list('planned_lesson_id', flat=True)[:1])
        found = list(found)
        if not found:
            return None
        marker_id = found[0]
        with transaction.atomic():
            if marker_id is not None:
                list(PlannedLesson.objects.select_for_update()
                     .filter(id=marker_id).values_list('id'))
            obj = (CancellationPay.objects.select_for_update()
                   .filter(id=cancellation_id).first())
            if obj is None:
                return None
            if obj.planned_lesson_id != marker_id:
                continue  # транзакция пустая — откатывать нечего
            was_manual = obj.is_manual
            obj.is_manual = False
            obj.payment = obj.calc_payment
            obj.save(update_fields=['is_manual', 'payment'])
            changed = 0
            if marker_id is not None:
                changed = recalculate_cancellations(marker_ids=[marker_id])['changed']
        row = dictrow(CancellationPay.objects.filter(id=cancellation_id)
                      .values(*_CANCEL_FIELDS))
        if row is None:  # пересчёт удалил будущую сироту — отдаём, что было
            return {'id': cancellation_id, 'deleted': True, 'reset': was_manual,
                    'recalculated': changed}
        return {**row, 'reset': was_manual, 'recalculated': changed}
    raise CancellationPayConflict(
        'Отмена изменилась во время сброса (пересобран план) — обновите страницу.')


def teacher_month_cancellations(teacher_id: int, date_from: str, date_to: str) -> list[dict]:
    """Начисления за отмены ОДНОГО преподавателя за период — кабинет. teacher_id
    из JWT, как в teacher_month_entries; индекс (teacher, lesson_date)."""
    qs = (
        CancellationPay.objects
        .filter(teacher_id=teacher_id, lesson_date__gte=date_from, lesson_date__lte=date_to)
        .values(
            'id', 'lesson_date', 'scheduled_time', 'payment', 'calc_payment', 'is_manual',
            'rule_note',
            group_name=F('group__name'),
            direction_name=F('group__direction__name'),
            direction_color=F('group__direction__color'),
        )
        .order_by('-lesson_date', '-scheduled_time', '-id')
    )
    return dictrows(qs)
