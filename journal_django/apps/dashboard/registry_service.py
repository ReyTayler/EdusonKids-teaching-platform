"""
RegistryService — «Реестр куратора»: список активных учеников для вкладки дашборда.

Строка реестра = УЧЕНИК (баланс пулится по student_id — единый пул по всем
направлениям, см. docs/superpowers/specs/2026-07-08-student-balance-pooling-design.md).

МАСШТАБ (вариант B, 2026-07-11): список пагинируется НА УРОВНЕ БД. `students_qs`
строит аннотированный queryset (balance/attended/planned/last/next/cancellations —
коррелированные Subquery, без fan-out), фильтр сегмента и поиск — WHERE,
сортировка — ORDER BY, срез — LIMIT/OFFSET штатным DRF-пагинатором. Так реестр
держит тысячи учеников, не вычитывая весь список в память на каждый запрос.

ОСТАТОК В EDUSON — ОСТАТОК КУРСА, А НЕ ДЕНЕГ (спека этапа 3, раздел B). Оплат в
Eduson нет (решение владельца на этапе 1), и прежний пул «куплено − пройдено»
метил «Закрыт» всю школу. Поле по-прежнему называется `balance` (имя в API и
ключ сортировки не менялись), но значит «сколько уроков курса осталось»:
    balance = Σ по активным членствам длина курса группы − attended,
где длина — apps.groups.course_length (groups.lessons_total, пусто → программа
направления), а attended — тот же прогресс, что в колонке «Пройдено»
(lessons_done + неоплачиваемые пропуски). Остаток и прогресс выводятся из одной
пары planned/attended, поэтому разъехаться не могут. Нет активного курса →
остатка нет (NULL), а не 0: иначе «Без группы» целиком стал бы «Закрыт».

Сводка (get_summary) — агрегаты по всей активной популяции (это тотал, считать
всех неизбежно), поэтому кэшируется (Redis) и прогревается Celery-beat. Список —
per-request дешёвая индексированная выборка страницы, не кэшируется.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.core.cache import cache
from django.db.models import (
    Case, Count, DecimalField, Exists, ExpressionWrapper, F, FloatField,
    IntegerField, Max, Min, OuterRef, QuerySet, Subquery, Sum, Value, When,
)
from django.db.models.functions import Coalesce

from apps.core.utils.dates import msk_now
from apps.core.utils.decimal import js_number, to_decimal
from apps.groups.course_length import effective_total_lessons_expr
from apps.lessons.models import LessonAttendance, LessonSkip
from apps.lessons.weights import HALF_LESSON_MINUTES
from apps.memberships.models import GroupMembership
from apps.scheduling import repository as sched_repo
from apps.scheduling.occurrences import CANCELLED, OVERDUE, PENDING
from apps.scheduling.models import PlannedLesson
from apps.students.models import Student

# Порог «простоя» (дней без проведённого урока).
IDLE_DAYS = 14

# Единый источник приоритета статусов (срочность по убыванию). Отсюда — и
# статус-бейдж (classify), и urgency_rank для сортировки в SQL (_URGENCY_CASE).
# При правке порядка синхронизировать оба места (тест это подстрахует).
STATUS_PRIORITY = ('closed', 'ending', 'idle', 'no_plan', 'ok')
STATUS_RANK = {s: i for i, s in enumerate(STATUS_PRIORITY)}

# Публичные whitelist'ы (валидируются во view).
SEGMENTS = ('all', 'ending', 'closed', 'idle', 'no_plan', 'cancellations')
SORTS = ('urgency', 'name', 'balance', 'progress', 'last_lesson')
SORT_DIRS = ('asc', 'desc')

# Кэш сводки: короткий TTL (базовая свежесть для всех мутаций); оплаты
# инвалидируют явно; в проде Celery-beat держит тёплым. Список НЕ кэшируется.
SUMMARY_CACHE_KEY = 'registry:summary'
SUMMARY_TTL = 120  # секунд

_DEC = DecimalField(max_digits=20, decimal_places=2)
_ZERO_DEC = Value(Decimal('0'), output_field=_DEC)


def _today() -> datetime.date:
    return msk_now().date()


def _idle_cutoff(today: datetime.date) -> datetime.date:
    return today - datetime.timedelta(days=IDLE_DAYS)


def _month_bounds(today: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Полуинтервал [1-е число, 1-е число следующего) МСК-месяца, в котором today.

    Из today, а не из отдельного «сейчас»: KPI «Отмены», сегмент и строка списка
    обязаны смотреть в один и тот же месяц (и тесты подменяют только _today).
    """
    start = today.replace(day=1)
    return start, (start + datetime.timedelta(days=32)).replace(day=1)


