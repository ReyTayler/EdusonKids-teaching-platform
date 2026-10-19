"""
StudentsRepository — единственное место доступа к данным раздела students.

CRUD (list/get/create/update/soft_delete) — ORM-порт services/repo/students.js
(раздел 09). Контракт пагинатора: { rows, total, page, page_size }.

student_stats() оставлен на сыром SQL — см. # ORM-EXCEPTION у функции.
"""
from __future__ import annotations

from typing import Any, Optional

from django.db import connection
from django.db.models import F, OuterRef, Subquery
from django.db.models.functions import Now

from apps.core.utils.orm import dictrows
from apps.finances.repository import balance_for_student

from .models import Student


# ---------------------------------------------------------------------------
# Helpers (только для student_stats — # ORM-EXCEPTION)
# ---------------------------------------------------------------------------

def _dictfetchall(cursor) -> list[dict]:
    """Вернуть список dict из cursor (аналог Node pg rows)."""
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Пагинация / фильтры
# ---------------------------------------------------------------------------

# Whitelist sort_by → ORM-поле. id DESC — вторичная сортировка.
# «age» → «birth_date»: возраст удалён из модели и считается на фронте из даты
# рождения, а birth_date монотонно связан с возрастом (раньше дата рождения =
# старше = больше возраст), поэтому сортировка по нему = сортировка по возрасту.
_SORTABLE: dict[str, str] = {
    'id':                  'id',
    'full_name':           'full_name',
    'birth_date':          'birth_date',
    'stage':               'stage_sort_order',
    'created_at':          'created_at',
}

_DEFAULT_SORT_BY = 'full_name'
_DEFAULT_SORT_DIR = 'asc'

# Поля-аннотации, у которых NULL — «нет значения», а не «пустое значение»:
# ученики без сделок должны быть в КОНЦЕ при обеих направлениях сортировки.
# Postgres по умолчанию даёт NULLS LAST только при ASC, при DESC — NULLS FIRST,
# и сортировка «по стадии по убыванию» начиналась бы с прочерков.
# Тот же приём, что в apps/renewals/repository.py (там NULLS LAST в сыром SQL).
_NULLS_LAST_FIELDS = frozenset({'stage_sort_order'})


def _order_expr(sort_field: str, sort_dir: str):
    """ORDER BY-выражение: строка для обычных полей, F(...) с nulls_last для
    аннотаций из _NULLS_LAST_FIELDS. Остальные поля не трогаем — для них
    дефолтное поведение Postgres уже правильное (и совпадает с прежним)."""
    descending = sort_dir != 'asc'
    if sort_field in _NULLS_LAST_FIELDS:
        expr = F(sort_field)
        return expr.desc(nulls_last=True) if descending else expr.asc(nulls_last=True)
    return f'{"-" if descending else ""}{sort_field}'

# Явный список колонок students для .values() — общий для list/get/create/update,
# чтобы новое поле модели требовалось добавить только в одном месте. manager_name
# везде добавляется отдельным kwarg-алиасом (F() несовместим с .values() без аргументов).
_STUDENT_VALUES_FIELDS = (
    'id', 'full_name', 'birth_date', 'platform_id', 'crm_link',
    'phone', 'telegram', 'email',
    'parent1_name', 'parent1_phone', 'parent1_email', 'parent1_telegram',
    'parent2_name', 'parent2_phone', 'parent2_email',
    'manager_id', 'created_at',
)

# Аннотации стадии: в .values() их нужно перечислять явно.
_STAGE_VALUES_FIELDS = ('stage_id', 'stage_sort_order', 'stage_outcome_at', 'stage_month')


