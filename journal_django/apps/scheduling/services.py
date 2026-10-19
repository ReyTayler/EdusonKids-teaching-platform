"""
Бизнес-логика планирования: сборка календаря плановых занятий за окно.

View → Service (здесь) → occurrences (чистый генератор) + repository (ORM).
Единый источник расписания — group_schedule_slots (не regex по имени группы).
"""
from __future__ import annotations

import datetime

from django.db import transaction

from apps.core.utils.dates import MSK, msk_now
from apps.extra_lessons import repository as extra_lessons_repository
from apps.extra_lessons.models import MAKEUP_DONE as EXTRA_DONE
from apps.scheduling import health, repository
from apps.scheduling.occurrences import (
    CANCELLED, DONE, OVERDUE, PENDING, planned_status,
)

_LABELS = {
    DONE: 'Заполнено',
    OVERDUE: 'Надо заполнить',
    PENDING: 'Пока урока не было',
    CANCELLED: 'Отменён',
}


def _iso(d: datetime.date | None) -> str | None:
    return d.isoformat() if d else None


def _hhmm(t: datetime.time | None) -> str | None:
    return t.strftime('%H:%M') if t else None


def _report_day(d: datetime.date) -> int:
    """Кодировка дня недели Вс=0 (как report.day / JS getDay) из реальной даты."""
    return (d.weekday() + 1) % 7


def _planned_status(r: dict, now_msk: datetime.datetime) -> str:
    """Историческое имя, которым пользуется build_calendar. Логика — в
    occurrences.planned_status: она общая для календаря и для плана группы
    (repository.get_plan), поэтому живёт в чистом модуле, а не здесь."""
    return planned_status(r, now_msk)


def _planned_label(status: str) -> str:
    return _LABELS.get(status, '')


def _planned_occurrence_dict(
    r: dict, students: list[dict], tnames: dict, now_msk: datetime.datetime,
) -> dict:
    """Строка planned_lessons → dict календаря (контракт ответа /api/calendar).

    teacher = имя препода занятия (planned_lesson.teacher), иначе имя учителя группы;
    teacherOverride = имя, если препод занятия ≠ учитель группы (как прежде).
    Перенос показывается через movedFrom (moved_from_date); movedTo не используется.
    """
    is_half = r['lesson_duration_minutes'] == 45
    status = _planned_status(r, now_msk)
    sub_id = r.get('substitute_teacher_id')
    content_teacher_id = r['teacher_id']
    group_teacher_id = r['group_teacher_id']
    effective_id = sub_id or content_teacher_id
    # Пометка «замена/подмена» — только когда реально ведёт НЕ штатный препод группы
    # (замена на того же препода группы бейдж не показывает).
    is_override = effective_id is not None and effective_id != group_teacher_id
    teacher = tnames.get(effective_id) if effective_id else tnames.get(group_teacher_id)
    ln = r['lesson_number']
    return {
        'group': r['group_name'],
        'groupId': r['group_pk'],
        # PlannedLesson.id. Раньше отдавался только admin-мапингом /plan (операции
        # переноса/отмены по id); теперь и teacher-календарём — им LessonForm
        # называет серверу, КАКОЕ занятие отмечается (submitLesson.plannedLessonId).
        # Единственный способ различить два занятия группы в один день (мультислот)
        # и не угадывать позицию по дате.
        'id': r['id'],
        'groupDisplay': r['group_name'],
        'teacher': teacher,
        'teacherOverride': tnames.get(effective_id) if is_override else None,
        'direction': r['direction_name'],
        'color': r['direction_color'],
        'isGroup': not r['is_individual'],
        'durationMinutes': r['lesson_duration_minutes'],
        'vkChat': r['group_vk_chat'] or None,
        'date': _iso(r['scheduled_date']),
        'time': _hhmm(r['scheduled_time']),
        'day': _report_day(r['scheduled_date']),
        'seq': r['seq'],
        'lessonNumber': float(ln) if ln is not None else None,
        'isHalf': is_half,
        'isExtra': r['seq'] is None,
        'status': status,
        'label': _planned_label(status),
        'movedFrom': _iso(r['moved_from_date']),
        'movedTo': None,
        'students': students,
    }


