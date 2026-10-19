"""
LessonsService — оркестрация записи урока (Lesson+attendance+счётчики+Payroll+
привязка к плановому занятию+синхронизация «Продлений»).

record_lesson — единое ядро (см. docs/superpowers/specs/2026-07-14-unify-lesson-recording-design.md),
используется и этим приложением (create_lesson_full — тонкий адаптер для
admin SPA), и apps.teacher_spa.services.submit_lesson. Транзакция управляется
ЗДЕСЬ (как submit_lesson); repository выполняет ORM-операции, cross-app
вызовы (link_facts/balances_for_students/renewals) — тоже здесь, не в repository.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from django.db import IntegrityError, transaction

from apps.groups.models import Group
from apps.lessons import repository
from apps.lessons.exceptions import (
    CoursePositionVanished, LessonAlreadyRecorded,
    LessonHasMakeupResolutions, SystemLessonProtected,
)
from apps.core.utils.decimal import to_decimal
from apps.lessons.models import SYSTEM_LESSON_TYPES, Lesson, LessonAttendance
from apps.lessons.submission_key import build_submission_key
from apps.memberships.repository import locked_through_map
from apps.payroll.calculator import calculate_payment, calculate_penalty
from apps.scheduling.repository import (
    attach_fact, find_course_position_by_date_and_number, link_facts,
    lock_course_position, relink_fact, sync_position_date,
)

# Подтипы уроков, которыми владеет apps.extra_lessons (факты доп.урока/сгорания).
# Общий CRUD /api/admin/lessons их не трогает — только откат из раздела «Доп.уроки».
# Набор — в apps.lessons.models (единый источник, там же COURSE_LESSON_TYPES).
_SYSTEM_LESSON_TYPES = SYSTEM_LESSON_TYPES


def _assert_not_system_lesson(lesson_id: int) -> None:
    """Бросает SystemLessonProtected, если урок — системный (extra/burned).
    No-op для несуществующего урока (тип None) — тогда работает обычный путь 404."""
    lesson_type = (
        Lesson.objects.filter(id=lesson_id).values_list('lesson_type', flat=True).first()
    )
    if lesson_type in _SYSTEM_LESSON_TYPES:
        raise SystemLessonProtected(lesson_type)


def _step(duration_minutes) -> Decimal:
    return Decimal('0.5') if duration_minutes == 45 else Decimal('1')


def record_lesson(*,
    lesson_date: str,
    teacher_id: int,
    group_id: int,
    original_teacher_id: Optional[int],
    lesson_number,
    lesson_duration_minutes: int,
    lesson_type: str,
    record_url: Optional[str],
    submitted_by_token: str,
    submit_date: str,
    attendance: list[dict],
    skip_balance_check: bool = False,
    planned_lesson_id: Optional[int] = None,
) -> dict:
    """
    Единое ядро записи урока. Атомарно создаёт Lesson+LessonAttendance,
    инкрементирует group_memberships.lessons_done, привязывает факт к
    planned_lessons (link_facts), создаёт Payroll (сервер считает
    payment/penalty сам — клиентского payroll не принимает), синхронизирует
    авто-стадию «Продлений» после коммита.

    planned_lesson_id — позиция курса, за которой закрепляется факт (резолвит
    вызывающий: apps.teacher_spa.services.submit_lesson). Если задан:
      • lesson_number берётся ИЗ ПОЗИЦИИ, а не из переданного аргумента —
        позиция является источником правды по номеру урока
        (номер читается из позиции ПОСЛЕ select_for_update — см. тело функции);
      • позиция лочится (select_for_update) и захватывается в этой же
        транзакции, что делает повторную запись того же занятия невозможной:
        второй захват видит проставленный fact_lesson_id → LessonAlreadyRecorded;
      • link_facts для этого факта не нужен — захват и есть привязка.
    Не задан → прежнее поведение: номер из аргумента + «угадывающий» link_facts
    (админский путь, группы без плана, запись на дату вне плана).

    attendance: [{'student_id': int, 'present': bool}, ...] — student_id уже
    резолвлен вызывающей стороной (teacher_spa резолвит по имени, admin SPA
    передаёт id напрямую).

    submit_date — для calculate_penalty: teacher SPA передаёт «сегодня»
    (штраф за просрочку отчёта), admin SPA передаёт submit_date=lesson_date
    всегда (админ не должен штрафоваться за административную запись задним
    числом — см. design doc).

    Бросает UnpaidAttendanceBlocked (apps.lessons.exceptions), если у кого-то
    из present-учеников остаток оплаченных уроков <= 0 — ДО открытия транзакции,
    ничего не пишется.

    Переведённые ученики, у которых lesson_number <= B (locked_through_map,
    накопленные уроки в исходной группе), молча исключаются из attendance ДО
    любых расчётов — как будто их не было в группе на этом уроке: ни
    LessonAttendance-строки, ни вклада в total_students/present_count/payroll,
    ни инкремента lessons_done. В отличие от update_attendance_cell (точечная
    правка одной клетки — там та же блокировка бросает AttendanceLockedByTransfer),
    здесь это массовая запись урока, поэтому — тихое исключение, а не ошибка.

    Возвращает {'lesson_id': int, 'payment': int, 'penalty': int}.
    """
    all_student_ids = [a['student_id'] for a in attendance]
    locked_map = locked_through_map(group_id, all_student_ids)
    if locked_map:
        lesson_num_dec = to_decimal(lesson_number)
        attendance = [
            a for a in attendance
            if lesson_num_dec > locked_map.get(a['student_id'], Decimal('0'))
        ]

    # Слот-маркеры «неоплачиваемый пропуск» (LessonSkip): пропуски, поставленные
    # ЗАРАНЕЕ — в т.ч. когда урока этого слота ещё не было (Вариант A). При записи
    # урока форсим помеченных учеников в present=false/unpaid_skip, что бы ни пришло
    # с фронта. Так «начал не с 1-го / переведён» отрабатывает автоматически, когда
    # группа доходит до этих уроков.
    marked_skip = set(repository.list_lesson_skips(group_id, lesson_number))
    if marked_skip:
        for a in attendance:
            if a['student_id'] in marked_skip:
                a['present'] = False
                a['unpaid_skip'] = True
                a['is_free'] = False

    # Исход «бесплатное занятие»: ученик присутствовал (present=true), прогресс и
    # сделка продления идут (present=true), занятие бесплатно ТОЛЬКО для ученика
    # (баланс/FIFO не списывают по флагу is_free, это делает finances). Преподаватель
    # получает за него зарплату как за обычного присутствующего (решение 2026-09-28).
    # Баланса для free не требуется. См. lesson-outcomes-spec.
    free_ids = {a['student_id'] for a in attendance if a.get('is_free')}
    # Исход «неоплачиваемый пропуск»: ученика на этом уроке как будто нет — из
    # зарплаты исключён, pending-резолюцию НЕ порождает (в отличие от обычного
    # «не был»). present=false, денег ноль. См. lesson-outcomes-spec.
    skip_ids = {a['student_id'] for a in attendance if a.get('unpaid_skip')}

    present_student_ids = [a['student_id'] for a in attendance if a['present']]
    # Оплату проверяем только у ПЛАТНЫХ present-учеников (free не обязан иметь баланс).
    # skip_balance_check — ручное решение администратора «записать, невзирая на долг»:
    # занятие остаётся платным (спишется с баланса, зарплата начислится), снят только
    # запрет. Прокидывается ТОЛЬКО из management-команды; ни teacher SPA, ни admin API
    # его не передают — иначе блокировка перестала бы защищать.
    if not skip_balance_check:
        repository.assert_students_paid([s for s in present_student_ids if s not in free_ids])

    is_half = lesson_duration_minutes == 45
    step = _step(lesson_duration_minutes)
    # Зарплата (правило 2026-09-28):
    #   • unpaid_skip — ученика на этом уроке как будто нет: вне total И вне present;
    #   • is_free — оплачивается преподавателю как обычное присутствие (в total и в
    #     present). Бесплатность касается только денег ученика.
    payroll_attendance = [a for a in attendance if a['student_id'] not in skip_ids]
    total_students = len(payroll_attendance)
    present_count = len([a for a in payroll_attendance if a['present']])

    payment = calculate_payment(total_students, present_count, is_half)
    penalty = calculate_penalty(lesson_date, submit_date, present_count)

    direction_id = (
        Group.objects.filter(id=group_id).values_list('direction_id', flat=True).first()
        if present_student_ids else None
    )

    with transaction.atomic():
        # Захват позиции курса — АВТОРИТЕТНАЯ проверка «урок за это занятие уже
        # записан». Вызывающий проверяет то же самое заранее (ради внятного 409
        # без открытия транзакции), но решает здесь, под select_for_update: два
        # параллельных повтора иначе оба прошли бы предварительную проверку до
        # коммита друг друга и создали два урока. Тот же приём, что в
        # apps.extra_lessons.services.burn (lock_for_record).
        position = None
        if planned_lesson_id is not None:
            position = lock_course_position(planned_lesson_id, group_id)
            if position is None:
                # Позицию отменили/удалили между предпроверкой и локом. Молча
                # продолжать нельзя: без позиции ядро уходит на расчёт номера из
                # прогресса — незащищённый путь, на котором повтор даёт дубль.
                raise CoursePositionVanished()
        if position is not None and position['fact_lesson_id'] is not None:
            raise LessonAlreadyRecorded(
                position['lesson_number'], position['scheduled_date'],
            )

        # Номер — из позиции, захваченной ПОД БЛОКИРОВКОЙ, а не из аргумента:
        # аргумент посчитан вызывающим до транзакции по незалоченному чтению, и
        # между этими моментами план могли перенумеровать (отмена другого
        # занятия группы вызывает _renumber_persist). Разъехавшиеся номер факта
        # и номер позиции ломают последующий матчинг link_facts/relink_fact.
        #
        # Если номер УСПЕЛ измениться — записывать нельзя вовсе, а не просто взять
        # новый: выше по функции от старого номера уже посчитаны блокировка
        # переведённых учеников (locked_through_map) и слот-маркеры
        # «неоплачиваемый пропуск» (list_lesson_skips). Записать урок под новым
        # номером со списком посещаемости, отфильтрованным по старому, значит
        # засчитать переведённому ученику занятие дважды и списать его с баланса.
        if position is not None and to_decimal(position['lesson_number']) != to_decimal(lesson_number):
            raise CoursePositionVanished()
        if position is not None:
            lesson_number = position['lesson_number']

        # Ключ отправки — последний рубеж от дубля. Работает и там, где позиции
        # курса нет вовсе (группа без плана, дата вне плана): именно на этом
        # пути повтор создавал второй платный урок (ПГ215).
        submission_key = build_submission_key(
            group_id=group_id,
            lesson_date=lesson_date,
            planned_lesson_id=position['id'] if position is not None else None,
        )
        try:
            # Вложенный atomic = SAVEPOINT: конфликт уникального индекса не
            # обязан рвать всю внешнюю транзакцию до отката.
            with transaction.atomic():
                lesson_id = repository.insert_lesson({
                    'lesson_date': lesson_date,
                    'teacher_id': teacher_id,
                    'group_id': group_id,
                    'original_teacher_id': original_teacher_id,
                    'lesson_number': lesson_number,
                    'lesson_duration_minutes': lesson_duration_minutes,
                    'lesson_type': lesson_type,
                    'record_url': record_url,
                    'submitted_by_token': submitted_by_token,
                    'submission_key': submission_key,
                })
        except IntegrityError as exc:
            if ('lessons_submission_key_unique' not in str(exc)
                    and 'lessons_natural_key' not in str(exc)):
                raise
            # Урок за это занятие уже записан — почти всегда повторная отправка
            # после потерянного ответа. Доменное исключение → 409, а не 500.
            raise LessonAlreadyRecorded(lesson_number, lesson_date) from exc
        # Привязать факт к плановой строке (planned_lessons.fact_lesson_id/status='done'),
        # иначе занятие остаётся «не проведено» в расписании/календаре.
        # Позиция названа явно → закрепляем за ней; иначе прежний матчинг по номеру.
        if position is not None:
            attach_fact(position['id'], lesson_id)
        else:
            link_facts(group_id)
        repository.increment_lessons_done(group_id, present_student_ids, step)
        repository.insert_attendance(lesson_id, attendance)
        repository.insert_payroll({
            'lesson_id': lesson_id,
            'teacher_id': teacher_id,
            'total_students': total_students,
            'present_count': present_count,
            'payment': payment,
            'penalty': penalty,
        })

        # Авто-создание «пропусков, требующих решения» — для ВСЕХ занятий курса
        # (regular/substitution/reschedule). Исключены только системные факты
        # extra/burned: они сами являются РЕЗУЛЬТАТОМ решения и пропусков не порождают.
        #
        # Условие ОБЯЗАНО быть «не системный», а не «== regular»: замена и перенос —
        # такие же занятия курса (COURSE_LESSON_TYPES), пропуск на них требует
        # отработки ровно так же. Тип выводит СЕРВЕР (apps.teacher_spa.services:
        # чужая группа → substitution, moved_from_date задан → reschedule), препод
        # его не выбирает и не видит — поэтому проверка на 'regular' тихо съедала
        # заявки на всех перенесённых и замещённых занятиях: ПГ216 от 15.08.2026,
        # ни одна из трёх заявок на таких уроках не создалась автоматически.
        #
        # Ленивый импорт: apps.extra_lessons.repository импортит apps.lessons.models,
        # прямой top-level импорт здесь завёл бы цикл.
        if lesson_type not in SYSTEM_LESSON_TYPES:
            # Неоплачиваемый пропуск — терминальный исход, pending НЕ порождает
            # (в отличие от обычного «не был»).
            absent_student_ids = [
                a['student_id'] for a in attendance
                if not a['present'] and a['student_id'] not in skip_ids
            ]
            from apps.extra_lessons import services as extra_lessons_services
            if absent_student_ids:
                extra_lessons_services.autocreate_pending_for_lesson(lesson_id, absent_student_ids)
            # Обратный исход: ученик всё-таки пришёл. Заранее назначенный доп.урок
            # за ЭТОТ урок остался без основания — снимаем назначение, иначе оно
            # превратится в лишнее занятие сверх курса, которое ученик оплатит, а
            # его факт получит номер уже проведённого урока и схлопнется с ним в
            # сетке прогресса. Назначенному преподавателю уходит уведомление об
            # отмене — за назначением стоит забронированное время.
            if present_student_ids:
                extra_lessons_services.drop_extra_for_present_students(
                    lesson_id, present_student_ids)

        for sid in present_student_ids:
            transaction.on_commit(lambda sid=sid: repository._sync_renewal_stage(sid, direction_id))

    return {'lesson_id': lesson_id, 'payment': payment, 'penalty': penalty}


def list_lessons(
    page: int = 1,
    page_size: int = 50,
    sort_by: str = 'lesson_date',
    sort_dir: str = 'desc',
    filters: Optional[dict] = None,
) -> dict:
    return repository.list_lessons(
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_dir=sort_dir,
        filters=filters,
    )


def get_lesson_full(lesson_id: int) -> Optional[dict]:
    return repository.get_lesson_full(lesson_id)


def create_lesson_full(data: dict) -> dict:
    """
    Admin SPA — тонкий адаптер над record_lesson. submit_date=lesson_date
    всегда (админ не штрафуется за административную запись задним числом).
    Возвращает {'lesson_id', 'payment', 'penalty'} (view делает повторный
    get_lesson_full для полного ответа, как раньше).

    Позиция курса резолвится по дате И НОМЕРУ: номер — единственное, чем админ
    различает два занятия одного дня, и без этого резолва оба получили бы один
    ключ отправки (slot:<group>:<date>), то есть второе законное занятие
    мультислотового дня упёрлось бы в конфликт. Заодно это ставит админский путь
    в ОДНО пространство ключей с преподавательским: то же занятие, записанное с
    двух сторон (препод отправил, ответ потерялся, админ завёл заново), теперь
    даёт 409, а не второй платный урок.
    """
    position = find_course_position_by_date_and_number(
        data['group_id'], data['lesson_date'], data['lesson_number'],
    )

    return record_lesson(
        planned_lesson_id=position['id'] if position is not None else None,
        lesson_date=data['lesson_date'],
        teacher_id=data['teacher_id'],
        group_id=data['group_id'],
        original_teacher_id=data.get('original_teacher_id'),
        lesson_number=data['lesson_number'],
        lesson_duration_minutes=data.get('lesson_duration_minutes') or 90,
        lesson_type=data.get('lesson_type') or 'regular',
        record_url=data.get('record_url') or None,
        submitted_by_token=data.get('submitted_by_token') or 'admin-imported',
        submit_date=data['lesson_date'],
        attendance=data.get('attendance') or [],
        # Право на флаг проверено во вьюхе (только superadmin) — сюда он приходит
        # уже разрешённым. Занятие остаётся платным, снят лишь запрет по балансу.
        skip_balance_check=bool(data.get('allow_debt')),
    )


def update_lesson(lesson_id: int, fields: dict) -> Optional[dict]:
    _assert_not_system_lesson(lesson_id)
    updated = repository.update_lesson(lesson_id, fields)
    if updated is None:
        return None
    # Правка номера/типа могла увести факт с его позиции курса — пересчитываем
    # привязку (link_facts существующие привязки не переносит, см. relink_fact).
    # На саму привязку (relink_fact) дата по-прежнему не влияет: матчинг там
    # идёт только по lesson_number, потому что цель — найти позицию своего
    # порядкового номера, а не позицию с подходящей датой.
    if fields.get('lesson_number') is not None or fields.get('lesson_type') is not None:
        relink_fact(lesson_id)
        # Позиция, освободившаяся после переезда, могла ждать другой свободный факт.
        link_facts(updated['group_id'])
    # Плановая дата проведённого занятия следует за фактической (инвариант спеки
    # 2026-08-05 §2). Прежде дата НЕ синхронизировалась, и правка дат уроков
    # оставляла плановые строки на старых датах: занятие висело в календаре не в
    # свой день, а «перенос навсегда» клал хвост курса поверх него (ПИ337).
    if fields.get('lesson_date') is not None:
        sync_position_date(lesson_id)
    # Ключ отправки выведен из даты и позиции — при их правке он обязан
    # пересчитаться, иначе появляются два скрытых дефекта:
    #   • правка даты: старая дата остаётся заблокированной навсегда, а на новую
    #     спокойно ляжет второй урок;
    #   • правка номера: факт уезжает на другую позицию (relink_fact), а ключ
    #     остаётся указывать на прежнюю — та выглядит свободной, но запись на неё
    #     упирается в уникальный индекс, то есть ЛОЖНЫЙ 409 и незаполнимая позиция.
    if fields.get('lesson_date') is not None or fields.get('lesson_number') is not None:
        _refresh_submission_key(lesson_id)
    return updated


def _refresh_submission_key(lesson_id: int) -> None:
    """
    Пересчитать ключ отправки после правки урока — тем же резолвом, что при
    создании (позиция по дате И номеру), чтобы созданные и отредактированные
    уроки жили в одном пространстве ключей.

    Системные уроки (доп.урок/сгорание) ключа не имеют и здесь не участвуют:
    их защита от повтора — лок статуса резолюции в apps.extra_lessons.
    """
    row = (
        Lesson.objects.filter(id=lesson_id)
        .values('group_id', 'lesson_date', 'lesson_number', 'lesson_type')
        .first()
    )
    if row is None or row['lesson_type'] in SYSTEM_LESSON_TYPES:
        return

    position = find_course_position_by_date_and_number(
        row['group_id'], row['lesson_date'], row['lesson_number'],
    )
    repository.set_submission_key(lesson_id, build_submission_key(
        group_id=row['group_id'],
        lesson_date=row['lesson_date'],
        planned_lesson_id=position['id'] if position is not None else None,
    ))


def _assert_no_makeup_done_resolutions(lesson_id: int) -> None:
    """Бросает LessonHasMakeupResolutions, если по пропускам этого урока уже
    проведён доп.урок (makeup_done) ИЛИ пропуск сожжён (burned). Без этого гарда
    DB-level ON DELETE CASCADE (миграция extra_lessons.0007) снёс бы резолюцию
    каскадом, осиротив факт (extra/burned) + Payroll. pending/makeup_scheduled
    (без факта/денег) удалять каскадом безопасно."""
    from apps.extra_lessons.models import BURNED, MAKEUP_DONE, AbsenceResolution
    if AbsenceResolution.objects.filter(
        missed_lesson_id=lesson_id, status__in=[MAKEUP_DONE, BURNED],
    ).exists():
        raise LessonHasMakeupResolutions()


def delete_lesson_full(lesson_id: int) -> bool:
    _assert_not_system_lesson(lesson_id)
    _assert_no_makeup_done_resolutions(lesson_id)
    group_id = (
        Lesson.objects.filter(id=lesson_id).values_list('group_id', flat=True).first()
    )
    deleted = repository.delete_lesson_full(lesson_id)
    # Позиция удалённого урока вернулась в «ждём» (unlink_fact). На неё мог
    # претендовать другой свободный факт с тем же номером — подхватываем его,
    # иначе позиция висит непроведённой при фактически прошедшем занятии (ВДГ18).
    if deleted and group_id is not None:
        link_facts(group_id)
    return deleted


def update_attendance_cell(
    lesson_id: int, student_id: int, present: bool, is_free: bool = False,
    allow_debt: bool = False,
) -> bool:
    """Точечная правка исхода ученика на проведённом уроке. present — был/не был;
    is_free — «бесплатное занятие» (present=true, с ученика не списывается,
    зарплата преподавателю начисляется). is_free при present=false игнорируется.
    Пересчитывает Payroll (см. repository).

    allow_debt — снять запрет по отрицательному балансу («записать в долг»).
    Право на него проверяет вьюха: только superadmin. Урок остаётся ПЛАТНЫМ
    (спишется с баланса) — в отличие от is_free.

    Снятие present (флип в «не был») порождает pending-резолюцию так же, как если
    бы ученика отметили отсутствующим сразу при записи урока — см.
    _autocreate_pending_for_cell.

    Отметка «был» (present=true, в т.ч. free) разбирает заявку на этот пропуск —
    см. apps.extra_lessons.services.release_absence_for_present: нерешённая
    удаляется, назначенный доп.урок блокирует (AttendanceHasScheduledMakeup),
    проведённый/сожжённый — тоже (AttendanceCompensatedElsewhere)."""
    _assert_not_system_lesson(lesson_id)
    # Одна транзакция на правку ячейки и судьбу заявки на пропуск: иначе флип
    # закоммитился бы, а заявка осталась (или наоборот) — ровно так урок ВДГ18
    # учёлся дважды. Вложенный atomic внутри repository становится SAVEPOINT'ом.
    with transaction.atomic():
        if present:
            # Заявку лочим и разбираем ДО флипа. Это же гард двойного учёта
            # (компенсированный/сожжённый пропуск нельзя отметить «был»), но под
            # блокировкой — гонку с параллельным burn()/record() он закрывает.
            from apps.extra_lessons import services as extra_lessons_services
            extra_lessons_services.release_absence_for_present(lesson_id, student_id)
        changed = repository.update_attendance_cell(
            lesson_id, student_id, present, is_free, skip_balance_check=allow_debt)
        if changed and not present:
            _autocreate_pending_for_cell(lesson_id, student_id)
        if changed and present:
            # Ученика отметили присутствовавшим задним числом — заранее назначенный
            # доп.урок за этот урок теряет основание ровно так же, как при обычной
            # записи. Правило одно на оба пути: кто именно проставил «был» —
            # преподаватель в момент урока или админ потом — значения не имеет.
            from apps.extra_lessons import services as extra_lessons_services
            extra_lessons_services.drop_extra_for_present_students(
                lesson_id, [student_id])
        return changed


def _autocreate_pending_for_cell(lesson_id: int, student_id: int) -> None:
    """Поставить пропуск в очередь «ждёт решения» после ретроактивного снятия
    present. Раньше этого не делалось вовсе: record_lesson создавал pending только
    в момент записи урока, поэтому пропуск, проставленный админом задним числом,
    в раздел «Доп.уроки» не попадал никогда.

    Не порождает заявку, если ячейка помечена «неоплачиваемый пропуск»
    (unpaid_skip): это терминальный прощённый исход — ученик урок не посещает
    (перевод / начал не с 1-го), денег ноль, отработка не требуется. Тот же
    инвариант, что в record_lesson (там ученики из skip_ids исключаются из
    absent_student_ids) и в бэкфилле (`la.unpaid_skip = false`).

    Системные уроки (extra/burned) сюда не доходят — вызывающий уже отработал
    _assert_not_system_lesson. Идемпотентно: autocreate_pending делает
    ON CONFLICT DO NOTHING по UNIQUE(missed_lesson, student), поэтому повторный
    флип не плодит дублей и не сбрасывает уже принятое решение (makeup_done/burned)
    обратно в pending.
    """
    row = (
        LessonAttendance.objects
        .filter(lesson_id=lesson_id, student_id=student_id)
        .values('unpaid_skip')
        .first()
    )
    if row is None or row['unpaid_skip']:
        return
    from apps.extra_lessons import services as extra_lessons_services
    extra_lessons_services.autocreate_pending_for_lesson(lesson_id, [student_id])


def set_unpaid_skip(lesson_id: int, student_id: int, value: bool) -> bool:
    """Ставит/снимает исход «неоплачиваемый пропуск» на уроке (в т.ч. проведённом,
    в т.ч. вновь добавленному ученику). См. repository.set_unpaid_skip."""
    _assert_not_system_lesson(lesson_id)
    return repository.set_unpaid_skip(lesson_id, student_id, value)


def set_lesson_skip(group_id: int, student_id: int, lesson_number, value: bool) -> None:
    """Ставит/снимает пометку «неоплачиваемый пропуск» на СЛОТ группы (работает и на
    ещё не проведённом уроке). См. repository.set_lesson_skip (Вариант A)."""
    repository.set_lesson_skip(group_id, student_id, lesson_number, value)


def list_lesson_skips(group_id: int, lesson_number) -> list[int]:
    """student_id, помеченных «неоплачиваемый пропуск» на слот. См. repository."""
    return repository.list_lesson_skips(group_id, lesson_number)
