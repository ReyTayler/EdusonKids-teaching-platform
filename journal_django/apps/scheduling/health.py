"""
Проверки здоровья планов занятий — ТОЛЬКО ЧТЕНИЕ, ничего не меняют.

Ловят рассогласования между planned_lessons и lessons, которые ломают календарь
и операции планирования. Разбор случаев и обоснование набора проверок —
docs/superpowers/specs/2026-08-05-plan-health-design.md §4.

check_all() считает сводку по всем активным группам ОДНИМ запросом: на проде 134
активные группы, цикл по группам недопустим (CLAUDE.md, раздел про
производительность). check_group() — те же проверки по одной группе, но с
конкретными строками для интерфейса.

Длина курса берётся как COALESCE(groups.lessons_total, directions.total_lessons) —
ручная длина группы перекрывает длину направления, см. apps.groups.course_length.
"""
from __future__ import annotations

from django.db import connection

from apps.lessons.models import COURSE_LESSON_TYPES

# Ключи проверок в порядке убывания серьёзности. Русские подписи — на фронте.
CHECKS = (
    'fact_without_position',
    'beyond_course',
    'number_mismatch',
    'date_mismatch',
    'done_in_future',
    'collision',
    'duplicate_dates',
)

_SUMMARY_SQL = """
WITH course_len AS (
  SELECT g.id AS gid, g.name,
         COALESCE(g.lessons_total, d.total_lessons) AS total
  FROM groups g LEFT JOIN directions d ON d.id = g.direction_id
  WHERE g.active OR %(include_inactive)s
),
c_collision AS (
  SELECT group_id gid, count(*) n FROM (
    SELECT group_id, scheduled_date, scheduled_time FROM planned_lessons
    WHERE seq IS NOT NULL AND status <> 'cancelled'
    GROUP BY 1,2,3 HAVING count(*) > 1) x GROUP BY 1),
c_done_future AS (
  SELECT group_id gid, count(*) n FROM planned_lessons
  WHERE status = 'done' AND scheduled_date > CURRENT_DATE GROUP BY 1),
c_date AS (
  SELECT p.group_id gid, count(*) n FROM planned_lessons p
  JOIN lessons l ON l.id = p.fact_lesson_id
  WHERE p.scheduled_date <> l.lesson_date GROUP BY 1),
c_number AS (
  SELECT p.group_id gid, count(*) n FROM planned_lessons p
  JOIN lessons l ON l.id = p.fact_lesson_id
  WHERE p.lesson_number <> l.lesson_number GROUP BY 1),
c_orphan AS (
  SELECT l.group_id gid, count(*) n FROM lessons l
  WHERE l.lesson_type IN %(types)s
    AND NOT EXISTS (SELECT 1 FROM planned_lessons p WHERE p.fact_lesson_id = l.id)
  GROUP BY 1),
c_beyond AS (
  SELECT p.group_id gid, count(*) n FROM planned_lessons p
  JOIN course_len cl ON cl.gid = p.group_id
  WHERE p.seq IS NOT NULL AND cl.total IS NOT NULL AND p.lesson_number > cl.total
  GROUP BY 1),
c_dupdate AS (
  SELECT group_id gid, count(*) n FROM (
    SELECT group_id, lesson_date FROM lessons
    WHERE lesson_type IN %(types)s
    GROUP BY 1,2 HAVING count(*) > 1) y GROUP BY 1)
SELECT cl.gid, cl.name,
       COALESCE(c_orphan.n, 0), COALESCE(c_beyond.n, 0),
       COALESCE(c_number.n, 0), COALESCE(c_date.n, 0),
       COALESCE(c_done_future.n, 0), COALESCE(c_collision.n, 0),
       COALESCE(c_dupdate.n, 0)
FROM course_len cl
LEFT JOIN c_collision   ON c_collision.gid = cl.gid
LEFT JOIN c_done_future ON c_done_future.gid = cl.gid
LEFT JOIN c_date        ON c_date.gid = cl.gid
LEFT JOIN c_number      ON c_number.gid = cl.gid
LEFT JOIN c_orphan      ON c_orphan.gid = cl.gid
LEFT JOIN c_beyond      ON c_beyond.gid = cl.gid
LEFT JOIN c_dupdate     ON c_dupdate.gid = cl.gid
"""


def check_all(include_inactive: bool = False) -> dict:
    """
    Сводка по всем активным группам (include_inactive=True — и по архивным:
    сверке импорта нужны все группы, интерфейсу — только живые).

    {'entity': 'plan-health', 'checked': <групп проверено>,
     'groups': [{'group_id', 'name', 'counts': {<ключ>: <нарушений>}}, ...]}

    В groups попадают только группы, где хотя бы одна проверка ненулевая;
    порядок — по суммарному числу нарушений убыв., затем по имени.
    """
    with connection.cursor() as cur:
        cur.execute(_SUMMARY_SQL, {'types': tuple(COURSE_LESSON_TYPES),
                                   'include_inactive': include_inactive})
        rows = cur.fetchall()

    groups = []
    for gid, name, *values in rows:
        counts = {key: n for key, n in zip(CHECKS, values) if n}
        if counts:
            groups.append({'group_id': gid, 'name': name, 'counts': counts})

    groups.sort(key=lambda r: (-sum(r['counts'].values()), r['name']))
    return {'entity': 'plan-health', 'checked': len(rows), 'groups': groups}


