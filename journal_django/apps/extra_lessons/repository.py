"""ExtraLessonsRepository — единственное место ORM-доступа раздела (пер-ученик AbsenceResolution)."""
from __future__ import annotations

from typing import Optional

from django.db import transaction
from django.db.models import Case, F, IntegerField, Value, When
from django.db.models.functions import Coalesce

from apps.extra_lessons.models import (
    BURNED, EXTRA, MAKEUP, MAKEUP_DONE, MAKEUP_SCHEDULED, PENDING, AbsenceResolution,
)
from apps.lessons.models import Lesson, LessonAttendance


def autocreate_pending(missed_lesson_id, student_ids) -> int:
    """Идемпотентно создать pending-резолюции по списку отсутствовавших.
    bulk_create(ignore_conflicts=True) → INSERT ... ON CONFLICT DO NOTHING по
    UNIQUE(missed_lesson, student). Возвращает len(student_ids) (верхняя оценка;
    тесты проверяют факт создания выборкой). Пустой список — no-op (return 0).

    Через ORM, а не raw executemany: последний несовместим с инъекцией
    pghistory-контекста под HTTP-запросом (не все аргументы форматируются)."""
    if not student_ids:
        return 0
    AbsenceResolution.objects.bulk_create(
        [AbsenceResolution(missed_lesson_id=missed_lesson_id, student_id=sid, status=PENDING)
         for sid in student_ids],
        ignore_conflicts=True,
    )
    return len(student_ids)


def adopt_extra_for_lesson(missed_lesson_id, student_ids) -> set:
    """Привязать к только что записанному пропуску ЗАРАНЕЕ назначенный доп.урок
    «сверх курса» — вместо того, чтобы заводить по тому же пропуску вторую,
    независимую запись.

    Менеджер назначает доп.урок за урок №N, которого ЕЩЁ НЕ БЫЛО (ученик
    предупредил о пропуске заранее). Реального урока №N в этот момент нет,
    поэтому create_extra_assignment уводит назначение в kind='extra' с
    missed_lesson=NULL: номер N остаётся только в target_lesson_number, как
    подпись, связи с будущим уроком нет. Когда урок №N наконец проводится и
    ученика на нём не оказывается, autocreate_pending завёл бы ВТОРУЮ запись —
    pending по тому же самому пропуску. Две записи друг о друге не знают:
    доп.урок спишет урок при проведении, а осиротевший pending закрыть можно
    только ЕЩЁ одним списанием (burn или второй доп.урок — действия «закрыть без
    списания» в разделе нет), то есть ученик платит за один пропуск дважды. Плюс
    ячейка пропуска остаётся красной: compensated_map в apps.groups.repository
    ищет резолюции по missed_lesson_id, а у extra он пуст.

    Усыновление переводит найденный extra в обычный makeup ЭТОГО пропуска:
    missed_lesson проставляется, kind='makeup', group и target_lesson_number
    обнуляются (у makeup группа и номер берутся из самого пропуска — см. record()).
    Статус, преподаватель, дата, время и факт сохраняются: для менеджера и
    преподавателя назначение не меняется, уведомлять не о чем. Побочный эффект,
    который и требуется: record() для makeup берёт длительность из пропущенного
    урока, поэтому вес списания перестаёт зависеть от того, что менеджер выбрал
    руками при назначении.

    Усыновляется и УЖЕ ПРОВЕДЁННЫЙ доп.урок (makeup_done): его могли провести
    раньше пропущенного занятия. Тогда списание так и остаётся одно, а пропуск
    сразу помечается компенсированным. Других статусов у extra не бывает:
    создаётся он сразу makeup_scheduled (create_extra_direct), сжечь его нельзя
    (burn требует pending), а откат факта удаляет его целиком (delete_fact).

    Возвращает student_id, для которых доп.урок усыновлён, — вызывающий исключает
    их из autocreate_pending.
    """
    if not student_ids:
        return set()
    lesson = (
        Lesson.objects
        .filter(id=missed_lesson_id)
        .values('group_id', 'lesson_number')
        .first()
    )
    if lesson is None:
        return set()

    with transaction.atomic():
        # Ученики, у которых по ЭТОМУ пропуску резолюция уже есть: усыновление дало
        # бы вторую строку на (missed_lesson, student) — нарушение
        # absence_resolutions_missed_student_key. Штатный путь сюда не приводит
        # (создать extra, когда урок уже существует, нельзя — create_extra_assignment
        # тогда уходит в makeup), но проверка дешёвая и оставляет любой неучтённый
        # ретро-сценарий без 500.
        taken = set(
            AbsenceResolution.objects
            .filter(missed_lesson_id=missed_lesson_id, student_id__in=student_ids)
            .values_list('student_id', flat=True)
        )
        # select_for_update — против гонки с параллельным проведением/отменой того
        # же доп.урока: без блокировки record() мог бы закрыть резолюцию как extra
        # уже после того, как мы прочитали её кандидатом.
        candidates = list(
            AbsenceResolution.objects
            .select_for_update()
            .filter(
                kind=EXTRA,
                group_id=lesson['group_id'],
                student_id__in=student_ids,
                target_lesson_number=lesson['lesson_number'],
                status__in=(MAKEUP_SCHEDULED, MAKEUP_DONE),
            )
            .order_by('student_id', 'id')
            .values('id', 'student_id')
        )

        adopted = set()
        for row in candidates:
            sid = row['student_id']
            if sid in taken or sid in adopted:
                # Либо резолюция по пропуску уже есть, либо один доп.урок этого
                # ученика уже усыновлён: остальные остаются «сверх курса». Дубли
                # назначения раздел допускает — UNIQUE(missed_lesson, student) их
                # не ловит, потому что NULL в Postgres не конфликтуют.
                continue
            AbsenceResolution.objects.filter(id=row['id']).update(
                missed_lesson_id=missed_lesson_id,
                kind=MAKEUP,
                group_id=None,
                target_lesson_number=None,
            )
            adopted.add(sid)
        return adopted

