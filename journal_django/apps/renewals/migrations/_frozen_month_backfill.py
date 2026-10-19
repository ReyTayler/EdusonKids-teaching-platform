"""SQL бэкфила месяца заморозки — отдельным модулем, чтобы тест мог прогнать
ровно то же выражение, что и миграция 0013 (тот же приём, что
apps/students/migrations/_frozen_backfill_util.py).

Читает students.frozen_until, которую удаляет students/0016 — поэтому 0016
объявляет зависимость от renewals/0013.
"""

BACKFILL_SQL = """
    UPDATE renewal_deal d
       SET frozen_until_month = date_trunc('month', s.frozen_until)::date
      FROM students s, renewal_stage st
     WHERE s.id = d.student_id
       AND st.id = d.stage_id
       AND st.key = 'frozen'
       AND d.outcome_at IS NULL
       AND d.frozen_until_month IS NULL
       AND s.frozen_until IS NOT NULL
"""
