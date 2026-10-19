# journal_django/apps/sync/backfills/rebuild_counters.py
"""Пересчёт group_memberships.lessons_done из lesson_attendance. Порт scripts/rebuild-counters.js."""
from __future__ import annotations

from django.db import connection


def run(dry_run: bool = False) -> dict:
    result = {
        'entity': 'counters-rebuild', 'memberships_total': 0, 'updated': 0,
        'unchanged': 0, 'dry_run': dry_run, 'top_drifts': [],
    }

    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT gm.id, gm.lessons_done AS stored,
                   COALESCE(SUM(
                     CASE WHEN la.present THEN
                       CASE WHEN l.lesson_duration_minutes = 45 THEN 0.5 ELSE 1 END
                     ELSE 0 END
                   ), 0)::numeric(6,1) AS calculated,
                   s.full_name AS student_name, g.name AS group_name
              FROM group_memberships gm
              JOIN students s ON s.id = gm.student_id
              JOIN groups   g ON g.id = gm.group_id
              LEFT JOIN lessons l ON l.group_id = gm.group_id
              LEFT JOIN lesson_attendance la ON la.lesson_id = l.id AND la.student_id = gm.student_id
             GROUP BY gm.id, gm.lessons_done, s.full_name, g.name
             ORDER BY gm.id
            """
        )
        rows = cur.fetchall()
        result['memberships_total'] = len(rows)

        drifts = []
        for membership_id, stored, calculated, student_name, group_name in rows:
            stored = float(stored)
            calculated = float(calculated)
            if stored == calculated:
                result['unchanged'] += 1
                continue

            drifts.append({
                'membership_id': membership_id, 'student': student_name, 'group': group_name,
                'stored': stored, 'calculated': calculated,
                'delta': round(calculated - stored, 1),
            })

            if not dry_run:
                cur.execute(
                    'UPDATE group_memberships SET lessons_done = %s WHERE id = %s',
                    [calculated, membership_id],
                )
                result['updated'] += 1

        drifts.sort(key=lambda d: abs(d['delta']), reverse=True)
        result['top_drifts'] = drifts[:20]

    return result