def find_open_extra_for_lesson(lesson_id, student_ids) -> list:
    """id доп.уроков «сверх курса», назначенных ЗА ЭТОТ урок ученикам из списка и
    ещё НЕ проведённых — кандидаты на удаление, когда ученик оказался на занятии.

    Зеркало adopt_extra_for_lesson: тот же поиск «назначение за этот номер в этой
    группе», но для обратного исхода. Ученик предупредил о пропуске, менеджер
    заранее назначил отработку, а ученик всё-таки пришёл — основание для доп.урока
    исчезло. В системе это уже запрещённое состояние: ручное назначение за
    посещённый урок отбивается гардом StudentWasPresent (см.
    services._assign_makeup_for_lesson). Наш случай просто просачивается мимо
    него, потому что назначение сделано РАНЬШЕ, чем урок состоялся.

    Только status='makeup_scheduled'. Проведённый доп.урок (makeup_done) не
    трогаем ни при каких условиях: за ним стоит факт-урок и Payroll, удаление
    резолюции осиротило бы их (ON DELETE SET NULL на fact_lesson). Такой доп.урок
    состоялся как занятие сверх курса — ученик его посетил и оплатил, отменять
    задним числом нечего.

    Дату назначения НЕ смотрим: разграничивает именно статус. Назначение с уже
    прошедшей датой, но без факта — просроченное (висит во вкладке «Заполнить» и
    в вечернем дайджесте), и его основание исчезло ровно так же.
    """
    if not student_ids:
        return []
    lesson = (
        Lesson.objects
        .filter(id=lesson_id)
        .values('group_id', 'lesson_number')
        .first()
    )
    if lesson is None:
        return []
    return list(
        AbsenceResolution.objects
        .select_for_update()
        .filter(
            kind=EXTRA,
            group_id=lesson['group_id'],
            student_id__in=student_ids,
            target_lesson_number=lesson['lesson_number'],
            status=MAKEUP_SCHEDULED,
        )
        .order_by('id')
        .values_list('id', flat=True)
    )