def _annotate_stage(qs):
    """Стадия ПОСЛЕДНЕЙ сделки ученика (max cycle_no) — «статус» ученика после
    удаления enrollment_status (спека 2026-07-25).

    Четыре коррелированных подзапроса вместо джойна: одна сделка на строку —
    Index Scan Backward по UNIQUE renewal_deal_student_cycle_uq
    (student_id, cycle_no), проверено EXPLAIN ANALYZE на dev-БД. Отдельный
    индекс под это не нужен: UNIQUE-констрейнт — тот же btree (см. миграцию
    renewals/0015, которая убрала дубль).
    Подписи стадий подзапросом НЕ тянем — воронка это 11 строк, их отдаёт
    _stage_index() одним запросом (см. _attach_stage).
    """
    from apps.renewals.models import RenewalDeal
    latest = RenewalDeal.objects.filter(student_id=OuterRef('pk')).order_by('-cycle_no')
    return qs.annotate(
        stage_id=Subquery(latest.values('stage_id')[:1]),
        stage_sort_order=Subquery(latest.values('stage__sort_order')[:1]),
        stage_outcome_at=Subquery(latest.values('outcome_at')[:1]),
        stage_month=Subquery(latest.values('frozen_until_month')[:1]),
    )


def _stage_index() -> dict[int, dict]:
    """stage_id → {id, key, label, kind, sort_order}. Воронка мала, читаем целиком."""
    from apps.renewals.models import RenewalStage
    return {s['id']: s for s in RenewalStage.objects.values(
        'id', 'key', 'label', 'kind', 'sort_order')}


def _attach_stage(rows: list[dict]) -> list[dict]:
    """Разворачивает аннотации в контракт stage / stage_is_open /
    stage_frozen_until_month. Месяц показываем только на стадии 'frozen' —
    на других он либо мёртвый, либо ещё не обнулён историческими данными."""
    from apps.renewals.transitions import FROZEN_KEY
    index = _stage_index() if any(r.get('stage_id') for r in rows) else {}
    for r in rows:
        stage_id = r.pop('stage_id', None)
        outcome_at = r.pop('stage_outcome_at', None)
        month = r.pop('stage_month', None)
        r.pop('stage_sort_order', None)  # только для ORDER BY
        stage = index.get(stage_id)
        r['stage'] = dict(stage) if stage else None
        r['stage_is_open'] = stage is not None and outcome_at is None
        r['stage_frozen_until_month'] = (
            month if stage is not None and stage['key'] == FROZEN_KEY else None)
    return rows


def _apply_filters(qs, filters: dict[str, Any]):
    """
    Фильтры (мимикрируют F.*-билдеры services/pagination.js):
      full_name (LIKE), parent1_phone/parent1_name/platform_id (likeNullable),
      manager_id (exact), stage_id (exact, по аннотации).

    likeNullable (col IS NOT NULL AND LOWER(col) LIKE) выражается __icontains —
    NULL по col не матчится автоматически, поэтому IS NOT NULL избыточен.
    """
    full_name = filters.get('full_name')
    if full_name not in (None, ''):
        qs = qs.filter(full_name__icontains=str(full_name))

    parent1_phone = filters.get('parent1_phone')
    if parent1_phone not in (None, ''):
        qs = qs.filter(parent1_phone__icontains=str(parent1_phone))

    parent1_name = filters.get('parent1_name')
    if parent1_name not in (None, ''):
        qs = qs.filter(parent1_name__icontains=str(parent1_name))

    manager_id = filters.get('manager_id')
    if manager_id not in (None, ''):
        try:
            manager_id = int(manager_id)
        except (TypeError, ValueError):
            pass  # невалидное значение — фильтр молча игнорируется
        else:
            qs = qs.filter(manager_id=manager_id)

    platform_id = filters.get('platform_id')
    if platform_id not in (None, ''):
        qs = qs.filter(platform_id__icontains=str(platform_id))

    # Стадия последней сделки продления (аннотация _annotate_stage, применяется
    # до фильтров — иначе filter по несуществующему полю падает).
    stage_id = filters.get('stage_id')
    if stage_id not in (None, ''):
        try:
            stage_id = int(stage_id)
        except (TypeError, ValueError):
            pass  # невалидное значение — фильтр молча игнорируется (как manager_id)
        else:
            qs = qs.filter(stage_id=stage_id)

    # Фильтра по возрасту нет: поле удалено, возраст считается на фронте из
    # birth_date (вычисляемое → не фильтруется SQL-ом без пересчёта в диапазон дат).
    return qs


# ---------------------------------------------------------------------------
# Repository functions — students CRUD (ORM)
# ---------------------------------------------------------------------------

