"""
FIFO-оценка денег по партиям-оплатам. Чистая функция, без БД и побочных эффектов.

Дословный порт services/fifo.js computeFifo на Decimal (бухгалтерская точность,
см. память feedback_financial_accounting_precision):
  • все суммы — Decimal, через to_decimal(str(x));
  • деления/умножения точные, округление до копеек (ROUND_HALF_UP) — один раз на выходе;
  • цена урока партии = total_amount / (subscriptions_count × 4) (считает вызывающий);
  • строгий FIFO: старая партия гасится первой;
  • месяц — полуинтервал [month_start, month_end) (month_end эксклюзивный, как в Node).

ИЗВЕСТНОЕ РАСХОЖДЕНИЕ С EXPRESS (решение пользователя 2026-06-10): Express копит
суммы во float и округляет один раз; этот порт копит в точном Decimal. На ~4 из 248
ключей student:direction worked_off_by_month расходится с Express на ≤1 копейку в
ИСТОРИЧЕСКИХ месяцах (текущий месяц, balance, remaining_value совпадают). Django —
арифметически точнее; разница float-артефакт Express. По брифу 02 и памяти
feedback_financial_accounting_precision выбран Decimal. Следствие: e2e-diff
dashboard getMonthlyFinance не будет байт-пустым в нескольких исторических ячейках.

lots:         [{ 'lessons': n, 'price_per_lesson': Decimal, 'direction_id': int|None }]
              — в порядке оплаты (старые первыми). direction_id опционален: нужен
              только для remaining_by_direction (см. ниже), на очередь не влияет.
consumptions: [{ 'units': 1|0.5, 'date': 'YYYY-MM-DD', 'direction_id': int|None,
              'lesson_id': int|None }] — в порядке даты урока.
              direction_id — направление УРОКА (не оплаты), опционально.
              lesson_id — урок записи; нужен разрезу worked_off_by_lesson
              (вкладка «Уроки» карточки ученика), опционален.

Возврат (Decimal, округлены до копеек):
  worked_off_total, worked_off_month, remaining_value, over_consumed_lessons,
  worked_off_by_month: { 'YYYY-MM': Decimal }, worked_off_by_date: { 'YYYY-MM-DD': Decimal }
  (тот же факт по дню урока, точные Decimal без округления, пожизненный), worked_off_by_direction: { direction_id: Decimal },
  worked_off_unit_prices_month: [Decimal, ...] — цены за 1 урок партий, реально
  затронутых списаниями внутри [month_start, month_end); порядок = порядок FIFO
  (партии гасятся монотонно, поэтому цена не повторяется дважды не подряд);
  возвраты и списания сверх остатка (over_consumed) цену не добавляют.
  worked_off_units_month: [Decimal, ...] — сколько уроков (units, half-lesson=0.5)
  отработано по каждой цене выше; выровнен по индексу с worked_off_unit_prices_month
  (sum(units[i] × prices[i]) == worked_off_month до округления).
  remaining_by_direction: { direction_id|None: {'lessons': Decimal, 'value': Decimal} }
  — из партий каких направлений состоит непогашенный хвост. Нужен возврату средств
  (apps/payments/repository.py::refund_student): строка возврата пишется в то
  направление, чьи деньги реально возвращаются, иначе лимит курса не освобождается.
  remaining_lots: [{'lessons': Decimal, 'price_per_lesson': Decimal, 'direction_id': ...}]
  — тот же хвост, но по партиям и в порядке очереди. Нужен прогнозу выручки
  (apps/finances/revenue_forecast.py): месяц режется по 4 урока и может попасть
  на партии с РАЗНОЙ ценой, поэтому свёрнутой суммы по направлению не хватает.
  over_consumed_value: Decimal — долг деньгами: уроки, проведённые сверх
  оплаченных, оценённые по цене урока ПОСЛЕДНЕЙ партии ученика (его текущая
  цена; своей цены у таких уроков нет — партии кончились). Без единой партии
  цены нет вовсе, поэтому 0.
  worked_off_by_month_payment: { (ym, payment_id): Decimal } — признанная выручка
  в разрезе ПЛАТЕЖА (не направления): чьи именно деньги стали выручкой в этом
  месяце. remaining_by_payment / refunded_by_payment: { payment_id: Decimal } —
  тот же разрез для неотработанного остатка и для денег, погашенных возвратом.
  refunded_by_month_payment: { (ym, payment_id): Decimal } — возврат по месяцу
  ДАТЫ ВОЗВРАТА: бухгалтерская сверка аванса за месяц («аванс на начало −
  отработано + оплачено − возвраты = аванс на конец») требует возвратов месяца.
  Все три — ТОЧНЫЕ Decimal без округления (потребитель — реестр признания
  выручки, apps/finances/reports.py — округляет один раз). Партии без
  payment_id в эти разрезы не попадают.
  worked_off_by_lesson: { lesson_id: Decimal } — ДЕНЬГИ, признанные конкретным
  уроком: сколько списала эта запись потребления. Урок на стыке абонементов
  гасится двумя партиями — здесь лежит сумма обеих долей.
  over_consumed_by_lesson: { lesson_id: Decimal } — сколько УРОКОВ этой записи
  прошло сверх оплаченного остатка (half-lesson = 0.5). ЭТО НЕ ДЕНЬГИ: гнать
  значение через round_kopecks и печатать как рубли нельзя — получится
  «0,50 ₽» вместо «полурока долга». Частично оплаченный урок попадает в ОБА
  разреза сразу: часть признана деньгами, часть ушла в долг.
  Оба разреза — ПОЖИЗНЕННЫЕ: полуинтервал [month_start, month_end) их не
  ограничивает (в отличие от worked_off_month), в них вся история ученика.
  Значения — ТОЧНЫЕ Decimal без округления, как у разрезов по платежу:
  округляет потребитель, один раз на строку. Потребления без lesson_id
  (синтетические возвраты, легаси-вызовы dashboard/reports/student_month)
  ни в один из разрезов не попадают.
  worked_off_by_month_lot_direction / worked_off_by_month_lesson_direction:
  { (ym, direction_id): {'value': Decimal, 'lessons': Decimal} } — один и тот же
  факт отработки в двух разрезах: по направлению ОПЛАТЫ (чьи деньги списаны) и по
  направлению УРОКА (где занимались). Пул оплат общий на ученика, поэтому разрезы
  расходятся, когда урок направления A гасит партию направления B.
"""
from __future__ import annotations