def _full_values(qs):
    return qs.values(
        'id', 'missed_lesson_id', 'student_id', 'assigned_teacher_id', 'scheduled_date',
        'scheduled_time', 'duration_minutes', 'status', 'fact_lesson_id',
        'kind', 'group_id', 'target_lesson_number',
        student_name=F('student__full_name'),
        teacher_name=F('assigned_teacher__name'),
        missed_lesson_group_id=F('missed_lesson__group_id'),
        missed_lesson_group_name=F('missed_lesson__group__name'),
        missed_lesson_date=F('missed_lesson__lesson_date'),
        missed_lesson_number=F('missed_lesson__lesson_number'),
        # Унифицированное имя группы для отображения: makeup → группа пропуска,
        # extra → собственная группа резолюции.
        resolution_group_name=Coalesce(F('group__name'), F('missed_lesson__group__name')))


def get_resolution_full(resolution_id) -> Optional[dict]:
    return _full_values(AbsenceResolution.objects.filter(id=resolution_id)).first()


def notification_context(resolution_id) -> Optional[dict]:
    """Данные одной резолюции для ТЕКСТА уведомления преподавателю.

    Отдельная выборка, а не расширение _full_values: последний обслуживает и
    постраничный список раздела (list_resolutions), где лишний JOIN к directions
    на каждую строку не нужен, а лишний ключ уезжал бы в ответ API.

    Имя группы и направления — Coalesce: у makeup они берутся из пропущенного
    урока, у extra (сверх курса, missed_lesson=NULL) — из собственной группы
    резолюции.
    """
    return (
        AbsenceResolution.objects.filter(id=resolution_id).values(
            'id', 'kind', 'status', 'assigned_teacher_id', 'scheduled_date', 'scheduled_time',
            student_name=F('student__full_name'),
            teacher_name=F('assigned_teacher__name'),
            group_name=Coalesce(F('group__name'), F('missed_lesson__group__name')),
            direction_name=Coalesce(
                F('group__direction__name'), F('missed_lesson__group__direction__name')),
        ).first()
    )


def lock_for_record(resolution_id) -> Optional[dict]:
    """SELECT ... FOR UPDATE внутри atomic() — авторитетная проверка статуса перед записью.

    of=('self',): missed_lesson теперь nullable (kind='extra' → NULL), из-за чего
    обращение к missed_lesson__group_id даёт LEFT OUTER JOIN, а FOR UPDATE по
    nullable-стороне outer join Postgres запрещает. Лочим только строку резолюции
    (не joined-таблицу lessons) — этого достаточно (сериализуем правки самой резолюции)."""
    return (AbsenceResolution.objects.select_for_update(of=('self',)).filter(id=resolution_id)
            .values('id', 'status', 'assigned_teacher_id', 'missed_lesson_id', 'student_id',
                    'scheduled_date', 'duration_minutes', 'kind', 'group_id',
                    'target_lesson_number',
                    missed_lesson_group_id=F('missed_lesson__group_id')).first())


def lock_for_delete(resolution_id) -> Optional[dict]:
    return (AbsenceResolution.objects.select_for_update().filter(id=resolution_id)
            .values('id', 'status', 'missed_lesson_id', 'student_id', 'fact_lesson_id').first())


def lock_for_assign(missed_lesson_id, student_id) -> Optional[dict]:
    """SELECT ... FOR UPDATE резолюции пропуска (урок × ученик). Два потребителя:
    назначение доп.урока (перевод в makeup_scheduled; None → строки нет, сервис
    создаст напрямую create_scheduled_direct) и отметка «был» задним числом
    (services.release_absence_for_present)."""
    return (AbsenceResolution.objects.select_for_update()
            .filter(missed_lesson_id=missed_lesson_id, student_id=student_id)
            .values('id', 'status').first())


def find_group_regular_lesson(group_id, lesson_number):
    """id проведённого ОБЫЧНОГО урока группы на слоте lesson_number (не extra/burned)
    — для роутинга ручного доп.урока: если реальный урок №N есть, ручное назначение
    идёт как makeup, привязанный к нему. None → такого урока нет (→ extra сверх курса)."""
    from decimal import Decimal
    return (Lesson.objects
            .filter(group_id=group_id, lesson_number=Decimal(str(lesson_number)))
            .exclude(lesson_type__in=('extra', 'burned'))
            .values_list('id', flat=True).first())