def _extra_lesson_status(status_value: str, scheduled_date, scheduled_time, now_msk) -> str:
    """
    Статус карточки доп.урока → тот же алфавит OccStatus, что и у planned_lessons.

    status_value — значение AbsenceResolution.status (свой словарь
    pending/makeup_scheduled/makeup_done из apps.extra_lessons.models), сравниваем
    с его константой EXTRA_DONE (=makeup_done), а не с DONE (тот — OccStatus для
    ВЫХОДА этой функции, из apps.scheduling.occurrences; строки совпадают случайно,
    это разные словари). Резолюции в статусе pending имеют scheduled_date=NULL и в
    окно календаря не попадают, поэтому сюда доходят только makeup_scheduled/done.
    """
    if status_value == EXTRA_DONE:
        return DONE
    occ_dt = datetime.datetime.combine(scheduled_date, scheduled_time, tzinfo=MSK)
    return OVERDUE if now_msk >= occ_dt else PENDING


def _extra_lesson_occurrence_dict(r: dict, now_msk: datetime.datetime) -> dict:
    """Строка extra_lessons.assignments_in_window → dict календаря (occurrence-
    форма). extraLessonId — дискриминатор для фронта (WeekGrid красит красным,
    OccurrenceMenu подставляет «Провести доп.урок» вместо «Отметить урок»)."""
    status = _extra_lesson_status(r['status'], r['scheduled_date'], r['scheduled_time'], now_msk)
    label = f"Доп.урок · {r['missed_lesson_group_name']}"
    return {
        'group': label,
        'groupId': None,
        # Не позиция курса: у карточки доп.урока своя сущность (extraLessonId ниже)
        # и свой путь отметки — ExtraLessonRecordModal → /api/extra-lessons/:id/record,
        # не submitLesson. Явный None, чтобы id доп.урока не приняли за позицию плана.
        'id': None,
        'groupDisplay': label,
        'teacher': r['teacher_name'],
        'teacherOverride': None,
        'direction': None,
        'color': None,
        # У planned occurrence isGroup — свойство группы (not is_individual);
        # здесь, за неимением группового контекста в самой карточке доп.урока,
        # это счётчик участников ЭТОГО назначения — осознанное отличие:
        # фронт всё равно узнаёт карточку доп.урока по extraLessonId (красный
        # цвет, своё меню), isGroup для неё чисто косметический сигнал.
        'isGroup': len(r['student_names']) > 1,
        'durationMinutes': r['duration_minutes'],
        'vkChat': None,
        'date': _iso(r['scheduled_date']),
        'time': _hhmm(r['scheduled_time']),
        'day': _report_day(r['scheduled_date']),
        'seq': None,
        'lessonNumber': None,
        'isHalf': False,
        'isExtra': False,
        'extraLessonId': r['id'],
        'status': status,
        'label': _planned_label(status),
        'movedFrom': None,
        'movedTo': None,
        'students': [{'name': n} for n in r['student_names']],
    }


def build_calendar(
    window_from: datetime.date,
    window_to: datetime.date,
    teacher_id: int | None = None,
) -> dict:
    """
    Плановые занятия преподавателя за окно — чтение materialize-on-write
    planned_lessons (заменило прежний compute-on-read из слотов + исключений).

    Скоуп — по `planned_lesson.teacher_id` (препод конкретного занятия): смена
    преподавателя занятия автоматически перекидывает урок между календарями.
    Статусы вычисляются на чтении (_planned_status). unscheduled — активные группы
    ЭТОГО преподавателя (по group.teacher_id) без единой плановой строки, с причиной.

    Возвращает {occurrences, unscheduled, window} — контракт сохранён 1:1.
    teacher_id=None → занятия ВСЕХ преподавателей (admin-календарь без фильтра).
    """
    rows = repository.planned_lessons_in_window(window_from, window_to, teacher_id)
    group_ids = sorted({r['group_pk'] for r in rows})
    students = repository.student_names_by_group(group_ids)
    tnames = repository.teacher_names()
    now = msk_now()

    occurrences: list[dict] = []
    for r in rows:
        g_students = [{'name': n} for n in students.get(r['group_pk'], [])]
        occurrences.append(_planned_occurrence_dict(r, g_students, tnames, now))

    for r in extra_lessons_repository.assignments_in_window(teacher_id, window_from, window_to):
        occurrences.append(_extra_lesson_occurrence_dict(r, now))

    occurrences.sort(key=lambda x: (x['date'], x['time'] or ''))
    return {
        'occurrences': occurrences,
        # unscheduled — пер-преподавательский data-quality сигнал (группы без плана).
        # В режиме «все преподаватели» (teacher_id=None) панель была бы по всей школе
        # и шумной — пропускаем, оставляя только реальные занятия.
        'unscheduled': repository.groups_without_plan(teacher_id) if teacher_id is not None else [],
        'window': {'from': window_from.isoformat(), 'to': window_to.isoformat()},
    }


