"""
TeacherSpaService — бизнес-логика teacher SPA.

Слой: View → Service (здесь) → Repository (ORM) для резолва входных данных;
запись самого урока делегируется в apps.lessons.services.record_lesson
(единое ядро — см. docs/superpowers/specs/2026-07-14-unify-lesson-recording-design.md).

Порт routes/teacher.js + services/teacher-repo.js.
"""
from __future__ import annotations

import logging
from typing import Optional

from apps.accounts.repository import get_by_id_with_teacher
from apps.lessons.exceptions import LessonAlreadyRecorded, UnpaidAttendanceBlocked
from apps.lessons.services import record_lesson
from apps.scheduling import repository as scheduling_repository
from apps.teacher_spa import repository
from apps.teacher_spa.calculator import format_msk_date


logger = logging.getLogger(__name__)

# Отказ при незакрытых предыдущих занятиях группы. Закрыть дыру задним числом
# может админ/менеджер через admin SPA — там гарда нет намеренно (escape hatch).
UNFILLED_LESSONS_BLOCKED = (
    'Есть не отмеченные занятия. Обратитесь к менеджеру или '
    'администратору за правкой расписания.'
)


def _resolve_course_position(
    group_id: int, date: str, planned_lesson_id: Optional[int],
) -> Optional[dict]:
    """
    Позиция курса, за которой закрепится записываемый урок. None → позиции нет,
    вызывающий считает номер по-старому (группа без плана, дата вне плана,
    неразличимый мультислот).

    Приоритет:
      1. plannedLessonId с фронта — занятие, по которому кликнули в календаре.
         Единственный способ различить мультислотовую группу (два занятия в один
         день). Принадлежность группе проверяет get_course_position — чужой id
         даёт None, а не доступ к чужой позиции. Дату сверяем здесь же: позицию
         могли перенести на другой день уже ПОСЛЕ того, как фронт её закэшировал
         (вкладка, открытая вчера), — id остаётся валидным и своей группе
         принадлежит, но указывает уже не на сегодняшнее занятие.
      2. Поиск по дате — вход «Мои уроки», где занятия календаря нет, а также
         фолбэк, когда presented id устарел по дате. Разовый перенос увозит
         позицию вместе с номером, поэтому по новой дате находится именно она
         (см. find_course_position_by_date).
    """
    if planned_lesson_id is not None:
        position = scheduling_repository.get_course_position(planned_lesson_id, group_id)
        # Дату сверяем обязательно: принадлежность группе get_course_position
        # проверяет, а дату — нет. Вкладка, открытая вчера, пришлёт id позиции,
        # которую с тех пор перенесли; по ней закрылось бы не то занятие, а
        # настоящая позиция дня осталась бы «не проведена».
        if position is not None and str(position['scheduled_date']) == str(date):
            return position
        # id не подошёл (чужая группа / не курсовая строка / отменена / уехал на
        # другую дату) — не падаем, ищем позицию по фактической дате.
    return scheduling_repository.find_course_position_by_date(group_id, date)


def get_current_teacher(account_id: int) -> Optional[str]:
    """
    Порт currentTeacher() из routes/teacher.js.

    Возвращает teacher_name или None если аккаунт не привязан к преподавателю.
    """
    acc = get_by_id_with_teacher(account_id)
    return acc['teacher_name'] if acc else None


def get_data(account_id: int) -> dict:
    """
    POST /api/getData — группы, где преподаватель владелец.

    Возвращает {'teacher': str, 'data': groupDict} или {'_error': str, '_status': int}.
    """
    acc = get_by_id_with_teacher(account_id)
    teacher = acc['teacher_name'] if acc else None
    if not teacher:  # порт JS if(!teacher): None и пустая строка → не привязан
        return {'_error': 'Аккаунт не привязан к преподавателю', '_status': 403}

    unified = repository.read_own_students(acc['teacher_id'])
    return {'teacher': teacher, 'data': unified['data'].get(teacher, {})}