def students_present_on(lesson_id, student_ids) -> set[int]:
    """student_id, отмеченные ПРИСУТСТВОВАВШИМИ (present=true) на уроке — гард
    ручного доп.урока: за посещённый урок доп.урок ставить нельзя."""
    return set(
        LessonAttendance.objects
        .filter(lesson_id=lesson_id, student_id__in=student_ids, present=True)
        .values_list('student_id', flat=True)
    )


def students_not_absent(missed_lesson_id, student_ids) -> list[int]:
    absent = set(LessonAttendance.objects.filter(
        lesson_id=missed_lesson_id, student_id__in=student_ids, present=False
    ).values_list('student_id', flat=True))
    return [sid for sid in student_ids if sid not in absent]


def assign_pending(resolution_id, *, assigned_teacher_id, scheduled_date, scheduled_time,
                   duration_minutes) -> None:
    """pending → makeup_scheduled с параметрами доп.урока."""
    AbsenceResolution.objects.filter(id=resolution_id).update(
        status=MAKEUP_SCHEDULED, assigned_teacher_id=assigned_teacher_id,
        scheduled_date=scheduled_date, scheduled_time=scheduled_time,
        duration_minutes=duration_minutes)


def create_scheduled_direct(*, missed_lesson_id, student_id, assigned_teacher_id,
                            scheduled_date, scheduled_time, duration_minutes) -> int:
    """Edge: pending-строки нет (пропуск до релиза) → создать сразу makeup_scheduled."""
    obj = AbsenceResolution.objects.create(
        missed_lesson_id=missed_lesson_id, student_id=student_id,
        assigned_teacher_id=assigned_teacher_id, status=MAKEUP_SCHEDULED,
        scheduled_date=scheduled_date, scheduled_time=scheduled_time,
        duration_minutes=duration_minutes)
    return obj.id


def create_extra_direct(*, group_id, student_id, assigned_teacher_id, scheduled_date,
                        scheduled_time, duration_minutes, target_lesson_number) -> int:
    """Назначить доп.урок СВЕРХ курса (kind='extra', без пропуска): создаётся сразу
    makeup_scheduled. Группа — из group_id (не из пропуска), «за какой урок» —
    target_lesson_number (может быть None → record() возьмёт следующую позицию)."""
    obj = AbsenceResolution.objects.create(
        kind=EXTRA, missed_lesson_id=None, group_id=group_id, student_id=student_id,
        assigned_teacher_id=assigned_teacher_id, status=MAKEUP_SCHEDULED,
        scheduled_date=scheduled_date, scheduled_time=scheduled_time,
        duration_minutes=duration_minutes, target_lesson_number=target_lesson_number)
    return obj.id


def delete_resolution(resolution_id) -> None:
    """Полностью удалить резолюцию — для extra (доп.урок сверх курса) отмена/откат
    означают отмену самого назначения (в отличие от makeup, где есть pending-пропуск,
    к которому возвращаемся)."""
    AbsenceResolution.objects.filter(id=resolution_id).delete()


def back_to_pending(resolution_id) -> None:
    """Отмена назначения / откат факта → pending. Сбрасывает параметры и факт."""
    AbsenceResolution.objects.filter(id=resolution_id).update(
        status=PENDING, assigned_teacher_id=None, scheduled_date=None,
        scheduled_time=None, duration_minutes=None, fact_lesson_id=None)


def mark_makeup_done(resolution_id, *, fact_lesson_id) -> None:
    AbsenceResolution.objects.filter(id=resolution_id).update(
        status=MAKEUP_DONE, fact_lesson_id=fact_lesson_id)


def mark_burned(resolution_id, *, fact_lesson_id) -> None:
    """pending → burned с привязкой к созданному burned-факту (Lesson)."""
    AbsenceResolution.objects.filter(id=resolution_id).update(
        status=BURNED, fact_lesson_id=fact_lesson_id)


def pending_count() -> int:
    """Число необработанных пропусков (status=pending) — для бейджа в сайдбаре."""
    return AbsenceResolution.objects.filter(status=PENDING).count()