from decimal import Decimal

from apps.core.utils.decimal import round_kopecks, to_decimal

_ZERO = Decimal('0')


def compute_fifo(lots, consumptions, month_start: str, month_end: str) -> dict:
    """
    Порт computeFifo (services/fifo.js) на Decimal.

    Семантика идентична Node: индекс текущей партии lot_idx, остаток lot_remaining;
    каждое посещение гасится из партий по FIFO, недостача → over_consumed_lessons.
    Каждая запись consumption может нести 'direction_id' (направление урока) —
    используется только для атрибуции worked_off_by_direction в отчётах, партию
    FIFO-очереди это не меняет (лоты и посещения уже приходят единым пулом на
    ученика — см. apps/finances/repository.py::fifo_inputs).
    Запись consumption может нести 'refund': True — такая запись гасит партии
    (уменьшает remaining_value), но не идёт в worked_off_total/worked_off_month/
    by_month/by_direction и не учитывается в over_consumed_lessons.
    """
    lot_idx = 0
    lot_remaining = to_decimal(lots[0]['lessons']) if lots else _ZERO
    worked_off_total = _ZERO
    worked_off_month = _ZERO
    over_consumed_lessons = _ZERO
    by_month: dict[str, Decimal] = {}
    by_direction: dict = {}
    # Отработано по (месяц, направление ПАРТИИ-ОПЛАТЫ). НЕ путать с by_direction
    # выше: тот считает по направлению УРОКА (кто отработал), а этот — по
    # направлению оплаты (чьи деньги), как remaining_lots. Пул общий на ученика,
    # поэтому эти два разреза расходятся, когда урок направления A гасит партию
    # направления B. Нужен прогнозу выручки (revenue_forecast) там, где счёт идёт
    # именно ДЕНЬГАМ направления: сколько денег этой партии уже потрачено в
    # стартовом месяце — на столько меньше плановый добор этого месяца.
    by_month_lot_direction: dict = {}
    # Отработано по (месяц, направление УРОКА) — «где реально занимались». Тот же
    # ключ-месяц, что у разреза выше, но направление берётся из consumption, а не
    # из партии. Нужен прогнозу выручки: бухгалтерии важно видеть признанную
    # выручку на том курсе, который её заработал, даже если деньги пришли с
    # партии другого направления (пул оплат общий на ученика).
    by_month_lesson_direction: dict = {}
    # Разрезы ПО ПЛАТЕЖУ (реестр признания выручки, спека 2026-09-08). Партия
    # знает свой payment_id (build_lots); оплата с доплатами даёт несколько
    # партий с одним id — здесь они складываются обратно в один платёж.
    # ВАЖНО: значения НЕ округляются до копеек (в отличие от worked_off_*) —
    # потребитель округляет один раз и распределяет невязку, та же дисциплина,
    # что у remaining_lots.price_per_lesson.
    by_month_payment: dict = {}
    lessons_by_month_payment: dict = {}      # тот же разрез, но в УРОКАХ
    remaining_by_payment: dict = {}
    remaining_lessons_by_payment: dict = {}  # хвост оплаты в уроках
    refunded_by_payment: dict = {}
    refunded_by_month_payment: dict = {}
    # Перерасход ВНУТРИ месяца — отдельно от накопленного за всю историю:
    # строка отчёта рассказывает про месяц, а не про весь долг ученика.
    over_consumed_month = _ZERO
    unit_prices_month: list[Decimal] = []
    unit_qtys_month: list[Decimal] = []  # уроков (units, half-lesson=0.5) на каждую цену
    # Разрез ПО УРОКУ (вкладка «Уроки» карточки ученика, спека 2026-09-14):
    # сколько денег списала конкретная запись потребления и сколько её уроков
    # прошло сверх оплаченного остатка. Ключ — lesson_id записи; потребления без
    # него (синтетические возвраты) в разрезы не попадают. Значения — ТОЧНЫЕ
    # Decimal без округления, как у разрезов по платежу: округляет потребитель,
    # один раз на строку.
    by_lesson: dict[int, Decimal] = {}
    over_consumed_by_lesson: dict[int, Decimal] = {}
    # Тот же факт признания выручки, что by_month, но с точностью до ДНЯ урока
    # (график «Recognized revenue daily», спека 2026-09-18). Пожизненный, как
    # by_lesson: полуинтервал месяца его не ограничивает — период режет потребитель.
    by_date: dict[str, Decimal] = {}

    for c in consumptions:
        need = to_decimal(c['units'])
        # Полуинтервал [month_start, month_end); сравнение строк 'YYYY-MM-DD' = хронологическое.
        in_month = month_start <= c['date'] < month_end
        direction_id = c.get('direction_id')
        is_refund = bool(c.get('refund'))
        lesson_id = c.get('lesson_id')
        while need > 0 and lot_idx < len(lots):
            if lot_remaining <= 0:
                lot_idx += 1
                if lot_idx >= len(lots):
                    break
                lot_remaining = to_decimal(lots[lot_idx]['lessons'])
                continue
            take = need if need < lot_remaining else lot_remaining  # min(need, lot_remaining)
            value = take * to_decimal(lots[lot_idx]['price_per_lesson'])
            payment_id = lots[lot_idx].get('payment_id')
            if not is_refund:
                worked_off_total += value
                if lesson_id is not None:
                    by_lesson[lesson_id] = by_lesson.get(lesson_id, _ZERO) + value
                ym = c['date'][:7]
                by_month[ym] = by_month.get(ym, _ZERO) + value
                by_date[c['date']] = by_date.get(c['date'], _ZERO) + value
                if direction_id is not None:
                    by_direction[direction_id] = by_direction.get(direction_id, _ZERO) + value
                lot_bucket = by_month_lot_direction.setdefault(
                    (ym, lots[lot_idx].get('direction_id')),
                    {'value': _ZERO, 'lessons': _ZERO},
                )
                lot_bucket['value'] += value
                lot_bucket['lessons'] += take
                lesson_bucket = by_month_lesson_direction.setdefault(
                    (ym, direction_id), {'value': _ZERO, 'lessons': _ZERO},
                )
                lesson_bucket['value'] += value
                lesson_bucket['lessons'] += take
                if payment_id is not None:
                    mp_key = (ym, payment_id)
                    by_month_payment[mp_key] = by_month_payment.get(mp_key, _ZERO) + value
                    lessons_by_month_payment[mp_key] = (
                        lessons_by_month_payment.get(mp_key, _ZERO) + take
                    )
                if in_month:
                    worked_off_month += value
                    price = to_decimal(lots[lot_idx]['price_per_lesson'])
                    if not unit_prices_month or unit_prices_month[-1] != price:
                        unit_prices_month.append(price)
                        unit_qtys_month.append(take)
                    else:
                        unit_qtys_month[-1] += take
            elif payment_id is not None:
                # Возврат: деньги партии погашены, но выручкой не стали.
                refunded_by_payment[payment_id] = (
                    refunded_by_payment.get(payment_id, _ZERO) + value
                )
                rm_key = (c['date'][:7], payment_id)
                refunded_by_month_payment[rm_key] = (
                    refunded_by_month_payment.get(rm_key, _ZERO) + value
                )
            lot_remaining -= take
            need -= take
        if need > 0 and not is_refund:
            over_consumed_lessons += need
            if lesson_id is not None:
                over_consumed_by_lesson[lesson_id] = (
                    over_consumed_by_lesson.get(lesson_id, _ZERO) + need
                )
            if in_month:
                over_consumed_month += need

    remaining_value = _ZERO
    # Из чего состоит непогашенный хвост: { direction_id | None: {lessons, value} }.
    # Порядок ключей = порядок партий в очереди (dict сохраняет вставку).
    remaining_by_direction: dict = {}
    # Тот же хвост, но НЕ свёрнутый: партии по одной, в порядке FIFO-очереди.
    # Нужен прогнозу выручки (apps/finances/revenue_forecast.py), который режет
    # остаток на календарные месяцы по 4 урока: там важна не сумма по направлению,
    # а цена каждой партии — месяцы могут попадать на партии с разной ценой.
    remaining_lots: list[dict] = []

    def _keep(lot, lessons: Decimal) -> None:
        if lessons <= 0:
            return
        price = to_decimal(lot['price_per_lesson'])
        bucket = remaining_by_direction.setdefault(
            lot.get('direction_id'), {'lessons': _ZERO, 'value': _ZERO},
        )
        bucket['lessons'] += lessons
        bucket['value'] += lessons * price
        remaining_lots.append({
            'lessons': lessons,
            'price_per_lesson': price,
            'direction_id': lot.get('direction_id'),
        })
        payment_id = lot.get('payment_id')
        if payment_id is not None:
            remaining_by_payment[payment_id] = (
                remaining_by_payment.get(payment_id, _ZERO) + lessons * price
            )
            remaining_lessons_by_payment[payment_id] = (
                remaining_lessons_by_payment.get(payment_id, _ZERO) + lessons
            )

    if lot_idx < len(lots):
        remaining_value += lot_remaining * to_decimal(lots[lot_idx]['price_per_lesson'])
        _keep(lots[lot_idx], lot_remaining)
        for i in range(lot_idx + 1, len(lots)):
            remaining_value += to_decimal(lots[i]['lessons']) * to_decimal(lots[i]['price_per_lesson'])
            _keep(lots[i], to_decimal(lots[i]['lessons']))

    return {
        'worked_off_total': round_kopecks(worked_off_total),
        'worked_off_month': round_kopecks(worked_off_month),
        'remaining_value': round_kopecks(remaining_value),
        'over_consumed_lessons': round_kopecks(over_consumed_lessons),
        # Долг деньгами по цене последней партии — см. docstring модуля.
        'over_consumed_value': round_kopecks(
            over_consumed_lessons * to_decimal(lots[-1]['price_per_lesson']) if lots else _ZERO
        ),
        'worked_off_by_month': {k: round_kopecks(v) for k, v in by_month.items()},
        # { 'YYYY-MM-DD': Decimal } — признанная выручка по дню урока. Разрез
        # by_month с точностью до дня; значения ТОЧНЫЕ, без округления (суммирует
        # и округляет потребитель — дашборд «Recognized revenue»).
        'worked_off_by_date': dict(by_date),
        'worked_off_by_direction': {k: round_kopecks(v) for k, v in by_direction.items()},
        'worked_off_unit_prices_month': [round_kopecks(p) for p in unit_prices_month],
        # Кол-во уроков (units, half-lesson=0.5), отработанных по каждой цене выше —
        # выровнено по индексу с worked_off_unit_prices_month. Возвраты и перерасход
        # (over_consumed) сюда не идут, как и в цены. Не деньги → без округления до копеек.
        'worked_off_units_month': list(unit_qtys_month),
        # Состав непогашенного хвоста по направлениям партий (lots[i]['direction_id'],
        # т.е. направление ОПЛАТЫ, не урока). Сумма value == remaining_value до
        # округления каждой доли; lessons — без округления (half-lesson=0.5).
        'remaining_by_direction': {
            k: {'lessons': v['lessons'], 'value': round_kopecks(v['value'])}
            for k, v in remaining_by_direction.items()
        },
        # Несвёрнутый хвост в порядке FIFO: [{lessons, price_per_lesson, direction_id}].
        # price_per_lesson НЕ округляется — потребитель домножает её на уроки и
        # округляет один раз на выходе (та же дисциплина, что во всём модуле).
        'remaining_lots': remaining_lots,
        # { (ym, direction_id партии): {'value': Decimal, 'lessons': Decimal} } —
        # факт отработки в разрезе направления ОПЛАТЫ (см. комментарий у
        # by_month_lot_direction выше). value округлён до копеек, lessons — нет
        # (half-lesson=0.5).
        'worked_off_by_month_lot_direction': {
            k: {'value': round_kopecks(v['value']), 'lessons': v['lessons']}
            for k, v in by_month_lot_direction.items()
        },
        # { (ym, direction_id УРОКА): {'value': Decimal, 'lessons': Decimal} } —
        # факт отработки в разрезе направления УРОКА (см. комментарий у
        # by_month_lesson_direction выше). Формат тот же, что у разреза по оплате:
        # value округлён до копеек, lessons — нет (half-lesson=0.5). Направление
        # урока может быть None — тогда и ключ None.
        'worked_off_by_month_lesson_direction': {
            k: {'value': round_kopecks(v['value']), 'lessons': v['lessons']}
            for k, v in by_month_lesson_direction.items()
        },
        # Разрезы по ПЛАТЕЖУ для реестра признания выручки: чьи именно деньги
        # стали выручкой в этом месяце, сколько от платежа осталось авансом и
        # сколько погашено возвратом. ТОЧНЫЕ Decimal без округления —
        # округление один раз делает отчёт, отдавая невязку последнему месяцу.
        # Партии без payment_id (легаси-вызовы) сюда не попадают.
        'worked_off_by_month_payment': dict(by_month_payment),
        # Те же разрезы в УРОКАХ (half-lesson=0.5) — деньги на уроки не делятся,
        # когда у оплаты несколько цен (доплата к абонементу дробит партию).
        'worked_off_lessons_by_month_payment': dict(lessons_by_month_payment),
        'remaining_by_payment': dict(remaining_by_payment),
        'remaining_lessons_by_payment': dict(remaining_lessons_by_payment),
        'refunded_by_payment': dict(refunded_by_payment),
        'refunded_by_month_payment': dict(refunded_by_month_payment),
        # { lesson_id: Decimal } — признанные ДЕНЬГИ в разрезе конкретного урока
        # и { lesson_id: Decimal } — сколько УРОКОВ этой записи прошло сверх
        # оплаченного (half-lesson = 0.5; второй разрез — не деньги, через
        # round_kopecks его печатать нельзя). Оба пожизненные, вне [month_start,
        # month_end). Точные Decimal без округления — округляет потребитель
        # (планируется apps/finances/repository.py::student_lesson_recognition).
        'worked_off_by_lesson': dict(by_lesson),
        'over_consumed_by_lesson': dict(over_consumed_by_lesson),
        # Перерасход внутри [month_start, month_end): уроки и их стоимость по
        # цене последней партии (см. over_consumed_value выше).
        'over_consumed_lessons_month': round_kopecks(over_consumed_month),
        'over_consumed_value_month': round_kopecks(
            over_consumed_month * to_decimal(lots[-1]['price_per_lesson']) if lots else _ZERO
        ),
    }
