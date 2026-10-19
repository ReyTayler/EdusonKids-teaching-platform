"""Repository renewals: чтение агрегатов из memberships/finances + операции над сделками."""
from __future__ import annotations

from django.db import connection

from apps.renewals import cycle


def _directions_agg(student_col: str) -> str:
    """json-массив активных направлений ученика [{name, color}] для SELECT."""
    return f"""
    COALESCE((
        SELECT json_agg(json_build_object('name', x.name, 'color', x.color)
                        ORDER BY x.name)
        FROM (SELECT DISTINCT dd.name, dd.color
              FROM group_memberships mm
              JOIN groups gg ON gg.id = mm.group_id
              JOIN directions dd ON dd.id = gg.direction_id
              WHERE mm.student_id = {student_col} AND mm.active = true) x
    ), '[]'::json)
"""


def _deal_directions_agg(deal_alias: str = 'd') -> str:
    """Направления СДЕЛКИ: снимок цикла, если он снят, иначе живые членства.

    Снимок хранит только id (см. RenewalDeal.directions_snapshot), поэтому имена
    и цвета всегда свежие — переименование курса видно и в закрытых сделках.
    Порядок — по имени, чтобы карточка не «прыгала» между запросами.
    """
    return f"""
    CASE WHEN {deal_alias}.directions_snapshot IS NOT NULL THEN COALESCE((
        SELECT json_agg(json_build_object('name', sd.name, 'color', sd.color)
                        ORDER BY sd.name)
        FROM directions sd
        WHERE sd.id IN (SELECT jsonb_array_elements_text(
                            {deal_alias}.directions_snapshot)::int)
    ), '[]'::json)
    ELSE {_directions_agg(f'{deal_alias}.student_id')} END
"""


DIRECTIONS_AGG_SQL = _deal_directions_agg('d')


# Кандидаты сводки «Без сделок» — ОДНО правило на список и на счётчик бейджа:
# активное членство есть, сделки не было ни разу. Держим общим куском SQL, иначе
# бейдж и содержимое диалога однажды разойдутся (тест test_unassigned_count_matches_list).
_UNASSIGNED_SOURCE = """
    FROM students s
    WHERE EXISTS (SELECT 1 FROM group_memberships m
                  WHERE m.student_id = s.id AND m.active = true)
      AND NOT EXISTS (SELECT 1 FROM renewal_deal d
                      WHERE d.student_id = s.id)
"""


def count_students_without_deal() -> int:
    """
    Число учеников сводки — для бейджа «Без сделок (N)» в шапке раздела.

    Отдельно от students_without_deal осознанно: бейдж читается при КАЖДОМ входе
    в раздел, а сам список — только при открытии диалога. Здесь нет ни
    per-row подзапросов (направления, посещаемость), ни расчёта балансов,
    поэтому цена не зависит от длины списка.
    """
    with connection.cursor() as cur:
        cur.execute(f'SELECT count(*) {_UNASSIGNED_SOURCE}')
        return cur.fetchone()[0]


def students_without_deal() -> list[dict]:
    """
    Сводка «Ученики без сделок»: активный membership есть, а сделки не было
    НИКОГДА — ни открытой, ни закрытой. То есть только новички.

    Ученик с закрытой сделкой (ушёл, вернулся, спавн следующего цикла не
    состоялся) сюда НЕ попадает осознанно (решение 2026-07-27): его возвращают в
    воронку переоткрытием его же сделки («Список» → «Показать закрытые» →
    «Переоткрыть»), при котором сохраняется номер цикла и прогресс. Создание
    новой сделки перешагнуло бы занятый номер и обнулило прогресс.

    Для каждого — направления, суммарно посещено, открытый цикл и флаг долга.
    Из неё менеджер вручную создаёт сделку (POST /api/admin/renewals).
    """
    from apps.finances.repository import balances_for_students

    sql = f"""
        SELECT s.id AS student_id, s.full_name AS student_name,
               {_directions_agg('s.id')} AS directions,
               COALESCE((
                   SELECT SUM(CASE WHEN l.lesson_duration_minutes = 45
                                   THEN 0.5 ELSE 1 END)
                   FROM lesson_attendance la
                   JOIN lessons l ON l.id = la.lesson_id
                   WHERE la.student_id = s.id AND la.present = true
               ), 0) AS attended
        {_UNASSIGNED_SOURCE}
        ORDER BY s.full_name
    """
    with connection.cursor() as cur:
        cur.execute(sql)
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    balances = balances_for_students([r['student_id'] for r in rows])
    for r in rows:
        r['attended'] = float(r['attended'])
        r['cycle_no'] = cycle.open_cycle_no(r['attended'])
        r['debt'] = float(balances.get(r['student_id'], 0)) < 0
    return rows