# ---------------------------------------------------------------------------
# Классификация (чистая) — статус-бейдж + флаги сигналов. Без БД.
# ---------------------------------------------------------------------------

def classify(balance, last_date, next_date, today: datetime.date,
             cancellations: int = 0) -> dict:
    """
    Остаток курса/даты → флаги сигналов (пересекающиеся) + статус-бейдж (самый
    срочный). closed > ending > idle > no_plan > ok. Точечно тестируемо на границах.

    balance=None — у ученика нет активного курса (режим «Без группы»): ни
    «Закрыт», ни «Заканчивается» про него сказать нельзя.

    «Отмены» — только флаг сегмента, в статус-бейдж не входит: отменённое
    занятие — повод присмотреться, а не состояние ученика, и вытеснять им
    «Закрыт»/«Простой» из бейджа было бы неверно.
    """
    cutoff = _idle_cutoff(today)
    flags = {
        'closed': balance is not None and balance <= 0,
        'ending': balance is not None and 0 < balance <= 2,
        'idle': last_date is not None and last_date < cutoff,
        'no_plan': next_date is None,
        'cancellations': cancellations > 0,
    }
    status = next((s for s in STATUS_PRIORITY if flags.get(s)), 'ok')
    return {**flags, 'status': status}


# ---------------------------------------------------------------------------
# Аннотированный queryset активных учеников (вариант B). Все агрегаты —
# коррелированные Subquery по student_id → без fan-out от JOIN'ов.
# ---------------------------------------------------------------------------

def _course_total_expr():
    """Длина курса группы для queryset'а членств (правило — apps.groups.course_length)."""
    return effective_total_lessons_expr('group__')


def _membership_sum_subquery(expr):
    """
    Σ выражения по активным membership ученика (в активных группах).

    Без Coalesce внутри: «нет ни одного членства / ни одной известной длины
    курса» должно остаться NULL — для остатка курса это «не знаем», а не 0.
    Где нужен ноль, вызывающий оборачивает в Coalesce сам.
    """
    return Subquery(
        GroupMembership.objects
        .filter(student_id=OuterRef('pk'), active=True, group__active=True)
        .values('student_id')
        .annotate(s=Sum(expr, output_field=_DEC))
        .values('s')[:1],
        output_field=_DEC,
    )


def _cancelled_lessons(month_start: datetime.date, month_end: datetime.date) -> QuerySet:
    """
    Отменённые плановые занятия активных групп за месяц.

    Предикат ровно тот же, что у KPI «Отмены» (sched_repo.cancellations_count):
    сегмент — это «ученики, которых касается этот KPI». Повторён здесь, а не
    вызван, потому что KPI отдаёт готовое число, а сегменту нужен queryset для
    группировки по ученику. При правке определения KPI — править и тут.
    """
    return PlannedLesson.objects.filter(
        group__active=True,
        status=CANCELLED,
        scheduled_date__gte=month_start,
        scheduled_date__lt=month_end,
    )


def _cancellations_subquery(month_start: datetime.date, month_end: datetime.date):
    """
    Число отмен месяца в активных группах ученика (активное членство).

    Оба условия на членство — в одном filter(): один JOIN, иначе Django завёл
    бы два и условие «активно» проверялось бы не у того членства.
    Индекс planned_lessons_group_date_idx (group_id, scheduled_date).
    """
    return Coalesce(Subquery(
        _cancelled_lessons(month_start, month_end)
        .filter(group__memberships__active=True,
                group__memberships__student_id=OuterRef('pk'))
        .values('group__memberships__student_id')
        .annotate(c=Count('id'))
        .values('c')[:1],
        output_field=IntegerField(),
    ), Value(0))


