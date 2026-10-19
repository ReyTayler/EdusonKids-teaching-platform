"""
DashboardService — сводка и ряды дашборда «Финансы» (порт getDashboard).

FIFO через единый apps/finances (fifo_inputs + compute_fifo) — не дублируем.
Денежные значения отдаются как JSON-числа (js_number: целое→int, дробное→float),
ровно как Express (Number + JSON.stringify), а не строкой. Округление до 2 знаков —
js_round2 (Math.round(x*100)/100), не round_kopecks.

ИЗВЕСТНОЕ РАСХОЖДЕНИЕ (решение пользователя): worked_off_month/deferred_total
суммируют точные Decimal из FIFO, тогда как Express копил во float — отсюда
≤1 копейка разницы. См. apps/finances/fifo.py и память project_fifo_decimal_decision.
"""
from __future__ import annotations

import calendar
import datetime
import time
from decimal import Decimal
from typing import Optional

from django.core.cache import cache

from apps.core.utils.dates import msk_month_range_triple, msk_today
from apps.core.utils.decimal import js_number, js_round2
from apps.dashboard import repository
from apps.finances.fifo import compute_fifo
from apps.finances.repository import fifo_inputs

_ZERO = Decimal('0')


def _add_day(d: str) -> str:
    """d + 1 день (UTC), 'YYYY-MM-DD'. Порт dashboard.js _addDay (для эксклюзивного to)."""
    return (datetime.date.fromisoformat(d) + datetime.timedelta(days=1)).strftime('%Y-%m-%d')


DEFAULT_PERIOD_MONTHS = 3


def default_period(today: Optional[str] = None) -> tuple[str, str]:
    """
    Период страницы «Финансы» по умолчанию: последние 3 месяца по сегодня (МСК),
    обе границы включительно. День зажимается в короткий месяц (31.05 → 28/29.02).
    """
    end = datetime.date.fromisoformat(today or msk_today())
    y, m = end.year, end.month - DEFAULT_PERIOD_MONTHS
    while m < 1:
        y, m = y - 1, m + 12
    start = datetime.date(y, m, min(end.day, calendar.monthrange(y, m)[1]))
    return start.isoformat(), end.isoformat()


def _iter_days(from_: str, to: str):
    """Даты периода включительно, 'YYYY-MM-DD'. Общий обход для дневных рядов графиков."""
    day = datetime.date.fromisoformat(from_)
    end = datetime.date.fromisoformat(to)
    while day <= end:
        yield day.isoformat()
        day += datetime.timedelta(days=1)


def _daily_monthly(from_: str, to: str, by_date: dict[str, Decimal], field: str) -> tuple[list, list]:
    """
    Ряды для графика daily/monthly из карты «дата → сумма»: каждый день периода
    (пустые — ноль, чтобы линия не рвалась) и свёртка по календарным месяцам.
    Округление до копеек — один раз, на выходе.
    """
    daily, monthly = [], []
    for iso in _iter_days(from_, to):
        value = by_date.get(iso, _ZERO)
        daily.append({'date': iso, field: js_number(js_round2(value))})
        if not monthly or monthly[-1]['month'] != iso[:7]:
            monthly.append({'month': iso[:7], field: _ZERO})
        monthly[-1][field] += value
    for m in monthly:
        m[field] = js_number(js_round2(m[field]))
    return daily, monthly


