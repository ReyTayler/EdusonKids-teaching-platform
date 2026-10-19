"""
Повторная отправка урока не должна создавать второй платный урок ни на одном
из путей записи. Инцидент ПГ215 (31.07.2026): три отправки одного занятия с
интервалом 10 и 15 секунд дали три урока, три начисления зарплаты и три
списания с баланса учеников.
"""
from __future__ import annotations

import pytest
from django.db import connection

pytestmark = pytest.mark.django_db


@pytest.fixture
def fact_lesson_ids(group_with_two_slots, teacher_fixture):
    """
    Два факт-урока для привязки к двум позициям плана. Два, а не один: у
    `planned_lessons.fact_lesson_id` есть UniqueConstraint
    (`planned_lessons_fact_lesson_id_key`) — один факт не может занимать две
    плановые позиции разом, поэтому «обе позиции заняты» требует двух разных
    фактов.

    Создаются напрямую (не через поиск «любого последнего урока» по всей
    таблице `lessons`): eduson_test — общая БД, и на момент написания теста в
    ней не было ни одной строки `lessons` — такой поиск молча скипал тест,
    пряча то, что тест должен ловить.
    """
    group_id, _date, _positions = group_with_two_slots
    teacher_id, _ = teacher_fixture
    ids = []
    with connection.cursor() as cur:
        for n in (1, 2):
            cur.execute(
                """
                INSERT INTO lessons
                    (group_id, teacher_id, lesson_date, lesson_number,
                     lesson_duration_minutes, lesson_type, submitted_at, submitted_by_token)
                VALUES (%s, %s, '2026-08-16', %s, 60, 'regular', NOW(), %s)
                RETURNING id
                """,
                [group_id, teacher_id, n, f'__spa_dup_test_token_{n}__'],
            )
            ids.append(cur.fetchone()[0])
    yield ids
    with connection.cursor() as cur:
        cur.execute('DELETE FROM lessons WHERE id = ANY(%s)', [ids])


def _purge_group_lessons(group_id: int) -> None:
    """
    Уборка за собой: eduson_test — общая БД, оставленный платный урок исказил бы
    чужие тесты и не дал бы удалиться группе (FK). Чистим ВСЕ уроки группы, а не
    только успешно возвращённый id: в RED-состоянии дубль как раз и создаётся, а
    его id тест не получает (падает на pytest.raises).
    """
    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM payroll WHERE lesson_id IN '
            '(SELECT id FROM lessons WHERE group_id = %s)',
            [group_id],
        )
        cur.execute('DELETE FROM lessons WHERE group_id = %s', [group_id])


def test_all_positions_taken_returns_position_not_none(group_with_two_slots, fact_lesson_ids):
    """
    Мультислот, обе позиции дня заняты фактами. Резолвер обязан вернуть позицию
    (вызывающий по ней отдаст 409), а не None — None увёл бы запись на расчёт
    номера из прогресса учеников, то есть ровно в механику ПГ215.
    """
    group_id, date, positions = group_with_two_slots
    with connection.cursor() as cur:
        for pos_id, fact_id in zip(positions, fact_lesson_ids):
            cur.execute(
                "UPDATE planned_lessons SET fact_lesson_id = %s, status = 'done' "
                'WHERE id = %s',
                [fact_id, pos_id],
            )

    from apps.scheduling.repository import find_course_position_by_date
    resolved = find_course_position_by_date(group_id, date)

    assert resolved is not None
    assert resolved['fact_lesson_id'] is not None


def test_one_free_position_is_returned(group_with_two_slots, fact_lesson_ids):
    """Одна из двух позиций свободна — резолвер обязан выбрать именно её."""
    group_id, date, positions = group_with_two_slots
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE planned_lessons SET fact_lesson_id = %s, status = 'done' "
            'WHERE id = %s',
            [fact_lesson_ids[0], positions[0]],
        )

    from apps.scheduling.repository import find_course_position_by_date
    resolved = find_course_position_by_date(group_id, date)

    assert resolved is not None
    assert resolved['id'] == positions[1]
    assert resolved['fact_lesson_id'] is None


def test_two_free_positions_are_ambiguous(group_with_two_slots):
    """
    Обе позиции свободны — по дате их не различить, нужен явный plannedLessonId
    из календаря. Возврат None здесь корректен.
    """
    group_id, date, _positions = group_with_two_slots

    from apps.scheduling.repository import find_course_position_by_date
    assert find_course_position_by_date(group_id, date) is None


