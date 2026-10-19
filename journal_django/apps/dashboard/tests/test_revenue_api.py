"""
GET /api/admin/dashboard/revenue — Revenue / Orders + разбивка по дням и месяцам
(спека docs/superpowers/specs/2026-09-18-revenue-dashboard-design.md).

Тест-БД общая и засеяна боевыми оплатами, поэтому свои оплаты кладём в 2099 год —
там гарантированно пусто, суммы сверяются точно.
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.dashboard import services as svc

pytestmark = pytest.mark.django_db

URL = '/api/admin/dashboard/revenue'


@pytest.fixture(scope='session')
def django_db_setup():
    pass


@pytest.fixture
def student_id():
    with connection.cursor() as cur:
        cur.execute("INSERT INTO students (full_name) VALUES ('__revenue_student__') RETURNING id")
        return cur.fetchone()[0]


def _pay(student_id, paid_at, amount, kind='purchase'):
    lessons = -4 if kind == 'refund' else 4
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO payments (student_id, kind, subscriptions_count, lessons_count, "
            "unit_price, total_amount, paid_at, created_by) "
            "VALUES (%s,%s,1,%s,%s,%s,%s,'test')",
            [student_id, kind, lessons, abs(amount), amount, paid_at],
        )


@pytest.fixture
def direction_id():
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO directions (name, total_lessons, active) "
            "VALUES ('__revenue_dir__', 16, true) RETURNING id"
        )
        return cur.fetchone()[0]


def _pay_full(student_id, direction_id, paid_at, amount, *,
              subscriptions=None, lessons=None, kind='purchase', parent=None):
    """
    Оплата с явным сроком: subscriptions — абонементы, lessons — уроки.
    Для kind='surcharge' БД требует ссылку на абонемент-родителя
    (CHECK payments_surcharge_shape) — передаём его id в parent.
    """
    index = 1 if parent is not None else None
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO payments (student_id, direction_id, kind, subscriptions_count, "
            "lessons_count, unit_price, total_amount, paid_at, created_by, "
            "parent_payment_id, subscription_index) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'test',%s,%s) RETURNING id",
            [student_id, direction_id, kind, subscriptions, lessons,
             abs(amount), amount, paid_at, parent, index],
        )
        return cur.fetchone()[0]


def _products(client, date_from, date_to):
    body = client.get(URL, {'from': date_from, 'to': date_to}).json()
    return body, {r['direction']: r for r in body['products']}


# ---------------------------------------------------------------------------
# Доступ и валидация
# ---------------------------------------------------------------------------

def test_requires_auth(anon_client):
    assert anon_client.get(URL).status_code == 401


def test_teacher_forbidden(teacher_client):
    assert teacher_client.get(URL).status_code == 403


@pytest.mark.parametrize('params', [{'from': '2026-13-01'}, {'to': '2026-02-30'}])
def test_invalid_date(manager_client, params):
    resp = manager_client.get(URL, params)
    assert resp.status_code == 400
    assert resp.json() == {'error': 'invalid_date'}


def test_invalid_range(manager_client):
    resp = manager_client.get(URL, {'from': '2099-02-01', 'to': '2099-01-01'})
    assert resp.status_code == 400
    assert resp.json() == {'error': 'invalid_range'}


# ---------------------------------------------------------------------------
# Расчёт
# ---------------------------------------------------------------------------

def test_totals_daily_and_monthly(admin_client, student_id):
    _pay(student_id, '2099-01-30', 10000)
    _pay(student_id, '2099-01-30', 5000.50)
    _pay(student_id, '2099-02-01', 8000)
    _pay(student_id, '2099-02-02', -3000, kind='refund')
    _pay(student_id, '2099-02-05', 99999)            # за границей периода

    resp = admin_client.get(URL, {'from': '2099-01-30', 'to': '2099-02-02'})
    assert resp.status_code == 200
    body = resp.json()

    assert body['from'] == '2099-01-30' and body['to'] == '2099-02-02'
    # Дашборд показывает поступления: возврат на 3000 ₽ сумму НЕ уменьшает
    # (решение пользователя 2026-10-05) и заказом не считается.
    assert body['revenue'] == 23000.5
    assert body['orders'] == 3
    assert body['aov'] == 7666.83                           # 23000.50 / 3, до копеек
    assert body['daily'] == [
        {'date': '2099-01-30', 'revenue': 15000.5, 'orders': 2, 'aov': 7500.25},
        # пустой день: сумма и заказы — ноль, средний чек — нет значения
        {'date': '2099-01-31', 'revenue': 0, 'orders': 0, 'aov': None},
        {'date': '2099-02-01', 'revenue': 8000, 'orders': 1, 'aov': 8000},
        # в этот день был только возврат — для дашборда день пустой, не минусовой
        {'date': '2099-02-02', 'revenue': 0, 'orders': 0, 'aov': None},
    ]
    assert body['monthly'] == [
        {'month': '2099-01', 'revenue': 15000.5, 'orders': 2, 'aov': 7500.25},
        {'month': '2099-02', 'revenue': 8000, 'orders': 1, 'aov': 8000},
    ]


def test_refund_only_period_is_empty(manager_client, student_id):
    """Период, где был только возврат: ноль, а не минус (возвраты в Revenue не идут)."""
    _pay(student_id, '2099-04-10', -5000, kind='refund')
    body = manager_client.get(URL, {'from': '2099-04-01', 'to': '2099-04-30'}).json()
    assert body['revenue'] == 0
    assert body['orders'] == 0
    assert body['aov'] is None
    assert all(d['revenue'] == 0 for d in body['daily'])


def test_aov_null_without_orders(manager_client):
    body = manager_client.get(URL, {'from': '2099-06-01', 'to': '2099-06-02'}).json()
    assert body['orders'] == 0 and body['aov'] is None


def test_bounds_are_inclusive(manager_client, student_id):
    _pay(student_id, '2099-03-01', 100)
    _pay(student_id, '2099-03-10', 200)
    body = manager_client.get(URL, {'from': '2099-03-01', 'to': '2099-03-10'}).json()
    assert body['revenue'] == 300 and body['orders'] == 2
    assert len(body['daily']) == 10


def test_default_period_is_last_three_months(manager_client):
    body = manager_client.get(URL).json()
    assert (body['from'], body['to']) == svc.default_period()
    assert body['daily'][0]['date'] == body['from']
    assert body['daily'][-1]['date'] == body['to']


# ---------------------------------------------------------------------------
# Сводка по курсам (products)
# ---------------------------------------------------------------------------

def test_products_group_by_direction(admin_client, student_id, direction_id):
    """Строка на направление: покупки, оплаченные месяцы, поступления, ASP."""
    _pay_full(student_id, direction_id, '2099-07-05', 10000, subscriptions=2, lessons=8)
    _pay_full(student_id, direction_id, '2099-07-20', 6000, subscriptions=1, lessons=4)

    body, by_name = _products(admin_client, '2099-07-01', '2099-07-31')
    row = by_name['__revenue_dir__']
    assert row['purchases'] == 2
    assert row['months'] == 3
    assert row['revenue'] == 16000
    assert row['asp'] == 8000                      # 16000 / 2 покупки


def test_products_months_from_lessons(manager_client, student_id, direction_id):
    """Поштучная покупка без абонементов: месяцы = уроки / 4 (1 урок = 0.25)."""
    _pay_full(student_id, direction_id, '2099-08-10', 1600, lessons=1)
    _pay_full(student_id, direction_id, '2099-08-11', 3200, lessons=2)

    _, by_name = _products(manager_client, '2099-08-01', '2099-08-31')
    row = by_name['__revenue_dir__']
    assert row['months'] == 0.75                   # 0.25 + 0.5
    assert row['arpm'] == 6400                     # среднее (1600/0.25, 3200/0.5)


def test_products_arpm_is_average_of_orders(manager_client, student_id, direction_id):
    """ARPM — среднее по заказам «цена ÷ срок», а НЕ revenue / months."""
    _pay_full(student_id, direction_id, '2099-09-01', 10000, subscriptions=2, lessons=8)
    _pay_full(student_id, direction_id, '2099-09-02', 3000, subscriptions=1, lessons=4)

    _, by_name = _products(manager_client, '2099-09-01', '2099-09-30')
    row = by_name['__revenue_dir__']
    assert row['arpm'] == 4000                     # (5000 + 3000) / 2 заказа
    assert row['revenue'] / row['months'] == 13000 / 3   # а так было бы 4333.33


def test_products_surcharge_is_money_without_order(manager_client, student_id, direction_id):
    """Доплата к абонементу: деньги в Revenue, но не покупка и не месяц."""
    parent = _pay_full(student_id, direction_id, '2099-10-01', 8000,
                       subscriptions=1, lessons=4)
    _pay_full(student_id, direction_id, '2099-10-02', 1500,
              kind='surcharge', parent=parent)

    _, by_name = _products(manager_client, '2099-10-01', '2099-10-31')
    row = by_name['__revenue_dir__']
    assert row['revenue'] == 9500
    assert row['purchases'] == 1
    assert row['months'] == 1
    assert row['arpm'] == 8000                     # доплата срока не имеет


def test_products_legacy_without_direction_is_last(manager_client, student_id, direction_id):
    """Легаси-оплата без направления — отдельной строкой, в самом низу."""
    _pay_full(student_id, direction_id, '2099-11-01', 500, subscriptions=1, lessons=4)
    _pay_full(student_id, None, '2099-11-02', 99000, subscriptions=1, lessons=4)

    body, _ = _products(manager_client, '2099-11-01', '2099-11-30')
    rows = body['products']
    assert rows[-1]['direction'] is None           # ниже всех, хотя сумма больше
    assert rows[-1]['revenue'] == 99000
    assert rows[0]['direction'] == '__revenue_dir__'


def test_products_sum_matches_revenue_tile(manager_client, student_id, direction_id):
    """Сумма строк таблицы = плитка Revenue за тот же период."""
    _pay_full(student_id, direction_id, '2099-12-01', 7000, subscriptions=1, lessons=4)
    _pay_full(student_id, None, '2099-12-02', 2500, subscriptions=1, lessons=4)
    _pay(student_id, '2099-12-03', -1000, kind='refund')     # возврат не считается

    body, _ = _products(manager_client, '2099-12-01', '2099-12-31')
    assert sum(r['revenue'] for r in body['products']) == body['revenue'] == 9500


# ---------------------------------------------------------------------------
# default_period — зажим дня в короткий месяц и переход года
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('today, expected', [
    ('2026-09-18', ('2026-06-18', '2026-09-18')),
    ('2026-05-31', ('2026-02-28', '2026-05-31')),
    ('2028-05-31', ('2028-02-29', '2028-05-31')),
    ('2026-02-15', ('2025-11-15', '2026-02-15')),
])
def test_default_period(today, expected):
    assert svc.default_period(today) == expected