# ---------------------------------------------------------------------------
# Admin-план: оркестрация операций над planned_lessons.
#
# Тонкие функции: валидация — в сериализаторах (view), ORM/логика дат — в
# repository/planner. Здесь — конвертация строк во date/time-объекты и вызов
# repository. RBAC/CSRF — на уровне view.
#
# В журнал ИБ (security_audit_log) эти мутации НЕ пишутся: он для событий
# безопасности (вход/2FA/учётки). Изменения плана покрыты «Журналом изменений»
# (pghistory, правила plan.* в apps/changelog/labels.py).
# ---------------------------------------------------------------------------

def _to_date(value) -> datetime.date | None:
    """'YYYY-MM-DD' → date. None/'' → None. (DateStringField отдаёт валидную строку.)"""
    if not value:
        return None
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(value)


def _to_time(value) -> datetime.time | None:
    """'HH:MM' / 'HH:MM:SS' → time. None/'' → None. (Формат уже проверен сериализатором.)"""
    if not value:
        return None
    if isinstance(value, datetime.time):
        return value
    parts = [int(x) for x in value.split(':')]
    return datetime.time(parts[0], parts[1], parts[2] if len(parts) > 2 else 0)


def _effective_teacher_id(row: dict) -> int | None:
    """Кто реально ведёт занятие: замена на дату, иначе преподаватель контента."""
    return row['substitute_teacher_id'] or row['teacher_id']


def _notify_planned(row: dict, kind: str, *, teacher_ids: list[int],
                    substitute_name: str = '', from_day=None,
                    course_end=None) -> None:
    """
    Поставить уведомление об изменении расписания.

    row — словарь плановой строки из repository.notification_context (date/time —
    объекты, названия группы и направления уже подтянуты одним запросом).
    teacher_ids — кому сообщить: при замене их двое (кто выходит и кого сняли),
    порядок значим, см. ниже.

    Вызывается ВНУТРИ транзакции самой операции (transactional outbox): либо
    перенос и сообщение, либо ни того, ни другого.

    Импорт локальный: apps.scheduling не зависит от apps.notifications на уровне
    модуля — раздел расписания про Telegram не знает, он лишь сообщает факт.
    """
    from apps.notifications import messages as notif_messages
    from apps.notifications import services as notif_services
    from apps.notifications.constants import (
        KIND_LESSON_CANCELLED, KIND_LESSON_MOVED,
        KIND_SUBSTITUTE_ASSIGNED, KIND_SUBSTITUTE_REMOVED,
    )

    day = row['scheduled_date']
    time_str = row['scheduled_time'].strftime('%H:%M') if row['scheduled_time'] else '—'
    base = {'group': row['group_name'], 'direction': row['direction_name']}

    # dict.fromkeys — порядок сохраняется, дубли схлопываются: замена «сам на
    # себя» не должна давать два одинаковых сообщения одному человеку.
    for index, teacher_id in enumerate(dict.fromkeys(tid for tid in teacher_ids if tid)):
        if kind == KIND_LESSON_MOVED:
            text = notif_messages.lesson_moved(
                **base, seq=row.get('seq'), from_day=from_day, to_day=day, time=time_str)
        elif kind == KIND_LESSON_CANCELLED:
            text = notif_messages.lesson_cancelled(
                **base, seq=row.get('seq'), day=day, time=time_str,
                course_end=course_end)
        elif kind == KIND_SUBSTITUTE_ASSIGNED:
            # index == 0 — тот, кого назначили заменой; остальные — кого сняли.
            text = notif_messages.substitute_assigned(
                **base, day=day, time=time_str, substitute_name=substitute_name,
                for_substitute=(index == 0))
        elif kind == KIND_SUBSTITUTE_REMOVED:
            # index == 0 — бывшая замена; остальные — основной преподаватель.
            text = notif_messages.substitute_removed(
                **base, day=day, time=time_str, for_substitute=(index == 0))
        else:  # pragma: no cover — защита от опечатки в вызове
            raise ValueError(f'Неизвестный тип уведомления о расписании: {kind}')

        notif_services.notify_teacher(
            kind=kind, teacher_id=teacher_id, text=text,
            # updated_at в ключе, а не только дата: занятие можно вернуть на
            # дату, где оно уже было (A→B→A), и по одной дате такая операция
            # неотличима от повтора первой — сообщение бы не ушло. Штамп меняется
            # при каждой мутации строки, а внутри одной транзакции неизменен,
            # поэтому настоящие дубли по-прежнему схлопываются.
            dedup_prefix=(
                f'{kind}:{row["id"]}:{day.isoformat()}:{teacher_id}'
                f':{row["updated_at"].isoformat() if row.get("updated_at") else ""}'
            ),
            source_kind='planned_lesson', source_id=row['id'],
            # Дубль в общий чат ставится ОДИН раз на событие, а не по разу на
            # каждого адресата: иначе при замене чат получил бы два одинаковых.
            also_to_group_chat=(index == 0),
        )