def test_renumbered_position_refuses_instead_of_writing_mismatch(
    group_with_two_slots, teacher_fixture,
):
    """
    План перенумеровали между предпроверкой и захватом позиции → отказ.

    Взять новый номер и всё-таки записать нельзя: выше по record_lesson от
    СТАРОГО номера уже посчитаны блокировка переведённых учеников
    (locked_through_map) и слот-маркеры «неоплачиваемый пропуск»
    (list_lesson_skips). Урок под новым номером со списком посещаемости,
    отфильтрованным по старому, засчитал бы переведённому ученику занятие
    дважды и списал его с баланса.
    """
    from apps.lessons.exceptions import CoursePositionVanished
    from apps.lessons.services import record_lesson

    group_id, date, positions = group_with_two_slots
    teacher_id, _ = teacher_fixture
    with connection.cursor() as cur:
        cur.execute(
            'UPDATE planned_lessons SET lesson_number = 99 WHERE id = %s',
            [positions[0]],
        )

    try:
        with pytest.raises(CoursePositionVanished):
            record_lesson(
                group_id=group_id,
                teacher_id=teacher_id,
                original_teacher_id=None,
                lesson_date=date,
                lesson_number=31,          # устаревший аргумент
                lesson_duration_minutes=60,
                lesson_type='regular',
                record_url=None,
                submitted_by_token='test:number',
                submit_date=date,
                attendance=[],
                planned_lesson_id=positions[0],
            )
    finally:
        _purge_group_lessons(group_id)


def test_lesson_number_comes_from_locked_position(group_with_two_slots, teacher_fixture):
    """
    Согласованный номер пишется из позиции, захваченной под блокировкой, а не из
    аргумента: позиция — источник правды, аргумент лишь дублирует её значение.
    """
    from apps.lessons.services import record_lesson

    group_id, date, positions = group_with_two_slots
    teacher_id, _ = teacher_fixture

    try:
        result = record_lesson(
            group_id=group_id,
            teacher_id=teacher_id,
            original_teacher_id=None,
            lesson_date=date,
            lesson_number=31,
            lesson_duration_minutes=60,
            lesson_type='regular',
            record_url=None,
            submitted_by_token='test:number',
            submit_date=date,
            attendance=[],
            planned_lesson_id=positions[0],
        )

        with connection.cursor() as cur:
            cur.execute(
                'SELECT lesson_number, submission_key FROM lessons WHERE id = %s',
                [result['lesson_id']],
            )
            written, key = cur.fetchone()

        assert int(written) == 31
        assert key == f'pos:{positions[0]}', 'ключ должен указывать на позицию курса'
    finally:
        with connection.cursor() as cur:
            cur.execute(
                'UPDATE planned_lessons SET fact_lesson_id = NULL WHERE id = %s',
                [positions[0]],
            )
        _purge_group_lessons(group_id)


def test_vanished_position_raises_instead_of_silent_fallback(group_with_two_slots, teacher_fixture):
    """
    Позицию отменили между предпроверкой и локом → явная ошибка. Молчаливое
    продолжение записи означало бы переход на незащищённый путь.
    """
    from apps.lessons.exceptions import CoursePositionVanished
    from apps.lessons.services import record_lesson

    group_id, date, positions = group_with_two_slots
    teacher_id, _ = teacher_fixture
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE planned_lessons SET status = 'cancelled' WHERE id = %s",
            [positions[0]],
        )

    with pytest.raises(CoursePositionVanished):
        record_lesson(
            group_id=group_id,
            teacher_id=teacher_id,
            original_teacher_id=None,
            lesson_date=date,
            lesson_number=31,
            lesson_duration_minutes=60,
            lesson_type='regular',
            record_url=None,
            submitted_by_token='test:vanished',
            submit_date=date,
            attendance=[],
            planned_lesson_id=positions[0],
        )


def test_stale_position_id_with_other_date_is_ignored(group_with_two_slots):
    """
    Клиент прислал id позиции, которая стоит на другой дате (календарь во
    вкладке устарел). Использовать её нельзя — закроется не то занятие.
    Резолвер обязан уйти на поиск по фактической дате.
    """
    from apps.teacher_spa.services import _resolve_course_position

    group_id, date, positions = group_with_two_slots
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE planned_lessons SET scheduled_date = '2026-09-20' WHERE id = %s",
            [positions[0]],
        )

    resolved = _resolve_course_position(group_id, date, positions[0])

    # Устаревшую позицию брать нельзя. Допустимо: вернуть оставшуюся позицию
    # этого дня (она теперь единственная) либо None.
    assert resolved is None or resolved['id'] != positions[0]