def list_students(
    page: int = 1,
    page_size: int = 50,
    sort_by: str = _DEFAULT_SORT_BY,
    sort_dir: str = _DEFAULT_SORT_DIR,
    filters: Optional[dict] = None,
) -> dict:
    """Пагинированный список учеников. Контракт { rows, total, page, page_size }."""
    if filters is None:
        filters = {}

    sort_field = _SORTABLE.get(sort_by) or _SORTABLE[_DEFAULT_SORT_BY]

    # Аннотация стадии нужна для COUNT только если по ней фильтруют или сортируют:
    # иначе считаем по «чистому» queryset, не платя за 4 подзапроса на строку.
    needs_stage_in_count = (filters.get('stage_id') not in (None, '')
                            or sort_field == 'stage_sort_order')
    base = Student.objects.all()
    if needs_stage_in_count:
        base = _annotate_stage(base)
    base = _apply_filters(base, filters)

    total = base.count()

    page_qs = base if needs_stage_in_count else _annotate_stage(base)
    offset = max(0, (page - 1) * page_size)
    ordered = page_qs.order_by(_order_expr(sort_field, sort_dir), '-id')
    rows = dictrows(ordered[offset:offset + page_size].values(
        *_STUDENT_VALUES_FIELDS, *_STAGE_VALUES_FIELDS,
        manager_name=F('manager__full_name'),
    ))

    return {
        'rows': _attach_stage(rows),
        'total': total,
        'page': page,
        'page_size': page_size,
    }


def get_student(student_id: int) -> Optional[dict]:
    """Возвращает одного ученика по id или None."""
    rows = _attach_stage(dictrows(
        _annotate_stage(Student.objects.filter(id=student_id)).values(
            *_STUDENT_VALUES_FIELDS, *_STAGE_VALUES_FIELDS,
            manager_name=F('manager__full_name'),
        )))
    return rows[0] if rows else None


def student_exists(student_id: int) -> bool:
    """Есть ли такой ученик — SELECT 1 ... LIMIT 1.

    Отдельно от get_student: там, где нужен только 404-гейт (stats, комментарии),
    полная выборка обходится в 4 коррелированных подзапроса стадии + запрос
    справочника стадий (_annotate_stage / _stage_index) — за факт существования
    столько платить незачем.
    """
    return Student.objects.filter(id=student_id).exists()


def create_student(data: dict) -> dict:
    """
    Создаёт ученика (INSERT ... RETURNING *).

    NULLIF('', '') → пустая строка → None (platform_id/crm_link/контакты ребёнка/parent1_*/parent2_*).
    created_at — DB DEFAULT now() через Now().
    """
    obj = Student.objects.create(
        full_name=data['full_name'],
        birth_date=data.get('birth_date') or None,
        platform_id=data.get('platform_id') or None,
        crm_link=data.get('crm_link') or None,
        phone=data.get('phone') or None,
        telegram=data.get('telegram') or None,
        email=data.get('email') or None,
        parent1_name=data.get('parent1_name') or None,
        parent1_phone=data.get('parent1_phone') or None,
        parent1_email=data.get('parent1_email') or None,
        parent1_telegram=data.get('parent1_telegram') or None,
        parent2_name=data.get('parent2_name') or None,
        parent2_phone=data.get('parent2_phone') or None,
        parent2_email=data.get('parent2_email') or None,
        created_at=Now(),
    )
    # Через get_student — чтобы ответ POST нёс те же поля стадии, что GET.
    return get_student(obj.pk)


# Поля, которые PATCH умеет очищать (null или ''). full_name обязателен, а
# birth_date сохраняет прежнее правило «пусто — не трогаем».
_PATCH_CLEARABLE_FIELDS = (
    'platform_id', 'crm_link', 'phone', 'telegram', 'email',
    'parent1_name', 'parent1_phone', 'parent1_email', 'parent1_telegram',
    'parent2_name', 'parent2_phone', 'parent2_email',
)


