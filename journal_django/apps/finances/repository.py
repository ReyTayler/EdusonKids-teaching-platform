"""
FinancesRepository — единственное место доступа к данным вычислительного слоя финансов.

ORM-порт (раздел 09):
  • fifo_inputs()                ← _fifoInputs() (dashboard.js)
  • balance_for_student()        ← общий пул (2026-07-08 редизайн)
  • paid_by_direction_rows()     ← информ. разбивка «оплачено по направлениям» (2026-07-08)
  • attended_by_direction_rows() ← информ. разбивка «отработано по направлениям» (2026-07-08)
  • total_paid_amount()          ← getStudentBalance итоговая сумма (payments.js)

Баланс выводится, не хранится: balance_for_student — единый пул (все купленные
минус все отработанные уроки, без разбивки по направлению). Разбивки
paid_by_direction_rows / attended_by_direction_rows — только информационные.

Число баланса отдаётся через _js_number (int/float, как Number(x) в JS). Сырые
поля оплат (unit_price/total_amount) — Decimal, в строку их приводит
DateSafeJSONRenderer. fifo.py (чистый Python) не трогаем.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Iterable, Optional

from django.db.models import DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce

from apps.core.utils.decimal import round_kopecks, to_decimal
from apps.finances.lots import build_lots

from apps.directions.models import Direction
from apps.lessons.models import LessonAttendance
from apps.payments.models import Payment


_DEC = DecimalField(max_digits=20, decimal_places=2)
_ZERO = Value(Decimal('0'), output_field=_DEC)

# Окно месяца для compute_fifo, раскрытое на всю историю: per-student расчёты
# (остаток к возврату, признание по урокам) нарезки по месяцу не делают — им
# нужны сквозные итоги, а не строка отчёта за месяц.
_FIFO_FULL_RANGE = ('0001-01-01', '9999-12-31')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _js_number(d) -> int | float:
    """JS Number(x): целое → int, дробное → float. Decimal('8.0')→8, Decimal('7.5')→7.5."""
    f = float(d)
    return int(f) if f == int(f) else f


def _date_str(value) -> str:
    """lesson_date (date) → 'YYYY-MM-DD' (Node отдаёт строку через type-parser)."""
    if isinstance(value, datetime.date):
        return value.strftime('%Y-%m-%d')
    return str(value)[:10]


def _attended_units_case():
    """
    half-lesson: SUM(CASE WHEN duration=45 THEN 0.5 ELSE 1) как Decimal-выражение.

    Своего определения здесь больше нет: вес — общий инвариант проекта и живёт в
    apps/lessons/weights.py. Имя оставлено, потому что на него завязаны вызовы
    внутри модуля.
    """
    from apps.lessons.weights import attended_units_case
    return attended_units_case()


def surcharges_by_parent(student_id: int | None = None) -> dict[int, dict[int, Decimal]]:
    """{parent_payment_id: {subscription_index: сумма}} — доплаты к абонементам.

    Без student_id — один запрос на всю школу (fifo_inputs: доплат мало, редкий
    случай недобора платежа, это не N+1). С student_id — тот же запрос, но
    отфильтрованный по ученику (student_fifo_remaining: считаем остаток одного
    ученика, не нужно тянуть доплаты всей школы). См. apps/finances/lots.py::build_lots.
    """
    out: dict[int, dict[int, Decimal]] = {}
    qs = Payment.objects.filter(kind='surcharge')
    if student_id is not None:
        qs = qs.filter(parent_payment__student_id=student_id)
    rows = qs.values('parent_payment_id', 'subscription_index', 'total_amount')
    for r in rows:
        bucket = out.setdefault(r['parent_payment_id'], {})
        idx = r['subscription_index']
        bucket[idx] = bucket.get(idx, Decimal('0')) + to_decimal(r['total_amount'])
    return out


# ---------------------------------------------------------------------------
# FIFO-входы (порт _fifoInputs из dashboard.js)
# ---------------------------------------------------------------------------

def fifo_inputs() -> dict:
    """
    Загружает FIFO-входы по ключу student_id (общий пул на ученика).

    С 2026-07-08 payments.direction_id и направление урока — раздельные измерения:
    оплата с тегом направления A может быть погашена уроком в направлении B (см.
    docs/superpowers/specs/2026-07-08-student-balance-pooling-design.md). Партии
    (lots) сортируются по paid_at глобально по ученику; посещения (consumptions) —
    по lesson_date глобально. direction_id урока сохраняется в каждой
    consumption-записи только для атрибуции worked_off_by_direction в отчётах.

    Возвращает lots_by_key / purchased_by_key / cons_by_key / consumed_by_key / keys.
    Guard: оплаты с subscriptions_count NULL/0 (lessons ≤ 0) пропускаются —
    иначе деление на 0 / Infinity ломает суммы. Партии не фильтруются по
    direction_id — легаси-оплаты (direction_id NULL, subscriptions_count
    проставлен миграцией 0004 apps/payments) пулятся наравне с обычными,
    как и в balance_for_student (2026-07-09: убран лишний фильтр
    direction_id__isnull=False, оставшийся от дизайна до редизайна общего
    пула 2026-07-08 — иначе дашборд «Долги» считал устаревшие остатки).
    Строки kind='refund' не образуют партий — становятся синтетическими
    consumption-записями (флаг refund), готовыми для compute_fifo.
    Строки kind='surcharge' сами по себе партий не образуют (не покупка) — они
    дробят партию своего parent_payment на блоки по 4 урока и поднимают цену
    только своего блока (apps/finances/lots.py::build_lots). См.
    docs/superpowers/specs/2026-07-28-course-surcharge-design.md.
    """
    lots_rows = (
        Payment.objects
        .exclude(kind='surcharge')          # доплаты партий не образуют
        .order_by('student_id', 'paid_at', 'id')
        .values('id', 'student_id', 'total_amount', 'lessons_count', 'kind', 'paid_at',
                'direction_id')
    )
    surcharges = surcharges_by_parent()

    cons_rows = (
        LessonAttendance.objects
        # is_free=False: «бесплатное занятие» (present=true) в FIFO-потребление НЕ
        # идёт — деньги за него не берутся и партии оплат не гасятся. См.
        # docs/superpowers/specs/2026-07-23-lesson-outcomes-spec.md.
        .filter(present=True, is_free=False)
        # доп.урок (lesson_type='extra') ТЕПЕРЬ учитывается в потреблении: в новой
        # модели компенсации исходный пропуск остаётся present=false навсегда, а
        # потребление идёт от самого факта доп.урока (его вес = длительность
        # исходного урока) в дату его проведения. Один пропуск списывается ровно
        # один раз — уже не через ретроактивную отметку исходного занятия.
        .annotate(units=_attended_units_case())
        .order_by('student_id', 'lesson__lesson_date', 'lesson_id')
        .values(
            'student_id', 'units', 'lesson_id',
            direction_id=F('lesson__group__direction_id'),
            lesson_date=F('lesson__lesson_date'),
        )
    )

    purchase_rows_by_key: dict[str, list] = {}  # сырые строки покупок — вход build_lots
    purchased_by_key: dict[str, int] = {}
    refund_cons: dict[str, list] = {}   # синтетические списания-возвраты
    for r in lots_rows:
        key = str(r['student_id'])
        raw = r['lessons_count']
        if r['kind'] == 'refund':
            # возврат: гасит остаток (units = |lessons_count|, точный Decimal), без выручки
            refund_cons.setdefault(key, []).append({
                'units': -to_decimal(raw) if raw is not None else Decimal('0'),
                'date': _date_str(r['paid_at']),
                'direction_id': None,
                'refund': True,
            })
            continue
        lessons = int(raw) if raw is not None else 0  # покупки всегда целые
        if not (lessons > 0):  # guard: NULL/0
            continue
        purchase_rows_by_key.setdefault(key, []).append(r)
        purchased_by_key[key] = purchased_by_key.get(key, 0) + lessons

    # Партии строим одним общим билдером (apps/finances/lots.py) — оплата без
    # доплат остаётся одной партией байт-в-байт как раньше, с доплатами дробится
    # на блоки по 4 урока (см. Task 3, docstring build_lots).
    lots_by_key: dict[str, list] = {
        key: build_lots(rows, surcharges) for key, rows in purchase_rows_by_key.items()
    }

    cons_by_key: dict[str, list] = {}
    consumed_by_key: dict[str, Decimal] = {}
    for r in cons_rows:
        key = str(r['student_id'])
        units = to_decimal(r['units'])
        # Месяц денег = lesson_date самой записи-урока. Для доп.урока/сгорания это
        # дата их проведения (extra/burned — отдельные Lesson в свой месяц), для
        # обычной посещаемости — дата урока. Отдельного ремаппинга (burned_at/
        # makeup-dates) больше нет: каждое потребление несёт свою дату.
        date = r['lesson_date']
        cons_by_key.setdefault(key, []).append({
            'units': units,
            'date': _date_str(date),
            'direction_id': r['direction_id'],
        })
        consumed_by_key[key] = consumed_by_key.get(key, Decimal('0')) + units

    # Возвраты — синтетические списания всего остатка на дату возврата: мержим в
    # consumption'ы и пересортируем по дате (возврат после посещений того же дня).
    for key, refs in refund_cons.items():
        cons_by_key.setdefault(key, []).extend(refs)
    for key, lst in cons_by_key.items():
        lst.sort(key=lambda c: (c['date'], 1 if c.get('refund') else 0))

    keys = list(lots_by_key.keys())
    for k in cons_by_key:
        if k not in lots_by_key:
            keys.append(k)
    return {
        'lots_by_key': lots_by_key,
        'purchased_by_key': purchased_by_key,
        'cons_by_key': cons_by_key,
        'consumed_by_key': consumed_by_key,
        'keys': keys,
    }


# ---------------------------------------------------------------------------
# Баланс (порт из payments.js — единый дом)
# ---------------------------------------------------------------------------

def balances_for_students(student_ids: Iterable[int]) -> dict[int, int | float]:
    """
    Общий баланс (purchased − attended) сразу для набора учеников — без N+1.

    Используется там, где строк много за один раз (срез групп преподавателя в
    teacher_spa, сводки по школе) — на 2 CPU/2 ГБ VPS. Каждый переданный student_id
    гарантированно есть в результате (0, если нет ни оплат, ни посещений).

    purchased = SUM(lessons_count) (включает отрицательные строки возврата → net).
    """
    ids = list(student_ids)
    if not ids:
        return {}

    balances: dict[int, Decimal] = {sid: Decimal('0') for sid in ids}

    purchased = (
        Payment.objects.filter(student_id__in=ids)
        .values('student_id')
        .annotate(s=Coalesce(Sum('lessons_count', output_field=_DEC), _ZERO))
    )
    for r in purchased:
        balances[r['student_id']] += r['s']

    attended = (
        # is_free=False: «бесплатное занятие» не списывает баланс (present=true, но
        # деньги не трогаются). В ОТЛИЧИЕ от продления (attended_units_total), которое
        # free УЧИТЫВАЕТ — здесь метрики намеренно разведены. См. lesson-outcomes-spec.
        LessonAttendance.objects.filter(student_id__in=ids, present=True, is_free=False)
        # доп.урок (lesson_type='extra') учитывается в потреблении: исходный
        # пропуск остаётся present=false, потребление идёт от факта доп.урока.
        # Один пропуск списывается ровно один раз (новая модель компенсации).
        .values('student_id')
        .annotate(s=Coalesce(Sum(_attended_units_case()), _ZERO))
    )
    for r in attended:
        balances[r['student_id']] -= r['s']

    return {sid: _js_number(v) for sid, v in balances.items()}


def attended_units_total(student_id: int) -> Decimal:
    """
    «Отработано» для ПРОДЛЕНИЙ (present=true), в тех же единицах (half-lesson
    45мин=0.5). Факт доп.урока (lesson_type='extra') учитывается: исходный пропуск
    остаётся present=false, потребление идёт от самого доп.урока, поэтому один
    пропуск считается ровно один раз.

    **Важно (2026-07-23):** эта метрика — источник правды ПРОГРЕССА СДЕЛКИ продления
    (apps.renewals.engine._attended_total) и НАМЕРЕННО ШИРЕ балансового «отработано».
    Она включает «бесплатное занятие» (present=true), которое из баланса/FIFO
    исключено (`is_free=False` там). Правило: сделку продления двигает любой исход
    `present=true ИЛИ списывает баланс`; free попадает под первую ветку. Для всех
    НЕ-free исходов эта метрика совпадает с балансовым «отработано» (инвариант
    «отчёт ↔ воронка» сохранён — есть тест). См. lesson-outcomes-spec, решение 5.
    """
    row = (
        LessonAttendance.objects
        .filter(student_id=student_id, present=True)
        .aggregate(s=Coalesce(Sum(_attended_units_case()), _ZERO))
    )
    return row['s']


def balance_for_student(student_id: int) -> int | float:
    """
    Общий баланс ученика (единый пул по всем направлениям): purchased − attended.
    half-lesson: 45→0.5. Делегирует в balances_for_students — одна формула на двоих.
    """
    return balances_for_students([student_id])[student_id]


def student_fifo_remaining(student_id: int) -> dict:
    """
    Неотработанный остаток ученика: сколько уроков и денег ещё не списано.
    remaining_lessons = баланс (purchased − attended, half-lesson учтён).
    remaining_value   = FIFO remaining_value по партиям-покупкам ученика.
    remaining_by_direction = из партий каких направлений состоит этот остаток —
    по нему возврат раскладывается на строки (apps/payments::refund_student),
    иначе лимит курса после возврата не освобождается.

    Партии и потребления строит _student_fifo_args — там же правила refund/surcharge.
    """
    from apps.finances.fifo import compute_fifo

    remaining_lessons = balance_for_student(student_id)
    lots, cons = _student_fifo_args(student_id)
    fifo = compute_fifo(lots, cons, *_FIFO_FULL_RANGE)
    return {
        'remaining_lessons': remaining_lessons,
        'remaining_value': fifo['remaining_value'],
        'remaining_by_direction': fifo['remaining_by_direction'],
    }


def _student_fifo_args(student_id: int) -> tuple[list[dict], list[dict]]:
    """
    Партии и потребления ОДНОГО ученика — общий вход всех per-student расчётов
    FIFO (остаток к возврату, признание по урокам). Общешкольный обход тех же
    правил отдельный — см. fifo_inputs(); внутри per-student пути обход один,
    иначе два независимых разъехались бы при первой же правке правил (доплаты,
    возвраты, бесплатные занятия).

    Строки kind='refund' (см. apps/payments/repository.py::refund_student) не
    образуют партий — как в fifo_inputs(), они становятся синтетическими
    consumption-записями (флаг refund), которые гасят остаток партий без
    учёта в worked_off_*. Иначе повторный вызов после возврата продолжал бы
    показывать остаток, который уже был возвращён (см. Task 4.2).
    Строки kind='surcharge' партий не образуют — как в fifo_inputs(), они
    дробят партию parent_payment на блоки по 4 урока (apps/finances/lots.py).
    Так возврат средств считается по уже подорожавшим ценам без отдельных правок.

    Каждое потребление несёт lesson_id — по нему compute_fifo строит разрез
    worked_off_by_lesson (вкладка «Уроки» карточки ученика). Синтетические
    списания-возвраты урока не имеют и в разрез не попадают.
    """
    payment_rows = (
        Payment.objects.filter(student_id=student_id)
        .exclude(kind='surcharge')          # доплаты партий не образуют
        .order_by('paid_at', 'id')
        .values('id', 'total_amount', 'lessons_count', 'kind', 'paid_at', 'direction_id')
    )
    purchase_rows = []
    refund_cons = []
    for r in payment_rows:
        if r['kind'] == 'refund':
            raw = r['lessons_count']
            refund_cons.append({
                'units': -to_decimal(raw) if raw is not None else Decimal('0'),
                'date': _date_str(r['paid_at']),
                'direction_id': None,
                'refund': True,
            })
            continue
        lessons = int(r['lessons_count']) if r['lessons_count'] is not None else 0
        if lessons > 0:
            purchase_rows.append(r)

    lots = build_lots(purchase_rows, surcharges_by_parent(student_id))

    cons_rows = (
        # is_free=False — как в fifo_inputs и balances_for_students: за бесплатное
        # занятие деньги не берутся, партии оно не гасит. Иначе остаток к возврату
        # оказался бы меньше баланса и клиент недополучил бы деньги.
        LessonAttendance.objects.filter(student_id=student_id, present=True, is_free=False)
        # доп.урок (lesson_type='extra') учитывается в потреблении: исходный
        # пропуск остаётся present=false, потребление идёт от факта доп.урока.
        # Один пропуск списывается ровно один раз (новая модель компенсации).
        .annotate(units=_attended_units_case())
        .order_by('lesson__lesson_date', 'lesson_id')
        .values('units', 'lesson_id', lesson_date=F('lesson__lesson_date'))
    )
    cons = [
        {
            'units': to_decimal(r['units']),
            # Месяц денег = lesson_date записи (extra/burned — своя дата проведения).
            'date': _date_str(r['lesson_date']),
            'direction_id': None,
            'lesson_id': r['lesson_id'],
        }
        for r in cons_rows
    ]
    cons.extend(refund_cons)
    cons.sort(key=lambda c: (c['date'], 1 if c.get('refund') else 0))
    return lots, cons


def student_lesson_recognition(student_id: int) -> dict[int, dict]:
    """
    { lesson_id: {'recognized': Decimal (копейки), 'is_debt': bool} } — сколько
    денег списал каждый урок ученика. Источник колонки «Признано» во вкладке
    «Уроки» карточки ученика (спека 2026-09-14).

    Считается по ВСЕЙ истории ученика: чтобы знать, какие абонементы к моменту
    урока уже погашены, частичной выборки не хватает. Поэтому функция — для
    карточки ОДНОГО ученика; построчно в списках учеников её звать нельзя
    (история каждого целиком на каждую строку).

    is_debt=True — урок прошёл сверх оплаченного остатка ЦЕЛИКОМ ИЛИ ЧАСТИЧНО;
    это не «денег ноль»: у частично оплаченного урока recognized > 0 и флаг
    стоит одновременно. Оценку самого долга деньгами не даём — колонка
    показывает признанную выручку. Бесплатное занятие в потребление не входит
    вовсе, поэтому в карте его нет — потребитель показывает 0 ₽.

    recognized округляется до копеек ПОСТРОЧНО, а отчёты округляют итог один
    раз, поэтому на неделящихся ценах (1000/3) сумма колонки может разойтись с
    отчётом на копейки — по копейке на строку. Колонка объясняет урок, сверять
    итоги по ней нельзя.
    """
    from apps.finances.fifo import compute_fifo

    lots, cons = _student_fifo_args(student_id)
    fifo = compute_fifo(lots, cons, *_FIFO_FULL_RANGE)
    debt = fifo['over_consumed_by_lesson']

    out: dict[int, dict] = {}
    for lesson_id, value in fifo['worked_off_by_lesson'].items():
        out[lesson_id] = {
            'recognized': round_kopecks(value),
            'is_debt': lesson_id in debt,
        }
    for lesson_id in debt:
        out.setdefault(lesson_id, {'recognized': Decimal('0.00'), 'is_debt': True})
    return out


def paid_by_direction_rows(student_id: int) -> list[dict]:
    """
    Оплачено по направлениям (по тегу оплаты payments.direction_id) — ТОЛЬКО
    информационная разбивка, не баланс (баланс общий — см. balance_for_student).
    """
    paid = (
        Payment.objects
        .filter(student_id=student_id)
        .exclude(direction_id__isnull=True)  # легаси (direction_id NULL) не джойнятся
        .values('direction_id')
        .annotate(total_paid=Sum('total_amount'))
    )
    totals = {r['direction_id']: r['total_paid'] for r in paid}
    if not totals:
        return []

    rows: list[dict] = []
    for d in Direction.objects.filter(id__in=totals).order_by('name').values('id', 'name', 'color'):
        rows.append({
            'direction_id': d['id'],
            'direction_name': d['name'],
            'direction_color': d['color'],
            'total_paid_amount': totals[d['id']],
        })
    return rows


def attended_by_direction_rows(student_id: int) -> list[dict]:
    """
    Отработано по направлениям (по направлению УРОКА, не оплаты) — ТОЛЬКО
    информационная разбивка, не баланс.
    """
    attended = (
        LessonAttendance.objects
        .filter(student_id=student_id, present=True)
        # доп.урок (lesson_type='extra') учитывается: исходный пропуск остаётся
        # present=false, потребление идёт от факта доп.урока (новая модель).
        .values(did=F('lesson__group__direction_id'))
        .annotate(attended=Sum(_attended_units_case()))
    )
    totals = {r['did']: r['attended'] for r in attended if r['did'] is not None}
    if not totals:
        return []

    rows: list[dict] = []
    for d in Direction.objects.filter(id__in=totals).order_by('name').values('id', 'name', 'color'):
        rows.append({
            'direction_id': d['id'],
            'direction_name': d['name'],
            'direction_color': d['color'],
            'attended_lessons': totals[d['id']],
        })
    return rows


def total_paid_amount(student_id: int) -> int | float:
    """Итоговая сумма всех оплат ученика. SUM(total_amount)."""
    total = Payment.objects.filter(student_id=student_id).aggregate(
        s=Coalesce(Sum('total_amount'), _ZERO),
    )['s']
    return _js_number(total)