def deal_computed(deal_id: int) -> dict | None:
    """
    Сделка + вычисляемые поля: имя ученика, активные направления (справочно),
    прогресс n/4 от общей истории, balance, days_in_stage. Баланс — apps.finances.
    """
    from apps.finances.repository import balance_for_student
    from apps.renewals import engine

    sql = f"""
        SELECT d.id, d.student_id, d.cycle_no, d.stage_id,
               d.assignee_id, d.reason_code,
               d.due_at, d.stage_entered_at, d.outcome_at, d.created_at,
               d.frozen_until_month,
               s.full_name AS student_name,
               {DIRECTIONS_AGG_SQL} AS directions,
               st.key AS stage_key, st.label AS stage_label, st.kind AS stage_kind,
               st.color AS stage_color,
               a.full_name AS assignee_name,
               EXTRACT(DAY FROM now() - d.stage_entered_at)::int AS days_in_stage,
               COALESCE((
                   SELECT SUM(CASE WHEN l.lesson_duration_minutes = 45
                                   THEN 0.5 ELSE 1 END)
                   FROM lesson_attendance la
                   JOIN lessons l ON l.id = la.lesson_id
                   WHERE la.student_id = d.student_id AND la.present = true), 0) AS attended
        FROM renewal_deal d
        JOIN students s   ON s.id = d.student_id
        JOIN renewal_stage st ON st.id = d.stage_id
        LEFT JOIN accounts a ON a.id = d.assignee_id
        WHERE d.id = %s
    """
    with connection.cursor() as cur:
        cur.execute(sql, [deal_id])
        row = cur.fetchone()
        if row is None:
            return None
        cols = [c[0] for c in cur.description]
        data = dict(zip(cols, row))
    attended = float(data.pop('attended') or 0)
    # Прогресс от номера цикла сделки (не attended % 4): у сделки цикла N свои
    # уроки (N−1)×4+1 .. N×4, иначе после 4-го урока прогресс «заворачивался».
    into = attended - (data['cycle_no'] - 1) * cycle.LESSONS_PER_CYCLE
    # 1..4, где 1 = «Не было урока цикла» (into<=0), 2..4 = «Урок 1..3» отработаны
    # (into=1..3). Текст на фронте (RenewalDrawer) разворачивает это в -1 при выводе.
    data['lesson_in_cycle'] = min(max(int(into), 0), cycle.LESSONS_PER_CYCLE - 1) + 1
    data['cycle_completed'] = into >= cycle.LESSONS_PER_CYCLE
    data['balance'] = balance_for_student(data['student_id'])
    data['debt'] = float(data['balance']) < 0
    # Переоткрывать можно только последнюю сделку ученика (engine._assert_reopenable).
    # Флаг нужен дроверу, чтобы не показывать кнопку, ведущую в 409; правило берём
    # из движка, а не повторяем здесь.
    data['can_reopen'] = (
        data['outcome_at'] is not None
        and not engine.reopen_blocked(data['student_id'], data['cycle_no'])
    )
    return data


_MONTHS_GENITIVE = (
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
)


def month_label(value) -> str:
    """date(2026, 9, 1) → «сентября 2026» — для текста активности таймлайна."""
    return f'{_MONTHS_GENITIVE[value.month - 1]} {value.year}'