def get_dashboard(from_: Optional[str] = None, to: Optional[str] = None) -> dict:
    """
    Сводка: revenue_month, worked_off_month, carryover, deferred_total + ряды
    признанной выручки по дням и месяцам периода.

    Порт dashboard.js getDashboard. Период [period_start, period_end):
    с from/to — заданный диапазон (to эксклюзивно через _add_day), иначе
    default_period() — последние 3 месяца (до 2026-09 был текущий МСК-месяц).
    В ответе from/to — фактически применённые границы (дефолт тоже).
    Долги считаются по student_id (общий пул, без разбивки по направлению —
    см. docs/superpowers/specs/2026-07-08-student-balance-pooling-design.md).
    """
    month = msk_month_range_triple()[0]
    if not (from_ or to):
        from_, to = default_period()
    period_start = from_ or '0001-01-01'
    period_end = _add_day(to) if to else '9999-12-31'

    revenue_month = js_round2(repository.revenue_for_period(period_start, period_end))

    inp = fifo_inputs()
    lots_by_key = inp['lots_by_key']
    cons_by_key = inp['cons_by_key']

    deferred_total = _ZERO
    recognized_by_date: dict[str, Decimal] = {}
    for key in inp['keys']:
        fifo = compute_fifo(
            lots_by_key.get(key, []), cons_by_key.get(key, []), period_start, period_end
        )
        deferred_total += fifo['remaining_value']
        # Разрез по дням пожизненный — оставляем только дни периода. Предикат тот
        # же, что у worked_off_month внутри FIFO, поэтому сумма ряда и есть
        # «Отработано за период» — считаем её здесь, из ТОЧНЫХ Decimal.
        # (fifo['worked_off_month'] округлён покопеечно на каждом ученике: сумма
        # таких значений даёт дрейф в пару копеек против ряда графика.)
        for iso, value in fifo['worked_off_by_date'].items():
            if period_start <= iso < period_end:
                recognized_by_date[iso] = recognized_by_date.get(iso, _ZERO) + value

    worked_off_month = sum(recognized_by_date.values(), _ZERO)

    # Без from период открыт слева ('0001-01-01') — ряд по дням начинаем с первого
    # дня, где выручка реально признана, иначе список тянулся бы с первого года.
    series_to = to or msk_today()
    series_from = from_ or (min(recognized_by_date) if recognized_by_date else series_to)
    recognized_daily, recognized_monthly = _daily_monthly(
        series_from, series_to, recognized_by_date, 'recognized',
    )

    worked_off_month = js_round2(worked_off_month)
    deferred_total = js_round2(deferred_total)
    carryover_month = js_round2(revenue_month - worked_off_month)

    return {
        'month': month,
        'from': from_ or None,
        'to': to or None,
        'revenue_month': js_number(revenue_month),
        'worked_off_month': js_number(worked_off_month),
        'carryover_month': js_number(carryover_month),
        'deferred_total': js_number(deferred_total),
        # Признанная выручка (FIFO) по дням и месяцам периода — график
        # «Recognized revenue». Ряды едут вместе со сводкой, а не отдельным
        # адресом: FIFO по всей базе считается один раз на запрос, второй вызов
        # удвоил бы самый тяжёлый расчёт системы.
        'recognized_daily': recognized_daily,
        'recognized_monthly': recognized_monthly,
    }


def _aov(revenue: Decimal, orders: int):
    """Средний чек: точное деление Decimal, округление до копеек; нет заказов → None."""
    return js_number(js_round2(revenue / orders)) if orders else None


def get_revenue(from_: Optional[str] = None, to: Optional[str] = None) -> dict:
    """
    Revenue / Orders за период + разбивка по дням и месяцам (графики
    «Revenue daily / monthly»). Обе границы включительно, по paid_at.

    Revenue — сумма поступлений: покупки, доплаты к абонементу и доп.уроки;
    возвраты исключены (решение пользователя 2026-10-05, см. repository);
    products — та же выручка в разрезе курсов (направлений);
    Orders — число оплат с total_amount > 0; AOV = Revenue / Orders (средний чек).
    Пустые дни/месяцы — нули, чтобы линия графика не рвалась; AOV там null
    (делить не на что — это отсутствие значения, а не ноль; на графике фронт
    рисует его нулём). Без from/to — default_period(); без from — с первой
    оплаты в базе; без to — по сегодня (МСК). Даты валидирует view.
    """
    if not (from_ or to):
        from_, to = default_period()
    to = to or msk_today()
    if not from_:
        first = repository.first_payment_date()
        from_ = min(first.isoformat(), to) if first else to

    by_day = {r['paid_at'].isoformat(): r for r in repository.revenue_by_day(from_, to)}

    daily: list[dict] = []
    monthly: list[dict] = []
    revenue_total, orders_total = _ZERO, 0
    for iso in _iter_days(from_, to):
        row = by_day.get(iso)
        rev = row['rev'] if row else _ZERO
        orders = row['orders'] if row else 0
        daily.append({
            'date': iso, 'revenue': js_number(js_round2(rev)), 'orders': orders,
            'aov': _aov(rev, orders),
        })
        if not monthly or monthly[-1]['month'] != iso[:7]:
            monthly.append({'month': iso[:7], 'revenue': _ZERO, 'orders': 0})
        monthly[-1]['revenue'] += rev
        monthly[-1]['orders'] += orders
        revenue_total += rev
        orders_total += orders

    for m in monthly:
        m['aov'] = _aov(m['revenue'], m['orders'])
        m['revenue'] = js_number(js_round2(m['revenue']))

    return {
        'from': from_,
        'to': to,
        'revenue': js_number(js_round2(revenue_total)),
        'orders': orders_total,
        'aov': _aov(revenue_total, orders_total),
        'daily': daily,
        'monthly': monthly,
        'products': _products(from_, to),
    }


def _products(from_: str, to: str) -> list[dict]:
    """
    Строки сводной таблицы по курсам: направление, покупки, оплаченные месяцы,
    поступления, ASP и ARPM. Сортировка — по поступлениям убыв., легаси-строка
    «Без направления» последней (решение пользователя 2026-10-07).

    ASP = revenue / purchases (средняя цена продажи). ARPM приходит из запроса —
    это среднее по заказам, а не revenue / months.
    """
    rows = []
    for r in repository.revenue_by_direction(from_, to):
        purchases = r['purchases']
        revenue = r['revenue'] or _ZERO
        months = r['months'] or _ZERO
        rows.append({
            'direction_id': r['direction_id'],
            'direction': r['direction__name'],
            'purchases': purchases,
            'months': js_number(round(months, 2)),
            'revenue': js_number(js_round2(revenue)),
            'asp': js_number(js_round2(revenue / purchases)) if purchases else None,
            'arpm': js_number(js_round2(r['arpm'])) if r['arpm'] is not None else None,
        })
    rows.sort(key=lambda x: (x['direction_id'] is None, -x['revenue']))
    return rows