def _notify_moved(row: dict, from_day) -> None:
    from apps.notifications.constants import KIND_LESSON_MOVED

    _notify_planned(row, KIND_LESSON_MOVED,
                    teacher_ids=[_effective_teacher_id(row)], from_day=from_day)


def _course_end_date(plan: list[dict]):
    """
    Новая дата последнего занятия курса — уже посчитанный план, без доп.запроса.

    Отмена не выбрасывает урок: он переезжает в конец курса, а хвост
    перенумеровывается. Маркеры отмены (status=cancelled) и доп.занятия
    (seq IS NULL) в длину курса не входят.
    """
    dates = [r['scheduled_date'] for r in plan
             if r['seq'] is not None and r['status'] != CANCELLED and r['scheduled_date']]
    return datetime.date.fromisoformat(max(dates)) if dates else None


def _notify_cancelled(row: dict, course_end=None) -> None:
    from apps.notifications.constants import KIND_LESSON_CANCELLED

    _notify_planned(row, KIND_LESSON_CANCELLED,
                    teacher_ids=[_effective_teacher_id(row)],
                    course_end=course_end)


def _notify_teacher_changed(row: dict, *, new_teacher_id: int,
                            substitute_name: str) -> None:
    """Уведомить ОБЕ стороны разовой замены: и кто выходит, и кого сняли.

    row — состояние строки ДО смены (дату/время замена не двигает, а прежний
    расклад преподавателей нужен, чтобы понять смысл операции):
      - вернули занятие преподавателю контента → замена снята;
      - иначе → назначена (в т.ч. когда одну замену меняют на другую).
    Порядок teacher_ids значим: ПЕРВЫЙ — тот, кто теперь ведёт занятие
    (for_substitute=True в текстах), второй — кого сняли.
    """
    from apps.notifications.constants import (
        KIND_SUBSTITUTE_ASSIGNED, KIND_SUBSTITUTE_REMOVED,
    )

    previous_id = _effective_teacher_id(row)
    if new_teacher_id == previous_id:
        return  # ничего не изменилось — молчим, а не шлём «замена на самого себя»

    if new_teacher_id == row['teacher_id']:
        _notify_planned(row, KIND_SUBSTITUTE_REMOVED,
                        teacher_ids=[previous_id, row['teacher_id']])
    else:
        _notify_planned(row, KIND_SUBSTITUTE_ASSIGNED,
                        teacher_ids=[new_teacher_id, previous_id],
                        substitute_name=substitute_name)


def get_plan(group_id: int) -> list[dict] | None:
    """Плановые строки группы (или None, если группы нет)."""
    return repository.get_plan(group_id)


