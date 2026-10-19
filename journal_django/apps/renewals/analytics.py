"""Аналитика продлений: воронка по стадиям + сводные KPI."""
from __future__ import annotations

from django.db import connection


def funnel(group_by: str | None = None) -> dict:
    """
    Распределение открытых сделок по стадиям + renewal rate за 30 дней
    (won / (won + lost) среди закрытых).

    group_by='month' добавляет когорты по месяцам: месяц = когда цикл отработан
    (due_at); оплатившие заранее (закрыты до 4-го урока, due_at пуст) попадают
    в месяц закрытия. Каждый цикл — ровно в одном месяце.

    Месяц считается по МСК, не по сессионному часовому поясу PostgreSQL
    (session timezone — UTC, Django его не переключает даже при
    TIME_ZONE='Europe/Moscow'): due_at — DATE без времени, безопасен сам по
    себе (при условии что записан по МСК — см. engine.msk_now()); outcome_at —
    timestamptz, поэтому явно конвертируется AT TIME ZONE 'Europe/Moscow'
    перед date_trunc, иначе события в окне 00:00–02:59 по Москве уезжают
    в предыдущий месяц.
    """
    with connection.cursor() as cur:
        cur.execute("""
            SELECT st.key, st.label, st.kind, COUNT(*) AS cnt
            FROM renewal_deal d JOIN renewal_stage st ON st.id = d.stage_id
            WHERE d.outcome_at IS NULL
            GROUP BY st.key, st.label, st.kind, st.sort_order
            ORDER BY st.sort_order
        """)
        cols = [c[0] for c in cur.description]
        stages = [dict(zip(cols, r)) for r in cur.fetchall()]

        cur.execute("""
            SELECT st.kind, COUNT(*) FROM renewal_deal d
            JOIN renewal_stage st ON st.id = d.stage_id
            WHERE d.outcome_at IS NOT NULL AND d.outcome_at >= now() - interval '30 days'
            GROUP BY st.kind
        """)
        closed = {r[0]: r[1] for r in cur.fetchall()}

        months: list[dict] = []
        if group_by == 'month':
            cur.execute("""
                SELECT to_char(COALESCE(date_trunc('month', d.due_at::timestamp),
                                        date_trunc('month', d.outcome_at AT TIME ZONE 'Europe/Moscow')),
                               'YYYY-MM') AS month,
                       COUNT(*) AS matured,
                       COUNT(*) FILTER (WHERE st.kind = 'won') AS won,
                       COUNT(*) FILTER (WHERE st.kind = 'lost') AS lost,
                       COUNT(*) FILTER (WHERE d.outcome_at IS NULL) AS in_progress
                FROM renewal_deal d JOIN renewal_stage st ON st.id = d.stage_id
                WHERE d.due_at IS NOT NULL OR d.outcome_at IS NOT NULL
                GROUP BY 1 ORDER BY 1 DESC LIMIT 24
            """)
            cols = [c[0] for c in cur.description]
            months = [dict(zip(cols, r)) for r in cur.fetchall()]
            for m in months:
                done = m['won'] + m['lost']
                m['conversion'] = round(m['won'] / done * 100, 1) if done else None

    won, lost = closed.get('won', 0), closed.get('lost', 0)
    rate = round(won / (won + lost) * 100, 1) if (won + lost) else None
    result = {'stages': stages, 'renewal_rate_30d': rate, 'won_30d': won, 'lost_30d': lost}
    if group_by == 'month':
        result['months'] = months
    return result