def _half_weight(duration_field: str) -> Case:
    """Вес слота курса в уроках: 45 мин = 0.5, иначе 1 (шаг lessons_done).

    Путь до длительности здесь — через группу или урок пропуска, поэтому
    attended_units_case() из apps.lessons.weights не подходит; порог — общий.
    """
    return Case(
        When(**{duration_field: HALF_LESSON_MINUTES}, then=Value(Decimal('0.5'))),
        default=Value(Decimal('1')),
        output_field=_DEC,
    )


def _skip_sources() -> tuple[QuerySet, QuerySet]:
    """
    «Неоплачиваемый пропуск» в активных группах ученика — два источника.

    Пропуск — терминальный исход «этот урок ученик прошёл в другом месте»
    (перевод / начал не с 1-го), поэтому в прогресс курса он идёт наравне с
    посещением. lessons_done его не содержит: счётчик растёт только на
    present=true (apps.lessons.services.record_lesson), а пропуск — present=false.

    Пропуск хранится в двух местах (apps.lessons.models):
      • lesson_skips — пометка на СЛОТ группы, в т.ч. на ещё не проведённый
        урок; вес — по длительности группы (урока ещё нет);
      • lesson_attendance.unpaid_skip — исход на проведённом уроке, ставится и
        точечно, без пометки; вес — по длительности урока. Слот, на котором
        стоит и пометка, отсюда исключён: пометка материализуется в посещаемость
        (set_lesson_skip), и без исключения слот посчитался бы дважды.

    Слоты ≤ lesson_number_offset не считаются: в группе-продолжении перевода
    lessons_done уже засеян этим числом уроков (Фаза 1b transfer-progress).

    Возвращает два queryset'а без группировки — список и сводка группируют их
    по-своему (коррелированно и пакетно), а условия живут в одном месте.
    """
    active_membership = GroupMembership.objects.filter(
        group_id=OuterRef('group_id'), student_id=OuterRef('student_id'), active=True,
    )
    slots = LessonSkip.objects.filter(
        Exists(active_membership),
        group__active=True,
        lesson_number__gt=F('group__lesson_number_offset'),
    )
    marked_slot = LessonSkip.objects.filter(
        group_id=OuterRef('lesson__group_id'),
        student_id=OuterRef('student_id'),
        lesson_number=OuterRef('lesson__lesson_number'),
    )
    attendance = LessonAttendance.objects.filter(
        Exists(GroupMembership.objects.filter(
            group_id=OuterRef('lesson__group_id'),
            student_id=OuterRef('student_id'),
            active=True,
        )),
        ~Exists(marked_slot),
        unpaid_skip=True,
        lesson__group__active=True,
        lesson__lesson_number__gt=F('lesson__group__lesson_number_offset'),
    ).exclude(lesson__lesson_type__in=('extra', 'burned'))
    return slots, attendance


_SLOT_SKIP_WEIGHT = 'group__lesson_duration_minutes'
_ATT_SKIP_WEIGHT = 'lesson__lesson_duration_minutes'


def _skipped_units_subquery():
    """Σ неоплачиваемых пропусков ученика в активных группах (в уроках)."""
    slots, attendance = _skip_sources()

    def per_student(qs: QuerySet, weight_field: str):
        return Coalesce(Subquery(
            qs.filter(student_id=OuterRef('pk'))
            .values('student_id')
            .annotate(u=Sum(_half_weight(weight_field)))
            .values('u')[:1],
            output_field=_DEC,
        ), _ZERO_DEC)

    return per_student(slots, _SLOT_SKIP_WEIGHT) + per_student(attendance, _ATT_SKIP_WEIGHT)


def _last_lesson_subquery():
    """
    Дата последнего посещённого занятия (для idle-флага).

    Намеренно БЕЗ exclude(lesson_type='extra'), в отличие от
    _attended_units_subquery: доп.урок — реальное присутствие ученика, его
    дата корректно двигает «последнее занятие» вперёд. Исключается из баланса
    (там задвоение), но не из активности/engagement-сигнала.
    """
    return Subquery(
        LessonAttendance.objects
        .filter(student_id=OuterRef('pk'), present=True)
        .order_by('-lesson__lesson_date')
        .values('lesson__lesson_date')[:1],
    )