def autogenerate_plan_on_setup(group_id: int) -> None:
    """Автогенерация плана при первичной настройке группы (Механизм 1).

    Срабатывает ТОЛЬКО первый раз: guard plan_exists(active_only=True) — непустой
    план ИЛИ неактивная/отсутствующая группа → тихий выход. Пока нет старта/слота/
    total — generate_for_group вернёт reason (written=0), план останется пустым,
    следующая правка попробует снова. Идемпотентно (generate_for_group create_only).

    Гонку двух почти одновременных триггеров (старт и слот) глушим на IntegrityError
    (UniqueConstraint(group, seq)): план уже создан конкурентом → выходим.
    Вызывается синхронно из groups.services после того, как repository закоммитил
    группу/слоты (ATOMIC_REQUESTS=False). Прежние параметры source/actor_email
    убраны вместе с событием plan_auto_generate: генерация плана — доменное
    действие, её место в «Журнале изменений», а не в журнале ИБ."""
    from django.db import IntegrityError

    if repository.plan_exists(group_id, active_only=True):
        return
    try:
        repository.generate_for_group(group_id)
    except IntegrityError:
        return


def generate_plan(group_id: int, request) -> list[dict] | None:
    """Идемпотентная генерация плана. None → группы нет."""
    result = repository.generate_for_group(group_id)
    if result is None:
        return None
    return result['plan']


def resize_plan(group_id: int) -> int:
    """Подогнать план под текущую длину курса группы. См. repository.resize_plan."""
    return repository.resize_plan(group_id)


def plan_health(group_id: int) -> dict | None:
    """
    Здоровье плана группы + предпросмотр починки. None → группы нет (404).

    {'group_id', 'name', 'findings': {...health.check_group...},
     'resync': {'blocked_by', 'changes', 'orphan_facts', 'freed'}}

    Только чтение. health.check_group уже посчитал проверки слоя 3 — передаём их
    в repository готовыми, чтобы не гонять те же запросы второй раз.
    """
    report = health.check_group(group_id)
    if report is None:
        return None
    blocked_by = repository.blockers_from_findings(report['findings'])
    return {
        **report,
        'resync': repository.plan_resync_diff(group_id, blocked_by=blocked_by),
    }


def resync_plan(group_id: int, *, expected) -> dict | None:
    """
    Применить починку плана. None → группы нет (404); PlanResyncBlocked → 409.

    Своего pghistory.context здесь НЕТ намеренно: в HTTP-пути контекст открывает
    ChangelogMiddleware, а явная метка перебила бы resolve_operation и правило
    plan.resync в labels.py стало бы мёртвым. Команда (где middleware нет) ставит
    контекст сама. Вся починка идёт одной транзакцией → одним контекстом, поэтому
    откат из журнала возвращает её целиком.
    """
    return repository.resync_plan_facts(group_id, expected=expected)


def reschedule(group_id: int, lesson_id: int, data: dict, request) -> dict | None:
    """Разовый перенос. None → строки нет; ValueError → перенос 'done'.

    Транзакция здесь, а не только внутри repository: уведомление обязано
    коммититься вместе с переносом (outbox), а не отдельным шагом после него.
    """
    new_teacher_id = data.get('new_teacher_id')
    with transaction.atomic():
        # Состояние ДО мутации: строка сейчас переедет, и старой даты («было»)
        # в ней уже не останется — moved_from_date перетирается своим же.
        before = repository.notification_context(lesson_id)
        row = repository.reschedule_lesson(
            group_id,
            lesson_id,
            _to_date(data['new_date']),
            _to_time(data.get('new_time')),
            new_teacher_id,
        )
        if row is None:
            return None
        after = repository.notification_context(lesson_id)
        _notify_moved(after, before['scheduled_date'])
    return row


def change_teacher(group_id: int, lesson_id: int, data: dict, request) -> dict | None:
    """Разовая смена преподавателя одной строки. None → строки нет (404);
    ValueError → строка проведена (view → 409). Дату/время не трогает."""
    new_teacher_id = data['new_teacher_id']
    with transaction.atomic():
        # Расклад преподавателей ДО смены: только он говорит, назначили замену
        # или сняли, и кого именно сняли.
        before = repository.notification_context(lesson_id)
        row = repository.change_teacher(group_id, lesson_id, new_teacher_id)
        if row is None:
            return None
        # row['teacher_name'] — имя ЭФФЕКТИВНОГО преподавателя после смены,
        # то есть новой замены: отдельного запроса за именем не нужно.
        _notify_teacher_changed(before, new_teacher_id=new_teacher_id,
                                substitute_name=row['teacher_name'] or '')
    return row


