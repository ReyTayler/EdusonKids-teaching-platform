# journal_django/apps/sync/backfills/rebuild_payroll.py
"""Пересчёт зарплаты из lessons+lesson_attendance (Sheets не трогает). Порт scripts/rebuild-payroll.js.

Переиспользует уже существующий Python-порт calculate_payment
(apps.payroll.calculator) — второй раз эту логику не пишем.
"""
from __future__ import annotations

from django.db import connection

from apps.payroll.calculator import calculate_payment


def run(dry_run: bool = False) -> dict:
    result = {
        'entity': 'payroll-rebuild', 'lessons_seen': 0, 'inserted': 0, 'updated': 0,
        'unchanged': 0, 'skipped_no_attendance': 0, 'dry_run': dry_run,
    }

    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT l.id, l.teacher_id, l.lesson_duration_minutes,
                   to_char(l.lesson_date, 'YYYY-MM-DD') AS lesson_date_str,
                   to_char((l.submitted_at AT TIME ZONE 'Europe/Moscow'), 'YYYY-MM-DD') AS submit_msk_date,
                   -- Headcount как боевой путь record_lesson (правило 2026-09-28):
                   -- unpaid_skip вне total И вне present; is_free оплачивается как
                   -- обычное присутствие. la.present IS NOT NULL = реальная строка
                   -- (LEFT JOIN даёт NULL без посещаемости).
                   COALESCE(SUM(CASE WHEN la.present IS NOT NULL AND NOT la.unpaid_skip
                                     THEN 1 ELSE 0 END), 0)::int AS total_students,
                   COALESCE(SUM(CASE WHEN la.present AND NOT la.unpaid_skip
                                     THEN 1 ELSE 0 END), 0)::int AS present_count
            FROM lessons l
            LEFT JOIN lesson_attendance la ON la.lesson_id = l.id
            GROUP BY l.id
            ORDER BY l.lesson_date, l.id
            """
        )
        rows = cur.fetchall()
        result['lessons_seen'] = len(rows)

        for lesson_id, teacher_id, duration, lesson_date_str, submit_date, total, present in rows:
            if total == 0:
                result['skipped_no_attendance'] += 1
                continue

            is_half = duration == 45
            payment = calculate_payment(total, present, is_half)
            penalty = 0 if submit_date == lesson_date_str else 40

            if dry_run:
                continue

            cur.execute(
                """
                INSERT INTO payroll (lesson_id, teacher_id, total_students, present_count, payment, penalty)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (lesson_id) DO UPDATE SET
                    teacher_id     = EXCLUDED.teacher_id,
                    total_students = EXCLUDED.total_students,
                    present_count  = EXCLUDED.present_count,
                    payment        = EXCLUDED.payment,
                    penalty        = EXCLUDED.penalty
                WHERE payroll.teacher_id     IS DISTINCT FROM EXCLUDED.teacher_id
                   OR payroll.total_students IS DISTINCT FROM EXCLUDED.total_students
                   OR payroll.present_count  IS DISTINCT FROM EXCLUDED.present_count
                   OR payroll.payment        IS DISTINCT FROM EXCLUDED.payment
                   OR payroll.penalty        IS DISTINCT FROM EXCLUDED.penalty
                RETURNING (xmax = 0) AS inserted
                """,
                [lesson_id, teacher_id, total, present, payment, penalty],
            )
            row = cur.fetchone()
            if row is None:
                result['unchanged'] += 1
            elif row[0]:
                result['inserted'] += 1
            else:
                result['updated'] += 1

    return result
