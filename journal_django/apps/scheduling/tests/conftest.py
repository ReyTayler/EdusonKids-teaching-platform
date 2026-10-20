"""
Фикстуры тестов scheduling: запланированная группа (старт + слот) у преподавателя A
и группа преподавателя B — для проверки скоупа календаря по учителю.

managed-схема eduson_test; чистим прямым DELETE в FK-безопасном порядке.
"""
from __future__ import annotations

import datetime

import pytest
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import connection
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.scheduling.models import PlannedLesson


def _jwt_client(account_id: int) -> APIClient:
    from apps.accounts.models import Account
    user = Account.objects.get(pk=account_id)
    refresh = RefreshToken.for_user(user)
    refresh['token_version'] = user.token_version
    client = APIClient()
    client.cookies[settings.SIMPLE_JWT.get('AUTH_COOKIE', 'access')] = str(refresh.access_token)
    return client


@pytest.fixture
def sched_setup(db):
    """
    Учитель A с запланированной группой (старт 2026-06-01 пн, слот Пн 10:00,
    direction.total_lessons=8) + учитель B со своей группой. Возвращает dict.

    В каждой группе есть активный ученик — как в жизни: пустая группа выпадает
    из «Заполнить» (repository.unfilled_planned_lessons), и без ученика фикстура
    молча проверяла бы не тот случай.
    """
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name, active) VALUES ('__sched_A__', true) RETURNING id")
        teacher_a = cur.fetchone()[0]
        cur.execute("INSERT INTO teachers (name, active) VALUES ('__sched_B__', true) RETURNING id")
        teacher_b = cur.fetchone()[0]

        pw = make_password('testpass_sched')
        cur.execute(
            "INSERT INTO accounts (email,password,role,teacher_id,is_active,is_staff,is_superuser,"
            "first_name,last_name,token_version,date_joined) "
            "VALUES ('__sched_a__@t.local',%s,'teacher',%s,true,false,false,'','',0,NOW()) RETURNING id",
            [pw, teacher_a],
        )
        account_a = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO directions (name,total_lessons,color,active) "
            "VALUES ('__sched_dir__',8,'#4F59F9',true) RETURNING id"
        )
        direction_id = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO groups (name,direction_id,teacher_id,is_individual,lesson_duration_minutes,"
            "group_start_date,active,vk_chat,lesson_number_offset) "
            "VALUES ('__sched_group_A__',%s,%s,false,60,'2026-06-01',true,'https://vk.me/join/sched_a',0) RETURNING id",
            [direction_id, teacher_a],
        )
        group_a = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_schedule_slots (group_id,day_of_week,start_time,effective_from) "
            "VALUES (%s,1,'10:00','2026-06-01')",  # day_of_week=1 → понедельник (Вс=0)
            [group_a],
        )

        cur.execute(
            "INSERT INTO groups (name,direction_id,teacher_id,is_individual,lesson_duration_minutes,"
            "group_start_date,active,lesson_number_offset) "
            "VALUES ('__sched_group_B__',%s,%s,false,60,'2026-06-01',true,0) RETURNING id",
            [direction_id, teacher_b],
        )
        group_b = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_schedule_slots (group_id,day_of_week,start_time,effective_from) "
            "VALUES (%s,3,'12:00','2026-06-01')",  # среда
            [group_b],
        )

        cur.execute("INSERT INTO students (full_name, created_at) "
                    "VALUES ('__sched_student__', NOW()) RETURNING id")
        student_id = cur.fetchone()[0]
        for gid in (group_a, group_b):
            cur.execute(
                'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
                'VALUES (%s,%s,0,true)', [gid, student_id])

    data = {
        'client_a': _jwt_client(account_a),
        'account_a': account_a, 'teacher_a': teacher_a, 'teacher_b': teacher_b,
        'group_a': group_a, 'group_b': group_b,
        'group_a_name': '__sched_group_A__', 'group_b_name': '__sched_group_B__',
        'direction_id': direction_id, 'student_id': student_id,
    }
    yield data

    with connection.cursor() as cur:
        # planned_lessons: FK на groups с Python-CASCADE (не ON DELETE в БД) —
        # raw-DELETE групп не каскадит, поэтому чистим детей явно и первыми.
        # Начисления за отмены создаёт сама операция отмены; FK держит группу.
        cur.execute('DELETE FROM payroll_cancellations WHERE group_id IN (%s,%s)', [group_a, group_b])
        cur.execute('DELETE FROM planned_lessons WHERE group_id IN (%s,%s)', [group_a, group_b])
        cur.execute('DELETE FROM group_memberships WHERE group_id IN (%s,%s)', [group_a, group_b])
        cur.execute('DELETE FROM lessons WHERE group_id IN (%s,%s)', [group_a, group_b])
        cur.execute('DELETE FROM group_schedule_slots WHERE group_id IN (%s,%s)', [group_a, group_b])
        cur.execute('DELETE FROM groups WHERE id IN (%s,%s)', [group_a, group_b])
        cur.execute('DELETE FROM directions WHERE id = %s', [direction_id])
        cur.execute('DELETE FROM students WHERE id = %s', [student_id])
        cur.execute('DELETE FROM accounts WHERE id = %s', [account_a])
        cur.execute('DELETE FROM teachers WHERE id IN (%s,%s)', [teacher_a, teacher_b])