def change_teacher_permanent(group_id: int, data: dict, request) -> list[dict] | None:
    """Смена преподавателя навсегда (хвост seq>=from_seq). None → группы нет
    (404); ValueError → нет курсовых строк с позиции (view → 400)."""
    plan = repository.change_teacher_permanent(
        group_id,
        from_seq=data['from_seq'],
        new_teacher_id=data['new_teacher_id'],
    )
    if plan is None:
        return None
    return plan


def permanent_change(group_id: int, data: dict, request) -> list[dict] | None:
    """«Изменить расписание» (чистая regen хвоста от effective_from).
    None → группы нет; ValueError → нет курсовых строк с позиции (view → 400).

    effective_from и new_slots — оба обязательны и приходят от клиента (единый
    контракт, см. repository.permanent_change/PlanPermanentChangeSerializer)."""
    new_slots = data['new_slots']
    plan = repository.permanent_change(
        group_id,
        from_seq=data['from_seq'],
        effective_from=_to_date(data['effective_from']),
        new_slots=new_slots,
        new_teacher_id=data.get('new_teacher_id'),
    )
    if plan is None:
        return None
    return plan


def preview_permanent_change(group_id: int, from_seq: int, effective_from) -> list[dict] | None:
    """Read-only превью: какие разовые операции (переносы/замены/отмены) в хвосте
    from_seq будут сброшены при «Изменить расписание» на effective_from. Ничего
    не пишет — см. repository.preview_affected.

    effective_from — строка 'YYYY-MM-DD' (как отдаёт DateStringField.validated_data)
    или date; конвертируем сами через _to_date, тем же способом, что и permanent_change.

    None → группы нет (view → 404), тем же контрактом, что и у реальной мутации
    permanent_change: repository.preview_affected сам по себе не проверяет
    существование группы (просто фильтрует по group_id, вернул бы [] на любой
    несуществующий id) — проверяем явно здесь, чтобы превью несуществующей
    группы не маскировалось под «нет разовых операций»."""
    from apps.groups.models import Group

    if not Group.objects.filter(id=group_id).exists():
        return None
    return repository.preview_affected(
        group_id, date_from=_to_date(effective_from), from_seq=from_seq)


def cancel(group_id: int, lesson_id: int, request) -> list[dict] | None:
    """Отмена (перенос отменённого урока в конец курса + перенумерация).
    from_date выводится из даты занятия lid.

    None → строки нет (404). ValueError → якорь не курсовой/активный (view → 400):
    отмена определена только для курсовых строк в статусе pending/overdue; для
    extra (seq IS NULL) и уже cancelled/moved операция не определена."""
    anchor = repository.get_plan_lesson(group_id, lesson_id)
    if anchor is None:
        return None
    if anchor['seq'] is None:
        raise ValueError('Отмена доступна только для курсового занятия (не доп. занятия).')
    if anchor['status'] in (CANCELLED, DONE):
        raise ValueError(
            'Отмена доступна только для активного занятия '
            '(не отменённого/проведённого).'
        )
    from_date = anchor['scheduled_date']
    # Маркер несёт ЭФФЕКТИВНОГО преподавателя отменённой сессии (замена на дату,
    # если была) — чтобы зачёркнутое занятие попало в календарь того, кто реально
    # должен был вести D, а не преподавателя контента.
    marker_teacher_id = anchor['substitute_teacher_id'] or anchor['teacher_id']
    with transaction.atomic():
        # Контекст для текста берём ДО отмены: строка переедет в конец курса, а
        # хвост перенумеруется — после операции и дата, и номер были бы уже
        # другими. Отдельный запрос от anchor: тому нужен статус, этому —
        # названия группы и направления.
        notify_row = repository.notification_context(lesson_id)
        plan = repository.cancel_lesson(
            group_id, from_date,
            marker_time=anchor['scheduled_time'],
            marker_teacher_id=marker_teacher_id,
            lesson_id=lesson_id,
        )
        _notify_cancelled(notify_row, course_end=_course_end_date(plan))
    return plan