def has_active_resolution(missed_lesson_id, student_id) -> bool:
    """Уже назначено / проведено / сожжено? (pending НЕ считается — его как раз
    разрешают). Guard от повторного назначения или сжигания уже закрытого пропуска."""
    return (AbsenceResolution.objects
            .filter(missed_lesson_id=missed_lesson_id, student_id=student_id,
                    status__in=[MAKEUP_SCHEDULED, MAKEUP_DONE, BURNED]).exists())


def has_scheduled_for_student_in_group(student_id, group_id) -> bool:
    """Есть ли у ученика НАЗНАЧЕННЫЙ (не проведённый) доп.урок в этой группе? Гейт
    снятия членства: makeup_scheduled нельзя удалять молча (за ним преподаватель +
    дата) — операция снятия членства блокируется до его разбора. Покрывает и makeup
    (группа пропуска), и extra сверх курса (собственная группа резолюции)."""
    from django.db.models import Q
    return (AbsenceResolution.objects
            .filter(Q(missed_lesson__group_id=group_id) | Q(group_id=group_id),
                    student_id=student_id, status=MAKEUP_SCHEDULED)
            .exists())


def delete_pending_for_student_in_group(student_id, group_id) -> int:
    """Снятие членства в группе: удалить pending («Ждёт решения») резолюции ученика
    по пропускам ИМЕННО этой группы. makeup_scheduled/makeup_done/burned не трогаем
    (первый блокирует снятие раньше, у остальных есть факт/деньги). Возвращает число."""
    qs = AbsenceResolution.objects.filter(
        student_id=student_id, missed_lesson__group_id=group_id, status=PENDING)
    n = qs.count()
    qs.delete()
    return n


# Порядок по умолчанию — очередь разбора: «Ждёт решения» сверху, внутри блока
# свежие заявки первыми. Это не колонка таблицы (парой «поле + направление» его
# не выразить), поэтому у него собственное имя. Любое значение sort_by вне
# _SORTABLE — включая пустое и мусорное — означает этот порядок.
QUEUE_ORDER = 'pending_first'

# Колонки, по которым список сортируется явным щелчком по заголовку.
_SORTABLE = {'scheduled_date': 'scheduled_date', 'status': 'status',
             'teacher_name': 'assigned_teacher__name', 'student_name': 'student__full_name'}


def _order_fields(sort_by: str, sort_dir: str) -> tuple:
    """
    Во что превращается запрошенная сортировка.

    Явная сортировка по колонке упорядочивает ВЕСЬ список: группировка по
    статусу при этом снимается намеренно — иначе «сортировать по ученику»
    означало бы «отсортировать два блока по отдельности», чего от таблицы
    никто не ждёт.

    Порядок очереди не полагается на то, что у pending scheduled_date пуст
    (раньше ожидающие всплывали наверх именно так: NULL при DESC идут первыми
    в Postgres). Это разваливалось от одного щелчка по «Дате доп.урока» и
    вообще не выражало намерения — теперь статус в сортировке назван прямо.
    """
    if sort_by in _SORTABLE:
        return (('' if sort_dir == 'asc' else '-') + _SORTABLE[sort_by], '-id')
    awaiting_first = Case(
        When(status=PENDING, then=Value(0)), default=Value(1),
        output_field=IntegerField(),
    )
    # '-id' — тай-брейк: created_at у пачки резолюций, созданных одной записью
    # урока, совпадает до микросекунд, а страницы без полного порядка
    # «переставляются» между запросами и строки задваиваются/пропадают.
    return (awaiting_first, '-created_at', '-id')


def list_resolutions(page=1, page_size=50, sort_by=QUEUE_ORDER, sort_dir='desc', filters=None) -> dict:
    filters = filters or {}
    qs = AbsenceResolution.objects.all()
    if filters.get('status'):
        qs = qs.filter(status=filters['status'])
    if filters.get('teacher_id'):
        qs = qs.filter(assigned_teacher_id=int(filters['teacher_id']))
    if filters.get('student_name'):
        qs = qs.filter(student__full_name__icontains=filters['student_name'])
    if filters.get('missed_lesson_group_name'):
        qs = qs.filter(missed_lesson__group__name__icontains=filters['missed_lesson_group_name'])
    total = qs.count()
    offset = max(0, (page - 1) * page_size)
    ordering = _order_fields(sort_by, sort_dir)
    rows = list(_full_values(qs.order_by(*ordering)[offset:offset + page_size]))
    return {'rows': rows, 'total': total, 'page': page, 'page_size': page_size}