def update_student(student_id: int, data: dict) -> Optional[dict]:
    """
    Обновляет ученика (PATCH). full_name/birth_date — только непустым значением;
    поля из _PATCH_CLEARABLE_FIELDS — если ключ прислан, включая очистку.
    """
    obj = Student.objects.filter(id=student_id).first()
    if obj is None:
        return None

    if data.get('full_name'):
        obj.full_name = data['full_name']
    if data.get('birth_date'):
        obj.birth_date = data['birth_date']
    # Nullable-поля PATCH: присланный ключ применяется всегда, пустая строка/null
    # очищают поле. Раньше (порт COALESCE из students.js) пустое значение
    # игнорировалось — стереть ошибочный телефон или ссылку было нельзя.
    for field in _PATCH_CLEARABLE_FIELDS:
        if field in data:
            setattr(obj, field, data[field] or None)

    obj.save()
    # Через get_student — единый контракт ответа с GET (включая поля стадии).
    return get_student(student_id)




def student_stats(student_id: int) -> dict:
    """
    Сводка посещаемости ученика по группам и направлениям.

    CTE-запрос дословно из services/repo/students.js studentStats().
    Пост-обработка на Python имитирует JS Math.round(x*1000)/10.

    # ORM-EXCEPTION: CTE с MSK-таймзоной (now() AT TIME ZONE 'Europe/Moscow'),
    # FILTER-агрегатами (COUNT(...) FILTER (WHERE present AND month-range)),
    # COUNT(DISTINCT) и составным условием LEFT JOIN lesson_attendance
    # (la.student_id = gm.student_id в ON, не в WHERE). ORM-перевод (FilteredRelation +
    # Case/When + zoneinfo) резко повышает риск расхождения на неверифицируемом пути
    # (Express удалён, data-тесты часто пропускаются). Сохраняем SQL дословно.
    """
    with connection.cursor() as cur:
        cur.execute(
            """
            WITH msk_now AS (
               SELECT (now() AT TIME ZONE 'Europe/Moscow')::date AS today
             ),
             msk_month AS (
               SELECT date_trunc('month', today)::date            AS m_start,
                      (date_trunc('month', today) + interval '1 month')::date AS m_end
                 FROM msk_now
             )
             SELECT gm.id AS membership_id,
                    gm.group_id,
                    gm.lessons_done,
                    gm.active AS membership_active,
                    g.name   AS group_name,
                    g.is_individual,
                    g.lesson_duration_minutes,
                    d.id     AS direction_id,
                    d.name   AS direction_name,
                    d.color  AS direction_color,
                    d.total_lessons AS course_total_lessons,
                    te.name  AS teacher_name,
                    te.id    AS teacher_id,
                    -- Взвешивание half-lesson: 45-мин занятие = 0.5 урока, иначе 1.
                    -- Раньше здесь был сырой COUNT(*), из-за чего 39 занятий по 45 мин
                    -- показывались как «39 из 56», хотя это 19.5 урока. Вес берём из
                    -- длительности САМОГО урока (l.lesson_duration_minutes) — ровно как
                    -- в балансе (apps/finances), чтобы прогресс и баланс совпадали.
                    -- Одна la на (урок, ученик) [UNIQUE], поэтому SUM по строкам = SUM
                    -- по уникальным урокам (COUNT DISTINCT больше не нужен).
                    COALESCE(SUM(CASE WHEN la.present
                                      THEN CASE WHEN l.lesson_duration_minutes = 45 THEN 0.5 ELSE 1 END
                                 END), 0)                                                        AS attended_count,
                    COALESCE(SUM(CASE WHEN l.id IS NOT NULL
                                      THEN CASE WHEN l.lesson_duration_minutes = 45 THEN 0.5 ELSE 1 END
                                 END), 0)                                                        AS lessons_recorded,
                    MIN(CASE WHEN la.present THEN l.lesson_date END)                            AS first_attended,
                    MAX(CASE WHEN la.present THEN l.lesson_date END)                            AS last_attended,
                    COALESCE(SUM(CASE WHEN l.id IS NOT NULL
                                       AND l.lesson_date >= (SELECT m_start FROM msk_month)
                                       AND l.lesson_date <  (SELECT m_end   FROM msk_month)
                                      THEN CASE WHEN l.lesson_duration_minutes = 45 THEN 0.5 ELSE 1 END
                                 END), 0)                                                        AS month_lessons,
                    COALESCE(SUM(CASE WHEN la.present
                                       AND l.lesson_date >= (SELECT m_start FROM msk_month)
                                       AND l.lesson_date <  (SELECT m_end   FROM msk_month)
                                      THEN CASE WHEN l.lesson_duration_minutes = 45 THEN 0.5 ELSE 1 END
                                 END), 0)                                                        AS month_attended
               FROM group_memberships gm
               JOIN groups g    ON g.id = gm.group_id
               JOIN directions d ON d.id = g.direction_id
               JOIN teachers te ON te.id = g.teacher_id
               LEFT JOIN lessons l ON l.group_id = gm.group_id
               LEFT JOIN lesson_attendance la ON la.lesson_id = l.id AND la.student_id = gm.student_id
              WHERE gm.student_id = %s
              GROUP BY gm.id, gm.group_id, gm.lessons_done, gm.active,
                       g.name, g.is_individual, g.lesson_duration_minutes,
                       d.id, d.name, d.color, d.total_lessons, te.name, te.id
              ORDER BY d.name, g.name
            """,
            [student_id],
        )
        groups_raw = _dictfetchall(cur)

    def _js_round(x: float) -> Optional[float]:
        """
        Имитирует JS Math.round(x * 1000) / 10.

        Python banker's rounding (round()) отличается от JS Math.round().
        Используем int(x * 1000 + 0.5) / 10 для точного совпадения.
        """
        if x is None:
            return None
        return int(x * 1000 + 0.5) / 10

    student_balance = balance_for_student(student_id)

    # Строим список статистики по группам (аналог JS groupStats.map()).
    # recorded/attended теперь ДРОБНЫЕ (взвешены half-lesson) — держим float, не int
    # (int(19.5) обрезал бы до 19). Веса кратны 0.5, float точен.
    group_stats: list[dict] = []
    for r in groups_raw:
        recorded = float(r['lessons_recorded'] or 0)
        attended = float(r['attended_count'] or 0)
        plan = int(r['course_total_lessons']) if r['course_total_lessons'] is not None else None
        denom = plan if (plan is not None and plan > 0) else recorded
        # Прогресс курса не превышает 100%: доп.уроки сверх плана (kind='extra')
        # реально проведены и наращивают attended, но completion курса зажат планом.
        # «Сверх курса» (attended − denom) показывает фронт отдельной пометкой.
        pct = min(_js_round(attended / denom), 100.0) if denom > 0 else None
        month_lessons = float(r['month_lessons'] or 0)
        month_attended = float(r['month_attended'] or 0)

        group_stats.append({
            'membership_id':           r['membership_id'],
            'group_id':                r['group_id'],
            'group_name':              r['group_name'],
            'direction_id':            r['direction_id'],
            'direction_name':          r['direction_name'],
            'direction_color':         r['direction_color'],
            'course_total_lessons':    plan,
            'teacher_id':              r['teacher_id'],
            'teacher_name':            r['teacher_name'],
            'is_individual':           r['is_individual'],
            'lesson_duration_minutes': r['lesson_duration_minutes'],
            'membership_active':       r['membership_active'],
            'lessons_done':            r['lessons_done'],
            'remaining':               student_balance,
            'lessons_recorded':        recorded,
            'attended_count':          attended,
            'missed_count':            max(recorded - attended, 0),
            'denominator':             denom,
            'attendance_pct':          pct,
            'first_attended':          r['first_attended'],
            'last_attended':           r['last_attended'],
            'this_month': {
                'lessons_recorded': month_lessons,
                'attended_count':   month_attended,
                'attendance_pct':   _js_round(month_attended / month_lessons) if month_lessons > 0 else None,
            },
        })

    # Агрегируем по направлению (аналог JS directionMap / Map())
    direction_map: dict[int, dict] = {}
    for g in group_stats:
        did = g['direction_id']
        if did not in direction_map:
            direction_map[did] = {
                'direction_id':         did,
                'direction_name':       g['direction_name'],
                'direction_color':      g['direction_color'],
                'course_total_lessons': g['course_total_lessons'],
                'attended_count':       0,
                'lessons_recorded':     0,
                'missed_count':         0,
                'first_attended':       None,
                'last_attended':        None,
                'this_month': {
                    'lessons_recorded': 0,
                    'attended_count':   0,
                },
                'groups': [],
            }
        d = direction_map[did]
        d['attended_count']   += g['attended_count']
        d['lessons_recorded'] += g['lessons_recorded']
        d['missed_count']     += g['missed_count']
        if g['first_attended'] and (
            d['first_attended'] is None or g['first_attended'] < d['first_attended']
        ):
            d['first_attended'] = g['first_attended']
        if g['last_attended'] and (
            d['last_attended'] is None or g['last_attended'] > d['last_attended']
        ):
            d['last_attended'] = g['last_attended']
        d['this_month']['lessons_recorded'] += g['this_month']['lessons_recorded']
        d['this_month']['attended_count']   += g['this_month']['attended_count']
        d['groups'].append(g)

    # Финализируем направления (добавляем denominator и attendance_pct)
    directions: list[dict] = []
    for d in direction_map.values():
        plan = d['course_total_lessons']
        denom = plan if (plan is not None and plan > 0) else d['lessons_recorded']
        month_lessons = d['this_month']['lessons_recorded']
        month_attended = d['this_month']['attended_count']
        directions.append({
            **d,
            'denominator':    denom,
            # Прогресс курса зажат на 100% (доп.уроки сверх плана не раздувают completion).
            'attendance_pct': min(_js_round(d['attended_count'] / denom), 100.0) if denom > 0 else None,
            'this_month': {
                **d['this_month'],
                'attendance_pct': _js_round(month_attended / month_lessons) if month_lessons > 0 else None,
            },
        })

    # Сортируем по direction_name (аналог JS localeCompare('ru'))
    directions.sort(key=lambda d: d['direction_name'] or '')

    # Итоговые totals (аналог JS groupStats.reduce())
    totals = {
        'lessons_recorded': 0,
        'attended_count':   0,
        'missed_count':     0,
        'denominator':      0,
        'month_lessons':    0,
        'month_attended':   0,
    }
    for g in group_stats:
        totals['lessons_recorded'] += g['lessons_recorded']
        totals['attended_count']   += g['attended_count']
        totals['missed_count']     += g['missed_count']
        totals['denominator']      += g['denominator']
        totals['month_lessons']    += g['this_month']['lessons_recorded']
        totals['month_attended']   += g['this_month']['attended_count']

    overall_pct = (
        min(_js_round(totals['attended_count'] / totals['denominator']), 100.0)
        if totals['denominator'] > 0 else None
    )
    month_pct = (
        _js_round(totals['month_attended'] / totals['month_lessons'])
        if totals['month_lessons'] > 0 else None
    )

    return {
        'student_id': student_id,
        'directions': directions,
        'groups':     group_stats,
        'overall': {
            'lessons_recorded': totals['lessons_recorded'],
            'attended_count':   totals['attended_count'],
            'missed_count':     totals['missed_count'],
            'denominator':      totals['denominator'],
            'attendance_pct':   overall_pct,
            'this_month': {
                'lessons_recorded': totals['month_lessons'],
                'attended_count':   totals['month_attended'],
                'attendance_pct':   month_pct,
            },
        },
    }


# list_payments / get_student_balance переехали в apps/payments/repository.py


# ---------------------------------------------------------------------------
# Repository functions — student comments (ORM)
# ---------------------------------------------------------------------------

def add_comment(student_id: int, body: str, author_id: Optional[int]):
    """Создаёт комментарий (INSERT). Существование student_id проверяет вызывающий (view)."""
    from .models import StudentComment
    return StudentComment.objects.create(student_id=student_id, body=body, author_id=author_id)


def delete_comment(student_id: int, comment_id: int) -> bool:
    """Удаляет комментарий. False если не найден (или принадлежит другому ученику)."""
    from .models import StudentComment
    deleted, _ = StudentComment.objects.filter(id=comment_id, student_id=student_id).delete()
    return deleted > 0