def _next_lesson_subquery(today: datetime.date):
    return Subquery(
        PlannedLesson.objects
        .filter(
            group__active=True,
            group__memberships__active=True,
            group__memberships__student_id=OuterRef('pk'),
            scheduled_date__gte=today,
            status__in=(PENDING, OVERDUE),
        )
        .order_by('scheduled_date')
        .values('scheduled_date')[:1],
    )


def base_students_qs(today: datetime.date, *, no_group: bool = False) -> QuerySet:
    """
    Активные ученики (есть активный membership в активной группе), аннотированные
    вычисляемыми полями. База и для списка, и для сводки.

    Статуса ученика больше нет (спека 2026-07-25): «учится» = активное членство.
    Ушедший ученик покидает реестр, когда менеджер снимает членство, а не по
    стадии сделки — фильтр по последней сделке был бы лишним коррелированным
    подзапросом в тяжёлом кешируемом запросе дашборда.

    no_group=True — режим «Без группы»: тот же queryset с теми же колонками и
    статусами, но предикат популяции перевёрнут (Exists → NOT Exists). Сюда
    попадают все, кого обычный реестр не показывает: нераспределённые новенькие,
    ученики со снятым членством и ученики заархивированных групп. Два режима
    разбивают учеников школы без пересечений и без потерь — инвариант закреплён
    тестом (test_registry_no_group.py::test_modes_partition_all_students).
    Сводка (_summary_rows) режим не знает: KPI описывают тех, кто учится.
    """
    active_membership = GroupMembership.objects.filter(
        student_id=OuterRef('pk'), active=True, group__active=True,
    )
    population = Student.objects.exclude if no_group else Student.objects.filter
    month_start, month_end = _month_bounds(today)
    qs = (
        population(Exists(active_membership))
        .annotate(
            # Σ длин курсов активных групп; NULL — активного курса нет.
            course_total=_membership_sum_subquery(_course_total_expr()),
            # Прогресс курса = пройдено в группах + неоплачиваемые пропуски в них
            # (уроки, пройденные в другом месте; см. _skip_sources).
            attended=ExpressionWrapper(
                Coalesce(_membership_sum_subquery('lessons_done'), _ZERO_DEC)
                + _skipped_units_subquery(),
                output_field=_DEC,
            ),
            planned=Coalesce(F('course_total'), _ZERO_DEC),
            # Остаток КУРСА (см. докстринг модуля); NULL - x = NULL сохраняет
            # «нет курса» без отдельного CASE.
            balance=ExpressionWrapper(
                F('course_total') - F('attended'), output_field=_DEC,
            ),
            last_lesson=_last_lesson_subquery(),
            next_lesson=_next_lesson_subquery(today),
            cancellations=_cancellations_subquery(month_start, month_end),
        )
    )
    # Второй проход: поля, зависящие от balance/дат (urgency_rank, progress_ratio).
    cutoff = _idle_cutoff(today)
    return qs.annotate(
        urgency_rank=Case(
            When(balance__lte=0, then=Value(0)),                 # closed
            When(balance__lte=2, then=Value(1)),                 # ending (0<bal≤2)
            When(last_lesson__lt=cutoff, then=Value(2)),         # idle
            When(next_lesson__isnull=True, then=Value(3)),       # no_plan
            default=Value(4),
            output_field=IntegerField(),
        ),
        progress_ratio=Case(
            When(planned__gt=0, then=ExpressionWrapper(
                F('attended') * Value(1.0) / F('planned'), output_field=FloatField(),
            )),
            default=Value(None),
            output_field=FloatField(),
        ),
    )


# ---------------------------------------------------------------------------
# Те же поля для СВОДКИ — пакетно, без коррелированных подзапросов.
# ---------------------------------------------------------------------------

