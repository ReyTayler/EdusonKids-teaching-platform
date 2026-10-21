"""
Агрегаты карточки преподавателя — источник данных для
GET /api/admin/teachers/<id>/stats.

Отдельный модуль, а не `repository.py`: тот отвечает за CRUD преподавателя,
здесь — только читающие агрегации по урокам и группам.

Что считается нагрузкой: ТОЛЬКО курсовые уроки
(`lesson_type IN COURSE_LESSON_TYPES`). Доп.уроки (`extra`) и сгорания
(`burned`) — не занятия курса и в нагрузку не входят.

Единицы: «занятий» — штуки (COUNT), «минут» — сумма фактической
`lessons.lesson_duration_minutes`. Вес half-lesson (45 мин = 0.5 урока) здесь
НЕ применяется: это мера программы курса, а не труда преподавателя. Слово
«уроков» в этом модуле относится только к прогрессу курса группы
(`group_progress`), где вес как раз применяется.

На проводе (JSON-ответ API) это разделение проведено явно: `sessions` —
занятия, штуки, без веса (нагрузка преподавателя); `lessons_*` —
уроки курса, с весом half-lesson (программа курса). Не смешивать: это два
разных числа для одних и тех же занятий, и одинаковое английское слово для
обоих уже приводило к путанице на проде.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.db.models import (
    Case, Count, DecimalField, F, Max, OuterRef, Q, Subquery, Sum, Value, When,
)
from django.db.models.functions import Cast, Coalesce, ExtractWeekDay, TruncMonth

from apps.groups.course_length import effective_total_lessons_expr
from apps.groups.models import Group
from apps.lessons.models import COURSE_LESSON_TYPES, Lesson, LessonAttendance


def month_bounds(month: str) -> tuple[str, str]:
    """'YYYY-MM' → ('YYYY-MM-01', 'YYYY-MM-<последний день>'), обе границы включительно.

    Формат `month` здесь НЕ валидируется — вызывающий обязан проверить его
    заранее. Сейчас единственный вызывающий — вьюха (`_MONTH_RE`), которая
    отдаёт 400 на плохой ввод; но `stats.py` — публичный модуль, и следующий
    вызывающий (Celery-таска, management-команда) может забыть про этот
    контракт. Невалидный `month` роняет `ValueError` из `int()`/`datetime.date`,
    а это 500, а не 400.
    """
    year, mon = int(month[:4]), int(month[5:7])
    first = datetime.date(year, mon, 1)
    next_first = datetime.date(year + (1 if mon == 12 else 0), (mon % 12) + 1, 1)
    return first.isoformat(), (next_first - datetime.timedelta(days=1)).isoformat()


def month_breakdown(teacher_id: int, month: str) -> dict:
    """
    Итог месяца + разбивки по направлениям и длительностям.

    Один запрос с GROUP BY (направление, длительность), свёртка в Python:
    строк — десятки (направлений у преподавателя единицы, длительностей три),
    отдельные запросы под каждую разбивку не окупаются.
    """
    date_from, date_to = month_bounds(month)

    rows = (
        Lesson.objects
        .filter(
            teacher_id=teacher_id,
            lesson_type__in=COURSE_LESSON_TYPES,
            lesson_date__gte=date_from,
            lesson_date__lte=date_to,
        )
        .values(
            'lesson_duration_minutes',
            direction_id=F('group__direction_id'),
            direction_name=F('group__direction__name'),
            direction_color=F('group__direction__color'),
        )
        .annotate(
            sessions=Count('id'),
            substitutions=Count('id', filter=Q(original_teacher__isnull=False)),
        )
    )

    by_direction: dict[int, dict] = {}
    by_duration: dict[int, int] = {}
    total_sessions = total_minutes = total_subs = 0

    for row in rows:
        count = row['sessions']
        duration = row['lesson_duration_minutes']
        minutes = count * duration

        total_sessions += count
        total_minutes += minutes
        total_subs += row['substitutions']

        bucket = by_direction.setdefault(row['direction_id'], {
            'direction_id': row['direction_id'],
            'name': row['direction_name'],
            'color': row['direction_color'],
            'sessions': 0,
            'minutes': 0,
        })
        bucket['sessions'] += count
        bucket['minutes'] += minutes

        by_duration[duration] = by_duration.get(duration, 0) + count

    return {
        'total': {
            'sessions': total_sessions,
            'minutes': total_minutes,
            'substitutions': total_subs,
        },
        # Сортировка по убыванию: первым идёт направление, где он работает больше
        # всего — это ответ на вопрос «кто он по профилю». Вторичный ключ по
        # `name` — БД не гарантирует порядок строк без ORDER BY, и при равном
        # числе занятий два направления могли бы менять места между
        # одинаковыми запросами (стабильная сортировка Python здесь не спасает:
        # сама последовательность на входе не детерминирована).
        'by_direction': sorted(by_direction.values(), key=lambda r: (-r['sessions'], r['name'])),
        'by_duration': sorted(
            [{'minutes': m, 'sessions': c} for m, c in by_duration.items()],
            key=lambda r: -r['minutes'],
        ),
    }


def year_series(teacher_id: int, year: int) -> list[dict]:
    """
    Занятий по месяцам КАЛЕНДАРНОГО года: январь–декабрь, всегда 12 точек.

    Именно календарный год, а не скользящее окно «12 месяцев назад от
    выбранного»: у скользящего окна при переключении месяца ◀ ▶ уезжают обе
    оси сразу — и данные, и подписи, — поэтому сравнить «стало ли больше»
    невозможно, картинка меняется целиком на каждый клик. У года ось
    неподвижна: переключение месяцев внутри года не двигает график вообще,
    а смена года заменяет его целиком и осознанно.

    Месяцы без занятий возвращаются с нулём: пропуск точки заставил бы график
    соединить соседние месяцы прямой и показать рост, которого не было. По той
    же причине возвращаются и будущие месяцы года — нулевой хвост честно
    показывает, что год не кончился, а не обрывает линию на месте.
    """
    rows = (
        Lesson.objects
        .filter(
            teacher_id=teacher_id,
            lesson_type__in=COURSE_LESSON_TYPES,
            lesson_date__gte=f'{year}-01-01',
            lesson_date__lte=f'{year}-12-31',
        )
        .annotate(bucket=TruncMonth('lesson_date'))
        .values('bucket')
        .annotate(sessions=Count('id'))
    )
    counts = {row['bucket'].month: row['sessions'] for row in rows}

    return [
        {'month': f'{year}-{mon:02d}', 'sessions': counts.get(mon, 0)}
        for mon in range(1, 13)
    ]


def last_lesson_date(teacher_id: int) -> str | None:
    """
    Дата последнего проведённого занятия — БЕЗ ограничения месяцем.

    Отвечает на вопрос «преподаватель ещё работает», а не «сколько провёл
    в июле», поэтому выбранный период здесь не при чём.
    """
    value = (
        Lesson.objects
        .filter(teacher_id=teacher_id, lesson_type__in=COURSE_LESSON_TYPES)
        .aggregate(last=Max('lesson_date'))['last']
    )
    return value.isoformat() if value else None


# Вес занятия в уроках курса: 45 мин = 0.5, иначе 1.
#
# Эта формула продублирована по всему проекту, а не только здесь — это
# известный долг, не тайна этого модуля. Python-копии: apps.lessons.services
# ._step, apps.lessons.repository._step, apps.extra_lessons.services._step,
# apps.scheduling.occurrences._step_for (и ещё несколько мест, где условие
# `duration_minutes == 45` инлайнится без функции — apps.teacher_spa.services/
# .repository, apps.scheduling.services, apps.groups.repository). SQL-копии
# как CASE-выражение ORM: apps.finances.repository._attended_units_case,
# apps.dashboard.registry_service (два инлайн-Case). SQL-копии как сырой CASE
# в RunSQL/raw-запросах: apps.students.repository, apps.renewals.rebuild,
# apps.renewals.management.commands.backfill_renewal_history.
#
# Консолидация в одно место — отдельная задача, сюда не входит: она заденет
# как минимум finances и dashboard, а те лежат по разные стороны разбиения
# django_db_setup между приложениями (часть no-op'ит и работает на общей
# eduson_test, часть пересоздаёт свежую test_eduson_test) — после такой
# правки обязателен полный `pytest -q`, а не прогон по отдельным apps.
_LESSON_WEIGHT = Case(
    When(lesson_duration_minutes=45, then=Value(Decimal('0.5'))),
    default=Value(Decimal('1')),
    output_field=DecimalField(max_digits=6, decimal_places=1),
)

_PROGRESS_FIELD = DecimalField(max_digits=6, decimal_places=1)


def group_progress(teacher_id: int) -> list[dict]:
    """
    Пройдено курса по каждой группе преподавателя, в УРОКАХ (45 мин = 0.5 урока).

    Считается по группе целиком, а не по этому преподавателю: прогресс курса —
    свойство группы, замена коллеги его не обнуляет и не удваивает.

    Архивные группы включены: карточка показывает их отдельной секцией.

    `lessons_done` — скалярный подзапрос, а НЕ второй Count в общем annotate.
    `groups` уже соединяется с `directions` ради длины курса; агрегат по второй
    связи в том же запросе перемножился бы на строки первой (классическая
    ловушка Django ORM — см. тест test_group_progress_not_inflated_by_members).

    `lessons_total` в ответе — `None` (длина курса не задана нигде: ни в группе,
    ни в направлении) ИЛИ `0` (`directions.total_lessons` явно выставлен в 0 —
    CHECK на этом поле допускает `>= 0`, в отличие от `groups.lessons_total`,
    где CHECK `> 0`). Оба случая долетают до потребителя как есть и оба
    означают одно и то же: «длины курса нет». Наивное `done / total` на клиенте
    без проверки на этих значениях даст `NaN` (0/0) или `Infinity` (N/0).
    """
    done = Subquery(
        Lesson.objects
        .filter(group=OuterRef('pk'), lesson_type__in=COURSE_LESSON_TYPES)
        .values('group')
        .annotate(done=Sum(_LESSON_WEIGHT))
        .values('done')[:1],
        output_field=_PROGRESS_FIELD,
    )

    rows = (
        Group.objects
        .filter(teacher_id=teacher_id)
        .annotate(
            # Cast — не косметика: DecimalField как output_field только
            # ПОДПИСЫВАЕТ Python-значение, конвертер scale навешивается лишь
            # для sqlite/mysql/oracle. На PostgreSQL без явного ::numeric(6,1)
            # SQL отдаёт scale операндов «как есть» — 2 занятия по 90 минут
            # дают Decimal('2') ("2" на проводе), а не Decimal('2.0') ("2.0"),
            # хотя оба идут через один и тот же output_field. Cast заставляет
            # компилятор ORM реально добавить ::numeric(6,1) в SQL и
            # зафиксировать scale, а не только тип Python-объекта.
            lessons_done=Cast(
                Coalesce(done, Value(Decimal('0')), output_field=_PROGRESS_FIELD),
                output_field=_PROGRESS_FIELD,
            ),
            # Алиас, а не `lessons_total=...`: `lessons_total` уже занято
            # ПОЛЕМ модели Group (ручной override длины курса). Django не
            # даёт annotate() перекрыть имя существующего поля — упадёт
            # ValueError при попытке. Аннотация хранит ЭФФЕКТИВНУЮ длину курса
            # (override группы ИЛИ длина направления), поле — только сырой
            # override; это разные вещи, и не стоит "упрощать" до одного имени.
            course_total=effective_total_lessons_expr(),
        )
        .values('id', 'lessons_done', 'course_total')
        .order_by('name')
    )

    return [
        {
            'group_id': row['id'],
            'lessons_done': row['lessons_done'],
            'lessons_total': row['course_total'],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Качество работы и то, что требует действия
# ---------------------------------------------------------------------------

def attendance(teacher_id: int, month: str) -> dict:
    """
    Доля присутствовавших на занятиях преподавателя за месяц.

    Знаменатель НЕ включает `unpaid_skip`: это ученики, которые этот слот не
    посещают по устройству курса (перевели, начал не с первого урока). Они
    отсутствуют не «у преподавателя», и их включение занижало бы показатель по
    причине, к работе преподавателя отношения не имеющей.

    `is_free` в знаменателе ОСТАЁТСЯ: ученик на занятии был, просто оно ему
    бесплатно — это про деньги, а не про посещаемость.

    `pct` — None, когда считать не из чего: 0% и «занятий не было» на экране
    выглядят одинаково, а значат противоположное.
    """
    date_from, date_to = month_bounds(month)

    row = (
        LessonAttendance.objects
        .filter(
            lesson__teacher_id=teacher_id,
            lesson__lesson_type__in=COURSE_LESSON_TYPES,
            lesson__lesson_date__gte=date_from,
            lesson__lesson_date__lte=date_to,
            unpaid_skip=False,
        )
        .aggregate(
            counted=Count('lesson_id'),
            present=Count('lesson_id', filter=Q(present=True)),
        )
    )
    counted, present = row['counted'] or 0, row['present'] or 0

    return {
        'present': present,
        'counted': counted,
        'pct': round(present * 100 / counted) if counted else None,
    }


def weekday_load(teacher_id: int, month: str) -> list[dict]:
    """
    Занятий по дням недели за месяц — все 7 дней, пустые нулями.

    Считается по ФАКТУ (`lessons.lesson_date`), а не по шаблону расписания
    (`group_schedule_slots`): шаблон версионируется по датам действия и говорит,
    когда занятия ДОЛЖНЫ быть, а вопрос «когда преподаватель занят» — про то,
    как вышло. Переносы и замены шаблон не показывает вовсе.

    Нумерация дней — Вс=0, как в `frontend/lib/slots.ts::DOW` и в
    `group_schedule_slots.day_of_week` (конвенция JS getDay). Postgres
    `EXTRACT(DOW)` даёт ровно её, Django `ExtractWeekDay` — сдвинутую на 1
    (1=воскресенье), поэтому вычитаем единицу.
    """
    date_from, date_to = month_bounds(month)

    rows = (
        Lesson.objects
        .filter(
            teacher_id=teacher_id,
            lesson_type__in=COURSE_LESSON_TYPES,
            lesson_date__gte=date_from,
            lesson_date__lte=date_to,
        )
        .annotate(dow=ExtractWeekDay('lesson_date'))
        .values('dow')
        .annotate(sessions=Count('id'))
    )
    counts = {(row['dow'] - 1): row['sessions'] for row in rows}

    return [{'day': day, 'sessions': counts.get(day, 0)} for day in range(7)]


def unfilled(teacher_id: int) -> dict:
    """
    Просроченные незаполненные занятия преподавателя — СЕЙЧАС, не за месяц.

    Это единственная метрика карточки, требующая действия, поэтому месяцем не
    ограничена: просрочка мая не перестаёт быть просрочкой оттого, что открыт
    июль.

    Переиспользует `dashboard.fill_service`, а не считает заново: там уже учтены
    порог overdue по московскому времени, доп.уроки и — важное — подмена
    преподавателя (занятие числится за тем, кто его реально ведёт).
    """
    # Локальный импорт: apps.dashboard тянет за собой Celery-задачи и кэш,
    # а карточке преподавателя нужен один список.
    from apps.dashboard import fill_service

    rows = fill_service.unfilled_lessons(teacher_id=teacher_id, sort_dir='asc')

    return {
        'count': len(rows),
        # Самая старая просрочка: одно число «3» не говорит, вчера это или в мае.
        'oldest_date': rows[0]['date'] if rows else None,
    }


def absences(teacher_id: int, month: str) -> dict:
    """
    Пропуски в группах преподавателя: сколько зарегистрировано за месяц и чем
    закончились, плюс сколько всего ждёт решения ПРЯМО СЕЙЧАС.

    Месяц считается по `created_at` — когда пропуск зарегистрировали. Не по
    `scheduled_date`: у сгоревшего пропуска отработка не назначалась, и даты
    там нет вовсе.

    `pending_now` намеренно без месяца: это очередь, требующая действия,
    и она не обнуляется при переключении периода.

    Привязка к преподавателю — через группу пропуска. Отработку может провести
    другой преподаватель (`assigned_teacher_id`), но пропуск случился на
    занятии этой группы, и разбирается с ним тот, кто её ведёт.
    """
    from apps.extra_lessons.models import (
        BURNED, MAKEUP_DONE, MAKEUP_SCHEDULED, PENDING, AbsenceResolution,
    )

    date_from, date_to = month_bounds(month)
    in_groups = AbsenceResolution.objects.filter(group__teacher_id=teacher_id)

    month_rows = (
        in_groups
        .filter(created_at__date__gte=date_from, created_at__date__lte=date_to)
        .values('status')
        .annotate(n=Count('id'))
    )
    by_status = {row['status']: row['n'] for row in month_rows}

    return {
        'registered': sum(by_status.values()),
        'makeup_done': by_status.get(MAKEUP_DONE, 0),
        'makeup_scheduled': by_status.get(MAKEUP_SCHEDULED, 0),
        'burned': by_status.get(BURNED, 0),
        'pending_now': in_groups.filter(status=PENDING).count(),
    }


def payroll_for_month(teacher_id: int, month: str) -> dict:
    """
    Начислено и удержано за месяц.

    Отдаётся ТОЛЬКО суперадмину — раздел «Зарплата» закрыт `IsSuperAdmin`
    (`apps/payroll/views.py`), а карточку преподавателя видит и менеджер.
    Решение о том, звать ли эту функцию, принимает вьюха; здесь только счёт.

    payment — к начислению всего: уроки (Payroll, месяц по дате урока) плюс
    отмены (CancellationPay, месяц по lesson_date отмены) — тот же итог, что
    sum_payment в сводке раздела «Зарплата» (apps.payroll.repository.payroll_summary),
    иначе плитка «Начислено» на карточке разойдётся с ведомостью. penalty —
    только уроки: у начисления за отмену штрафа нет.
    """
    from apps.payroll.models import CancellationPay, Payroll

    money = DecimalField(max_digits=20, decimal_places=2)
    date_from, date_to = month_bounds(month)
    cancellations = (
        CancellationPay.objects
        .filter(teacher_id=teacher_id, lesson_date__gte=date_from, lesson_date__lte=date_to)
        .aggregate(payment=Coalesce(Sum('payment'), Value(Decimal('0')), output_field=money))
    )
    row = (
        Payroll.objects
        .filter(
            teacher_id=teacher_id,
            lesson__lesson_date__gte=date_from,
            lesson__lesson_date__lte=date_to,
        )
        .aggregate(
            payment=Coalesce(Sum('payment'), Value(Decimal('0')),
                             output_field=DecimalField(max_digits=20, decimal_places=2)),
            penalty=Coalesce(Sum('penalty'), Value(Decimal('0')),
                             output_field=DecimalField(max_digits=20, decimal_places=2)),
        )
    )
    return {'payment': row['payment'] + cancellations['payment'], 'penalty': row['penalty']}
