"""
Дашборд «Посещения учеников» (спека 2026-09-15): сводка и агрегат по ученикам.

Счётчик один на весь экран — СПИСАННЫЕ уроки с весом (45 мин = 0.5): обычные,
доп.уроки и сгорания. Бесплатные занятия идут отдельной колонкой и в «Итого» не
входят: с абонемента за них не снимается, и подмешать их значило бы разойтись с
балансами и с плиткой за период.

Типы строк — те же четыре, что во вкладке «Уроки» карточки ученика, и выводятся
по тому же правилу (apps/students/lesson_history.py::row_kind): тип урока
проверяется раньше флага «бесплатное», потому что сгорание и доп.урок
бесплатными не бывают. Две классификации одного факта завести нельзя — колонки
перестанут сходиться между экранами.

Период — по дате ЗАНЯТИЯ (lesson_date), как в финансах: доп.урок и сгорание
попадают в период по дате своего проведения, а не по дате закрытого пропуска.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import DecimalField, Q, Sum
from django.db.models.functions import Coalesce

from apps.lessons.models import SYSTEM_LESSON_TYPES, LessonAttendance
from apps.lessons.weights import attended_units_case
from apps.students.lesson_history import ROW_KIND_BURNED, ROW_KIND_EXTRA

_DEC = DecimalField(max_digits=12, decimal_places=2)
_ZERO = Decimal('0')

# Разрешённые ключи сортировки: ключ запроса → поля ORM. Белый список, а не
# сырой sort_by в order_by. К ключу всегда добавляется ФИО и id: без этого
# строки с равным значением меняют порядок между запросами и страница 2
# повторит строку со страницы 1.
ORDERING_FIELDS = {
    'billed': 'billed',
    'full_name': 'student__full_name',
}
DEFAULT_SORT_BY = 'billed'
DEFAULT_SORT_DIR = 'desc'

_IS_SYSTEM = Q(lesson__lesson_type__in=SYSTEM_LESSON_TYPES)
# Литералы типов берутся из lesson_history — там же, где живёт row_kind, по
# которому классифицирует строки вкладка «Уроки». Один набор имён на два экрана:
# разойдясь, они покажут одно и то же занятие в разных колонках.
_Q_EXTRA = Q(lesson__lesson_type=ROW_KIND_EXTRA)
_Q_BURNED = Q(lesson__lesson_type=ROW_KIND_BURNED)
# «Бесплатный» и «обычный» — только среди НЕ системных уроков: у сгорания и
# доп.урока тип старше флага is_free.
_Q_FREE = ~_IS_SYSTEM & Q(is_free=True)
_Q_REGULAR = ~_IS_SYSTEM & Q(is_free=False)
# «Итого списано» — ИМЕННО сумма трёх колонок разбивки, а не «всё, кроме
# бесплатного». Разница видна, когда в системе заведут новый тип урока: при
# «всё кроме бесплатного» он попал бы в итог, но ни в одну колонку, и таблица
# молча перестала бы сходиться сама с собой.
_Q_BILLED = _Q_REGULAR | _Q_EXTRA | _Q_BURNED


def _weighted(condition: Q) -> Coalesce:
    """SUM(вес) по строкам, удовлетворяющим condition; пусто → 0, не NULL."""
    return Coalesce(
        Sum(attended_units_case(), filter=condition),
        Decimal('0'),
        output_field=_DEC,
    )


def _in_period(date_from: str, date_to: str) -> Q:
    return Q(lesson__lesson_date__gte=date_from, lesson__lesson_date__lte=date_to)


def summary(date_from: str, date_to: str) -> dict:
    """
    Две цифры экрана: списанные уроки за всё время и внутри периода.

    Считается по всей школе и НЕ зависит от фильтра по имени: иначе цифра
    прыгала бы при наборе букв в поиске и читалась как ошибка.
    """
    base = LessonAttendance.objects.filter(Q(present=True) & _Q_BILLED)
    row = base.aggregate(
        all_time=_weighted(Q()),
        period=_weighted(_in_period(date_from, date_to)),
    )
    # Строками — по той же причине, что и строки таблицы (см. serialize_rows).
    return {
        'all_time_lessons': str(row['all_time'] or _ZERO),
        'period_lessons': str(row['period'] or _ZERO),
    }


def student_rows_queryset(
    date_from: str,
    date_to: str,
    sort_by: str = DEFAULT_SORT_BY,
    sort_dir: str = DEFAULT_SORT_DIR,
    name_query: str = '',
):
    """
    Строка на ученика: разбивка по типам внутри периода.

    В выборке ВСЕ ученики, у кого есть хоть какое-то посещение (present=true) —
    даже если в периоде у них пусто: тогда во всех колонках нули, и видно, кто
    перестал ходить. Поэтому фильтр периода живёт внутри условных сумм, а не в
    WHERE: иначе такой ученик пропал бы из отчёта совсем.

    Значение sort_by вне белого списка сюда не доходит (вьюха отдаёт 400), здесь
    второй рубеж — откат на умолчание вместо сырой строки в order_by.
    """
    qs = LessonAttendance.objects.filter(present=True)
    if name_query:
        qs = qs.filter(student__full_name__icontains=name_query)

    period = _in_period(date_from, date_to)
    qs = (
        qs.values('student_id', 'student__full_name')
        .annotate(
            regular=_weighted(period & _Q_REGULAR),
            extra=_weighted(period & _Q_EXTRA),
            burned=_weighted(period & _Q_BURNED),
            free=_weighted(period & _Q_FREE),
            billed=_weighted(period & _Q_BILLED),
        )
    )
    field = ORDERING_FIELDS.get(sort_by, ORDERING_FIELDS[DEFAULT_SORT_BY])
    prefix = '' if sort_dir == 'asc' else '-'
    return qs.order_by(f'{prefix}{field}', 'student__full_name', 'student_id')


def serialize_rows(rows) -> list[dict]:
    """Строки агрегата в форму ответа API."""
    return [
        {
            'student_id': r['student_id'],
            'full_name': r['student__full_name'],
            # Уроки отдаются строками ('1.50'), как деньги и номер урока во
            # вкладке «Уроки»: DRF кодирует Decimal во float, а половинки лучше
            # не гонять через двоичную дробь. Фронт приводит через Number().
            'regular': str(r['regular']),
            'extra': str(r['extra']),
            'burned': str(r['burned']),
            'free': str(r['free']),
            'billed': str(r['billed']),
        }
        for r in rows
    ]