def get_all_data(account_id: int) -> dict:
    """
    POST /api/getAllData — данные для формы записи урока ЧУЖОЙ группы (замена).

    Несмотря на имя (осталось от routes/teacher.js), это НЕ срез всей школы: только
    группы самого преподавателя и чужие, где ему назначено ещё не проведённое
    занятие. Скоуп — на сервере (repository.read_teacher_students), клиент сузить
    или расширить его не может.

    Возвращает {'teacher': str, 'data': {преподаватель: {группа: ...}}} или
    {'_error': ..., '_status': 403}.
    """
    acc = get_by_id_with_teacher(account_id)
    teacher = acc['teacher_name'] if acc else None
    if not teacher:  # порт JS if(!teacher): None и пустая строка → не привязан
        return {'_error': 'Аккаунт не привязан к преподавателю', '_status': 403}

    unified = repository.read_teacher_students(acc['teacher_id'])
    return {'teacher': teacher, 'data': unified['data']}


def get_group_progress(account_id: int, group_name: str) -> dict:
    """
    Матрица посещаемости группы для страницы группы в teacher SPA.

    Доступ: владелец группы ИЛИ преподаватель, которому назначено хотя бы одно
    НЕотменённое плановое занятие этой группы («Сменить преподавателя» в admin).
    Ответ — тот же контракт, что admin GET /api/admin/groups/:id/progress
    (apps.groups.services.get_group_progress), без дублирования логики.
    """
    from apps.groups import services as groups_services

    acc = get_by_id_with_teacher(account_id)
    teacher_id = acc['teacher_id'] if acc else None
    if not teacher_id:
        return {'_error': 'Аккаунт не привязан к преподавателю', '_status': 403}

    grp = repository.resolve_group_meta(group_name)
    if grp is None:
        return {'_error': 'Группа не найдена', '_status': 404}
    if grp['teacher_id'] != teacher_id and not repository.teacher_has_any_planned_lesson(
        grp['id'], teacher_id,
    ):
        return {'_error': 'Нет доступа к этой группе', '_status': 403}

    data = groups_services.get_group_progress(grp['id'])
    if data is None:
        return {'_error': 'Группа не найдена', '_status': 404}
    return data


