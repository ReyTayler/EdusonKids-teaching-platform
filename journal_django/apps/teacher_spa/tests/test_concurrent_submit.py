"""
Гонка: две одновременные записи одного занятия. Должен пройти ровно один урок.

Зачем отдельный файл с transaction=True: нужны РЕАЛЬНЫЕ коммиты и отдельные
соединения к БД. Внутри обычной тестовой транзакции (django_db без transaction)
select_for_update ничего не сериализует — оба потока сидели бы в одной
транзакции, и тест доказывал бы не то.

⚠️ ЦЕНА transaction=True: pytest-django подставляет TransactionTestCase, а его
teardown делает `flush` — то есть ЧИСТИТ таблицы тестовой БД. Тесты идут в
`eduson_test` (guard в config/settings/test.py не даёт запуститься против
рабочей `eduson`), и по контракту это пустая схема без данных, так что чистить
там нечего. Но если кто-то засеет `eduson_test` или направит TEST_DB_NAME на
засеянный клон — обычный `pytest` эти данные снесёт. Сеять тестовую БД нельзя;
всё нужное создают фикстуры.

В инциденте ПГ215 повторы шли с интервалом 10 и 15 секунд, то есть ВТОРАЯ
отправка уходила раньше, чем отвечала первая — запросы обрабатывались
одновременно. До этого теста корректность блокировки держалась только на
рассуждении: параллельных тестов в проекте не было ни одного.
"""
from __future__ import annotations

import threading

import pytest
from django.db import connection, connections

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def race_group():
    """
    Группа + преподаватель для гонки. Создаются напрямую и удаляются в конце:
    transaction=True означает реальные коммиты, автоотката тестовой транзакции
    здесь НЕТ — за собой надо прибирать самому, БД общая.
    """
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO teachers (name, active) VALUES ('__race_teacher__', true) RETURNING id"
        )
        teacher_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO directions (name, total_lessons, active) "
            "VALUES ('__race_dir__', 8, true) RETURNING id"
        )
        direction_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) "
            "VALUES ('__race_group__', %s, %s, false, 60, true, 0) RETURNING id",
            [direction_id, teacher_id],
        )
        group_id = cur.fetchone()[0]

    yield group_id, teacher_id

    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM payroll WHERE lesson_id IN '
            '(SELECT id FROM lessons WHERE group_id = %s)', [group_id],
        )
        cur.execute('DELETE FROM lessons WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM groups WHERE id = %s', [group_id])
        cur.execute('DELETE FROM directions WHERE id = %s', [direction_id])
        cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])


def test_two_simultaneous_submits_create_one_lesson(race_group):
    """Два потока стартуют по барьеру и пишут одно занятие: урок должен быть один."""
    from apps.lessons.exceptions import LessonAlreadyRecorded
    from apps.lessons.services import record_lesson

    group_id, teacher_id = race_group
    date = '2026-08-25'
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            barrier.wait(timeout=10)      # стартуем строго одновременно
            record_lesson(
                group_id=group_id,
                teacher_id=teacher_id,
                original_teacher_id=None,
                lesson_date=date,
                lesson_number=1,
                lesson_duration_minutes=60,
                lesson_type='regular',
                record_url=None,
                submitted_by_token='acct:race',
                submit_date=date,
                attendance=[],
                planned_lesson_id=None,
            )
            with lock:
                outcomes.append('ok')
        except LessonAlreadyRecorded:
            with lock:
                outcomes.append('conflict')
        except Exception as exc:            # noqa: BLE001 — важно увидеть тип
            with lock:
                outcomes.append(f'error:{type(exc).__name__}')
        finally:
            connections.close_all()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    with connection.cursor() as cur:
        cur.execute(
            'SELECT count(*) FROM lessons WHERE group_id = %s AND lesson_date = %s',
            [group_id, date],
        )
        created = cur.fetchone()[0]

    assert created == 1, f'создано уроков: {created}, исходы: {outcomes}'
    # Ровно один успех и ровно один осмысленный отказ: 'error:IntegrityError'
    # в исходах означал бы, что конфликт не превратился в доменное исключение
    # и наружу ушёл бы 500 вместо 409.
    assert sorted(outcomes) == ['conflict', 'ok'], outcomes


def test_two_simultaneous_submits_of_same_position(race_group):
    """
    То же самое, но по пути С ПОЗИЦИЕЙ курса — основной путь преподавателя.

    Первый тест файла проверяет только уникальный индекс (там planned_lesson_id
    не передаётся). Здесь работает второй рубеж — захват позиции под
    select_for_update: второй поток обязан увидеть уже проставленный
    fact_lesson_id и получить отказ, а не создать второй платный урок.
    """
    from apps.lessons.exceptions import LessonAlreadyRecorded
    from apps.lessons.services import record_lesson

    group_id, teacher_id = race_group
    date = '2026-08-26'
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO planned_lessons
                (group_id, seq, lesson_number, scheduled_date, scheduled_time,
                 status, created_at, updated_at)
            VALUES (%s, 5, 5, %s, '14:00', 'pending', NOW(), NOW())
            RETURNING id
            """,
            [group_id, date],
        )
        position_id = cur.fetchone()[0]

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            barrier.wait(timeout=10)
            record_lesson(
                group_id=group_id,
                teacher_id=teacher_id,
                original_teacher_id=None,
                lesson_date=date,
                lesson_number=5,
                lesson_duration_minutes=60,
                lesson_type='regular',
                record_url=None,
                submitted_by_token='acct:race-pos',
                submit_date=date,
                attendance=[],
                planned_lesson_id=position_id,
            )
            with lock:
                outcomes.append('ok')
        except LessonAlreadyRecorded:
            with lock:
                outcomes.append('conflict')
        except Exception as exc:            # noqa: BLE001 — важно увидеть тип
            with lock:
                outcomes.append(f'error:{type(exc).__name__}')
        finally:
            connections.close_all()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    with connection.cursor() as cur:
        cur.execute(
            'SELECT count(*) FROM lessons WHERE group_id = %s AND lesson_date = %s',
            [group_id, date],
        )
        created = cur.fetchone()[0]
        cur.execute('UPDATE planned_lessons SET fact_lesson_id = NULL WHERE id = %s', [position_id])
        cur.execute('DELETE FROM planned_lessons WHERE id = %s', [position_id])

    assert created == 1, f'создано уроков: {created}, исходы: {outcomes}'
    assert sorted(outcomes) == ['conflict', 'ok'], outcomes