def _summary_rows(today: datetime.date) -> list[dict]:
    """
    Поля base_students_qs (balance/attended/planned/last_lesson/next_lesson/
    cancellations) по
    ВСЕЙ активной популяции, но каждый источник читается ровно ОДИН раз.

    Почему не переиспользуем base_students_qs: там на каждого ученика свой
    коррелированный подзапрос. Для страницы списка (50 строк) это дёшево, а
    сводке нужны все, и подзапрос «дата последнего занятия» вырождается в
    перебор журнала занятий с конца — отдельно на каждого ученика. Замер
    2026-08-02 на 236 учениках: 100 270 проб и 82% стоимости всего запроса,
    причём стоимость растёт как «ученики × занятия», то есть квадратично.

    Значения обязаны совпадать с base_students_qs до последнего знака —
    считает это одна и та же арифметика, меняется только форма выборки.
    """
    memberships = GroupMembership.objects.filter(active=True, group__active=True)

    # Активная популяция + суммы по членствам. Набор учеников тот же, что даёт
    # Exists(active_membership) в base_students_qs: «есть активное членство в
    # активной группе». direction/group — связь «к одному», fan-out нет.
    mem_rows = list(
        memberships
        .values('student_id')
        .annotate(
            attended=Coalesce(Sum('lessons_done', output_field=_DEC), _ZERO_DEC),
            # Без Coalesce — как course_total в base_students_qs (NULL = курса нет).
            course_total=Sum(_course_total_expr(), output_field=_DEC),
        )
    )

    # Дата последнего занятия. Бесплатное занятие её двигает (ученик был), как
    # и в _last_lesson_subquery списка.
    last_lesson = dict(
        LessonAttendance.objects
        .filter(present=True)
        .values('student_id')
        .annotate(last=Max('lesson__lesson_date'))
        .values_list('student_id', 'last')
    )

    # Отмены месяца: JOIN к членствам — по строке на (занятие, членство), а у
    # ученика в группе членство одно (UNIQUE group+student), так что Count
    # по ученику честный.
    month_start, month_end = _month_bounds(today)
    cancellations = dict(
        _cancelled_lessons(month_start, month_end)
        .filter(group__memberships__active=True)
        .values('group__memberships__student_id')
        .annotate(c=Count('id'))
        .values_list('group__memberships__student_id', 'c')
    )

    # Ближайшее занятие: JOIN к членствам даёт fan-out по группам ученика, но
    # Min от этого не зависит — как и MIN в исходном подзапросе с LIMIT 1.
    next_lesson = dict(
        PlannedLesson.objects
        .filter(
            group__active=True,
            group__memberships__active=True,
            scheduled_date__gte=today,
            status__in=(PENDING, OVERDUE),
        )
        .values('group__memberships__student_id')
        .annotate(d=Min('scheduled_date'))
        .values_list('group__memberships__student_id', 'd')
    )

    # Неоплачиваемые пропуски — те же два источника и условия, что в списке
    # (_skip_sources), только сгруппированные по всем ученикам сразу.
    skip_slots, skip_attendance = _skip_sources()
    skipped: dict[int, Decimal] = {}
    for qs, weight_field in ((skip_slots, _SLOT_SKIP_WEIGHT),
                             (skip_attendance, _ATT_SKIP_WEIGHT)):
        for sid, u in (
            qs.values('student_id')
            .annotate(u=Sum(_half_weight(weight_field)))
            .values_list('student_id', 'u')
        ):
            skipped[sid] = skipped.get(sid, Decimal('0')) + to_decimal(u)

    rows = []
    for m in mem_rows:
        sid = m['student_id']
        attended = to_decimal(m['attended']) + skipped.get(sid, Decimal('0'))
        course_total = m['course_total']
        rows.append({
            # build_summary ключ не читает — он нужен сверке с base_students_qs
            # (тест test_summary_batch_matches_annotated_queryset) и отладке.
            'student_id': sid,
            'balance': (to_decimal(course_total) - attended
                        if course_total is not None else None),
            'attended': attended,
            'planned': course_total if course_total is not None else Decimal('0'),
            'last_lesson': last_lesson.get(sid),
            'next_lesson': next_lesson.get(sid),
            'cancellations': cancellations.get(sid, 0),
        })
    return rows


# ---------------------------------------------------------------------------
# Список: фильтр сегмента + поиск + сортировка (view пагинирует queryset).
# ---------------------------------------------------------------------------