def test_matching_position_id_is_used(group_with_two_slots):
    """Актуальный id с совпадающей датой используется как раньше."""
    from apps.teacher_spa.services import _resolve_course_position

    group_id, date, positions = group_with_two_slots

    resolved = _resolve_course_position(group_id, date, positions[1])

    assert resolved is not None
    assert resolved['id'] == positions[1]


# ---------------------------------------------------------------------------
# Путь БЕЗ позиции курса: группа без плана / дата вне плана. Защита позицией
# здесь не работает вовсе — держит только ключ отправки (submission_key).
# ---------------------------------------------------------------------------

def test_second_record_without_position_is_rejected(group_fixture, teacher_fixture):
    """
    Группа без плана: повторная запись того же занятия обязана отказать, а не
    создать второй платный урок. Это путь, на котором произошёл ПГ215.
    """
    from apps.lessons.exceptions import LessonAlreadyRecorded
    from apps.lessons.services import record_lesson

    teacher_id, _ = teacher_fixture
    common = dict(
        group_id=group_fixture,
        teacher_id=teacher_id,
        original_teacher_id=None,
        lesson_date='2026-08-20',
        lesson_duration_minutes=60,
        lesson_type='regular',
        record_url=None,
        submit_date='2026-08-20',
        attendance=[],
        planned_lesson_id=None,
    )

    try:
        record_lesson(**common, lesson_number=1, submitted_by_token='acct:1')

        # Повтор: номер другой (его считают из прогресса учеников), токен тот же —
        # старый натуральный ключ такое не ловил, потому что номер каждый раз новый.
        with pytest.raises(LessonAlreadyRecorded):
            record_lesson(**common, lesson_number=2, submitted_by_token='acct:1')
    finally:
        _purge_group_lessons(group_fixture)


def test_duplicate_across_different_actors_is_rejected(group_fixture, teacher_fixture):
    """
    Препод записал урок, следом админ записывает то же занятие. Старый ключ
    включал submitted_by_token и такой дубль пропускал.
    """
    from apps.lessons.exceptions import LessonAlreadyRecorded
    from apps.lessons.services import record_lesson

    teacher_id, _ = teacher_fixture
    common = dict(
        group_id=group_fixture,
        teacher_id=teacher_id,
        original_teacher_id=None,
        lesson_date='2026-08-21',
        lesson_duration_minutes=60,
        lesson_type='regular',
        record_url=None,
        submit_date='2026-08-21',
        attendance=[],
        planned_lesson_id=None,
        lesson_number=5,
    )

    try:
        record_lesson(**common, submitted_by_token='acct:12')

        with pytest.raises(LessonAlreadyRecorded):
            record_lesson(**common, submitted_by_token='admin-imported')
    finally:
        _purge_group_lessons(group_fixture)


def test_different_dates_are_not_duplicates(group_fixture, teacher_fixture):
    """Два занятия одной группы в РАЗНЫЕ дни — не дубль, оба должны записаться."""
    from apps.lessons.services import record_lesson

    teacher_id, _ = teacher_fixture
    common = dict(
        group_id=group_fixture,
        teacher_id=teacher_id,
        original_teacher_id=None,
        lesson_duration_minutes=60,
        lesson_type='regular',
        record_url=None,
        attendance=[],
        planned_lesson_id=None,
        submitted_by_token='acct:1',
    )

    try:
        a = record_lesson(**common, lesson_date='2026-08-23', submit_date='2026-08-23', lesson_number=1)
        b = record_lesson(**common, lesson_date='2026-08-24', submit_date='2026-08-24', lesson_number=2)

        assert a['lesson_id'] != b['lesson_id']
    finally:
        _purge_group_lessons(group_fixture)