@pytest.fixture
def group_with_group(db):
    """Минимальная группа (direction total_lessons=4, слот вторник) + 4 pending
    строки (07/14/21/28 июля 2026). Общая фикстура wipe_one_offs/cancel_lesson."""
    with connection.cursor() as cur:
        cur.execute("INSERT INTO directions (name,total_lessons,active) "
                    "VALUES ('__wipe_dir__',4,true) RETURNING id")
        did = cur.fetchone()[0]
        cur.execute("INSERT INTO teachers (name) VALUES ('__wipe_t__') RETURNING id")
        tid = cur.fetchone()[0]
        cur.execute("INSERT INTO groups (name,direction_id,teacher_id,is_individual,"
                    "lesson_duration_minutes,lessons_per_week,active,lesson_number_offset) "
                    "VALUES ('__wipe_g__',%s,%s,false,60,1,true,0) RETURNING id", [did, tid])
        gid = cur.fetchone()[0]
        # Открытый слот (Вс=0 → 2=вторник, 18:00), совпадает с датами строк ниже —
        # нужен cancel_lesson для поиска следующего свободного слота в конце курса.
        cur.execute(
            "INSERT INTO group_schedule_slots (group_id,day_of_week,start_time,effective_from) "
            "VALUES (%s,2,'18:00','2026-07-01')", [gid])
    now = datetime.datetime(2026, 7, 1, 12, 0)
    for i, d in enumerate(['2026-07-07', '2026-07-14', '2026-07-21', '2026-07-28'], start=1):
        PlannedLesson.objects.create(
            group_id=gid, seq=i, lesson_number=i, scheduled_date=d,
            scheduled_time=datetime.time(18, 0), teacher_id=tid, status='pending',
            created_at=now, updated_at=now)
    yield gid, tid
    with connection.cursor() as cur:
        # Начисления за отмены создаёт сама операция отмены; FK держит группу.
        cur.execute("DELETE FROM payroll_cancellations WHERE group_id=%s", [gid])
        cur.execute("DELETE FROM planned_lessons WHERE group_id=%s", [gid])
        cur.execute("DELETE FROM group_schedule_slots WHERE group_id=%s", [gid])
        cur.execute("DELETE FROM groups WHERE id=%s", [gid])
        cur.execute("DELETE FROM teachers WHERE id=%s", [tid])
        cur.execute("DELETE FROM directions WHERE id=%s", [did])