def _apply_segment(qs: QuerySet, segment: str, today: datetime.date) -> QuerySet:
    """Фильтр по сигналу (пересекающиеся условия — не эксклюзивный статус)."""
    cutoff = _idle_cutoff(today)
    if segment == 'ending':
        return qs.filter(balance__gt=0, balance__lte=2)
    if segment == 'closed':
        return qs.filter(balance__lte=0)
    if segment == 'idle':
        return qs.filter(last_lesson__lt=cutoff)
    if segment == 'no_plan':
        return qs.filter(next_lesson__isnull=True)
    if segment == 'cancellations':
        return qs.filter(cancellations__gt=0)
    return qs  # 'all'


def _apply_search(qs: QuerySet, search: str) -> QuerySet:
    """Поиск по имени ИЛИ коду группы (Exists — без задваивания строк)."""
    q = (search or '').strip()
    if not q:
        return qs
    code_match = GroupMembership.objects.filter(
        student_id=OuterRef('pk'), active=True, group__active=True,
        group__name__icontains=q,
    )
    from django.db.models import Q
    return qs.filter(Q(full_name__icontains=q) | Exists(code_match))


_ORDER_FIELDS = {
    'urgency': ('urgency_rank', 'balance', 'full_name'),
    'name': ('full_name',),
    'balance': ('balance',),
    'progress': ('progress_ratio',),
    'last_lesson': ('last_lesson',),
}


def _apply_order(qs: QuerySet, sort_by: str, sort_dir: str) -> QuerySet:
    fields = _ORDER_FIELDS.get(sort_by, _ORDER_FIELDS['urgency'])
    desc = sort_dir == 'desc'
    order = []
    for i, name in enumerate(fields):
        f = F(name)
        # Первичный ключ — по направлению; вторичные (тай-брейки urgency) — asc.
        expr = (f.desc(nulls_last=True) if (desc and i == 0) else f.asc(nulls_last=True))
        order.append(expr)
    return qs.order_by(*order)


def students_qs(*, segment='all', search='', sort_by='urgency', sort_dir='asc',
                no_group=False) -> QuerySet:
    """Аннотированный, отфильтрованный и отсортированный queryset (view пагинирует).

    no_group переключает популяцию (см. base_students_qs); сегмент, поиск и
    сортировка работают в обоих режимах одинаково.
    """
    today = _today()
    qs = base_students_qs(today, no_group=no_group)
    qs = _apply_segment(qs, segment, today)
    qs = _apply_search(qs, search)
    return _apply_order(qs, sort_by, sort_dir)


def serialize_rows(students: list) -> list[dict]:
    """Строки ответа из Student-объектов страницы: догрузка кодов/преподов
    (батч по id страницы, без N+1) + статус через classify."""
    today = _today()
    ids = [s.pk for s in students]
    codes: dict[int, list[str]] = {}
    teachers: dict[int, list[str]] = {}
    if ids:
        rows = (
            GroupMembership.objects
            .filter(student_id__in=ids, active=True, group__active=True)
            .order_by('group__name')
            .values('student_id', group_name=F('group__name'), teacher_name=F('group__teacher__name'))
        )
        for r in rows:
            codes.setdefault(r['student_id'], []).append(r['group_name'])
            t = r['teacher_name']
            lst = teachers.setdefault(r['student_id'], [])
            if t and t not in lst:
                lst.append(t)

    out: list[dict] = []
    for s in students:
        # None (нет активного курса) не превращаем в 0 — это был бы «Закрыт».
        balance = js_number(to_decimal(s.balance)) if s.balance is not None else None
        attended = to_decimal(s.attended or 0)
        planned = int(s.planned or 0)
        progress_pct = round(float(attended) / planned * 100) if planned > 0 else None
        cancellations = int(s.cancellations or 0)
        status = classify(balance, s.last_lesson, s.next_lesson, today)['status']
        out.append({
            'student_id': s.pk,
            'student_name': s.full_name,
            'codes': codes.get(s.pk, []),
            'teacher_names': teachers.get(s.pk, []),
            'balance': balance,
            'attended': js_number(attended),
            'planned': planned,
            'progress_pct': progress_pct,
            'last_lesson_date': s.last_lesson.isoformat() if s.last_lesson else None,
            'next_lesson_date': s.next_lesson.isoformat() if s.next_lesson else None,
            'cancellations': cancellations,
            'status': status,
        })
    return out