_GROUP_SQL = {
    'collision': """
        SELECT p.id, p.seq, p.lesson_number, p.scheduled_date, NULL::date
        FROM planned_lessons p
        JOIN (SELECT scheduled_date, scheduled_time FROM planned_lessons
              WHERE group_id = %(gid)s AND seq IS NOT NULL AND status <> 'cancelled'
              GROUP BY 1,2 HAVING count(*) > 1) dup
          ON dup.scheduled_date = p.scheduled_date AND dup.scheduled_time = p.scheduled_time
        WHERE p.group_id = %(gid)s AND p.seq IS NOT NULL AND p.status <> 'cancelled'
        ORDER BY p.scheduled_date, p.seq""",
    'done_in_future': """
        SELECT p.id, p.seq, p.lesson_number, p.scheduled_date, l.lesson_date
        FROM planned_lessons p LEFT JOIN lessons l ON l.id = p.fact_lesson_id
        WHERE p.group_id = %(gid)s AND p.status = 'done'
          AND p.scheduled_date > CURRENT_DATE ORDER BY p.seq""",
    'date_mismatch': """
        SELECT p.id, p.seq, p.lesson_number, p.scheduled_date, l.lesson_date
        FROM planned_lessons p JOIN lessons l ON l.id = p.fact_lesson_id
        WHERE p.group_id = %(gid)s AND p.scheduled_date <> l.lesson_date
        ORDER BY p.seq""",
    'number_mismatch': """
        SELECT p.id, p.seq, p.lesson_number, p.scheduled_date, l.lesson_date
        FROM planned_lessons p JOIN lessons l ON l.id = p.fact_lesson_id
        WHERE p.group_id = %(gid)s AND p.lesson_number <> l.lesson_number
        ORDER BY p.seq""",
    'fact_without_position': """
        SELECT l.id, NULL::int, l.lesson_number, NULL::date, l.lesson_date
        FROM lessons l
        WHERE l.group_id = %(gid)s AND l.lesson_type IN %(types)s
          AND NOT EXISTS (SELECT 1 FROM planned_lessons p WHERE p.fact_lesson_id = l.id)
        ORDER BY l.lesson_number""",
    'beyond_course': """
        SELECT p.id, p.seq, p.lesson_number, p.scheduled_date, NULL::date
        FROM planned_lessons p
        JOIN groups g ON g.id = p.group_id
        LEFT JOIN directions d ON d.id = g.direction_id
        WHERE p.group_id = %(gid)s AND p.seq IS NOT NULL
          AND COALESCE(g.lessons_total, d.total_lessons) IS NOT NULL
          AND p.lesson_number > COALESCE(g.lessons_total, d.total_lessons)
        ORDER BY p.lesson_number""",
    'duplicate_dates': """
        SELECT l.id, NULL::int, l.lesson_number, NULL::date, l.lesson_date
        FROM lessons l
        JOIN (SELECT lesson_date FROM lessons
              WHERE group_id = %(gid)s AND lesson_type IN %(types)s
              GROUP BY 1 HAVING count(*) > 1) dup ON dup.lesson_date = l.lesson_date
        WHERE l.group_id = %(gid)s AND l.lesson_type IN %(types)s
        ORDER BY l.lesson_date, l.id""",
}


def check_group(group_id: int) -> dict | None:
    """
    Те же проверки по одной группе, но с конкретными строками для интерфейса.

    {'group_id', 'name', 'findings': {<ключ проверки>: [{'id', 'seq',
     'lesson_number', 'scheduled_date', 'fact_date'}, ...]}}

    ВНИМАНИЕ по полю id: у проверок, работающих от плана, это id плановой строки;
    у fact_without_position и duplicate_dates — id ЗАНЯТИЯ (позиции у них нет,
    поэтому seq и scheduled_date приходят пустыми). Интерфейс обязан различать
    эти два случая по ключу проверки, а не гадать по содержимому.

    В findings попадают только сработавшие проверки. Группы нет → None.
    Группа маленькая (десятки строк), поэтому здесь запрос на проверку — это
    дешевле и читаемее одного гигантского UNION.
    """
    with connection.cursor() as cur:
        cur.execute('SELECT name FROM groups WHERE id = %s', [group_id])
        row = cur.fetchone()
        if row is None:
            return None
        name = row[0]

        params = {'gid': group_id, 'types': tuple(COURSE_LESSON_TYPES)}
        findings = {}
        for key in CHECKS:
            cur.execute(_GROUP_SQL[key], params)
            rows = [
                {'id': r[0], 'seq': r[1], 'lesson_number': r[2],
                 'scheduled_date': r[3], 'fact_date': r[4]}
                for r in cur.fetchall()
            ]
            if rows:
                findings[key] = rows

    return {'group_id': group_id, 'name': name, 'findings': findings}