# ---------------------------------------------------------------------------
# Кэш финансового дашборда (Celery-спека 2026-07-13, фаза B).
#
# get_dashboard — самый тяжёлый расчёт системы (fifo_inputs
# читает ВСЕ payments+attendance, FIFO по каждому ученику). Views читают только
# кэшированные обёртки ниже; расчётные функции выше остаются чистыми.
#
# Инвалидация — generation-ключ: все ключи включают finance:{gen}:…, сброс =
# запись нового gen (timestamp), старые ключи умирают по TTL. Работает одинаково
# на LocMem и Redis, без delete_pattern. Сигналы Payment/Lesson (signals.py)
# меняют generation после коммита; bulk-правки посещаемости (без сигналов)
# покрывает короткий TTL. Кэш — оптимизация, не источник правды: любая ошибка
# кэша → синхронный расчёт (паттерн registry_service).
# ---------------------------------------------------------------------------

DASHBOARD_TTL = 120   # default-ключ; beat греет каждые 60с → всегда тёплый
RANGE_TTL = 300       # произвольные диапазоны — реже, живут дольше

_GEN_KEY = 'finance:gen'
_GEN_TTL = 7 * 24 * 3600   # страховка от вечного ключа; данные живут ≤ RANGE_TTL


def _generation() -> int:
    """Текущая генерация кэша. Промах/мёртвый кэш → свежая (= всегда пересчёт)."""
    try:
        gen = cache.get(_GEN_KEY)
        if gen is None:
            gen = time.time_ns()
            cache.set(_GEN_KEY, gen, _GEN_TTL)
        return int(gen)
    except Exception:
        return time.time_ns()


def _dashboard_key(from_: Optional[str], to: Optional[str]) -> str:
    if not (from_ or to):
        return f'finance:{_generation()}:dashboard:default'
    return f'finance:{_generation()}:dashboard:{from_ or ""}:{to or ""}'


def _cached(key: str, ttl: int, compute):
    """Прочитать из кэша, при промахе — посчитать и положить. Ошибки кэша глотаются."""
    try:
        hit = cache.get(key)
    except Exception:
        return compute()
    if hit is not None:
        return hit
    value = compute()
    try:
        cache.set(key, value, ttl)
    except Exception:
        pass
    return value


def get_dashboard_cached(from_: Optional[str] = None, to: Optional[str] = None) -> dict:
    """Сводка с кэшем. Вызывать ПОСЛЕ валидации дат во view (значения идут в ключ)."""
    ttl = DASHBOARD_TTL if not (from_ or to) else RANGE_TTL
    return _cached(_dashboard_key(from_, to), ttl,
                   lambda: get_dashboard(from_=from_, to=to))


def _revenue_key(from_: Optional[str], to: Optional[str]) -> str:
    return f'finance:{_generation()}:revenue:{from_ or ""}:{to or ""}'


def get_revenue_cached(from_: Optional[str] = None, to: Optional[str] = None) -> dict:
    """Revenue/Orders с кэшем. Дефолт завязан на сегодняшнюю дату → в ключ идёт
    фактический период, а не пустые параметры (иначе ключ пережил бы полночь)."""
    if not (from_ or to):
        from_, to = default_period()
    return _cached(_revenue_key(from_, to), RANGE_TTL,
                   lambda: get_revenue(from_=from_, to=to))


def refresh_dashboard() -> str:
    """Пересчитать default-сводку и положить в кэш (точка входа Celery-прогрева).
    Возвращает текущий месяц (для лога воркера)."""
    data = get_dashboard()
    try:
        cache.set(_dashboard_key(None, None), data, DASHBOARD_TTL)
    except Exception:
        pass
    return data['month']


def invalidate_finance_cache() -> None:
    """
    Сменить генерацию кэша (после мутаций Payment/Lesson — см. signals.py).

    Генерация обязана строго РАСТИ. Просто `time.time_ns()` этого не даёт:
    на Windows часы тикают примерно раз в 15 мс, поэтому два сброса подряд
    получают одно значение и второй молча ничего не инвалидирует; а перевод
    часов назад (синхронизация времени) «оживил» бы старые ключи с устаревшими
    данными. Поэтому при неувеличении берём предыдущее значение плюс единицу.
    """
    try:
        nxt = time.time_ns()
        current = cache.get(_GEN_KEY)
        if current is not None and nxt <= int(current):
            nxt = int(current) + 1
        cache.set(_GEN_KEY, nxt, _GEN_TTL)
    except Exception:
        pass