# ---------------------------------------------------------------------------
# Сводка: агрегаты по всей активной популяции + поток дня. Кэшируется.
# ---------------------------------------------------------------------------

def build_summary() -> dict:
    """KPI + счётчики сигналов + поток дня. Один лёгкий проход по активным
    ученикам (тотал — считать всех неизбежно); результат кэшируется."""
    today = _today()
    month_start, month_end = _month_bounds(today)

    rows = _summary_rows(today)

    active = 0
    signals = {'ending': 0, 'closed': 0, 'idle': 0, 'no_plan': 0, 'cancellations': 0}
    lessons_ahead = Decimal('0')
    progress_sum = 0
    progress_n = 0
    for r in rows:
        active += 1
        balance = r['balance']
        flags = classify(balance, r['last_lesson'], r['next_lesson'], today,
                         cancellations=r['cancellations'])
        for k in signals:
            if flags[k]:
                signals[k] += 1
        # lessons_ahead — уроков курса впереди по школе (только положительные остатки).
        if balance is not None and balance > 0:
            lessons_ahead += to_decimal(balance)
        planned = int(r['planned'] or 0)
        if planned > 0:
            progress_sum += round(float(to_decimal(r['attended'] or 0)) / planned * 100)
            progress_n += 1

    kpis = {
        'active_students': active,
        'renewal_upsell': signals['ending'] + signals['closed'],
        'idle': signals['idle'],
        'avg_progress': round(progress_sum / progress_n) if progress_n else 0,
        'lessons_ahead': js_number(lessons_ahead),
        'cancellations': sched_repo.cancellations_count(month_start, month_end),
    }
    return {
        'generated_at': msk_now().isoformat(),
        'kpis': kpis,
        'today_stream': _build_today_stream(today),
        'signals': {k: {'count': v} for k, v in signals.items()},
    }


def _build_today_stream(today: datetime.date) -> list[dict]:
    """«Поток дня»: плановые занятия всех активных групп на сегодня.

    Отдаём время начала и длительность, а не готовый ярлык «идёт/не отмечен»:
    сводка кэшируется (SUMMARY_TTL), и вычисленный здесь статус «замёрз» бы на
    два часа. Часы тикают в браузере — там статус и считается.
    """
    occ = sched_repo.occurrences_on_date(today)
    if not occ:
        return []
    tnames = sched_repo.teacher_names()
    group_ids = sorted({o['group_pk'] for o in occ})
    students_by_group = sched_repo.student_names_by_group(group_ids)
    stream: list[dict] = []
    for o in occ:
        t = o['scheduled_time']
        stream.append({
            'time': t.strftime('%H:%M') if t else None,
            'group_id': o['group_pk'],
            'group_code': o['group_name'],
            'teacher_name': tnames.get(o['teacher_id']),
            'student_names': students_by_group.get(o['group_pk'], []),
            'status': o['status'],
            'duration_minutes': o['duration_minutes'],
        })
    return stream


def get_summary() -> dict:
    """Сводка из кэша; при промахе/ошибке — свежий расчёт (и запись в кэш).
    Cache — оптимизация, не источник правды."""
    try:
        cached = cache.get(SUMMARY_CACHE_KEY)
    except Exception:
        return build_summary()
    if cached is not None:
        return cached
    summary = build_summary()
    try:
        cache.set(SUMMARY_CACHE_KEY, summary, SUMMARY_TTL)
    except Exception:
        pass
    return summary


def refresh_summary() -> str:
    """Пересчитать сводку и положить в кэш (точка входа Celery-прогрева).
    Возвращает generated_at."""
    summary = build_summary()
    try:
        cache.set(SUMMARY_CACHE_KEY, summary, SUMMARY_TTL)
    except Exception:
        pass
    return summary['generated_at']


def invalidate_registry_cache() -> None:
    """Сбросить кэш сводки (после мутаций баланса — вызывается из payments)."""
    try:
        cache.delete(SUMMARY_CACHE_KEY)
    except Exception:
        pass
