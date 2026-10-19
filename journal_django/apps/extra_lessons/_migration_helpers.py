"""Чистая переливка старых групповых назначений в пер-ученик AbsenceResolution."""
from __future__ import annotations

from decimal import Decimal


def migrate_assignments_to_resolutions(connection) -> None:
    with connection.cursor() as cur:
        cur.execute("""
            INSERT INTO absence_resolutions
                (missed_lesson_id, student_id, assigned_teacher_id, scheduled_date,
                 scheduled_time, duration_minutes, status, fact_lesson_id, created_at)
            SELECT a.missed_lesson_id, p.student_id, a.teacher_id, a.scheduled_date,
                   a.scheduled_time, a.duration_minutes, a.status, a.fact_lesson_id, a.created_at
            FROM extra_lesson_assignments a
            JOIN extra_lesson_participants p ON p.assignment_id = a.id
            ON CONFLICT (missed_lesson_id, student_id) DO NOTHING
        """)


def remap_statuses_1b(connection) -> None:
    """Фаза 1b: cancelled-строки удалить (терминального статуса больше нет,
    отмена/откат теперь → pending), затем переименовать scheduled→makeup_scheduled
    и done→makeup_done. Запускается ДО смены CHECK-констрейнта в миграции (старые
    значения должны переехать, пока новый CHECK их ещё не запретил)."""
    with connection.cursor() as cur:
        cur.execute("DELETE FROM absence_resolutions WHERE status = 'cancelled'")
        cur.execute("UPDATE absence_resolutions SET status = 'makeup_scheduled' WHERE status = 'scheduled'")
        cur.execute("UPDATE absence_resolutions SET status = 'makeup_done' WHERE status = 'done'")


def revert_historical_makeups(connection) -> None:
    """Фаза 1c-1: перевести исторические проведённые доп.уроки (makeup_done) на
    новую модель потребления. До 1c потребление шло от ИСХОДНОГО урока
    (apply_makeup_attendance флипал его в present=true), а extra-факт исключался;
    теперь потребление идёт от самого extra-факта, а исходный остаётся
    present=false. Для исторических записей это значит:

      1. У extra-факта выставить длительность = длительности ИСХОДНОГО урока
         (старый record() писал длительность НАЗНАЧЕНИЯ — под новой моделью вес
         потребления факта обязан совпасть с весом компенсируемого занятия, а не
         с операционной длительностью доп.урока).
      2. Снять историческую ретроактивную отметку исходного урока
         (present=true → false), иначе после снятия `.exclude(extra)` пропуск
         считался бы ДВАЖДЫ (исходный + extra).

    lessons_done НЕ трогаем: старый apply_makeup_attendance инкрементировал его на
    вес ИСХОДНОГО урока в группе пропуска — ровно то же, что теперь делает
    increment_lessons_done в record(); значение уже верное.

    Идемпотентно (guard-условия `IS DISTINCT FROM` / `present = true`)."""
    with connection.cursor() as cur:
        # 1. Длительность extra-факта → длительность исходного урока (вес потребления).
        cur.execute("""
            UPDATE lessons f
            SET lesson_duration_minutes = m.lesson_duration_minutes
            FROM absence_resolutions ar
            JOIN lessons m ON m.id = ar.missed_lesson_id
            WHERE ar.status = 'makeup_done'
              AND ar.fact_lesson_id = f.id
              AND f.lesson_duration_minutes IS DISTINCT FROM m.lesson_duration_minutes
        """)
        # 2. Исходный пропуск обратно в present=false (снять apply_makeup_attendance).
        cur.execute("""
            UPDATE lesson_attendance la
            SET present = false
            FROM absence_resolutions ar
            WHERE ar.status = 'makeup_done'
              AND la.lesson_id = ar.missed_lesson_id
              AND la.student_id = ar.student_id
              AND la.present = true
        """)