def test_admin_duplicate_returns_409_not_500(admin_client, group_fixture, teacher_fixture):
    """
    Повторное создание урока админом — понятный 409, а не «сломался сервер».

    Раньше конфликт уникального индекса уходил наверх необработанным
    IntegrityError: обработчик исключений DRF такое не знает и отдаёт голый 500.
    """
    teacher_id, _ = teacher_fixture
    payload = {
        'lesson_date': '2026-08-27',
        'group_id': group_fixture,
        'teacher_id': teacher_id,
        'lesson_number': 3,
        'lesson_duration_minutes': 60,
        'lesson_type': 'regular',
    }
    try:
        first = admin_client.post('/api/admin/lessons', payload, format='json')
        assert first.status_code == 201, first.content

        second = admin_client.post('/api/admin/lessons', payload, format='json')
        assert second.status_code == 409, second.content
        assert second.json()['code'] == 'lesson_already_recorded'
    finally:
        _purge_group_lessons(group_fixture)


def test_admin_can_record_both_slots_of_multislot_day(
    admin_client, group_with_two_slots, teacher_fixture,
):
    """
    Мультислот: в плане две позиции на день — админ обязан записать ОБА занятия.

    Регрессия: пока админский путь не резолвил позицию, ключ отправки у него был
    один на всю дату (slot:<group>:<date>), и второе занятие дня упиралось в 409,
    хотя оно законное.
    """
    group_id, date, _positions = group_with_two_slots
    teacher_id, _ = teacher_fixture
    base = {
        'lesson_date': date,
        'group_id': group_id,
        'teacher_id': teacher_id,
        'lesson_duration_minutes': 60,
        'lesson_type': 'regular',
    }
    try:
        first = admin_client.post('/api/admin/lessons', {**base, 'lesson_number': 31}, format='json')
        assert first.status_code == 201, first.content

        second = admin_client.post('/api/admin/lessons', {**base, 'lesson_number': 32}, format='json')
        assert second.status_code == 201, second.content
    finally:
        _purge_group_lessons(group_id)


def test_admin_cannot_duplicate_lesson_recorded_by_teacher(
    admin_client, group_with_two_slots, teacher_fixture,
):
    """
    Преподаватель записал занятие, ответ потерялся, и то же занятие заводит админ.

    Это оставшийся сценарий ПГ215: ключи разных путей не конфликтовали, потому
    что админский путь позицию не резолвил вовсе. Оба пути обязаны попадать в
    одно пространство ключей.
    """
    from apps.lessons.services import record_lesson

    group_id, date, positions = group_with_two_slots
    teacher_id, _ = teacher_fixture
    try:
        record_lesson(
            group_id=group_id, teacher_id=teacher_id, original_teacher_id=None,
            lesson_date=date, lesson_number=31, lesson_duration_minutes=60,
            lesson_type='regular', record_url=None, submitted_by_token='acct:7',
            submit_date=date, attendance=[], planned_lesson_id=positions[0],
        )

        clash = admin_client.post('/api/admin/lessons', {
            'lesson_date': date, 'group_id': group_id, 'teacher_id': teacher_id,
            'lesson_number': 31, 'lesson_duration_minutes': 60, 'lesson_type': 'regular',
        }, format='json')

        assert clash.status_code == 409, clash.content
    finally:
        _purge_group_lessons(group_id)


def test_editing_date_moves_the_guard_with_the_lesson(group_fixture, teacher_fixture):
    """
    Правка даты урока переносит ключ отправки на новую дату.

    Без пересчёта ключ остаётся привязан к СТАРОЙ дате: она блокируется навсегда
    (запись законного занятия упрётся в ложный 409), а новая дата остаётся без
    защиты — на неё ляжет второй платный урок.
    """
    from apps.lessons.services import record_lesson, update_lesson

    teacher_id, _ = teacher_fixture
    try:
        created = record_lesson(
            group_id=group_fixture, teacher_id=teacher_id, original_teacher_id=None,
            lesson_date='2026-09-10', lesson_number=1, lesson_duration_minutes=60,
            lesson_type='regular', record_url=None, submitted_by_token='acct:9',
            submit_date='2026-09-10', attendance=[], planned_lesson_id=None,
        )
        update_lesson(created['lesson_id'], {'lesson_date': '2026-09-11'})

        with connection.cursor() as cur:
            cur.execute(
                'SELECT submission_key FROM lessons WHERE id = %s', [created['lesson_id']],
            )
            key = cur.fetchone()[0]

        assert key == f'slot:{group_fixture}:2026-09-11', (
            'ключ должен указывать на новую дату, иначе старая заблокирована навсегда'
        )
    finally:
        _purge_group_lessons(group_fixture)