def active_direction_ids(student_id: int) -> list[int]:
    """id направлений ученика по АКТИВНЫМ членствам."""
    with connection.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT g.direction_id
            FROM group_memberships m
            JOIN groups g ON g.id = m.group_id
            WHERE m.student_id = %s AND m.active = true AND g.direction_id IS NOT NULL
            ORDER BY 1
        """, [student_id])
        return [r[0] for r in cur.fetchall()]


def cycle_direction_ids(student_id: int, cycle_no: int) -> list[int]:
    """id направлений по УРОКАМ цикла — основной источник снимка сделки.

    Членство в группе гасится, когда ученика выводят из группы, поэтому по нему
    историю восстановить нельзя: убрали из группы, потом закрыли сделку — и курс
    потерян. Проведённые уроки, наоборот, неизменны, и «на каком направлении был
    ученик в этом цикле» — это ровно направления его уроков.

    Границы цикла N — накопленным итогом по той же метрике, что и прогресс сделки
    (finances.attended_units_total: present=true, 45мин = 0.5 урока): урок входит
    в цикл, если накопленный итог после него больше (N−1)×4 и до него меньше N×4.
    Урок на стыке попадает в оба цикла — это верно, он и правда разделён между ними.
    """
    lo = (cycle_no - 1) * cycle.LESSONS_PER_CYCLE
    hi = cycle_no * cycle.LESSONS_PER_CYCLE
    with connection.cursor() as cur:
        cur.execute("""
            WITH att AS (
                SELECT g.direction_id,
                       CASE WHEN l.lesson_duration_minutes = 45 THEN 0.5 ELSE 1 END AS w,
                       SUM(CASE WHEN l.lesson_duration_minutes = 45 THEN 0.5 ELSE 1 END)
                           OVER (ORDER BY l.lesson_date, l.id
                                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cum
                FROM lesson_attendance la
                JOIN lessons l ON l.id = la.lesson_id
                JOIN groups g  ON g.id = l.group_id
                WHERE la.student_id = %s AND la.present = true
                  AND g.direction_id IS NOT NULL
            )
            SELECT DISTINCT direction_id FROM att
            WHERE cum > %s AND cum - w < %s
            ORDER BY 1
        """, [student_id, lo, hi])
        return [r[0] for r in cur.fetchall()]


def snapshot_directions(deal) -> bool:
    """Снять направления цикла, если снимок ещё не снят. True — если записали.

    Источник — уроки цикла (cycle_direction_ids); активные членства только как
    запасной вариант, когда уроков в цикле ещё не было (сделку закрыли до первого
    занятия — тогда членство и есть всё, что мы про курс знаем).

    Вызывающий обязан сам сохранить сделку: и созревание (engine), и ручной
    переход (move_deal) пишут её одним UPDATE вместе со своими полями, а лишний
    UPDATE поднял бы вторую запись в журнале изменений на ровном месте.
    """
    if deal.directions_snapshot is not None:
        return False
    deal.directions_snapshot = (cycle_direction_ids(deal.student_id, deal.cycle_no)
                                or active_direction_ids(deal.student_id))
    return True


def move_deal(deal_id: int, to_stage_id: int, reason_code: str | None,
              author_id: int | None, frozen_until_month=None,
              allow_nonpositive_balance: bool = False) -> dict | None:
    """Переместить сделку в стадию, записать активность, синхронизировать outcome.

    frozen_until_month («до какого месяца заморозка») пишется только при
    переходе НА стадию key='frozen'; при переходе с неё — обнуляется, чтобы
    мёртвый месяц не «прилипал» к сделке. Обязательность поля проверяет
    MoveSerializer (у него есть to_stage_id, значит и ключ стадии).

    allow_nonpositive_balance снимает гейт «Продлён только при балансе > 0»
    (право admin/superadmin решает вьюха по роли; гейт цикла остаётся).
    """
    from django.db import transaction
    from django.utils import timezone
    from apps.finances.repository import balance_for_student
    from apps.renewals import engine
    from apps.renewals.models import RenewalActivity, RenewalDeal, RenewalStage
    from apps.renewals.transitions import assert_allowed, InvalidTransition, FROZEN_KEY

    with transaction.atomic():
        deal = RenewalDeal.objects.select_for_update().filter(id=deal_id).first()
        if deal is None:
            return None
        to_stage = RenewalStage.objects.filter(id=to_stage_id, pipeline=deal.pipeline).first()
        if to_stage is None:
            raise InvalidTransition('Стадия не принадлежит воронке сделки')
        from_stage = deal.stage
        assert_allowed(from_kind=from_stage.kind, to_kind=to_stage.kind,
                       from_is_auto=from_stage.is_auto, to_is_auto=to_stage.is_auto,
                       from_key=from_stage.key, to_allow_mid_cycle=to_stage.allow_mid_cycle,
                       cycle_completed=engine.cycle_completed(deal),
                       balance=float(balance_for_student(deal.student_id)),
                       allow_nonpositive_balance=allow_nonpositive_balance)

        to_frozen = to_stage.key == FROZEN_KEY
        deal.frozen_until_month = frozen_until_month if to_frozen else None
        deal.stage = to_stage
        deal.stage_entered_at = timezone.now()
        if reason_code is not None:
            deal.reason_code = reason_code
        deal.outcome_at = timezone.now() if to_stage.kind in ('won', 'lost') else None
        # Ручной переход = сделку ведут к исходу, дальше живой список направлений
        # начнёт врать (перевод на другой курс гасит членство). Фиксируем здесь, а
        # не только при закрытии: «Закончил курс» и «Заморожен» сделку не закрывают.
        snapshot_directions(deal)
        deal.save(update_fields=['stage', 'stage_entered_at', 'reason_code',
                                 'outcome_at', 'frozen_until_month',
                                 'directions_snapshot', 'updated_at'])
        body = reason_code or ''
        if to_frozen and frozen_until_month is not None:
            body = f'Заморозка до {month_label(frozen_until_month)}'
        RenewalActivity.objects.create(
            deal=deal, kind='stage_change', from_stage=from_stage, to_stage=to_stage,
            author_id=author_id, body=body)
        # Менеджер вручную подтвердил продление — единственный путь закрытия
        # сделки как «Продлён» (оплата больше не закрывает сделку сама, см.
        # signals.py). Спавним следующий цикл, перешагивая занятые закрытые
        # номера (переоткрытия/возвраты могли оставить «дыру»).
        if to_stage.kind == 'won':
            next_cycle = engine.next_open_cycle_no(deal.student_id, deal.cycle_no + 1)
            engine.ensure_deal(deal.student_id, next_cycle)
            # Спавн ставит новый цикл на «Не было урока». Если ученик уже отходил
            # в него (посещения сверх рубежа), сразу двигаем на верную авто-стадию
            # — тот же sync, что и при ручном создании сделки (services.create_deal).
            engine.sync_lesson_stage(deal.student_id)
    return deal_computed(deal_id)


def set_outcome_date(deal_id: int, outcome_date, author_id: int | None) -> dict | str | None:
    """Переставить дату закрытия у ЗАКРЫТОЙ сделки. None — сделки нет,
    'not_closed' — она открыта, dict — переставили.

    Зачем. Сделку часто закрывают позже, чем ученик на самом деле ушёл или продлил,
    а аналитика и «Переходимость» относят событие к МЕСЯЦУ outcome_at. Без правки
    даты отчёт за июнь молча уезжает в июль, и починить его нечем.

    Время суток — 12:00 МСК выбранного дня: отчёты берут дату как
    `outcome_at AT TIME ZONE 'Europe/Moscow'`, и полдень гарантирует, что обратное
    преобразование даст ровно тот день при любом сдвиге зоны.

    Стадию не трогаем: меняется КОГДА закрыли, а не ЧЕМ закончилось.
    """
    import datetime
    from django.db import transaction
    from apps.core.utils.dates import MSK
    from apps.renewals.models import RenewalActivity, RenewalDeal

    with transaction.atomic():
        deal = RenewalDeal.objects.select_for_update().filter(id=deal_id).first()
        if deal is None:
            return None
        if deal.outcome_at is None:
            return 'not_closed'

        was = deal.outcome_at.astimezone(MSK).date()
        deal.outcome_at = datetime.datetime.combine(
            outcome_date, datetime.time(12, 0), tzinfo=MSK)
        deal.save(update_fields=['outcome_at', 'updated_at'])
        RenewalActivity.objects.create(
            deal=deal, kind='system', author_id=author_id,
            body=f'Дата закрытия изменена: {was.isoformat()} → {outcome_date.isoformat()}')
    return deal_computed(deal_id)


def patch_deal(deal_id: int, data: dict) -> dict | None:
    from django.utils import timezone
    from apps.renewals.models import RenewalDeal
    fields = {}
    for k in ('reason_code',):
        if k in data:
            fields[k] = data[k]
    if not fields:
        return deal_computed(deal_id)
    # .update() не триггерит auto_now — обновляем updated_at вручную (как в move_deal).
    fields['updated_at'] = timezone.now()
    updated = RenewalDeal.objects.filter(id=deal_id).update(**fields)
    return deal_computed(deal_id) if updated else None


def add_comment(deal_id: int, body: str, author_id: int | None) -> dict | None:
    from apps.renewals.models import RenewalActivity, RenewalDeal
    if not RenewalDeal.objects.filter(id=deal_id).exists():
        return None
    act = RenewalActivity.objects.create(
        deal_id=deal_id, kind='comment', body=body, author_id=author_id)
    return {'id': act.id, 'created_at': act.created_at}


def list_activity(deal_id: int) -> list[dict]:
    with connection.cursor() as cur:
        cur.execute("""
            SELECT ra.id, ra.kind, ra.body, ra.created_at,
                   fs.label AS from_label, ts.label AS to_label,
                   a.full_name AS author_name, ra.payment_id
            FROM renewal_activity ra
            LEFT JOIN renewal_stage fs ON fs.id = ra.from_stage_id
            LEFT JOIN renewal_stage ts ON ts.id = ra.to_stage_id
            LEFT JOIN accounts a ON a.id = ra.author_id
            WHERE ra.deal_id = %s ORDER BY ra.created_at DESC
        """, [deal_id])
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


COLUMN_LIMIT = 50  # карточек на колонку по умолчанию (остальное — «Показать ещё»)


def _board_where(filters: dict) -> tuple[str, list]:
    """Общий WHERE для board()/column_cards() — открытые сделки + опциональные фильтры."""
    where = ['d.outcome_at IS NULL']
    params: list = []
    if filters.get('assignee_id'):
        where.append('d.assignee_id = %s'); params.append(int(filters['assignee_id']))
    if filters.get('direction_id'):
        # Фильтр обязан совпадать с тем, что показано в карточке (_deal_directions_agg):
        # у сделки со снимком ищем по снимку, у прочих — по активным членствам ученика.
        # Иначе сделка, отфильтрованная по «Робототехнике», показывала бы «Шахматы».
        where.append("""(CASE WHEN d.directions_snapshot IS NOT NULL
            THEN d.directions_snapshot @> to_jsonb(%s::int)
            ELSE EXISTS (
                SELECT 1 FROM group_memberships fm
                JOIN groups fg ON fg.id = fm.group_id
                WHERE fm.student_id = d.student_id AND fm.active = true
                  AND fg.direction_id = %s) END)""")
        params.append(int(filters['direction_id']))
        params.append(int(filters['direction_id']))
    if filters.get('student'):
        # Поиск по имени ученика (per-column search в канбане). ILIKE — регистр
        # и раскладку не различаем; % экранировать не нужно (параметризованный %s).
        where.append('s.full_name ILIKE %s'); params.append(f"%{filters['student']}%")
    return ' AND '.join(where), params


def board(filters: dict | None = None) -> dict:
    """
    Доска: открытые сделки, сгруппированные по стадиям дефолтной воронки.
    Возвращает колонки в порядке sort_order с count и первыми N карточками.
    Остальные — через column_cards() («Показать ещё»).
    """
    filters = filters or {}
    from apps.renewals.models import RenewalPipeline, RenewalStage
    pipeline = RenewalPipeline.objects.get(is_default=True)
    # Терминальные (won/lost) колонки на доске не показываем: открытых сделок в них
    # не бывает (outcome_at ставится при закрытии), закрытие — через зоны drag'а,
    # архив — списочный вид с фильтром «Показать закрытые».
    stages = list(RenewalStage.objects.filter(pipeline=pipeline)
                  .exclude(kind__in=('won', 'lost')).order_by('sort_order'))

    where_sql, params = _board_where(filters)

    with connection.cursor() as cur:
        # JOIN students — на случай student-фильтра в where_sql (доска его штатно
        # не передаёт, но join 1:1 по FK безвреден и держит _board_where консистентным).
        cur.execute(f"""
            SELECT d.stage_id, COUNT(*) AS cnt
            FROM renewal_deal d
            JOIN students s ON s.id = d.student_id
            WHERE {where_sql} GROUP BY d.stage_id
        """, params)
        counts = {r[0]: r[1] for r in cur.fetchall()}

    columns = []
    for st in stages:
        cards = _deals_in_stage(st.id, where_sql, params, COLUMN_LIMIT, offset=0)
        columns.append({
            'stage_id': st.id, 'key': st.key, 'label': st.label, 'kind': st.kind,
            'color': st.color, 'count': counts.get(st.id, 0), 'cards': cards,
        })
    return {'columns': columns}


def _deals_in_stage(stage_id: int, where_sql: str, base_params: list,
                     limit: int, offset: int) -> list[dict]:
    with connection.cursor() as cur:
        cur.execute(f"""
            SELECT d.id, d.student_id, s.full_name AS student_name,
                   {DIRECTIONS_AGG_SQL} AS directions,
                   d.cycle_no,
                   d.due_at, a.full_name AS assignee_name,
                   d.frozen_until_month,
                   EXTRACT(DAY FROM now() - d.stage_entered_at)::int AS days_in_stage,
                   COALESCE((
                       SELECT SUM(CASE WHEN l.lesson_duration_minutes = 45
                                       THEN 0.5 ELSE 1 END)
                       FROM lesson_attendance la
                       JOIN lessons l ON l.id = la.lesson_id
                       WHERE la.student_id = d.student_id AND la.present = true), 0) AS attended
            FROM renewal_deal d
            JOIN students s ON s.id = d.student_id
            LEFT JOIN accounts a ON a.id = d.assignee_id
            WHERE {where_sql} AND d.stage_id = %s
            ORDER BY d.stage_entered_at
            LIMIT %s OFFSET %s
        """, base_params + [stage_id, limit, offset])
        cols = [c[0] for c in cur.description]
        cards = [dict(zip(cols, r)) for r in cur.fetchall()]
    # cycle_completed нужен фронту, чтобы во время drag'а скрыть зону
    # «Продлён» для сделок с незавершённым циклом (move всё равно ответит
    # 409, но так карточку туда даже не пытаются бросить, см. move_deal).
    for c in cards:
        attended = float(c.pop('attended') or 0)
        into = attended - (c['cycle_no'] - 1) * cycle.LESSONS_PER_CYCLE
        c['cycle_completed'] = into >= cycle.LESSONS_PER_CYCLE
    return _annotate_debt(cards)


def _annotate_debt(cards: list[dict]) -> list[dict]:
    """
    Баланс ученика и бейдж долга — батчем через apps.finances, без N+1.

    balance измеряется В УРОКАХ (оплачено минус посещено), не в рублях: ту же
    величину показывает drawer сделки («Баланс — −2 ур.»). Карточка канбана
    строит из неё бейдж «Долг N ур.», поэтому число нужно ей целиком, а не
    только знак.
    """
    from apps.finances.repository import balances_for_students
    ids = list({c['student_id'] for c in cards})
    if not ids:
        return cards
    balances = balances_for_students(ids)
    for c in cards:
        balance = float(balances.get(c['student_id'], 0))
        c['balance'] = balance
        c['debt'] = balance < 0
    return cards


def column_cards(stage_id: int, offset: int, filters: dict | None = None) -> dict:
    """
    Карточки одной колонки канбана: count (с учётом фильтров) + страница карточек
    от offset. Та же выборка/сортировка, что и в board(). Используется для
    «Показать ещё» и для поиска по имени ученика внутри колонки (filter[student]).
    count нужен, чтобы фронт знал, есть ли ещё совпадения (кнопка «Показать ещё»).
    """
    where_sql, params = _board_where(filters or {})
    with connection.cursor() as cur:
        cur.execute(f"""
            SELECT COUNT(*) FROM renewal_deal d
            JOIN students s ON s.id = d.student_id
            WHERE {where_sql} AND d.stage_id = %s
        """, params + [stage_id])
        count = cur.fetchone()[0]
    cards = _deals_in_stage(stage_id, where_sql, params, COLUMN_LIMIT, offset)
    return {'count': count, 'cards': cards}


def list_stages() -> list[dict]:
    from apps.renewals.models import RenewalPipeline, RenewalStage
    pipe = RenewalPipeline.objects.get(is_default=True)
    return list(RenewalStage.objects.filter(pipeline=pipe).order_by('sort_order')
                .values('id', 'key', 'label', 'color', 'kind', 'is_auto',
                        'allow_mid_cycle', 'sort_order'))


def create_stage(data: dict) -> dict:
    from apps.renewals.models import RenewalPipeline, RenewalStage
    from django.db.models import Max
    pipe = RenewalPipeline.objects.get(is_default=True)
    next_order = (RenewalStage.objects.filter(pipeline=pipe)
                  .aggregate(m=Max('sort_order'))['m'] or 0) + 1
    base_key = data.get('key') or _slugify_key(data['label'])
    key = _unique_stage_key(pipe, base_key)
    st = RenewalStage.objects.create(
        pipeline=pipe, key=key, label=data['label'], color=data.get('color'),
        kind=data['kind'], sort_order=next_order, is_auto=False,
        allow_mid_cycle=data.get('allow_mid_cycle', False))
    return _stage_dict(st)


def update_stage(stage_id: int, data: dict) -> dict | None:
    from apps.renewals.models import RenewalStage
    st = RenewalStage.objects.filter(id=stage_id).first()
    if st is None:
        return None
    for k in ('label', 'color', 'kind', 'allow_mid_cycle'):
        if k in data:
            setattr(st, k, data[k])
    st.save()
    return _stage_dict(st)


def delete_stage(stage_id: int) -> str:
    """Нельзя удалить стадию с ЛЮБЫМИ сделками (открытыми ИЛИ закрытыми — FK
    RESTRICT физически не даст) или единственную won/lost/progress."""
    from django.db.models.deletion import RestrictedError
    from apps.renewals.models import RenewalDeal, RenewalStage
    st = RenewalStage.objects.filter(id=stage_id).first()
    if st is None:
        return 'not_found'
    # закрытые сделки навсегда привязаны к won/lost-стадии (RESTRICT) — их наличие
    # тоже блокирует удаление, иначе st.delete() падает RestrictedError → 500.
    if RenewalDeal.objects.filter(stage_id=stage_id).exists():
        return 'has_open_deals'
    if st.is_auto or (RenewalStage.objects.filter(
            pipeline=st.pipeline, kind=st.kind).count() == 1 and st.kind in ('won', 'lost', 'progress')):
        return 'protected'
    try:
        st.delete()
    except RestrictedError:
        # гонка: сделка привязалась к стадии между проверкой и удалением.
        return 'has_open_deals'
    return 'ok'


def reorder_stages(order: list[int]) -> list[dict]:
    from apps.renewals.models import RenewalStage
    from django.db import transaction
    with transaction.atomic():
        for i, sid in enumerate(order):
            RenewalStage.objects.filter(id=sid).update(sort_order=i)
    return list_stages()


def _slugify_key(label: str) -> str:
    import re
    base = re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_') or 'stage'
    return base


def _unique_stage_key(pipeline, base_key: str) -> str:
    """
    Гарантирует уникальность key в рамках воронки (UNIQUE(pipeline, key)).
    _slugify_key схлопывает кириллицу (и вообще не-ASCII) в один и тот же
    fallback ('stage'), поэтому без этой проверки вторая такая стадия падает
    IntegrityError вместо понятной ошибки.
    """
    from apps.renewals.models import RenewalStage
    key = base_key
    suffix = 2
    while RenewalStage.objects.filter(pipeline=pipeline, key=key).exists():
        key = f'{base_key}_{suffix}'
        suffix += 1
    return key


def _stage_dict(st) -> dict:
    return {'id': st.id, 'key': st.key, 'label': st.label, 'color': st.color,
            'kind': st.kind, 'is_auto': st.is_auto,
            'allow_mid_cycle': st.allow_mid_cycle, 'sort_order': st.sort_order}


def list_deals(page: int, page_size: int, sort_by: str, sort_dir: str, filters: dict) -> dict:
    """Списочный вид: server-pagination. sort_dir валидируется вызывающим (view)."""
    where = ['1=1']
    params: list = []
    if not filters.get('include_closed'):
        where.append('d.outcome_at IS NULL')
    if filters.get('assignee_id'):
        where.append('d.assignee_id = %s'); params.append(int(filters['assignee_id']))
    if filters.get('direction_id'):
        where.append("""EXISTS (
            SELECT 1 FROM group_memberships fm
            JOIN groups fg ON fg.id = fm.group_id
            WHERE fm.student_id = d.student_id AND fm.active = true
              AND fg.direction_id = %s)""")
        params.append(int(filters['direction_id']))
    if filters.get('stage_id'):
        where.append('d.stage_id = %s'); params.append(int(filters['stage_id']))
    if filters.get('cycle_no'):
        where.append('d.cycle_no = %s'); params.append(int(filters['cycle_no']))
    if filters.get('student'):
        # Поиск по имени ученика — тот же ILIKE, что в _board_where (регистр/раскладку
        # не различаем; % экранировать не нужно — параметризованный %s).
        where.append('s.full_name ILIKE %s'); params.append(f"%{filters['student']}%")
    where_sql = ' AND '.join(where)

    sort_col = {
        'stage_entered_at': 'd.stage_entered_at',
        'cycle_no': 'd.cycle_no', 'student_name': 's.full_name',
    }.get(sort_by, 'd.stage_entered_at')
    direction = 'DESC' if sort_dir == 'desc' else 'ASC'

    with connection.cursor() as cur:
        # JOIN students нужен и в COUNT — фильтр по имени ученика (student ILIKE)
        # ссылается на s.full_name. FK 1:1, на total не влияет.
        cur.execute(
            f"SELECT COUNT(*) FROM renewal_deal d "
            f"JOIN students s ON s.id = d.student_id WHERE {where_sql}", params)
        total = cur.fetchone()[0]
        cur.execute(f"""
            SELECT d.id, s.full_name AS student_name,
                   {DIRECTIONS_AGG_SQL} AS directions,
                   d.cycle_no, st.label AS stage_label,
                   st.kind AS stage_kind, st.color AS stage_color,
                   d.due_at, a.full_name AS assignee_name,
                   d.frozen_until_month,
                   EXTRACT(DAY FROM now() - d.stage_entered_at)::int AS days_in_stage
            FROM renewal_deal d
            JOIN students s ON s.id = d.student_id
            JOIN renewal_stage st ON st.id = d.stage_id
            LEFT JOIN accounts a ON a.id = d.assignee_id
            WHERE {where_sql}
            ORDER BY {sort_col} {direction} NULLS LAST, d.id
            LIMIT %s OFFSET %s
        """, params + [page_size, (page - 1) * page_size])
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return {'rows': rows, 'total': total, 'page': page, 'page_size': page_size}