def convert_historical_burned_at(connection) -> None:
    """Фаза 2: конвертировать исторические «сгоревшие задним числом» правки
    (LessonAttendance.burned_at + Payroll.burn_surcharge_* от never-deployed
    burn-WIP) в штатную модель — отдельный burned-Lesson + AbsenceResolution.

    На каждую строку (present=true + burned_at) обычного урока L, ученик S:
      1. Создать Lesson(lesson_type='burned', lesson_date=burned_at, та же группа/
         преподаватель/номер/длительность, token='burn-migrated:L:S' —
         уникализирует lessons_natural_key).
      2. LessonAttendance(new, S, present=true) — потребление переезжает на него в
         месяц burned_at (сохранён); исходный урок больше не считается.
      3. Payroll(new) = доля surcharge исходного payroll (историческую зарплату
         сохраняем — «прошлое как есть»; при нескольких сожжённых на одном уроке
         surcharge делится поровну, остаток (копейки) — первому).
      4. Исходную строку вернуть в present=false, burned_at=NULL.
      5. У исходного payroll ОБНУЛИТЬ burn_surcharge (перенесён в п.3 → без
         двойного счёта зарплаты) и выровнять present_count на новый baseline.
      6. AbsenceResolution(missed=L, student=S, status='burned', fact=new).

    lessons_done НЕ трогаем (историч. flip уже инкрементировал его — как и новая
    burn()). Идемпотентно: урок с уже существующей burned-резолюцией пропускается;
    вставка резолюции — ON CONFLICT DO UPDATE (на случай авто-pending за пропуск).
    Числа (balance/attended/зарплата по преподавателю за месяц) — инвариант.
    """
    with connection.cursor() as cur:
        cur.execute(
            "SELECT la.lesson_id, la.student_id, la.burned_at, "
            "       l.group_id, l.teacher_id, l.lesson_number, l.lesson_duration_minutes "
            "FROM lesson_attendance la "
            "JOIN lessons l ON l.id = la.lesson_id "
            "WHERE la.burned_at IS NOT NULL AND la.present = true "
            "  AND l.lesson_type = 'regular' "
            "ORDER BY la.lesson_id, la.student_id"
        )
        rows = cur.fetchall()
        if not rows:
            return

        by_lesson: dict = {}
        for r in rows:
            by_lesson.setdefault(r[0], []).append(r)

        for lesson_id, grp in by_lesson.items():
            # Идемпотентность на уровне ПАРЫ (урок×ученик), а не всего урока:
            # мигрируем только тех, у кого ещё нет burned-резолюции. Иначе в редком
            # миксе (ученик A уже сожжён через live-burn, B несёт WIP-мету на том же
            # уроке) гард по уроку пропустил бы B и потерял его данные при 2c.
            # (Обычный повторный прогон и так no-op: исходные строки уже present=false
            #  → верхний SELECT их не вернёт.)
            pending = []
            for row in grp:
                cur.execute(
                    "SELECT 1 FROM absence_resolutions "
                    "WHERE missed_lesson_id = %s AND student_id = %s AND status = 'burned' LIMIT 1",
                    [row[0], row[1]])
                if not cur.fetchone():
                    pending.append(row)
            if not pending:
                continue

            cur.execute(
                "SELECT burn_surcharge_amount FROM payroll WHERE lesson_id = %s",
                [lesson_id])
            prow = cur.fetchone()
            total = prow[0] if prow and prow[0] is not None else Decimal('0')
            total_cents = int((total * 100).to_integral_value())
            n = len(pending)
            base = total_cents // n
            remainder = total_cents - base * n

            for i, (lid, sid, burned_at, group_id, teacher_id, lesson_number, dur) in enumerate(pending):
                share = Decimal(base + (remainder if i == 0 else 0)) / Decimal(100)
                cur.execute(
                    "INSERT INTO lessons "
                    "(group_id, teacher_id, lesson_date, lesson_number, "
                    " lesson_duration_minutes, lesson_type, submitted_at, submitted_by_token) "
                    "VALUES (%s, %s, %s, %s, %s, 'burned', now(), %s) RETURNING id",
                    [group_id, teacher_id, burned_at, lesson_number, dur,
                     f'burn-migrated:{lid}:{sid}'])
                new_lesson_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO lesson_attendance (lesson_id, student_id, present) "
                    "VALUES (%s, %s, true)", [new_lesson_id, sid])
                cur.execute(
                    "INSERT INTO payroll "
                    "(lesson_id, teacher_id, total_students, present_count, payment, penalty) "
                    "VALUES (%s, %s, 1, 1, %s, 0)", [new_lesson_id, teacher_id, share])
                cur.execute(
                    "INSERT INTO absence_resolutions "
                    "(missed_lesson_id, student_id, status, fact_lesson_id, created_at) "
                    "VALUES (%s, %s, 'burned', %s, now()) "
                    "ON CONFLICT (missed_lesson_id, student_id) "
                    "DO UPDATE SET status = 'burned', fact_lesson_id = EXCLUDED.fact_lesson_id",
                    [lid, sid, new_lesson_id])
                cur.execute(
                    "UPDATE lesson_attendance SET present = false, burned_at = NULL "
                    "WHERE lesson_id = %s AND student_id = %s", [lid, sid])

            # Исходный payroll: перенесённую надбавку обнулить + present_count → baseline.
            cur.execute(
                "SELECT COUNT(*) FROM lesson_attendance WHERE lesson_id = %s AND present = true",
                [lesson_id])
            baseline_present = cur.fetchone()[0]
            cur.execute(
                "UPDATE payroll SET burn_surcharge_amount = 0, burn_surcharge_at = NULL, "
                "present_count = %s WHERE lesson_id = %s",
                [baseline_present, lesson_id])
