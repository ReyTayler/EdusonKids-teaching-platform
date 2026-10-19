"""
PayrollRepository — единственное место доступа к данным раздела payroll.

ORM-порт services/repo/payroll.js (раздел 09).

Паритет с node-pg:
  • payment/penalty/sum_* (numeric) → Decimal → строка с масштабом (renderer).
  • lessons_count = COUNT(*) → строка (node-pg bigint = строка; в оригинале ::text).
  • list — порт paginate(): whitelist sort с тихим fallback, secondarySort p.id DESC,
    контракт { rows, total, page, page_size }.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from django.db import connection
from django.db.models import Count, DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce

from apps.core.utils.orm import dictrow, dictrows

from .models import Payroll


# Поля строки payroll (p.* / RETURNING *), в порядке схемы.
_PAYROLL_FIELDS = (
    'id', 'lesson_id', 'teacher_id', 'total_students', 'present_count',
    'payment', 'penalty',
)

# Whitelist sort_by → ORM-поле (алиасы entry_* — см. _base_entries).
# p.id DESC — вторичная сортировка.
_SORTABLE: dict[str, str] = {
    'lesson_date':   'entry_date',
    'lesson_number': 'lesson_number',
    'group_name':    'group_name',
    'teacher_name':  'teacher_name',
    'payment':       'entry_payment',
    'penalty':       'entry_penalty',
}

_DEFAULT_SORT_BY = 'lesson_date'
_DEFAULT_SORT_DIR = 'desc'

_ZERO = Value(Decimal('0'), output_field=DecimalField(max_digits=20, decimal_places=2))

# Колонки выборки строки payroll (алиасы entry_* сохранены для стабильности
# сортировки/переименования в list_payroll).
_ENTRY_VALUES = (
    'id', 'lesson_id', 'teacher_id', 'total_students', 'present_count',
    'entry_payment', 'entry_penalty', 'entry_date',
    'lesson_number', 'group_id', 'group_name', 'teacher_name',
)


def _apply_common_filters(qs, filters: dict[str, Any]):
    """teacher_id/group_id/group_name/teacher_name — общие для базовой и надбавочной
    выборки (диапазон дат — отдельно, см. _apply_date_range, у надбавки своя дата)."""
    teacher_id = filters.get('teacher_id')
    if teacher_id not in (None, ''):
        qs = qs.filter(teacher_id=int(teacher_id))

    group_id = filters.get('group_id')
    if group_id not in (None, ''):
        qs = qs.filter(lesson__group_id=int(group_id))

    group_name = filters.get('group_name')
    if group_name not in (None, ''):
        qs = qs.filter(lesson__group__name__icontains=str(group_name))

    teacher_name = filters.get('teacher_name')
    if teacher_name not in (None, ''):
        qs = qs.filter(teacher__name__icontains=str(teacher_name))

    return qs


def _apply_date_range(qs, filters: dict[str, Any], date_field: str):
    """date_from/date_to (+ алиасы lesson_date_from/to) — против ПЕРЕДАННОГО поля
    даты (lesson__lesson_date)."""
    for key in ('date_from', 'lesson_date_from'):
        val = filters.get(key)
        if val not in (None, ''):
            qs = qs.filter(**{f'{date_field}__gte': val})

    for key in ('date_to', 'lesson_date_to'):
        val = filters.get(key)
        if val not in (None, ''):
            qs = qs.filter(**{f'{date_field}__lte': val})

    return qs


def _base_entries(filters: dict[str, Any]):
    """Строка на каждый payroll — дата фильтруется/сортируется по lesson_date,
    сумма — payment. Для доп.урока/сгорания это отдельный Lesson (extra/burned)
    в свой месяц, поэтому спец-надбавок больше нет."""
    qs = _apply_common_filters(Payroll.objects.all(), filters)
    qs = _apply_date_range(qs, filters, 'lesson__lesson_date')
    return qs.annotate(
        entry_date=F('lesson__lesson_date'),
        entry_payment=F('payment'),
        entry_penalty=F('penalty'),
        lesson_number=F('lesson__lesson_number'),
        group_id=F('lesson__group_id'),
        group_name=F('lesson__group__name'),
        teacher_name=F('teacher__name'),
    ).values(*_ENTRY_VALUES)


# ---------------------------------------------------------------------------
# Repository functions
# ---------------------------------------------------------------------------

def list_payroll(
    page: int = 1,
    page_size: int = 50,
    sort_by: str = _DEFAULT_SORT_BY,
    sort_dir: str = _DEFAULT_SORT_DIR,
    filters: Optional[dict] = None,
) -> dict:
    """
    Пагинированный список payroll с joined-полями. Контракт {rows,total,page,page_size}.

    Одна строка на payroll (доп.урок/сгорание — отдельные Lesson в свой месяц,
    отдельных строк-надбавок больше нет).
    """
    if filters is None:
        filters = {}

    sort_field = _SORTABLE.get(sort_by) or _SORTABLE[_DEFAULT_SORT_BY]
    order_prefix = '' if sort_dir == 'asc' else '-'

    entries = _base_entries(filters)

    total = entries.count()

    offset = max(0, (page - 1) * page_size)
    ordered = entries.order_by(f'{order_prefix}{sort_field}', '-id')
    rows = dictrows(ordered[offset:offset + page_size])
    for r in rows:
        r['payment'] = r.pop('entry_payment')
        r['penalty'] = r.pop('entry_penalty')
        r['lesson_date'] = r.pop('entry_date')

    return {
        'rows': rows,
        'total': total,
        'page': page,
        'page_size': page_size,
    }


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

    result_by_teacher: dict[int, dict] = {}
    for r in base_rows:
        result_by_teacher[r['teacher_id']] = {
            'teacher_id': r['teacher_id'],
            'teacher_name': r['teacher_name'],
            'lessons_count': str(r['lessons_count']),   # ::text — bigint строкой
            'sum_payment': r['sum_payment'],
            'sum_penalty': r['sum_penalty'],
        }

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
    """
    obj = Payroll.objects.filter(id=payroll_id).first()
    if obj is None:
        return None

    if fields.get('total_students') is not None:
        obj.total_students = fields['total_students']
    if fields.get('present_count') is not None:
        obj.present_count = fields['present_count']
    if fields.get('payment') is not None:
        obj.payment = fields['payment']
    if fields.get('penalty') is not None:
        obj.penalty = fields['penalty']

    obj.save()
    return dictrow(Payroll.objects.filter(id=payroll_id).values(*_PAYROLL_FIELDS))