def unfilled_extra_lessons(today, teacher_id=None) -> list[dict]:
    """Незаполненные доп.уроки (отработки) по школе с датой <= today — источник
    вкладки «Заполнить». makeup_scheduled без факта; pending имеет scheduled_date
    NULL и не попадает, makeup_done имеет проставленный fact_lesson. Overdue-порог
    по времени досчитывает вызывающий (fill_service). group_id/group_name — группа
    пропущенного урока (для перехода). Опц. скоуп по назначенному преподавателю."""
    qs = AbsenceResolution.objects.filter(
        status=MAKEUP_SCHEDULED,
        fact_lesson__isnull=True,
        scheduled_date__lte=today,
    )
    if teacher_id is not None:
        qs = qs.filter(assigned_teacher_id=teacher_id)
    # Группа резолюции: для makeup — группа пропущенного урока; для extra (сверх
    # курса, missed_lesson=NULL) — из поля `group`. Coalesce покрывает оба.
    # Алиасы _grp_* (не group_id/group_name) — иначе конфликт с полем group_id.
    rows = list(
        qs.order_by('scheduled_date', 'scheduled_time').values(
            'id', 'scheduled_date', 'scheduled_time', 'assigned_teacher_id',
            _grp_id=Coalesce(F('group'), F('missed_lesson__group_id')),
            _grp_name=Coalesce(F('group__name'), F('missed_lesson__group__name')),
        )
    )
    for r in rows:
        r['group_id'] = r.pop('_grp_id')
        r['group_name'] = r.pop('_grp_name')
    return rows


def scheduled_on(target_date) -> list[dict]:
    """
    Назначенные доп.уроки на конкретную дату — по всей школе, одним запросом.

    Источник утреннего дайджеста (100 преподавателей → запрос на каждого
    недопустим на VPS 2 CPU/2 ГБ). Фильтр по статусу: только назначенные и ещё
    не проведённые (makeup_scheduled); проведённые в списке дня уже не нужны.
    group_pk — Coalesce(group_id, missed_lesson.group_id): у makeup группа
    берётся из пропущенного урока, у extra (сверх курса) — из своего поля.
    Имена группы/направления батчем подтягивает вызывающий (см. scheduling/services.py).
    """
    return list(
        AbsenceResolution.objects
        .filter(status=MAKEUP_SCHEDULED, scheduled_date=target_date)
        .values(
            'id', 'assigned_teacher_id', 'scheduled_date', 'scheduled_time',
            'student_id', 'kind',
            student_name=F('student__full_name'),
            group_pk=Coalesce('group_id', 'missed_lesson__group_id'),
        )
    )


def assignments_in_window(teacher_id, window_from, window_to) -> list[dict]:
    """Резолюции за окно — источник календаря доп.уроков. teacher_id=None → ВСЕ
    преподаватели (admin-календарь без фильтра); иначе — один. Каждая резолюция =
    одна карточка (пер-ученик), поэтому student_names — список из одного имени
    (форму сохраняем для совместимости с scheduling-консьюмером). Имя группы —
    Coalesce(group, missed_lesson.group): makeup берёт группу пропуска, extra —
    свою группу."""
    qs = AbsenceResolution.objects.filter(
        scheduled_date__gte=window_from, scheduled_date__lte=window_to)
    if teacher_id is not None:
        qs = qs.filter(assigned_teacher_id=teacher_id)
    rows = list(
        qs.values('id', 'scheduled_date', 'scheduled_time', 'duration_minutes', 'status',
                  teacher_name=F('assigned_teacher__name'),
                  missed_lesson_group_name=Coalesce(
                      F('group__name'), F('missed_lesson__group__name')),
                  _student_name=F('student__full_name')))
    for r in rows:
        name = r.pop('_student_name')
        r['student_names'] = [name] if name else []
    return rows