def submit_lesson(account_id: int, validated: dict) -> dict:
    """
    Порт POST /api/submitLesson из routes/teacher.js (lines 38-139).

    Резолвит группу/учителя/тип урока/номер урока сам (teacher-специфичная
    логика — замена/перенос выводятся из planned_lessons, half-lesson из
    lesson_duration_minutes). Сама запись (Lesson+attendance+счётчики+Payroll+
    link_facts+балансовая проверка) — apps.lessons.services.record_lesson.

    Возвращает:
      {'success': True, 'payment': int, 'penalty': int, 'lessonNumber': float|int}
      {'success': False, 'error': str}   — ошибки без статуса 4xx (как Express)
      {'_error': str, '_status': 403}    — аккаунт не привязан к преподу
    """
    group = validated['group']
    date = validated['date']
    record_url = validated.get('recordUrl') or None
    students = validated['students']

    # Дата занятия жёстко зафиксирована на фронте (LessonForm не даёт её менять) —
    # это подстраховка от гонки состояний (устаревший кэш календаря) и прямых
    # запросов к API. Сравнение только по дню (строки 'YYYY-MM-DD' сравнимы
    # лексикографически), без учёта времени начала урока.
    if date > format_msk_date():
        return {
            'success': False,
            'error': 'Урок ещё не наступил — отметить его можно только в день занятия или позже.',
        }

    # 1. Auth — препод из сессии
    teacher = get_current_teacher(account_id)
    if not teacher:  # порт JS if(!teacher): None и пустая строка → не привязан
        return {'_error': 'Аккаунт не привязан к преподавателю', '_status': 403}

    # 2. Актуальное состояние ТОЛЬКО ЭТОЙ группы. Группа ищется по имени среди
    #    ВСЕХ преподавателей: своя → обычный урок; чужая допустима только если
    #    занятие назначено этому преподавателю админом (проверка в шаге 3).
    #
    #    Выборка по одной группе, а не по школе: раньше на пути ЗАПИСИ урока —
    #    самого критичного действия — читались все активные membership школы плюс
    #    баланс по каждому ученику, при том что sync-воркеров на всю школу единицы
    #    и три одновременные отправки занимали сервер целиком (инцидент ПГ215).
    unified = repository.read_group_students(group)
    own_groups = unified['data'].get(teacher) or {}
    if group in own_groups:
        owner_name = teacher
    else:
        owner_name = next((t for t, gs in unified['data'].items() if group in gs), None)
    if owner_name is None:
        return {'success': False, 'error': 'Группа не найдена'}

    group_data = unified['data'][owner_name][group]

    # 3. Resolve IDs (submitter + группа + продолжительность). Делаем ДО расчётов:
    #    half-lesson теперь определяется СТРУКТУРНО (lesson_duration_minutes == 45),
    #    а не regex '/45\s*минут/' по имени группы (Ф4 — вывод regex из hot-path).
    ids = repository.resolve_ids(teacher, group)
    if not ids or not ids.get('submitter_teacher_id'):
        return {'success': False, 'error': 'Группа/преподаватель не найдены в БД'}

    lesson_teacher_id = ids['submitter_teacher_id']
    # Замена выводится СЕРВЕРОМ, а не флагом клиента: группа принадлежит другому
    # преподавателю. Право отметить чужой урок даёт ТОЛЬКО плановое занятие этой
    # группы на эту дату с teacher_id отправителя — его назначает админ через
    # «Сменить преподавателя» (клиентские isSubstitution/originalTeacher — 400
    # в SubmitLessonSerializer).
    is_substitution = (
        ids['group_owner_id'] is not None and ids['group_owner_id'] != lesson_teacher_id
    )
    if is_substitution and not repository.has_assigned_planned_lesson(
        ids['group_id'], date, lesson_teacher_id,
    ):
        return {'_error': 'Занятие этой группы вам не назначено', '_status': 403}
    original_teacher_id = ids['group_owner_id'] if is_substitution else None

    # Блокер порядка заполнения: пока у группы есть незакрытое занятие с плановой
    # датой РАНЬШЕ отмечаемой, писать урок нельзя. Иначе факт (дата = сегодня)
    # съедает плановую строку того раннего дня — link_facts матчит по номеру, а
    # номер берётся из прогресса учеников — план и факт разъезжаются МЕЖДУ ДНЯМИ.
    # По датам, не по часам: два занятия одного дня друг друга не блокируют.
    if scheduling_repository.has_unfilled_before(ids['group_id'], date):
        return {'success': False, 'error': UNFILLED_LESSONS_BLOCKED}

    # 4. lesson_number — из ПОЗИЦИИ КУРСА (planned_lessons), а не из прогресса учеников.
    #
    #    Раньше номер выводился как max(lessonsDone) + step. Это конфляция двух
    #    разных величин: lessons_done считает посещаемость КОНКРЕТНОГО ученика,
    #    а lesson_number — позицию занятия в курсе. Пока они совпадали, разницы
    #    не было; стоило lessons_done уехать (например, осиротевший пропуск
    #    «сожгли» — ученику +1 без занятия), факт получал номер следующей позиции,
    #    садился на неё, а своя позиция оставалась «не проведена». Плюс номер,
    #    выведенный из состояния, которое сама запись инкрементирует, делал
    #    lessons_natural_key бесполезным: каждый повтор отправки получал НОВЫЙ
    #    номер и создавал ещё один урок вместо конфликта (инцидент ПГ215 31.07.2026).
    #
    #    Позиция — источник правды: её номер стабилен между повторами, поэтому
    #    повторная отправка упирается в уже занятую позицию (LessonAlreadyRecorded).
    is_half = ids['lesson_duration_minutes'] == 45
    step = 0.5 if is_half else 1

    position = _resolve_course_position(
        ids['group_id'], date, validated.get('plannedLessonId'),
    )
    if position is not None:
        # Занятая позиция = урок за это занятие уже записан. Проверяем ЗДЕСЬ ради
        # внятного отказа без открытия транзакции; авторитетно то же самое решает
        # record_lesson под select_for_update (гонка двух одновременных повторов).
        if position['fact_lesson_id'] is not None:
            raise LessonAlreadyRecorded(
                position['lesson_number'], position['scheduled_date'],
            )
        lesson_num = float(position['lesson_number'])
    else:
        # Фолбэк — группы без плана и записи на дату вне плана: прежний расчёт.
        group_students = group_data.get('students', [])
        done = max((s.get('lessonsDone') or 0 for s in group_students), default=0)
        # lessonNum = Math.round((done + step) * 10) / 10
        # (done + step) * 10 всегда целое (step кратен 0.5), поэтому round — no-op.
        raw = (done + step) * 10
        lesson_num = round(raw) / 10

    # 5. Mapping student_name → student_id (группа знает имена, admin шлёт id напрямую —
    #    поэтому это остаётся teacher_spa-специфичным шагом, не частью record_lesson).
    #    Имя у Eduson не уникально (тёзки в одной группе): словарь {имя: строка}
    #    молча схлопывал двух «Миш» в одного и писал посещение не тому ребёнку.
    #    Поэтому сопоставляем по id; имя — только фолбэк для клиента без id и
    #    только когда оно однозначно в группе, иначе — отказ до записи.
    stud_rows = repository.resolve_students(ids['group_id'])
    by_id = {r['student_id']: r for r in stud_rows}
    by_name: dict[str, list[dict]] = {}
    for r in stud_rows:
        by_name.setdefault(r['full_name'], []).append(r)

    attendance: list[dict] = []
    for s in students:
        if s.get('id') is not None:
            meta = by_id.get(s['id'])
            if meta is None:
                # id не среди активных членств группы: ученика сняли или перевели,
                # пока форма была открыта. Молча отбросить — значит записать урок
                # без него и без сигнала; отказываем до записи.
                return {
                    'success': False,
                    'error': 'Список учеников изменился — обновите страницу.',
                    'code': 'roster_stale',
                }
        else:
            candidates = by_name.get(s['name'], [])
            if len(candidates) > 1:
                return {
                    'success': False,
                    'error': f'В группе несколько учеников с именем «{s["name"]}» — '
                             'обновите страницу и отметьте урок заново.',
                    'code': 'ambiguous_name',
                }
            meta = candidates[0] if candidates else None
        if meta is None:
            # Имя ученика (ПДн) в лог не пишем — для разбора хватает группы.
            logger.warning(
                'submitLesson: ученик без id не найден по имени среди активных '
                'членств group_id=%s', ids['group_id'],
            )
            continue
        attendance.append({
            'student_id': meta['student_id'],
            'present': bool(s['present']),
            'is_free': bool(s.get('is_free', False)),
        })

    # 5b. Блокер «пустого» урока: нельзя записать урок, на котором не отмечен ни
    #     один присутствующий ученик (все «Не пришёл» / пустой список). Урок без
    #     присутствия смысла не несёт (0 посещаемости, 0 потребления, 0 выплаты)
    #     и чаще всего означает ошибочное сохранение. Фронт тоже гейтит кнопку
    #     (LessonForm), это — авторитетная проверка на бэке.
    if not any(a['present'] for a in attendance):
        return {
            'success': False,
            'error': 'Нельзя сохранить урок без присутствующих учеников — '
                     'отметьте хотя бы одного пришедшего.',
        }

    # 6. subLabel — тип урока. Выводится СЕРВЕРОМ из плана (клиентский lessonType
    #    не принимается): замена — чужая группа (шаг 3); перенос — плановая строка
    #    этой группы/даты/препода перенесена НА эту дату (moved_from_date задан).
    if is_substitution:
        sub_label = 'substitution'
    elif repository.planned_lesson_is_moved(ids['group_id'], date, lesson_teacher_id):
        sub_label = 'reschedule'
    else:
        sub_label = 'regular'

    # 7. Запись — единое ядро (Lesson+attendance+счётчики+Payroll+link_facts+
    #    балансовая проверка+renewal-sync). Баланс — та же проверка, что и в
    #    admin SPA (UnpaidAttendanceBlocked), просто завёрнута в контракт teacher SPA.
    try:
        result = record_lesson(
            lesson_date=date,
            teacher_id=lesson_teacher_id,
            group_id=ids['group_id'],
            original_teacher_id=original_teacher_id,
            lesson_number=lesson_num,
            lesson_duration_minutes=ids['lesson_duration_minutes'],
            lesson_type=sub_label,
            record_url=record_url,
            submitted_by_token=f'acct:{account_id}',
            submit_date=format_msk_date(),
            attendance=attendance,
            planned_lesson_id=position['id'] if position is not None else None,
        )
    except UnpaidAttendanceBlocked as e:
        return {'success': False, 'error': str(e)}

    # lessonNumber: если целое → int, иначе float (JS-совместимость)
    lesson_number_out = int(lesson_num) if lesson_num == int(lesson_num) else lesson_num

    return {
        'success': True,
        'payment': result['payment'],
        'penalty': result['penalty'],
        'lessonNumber': lesson_number_out,
    }
