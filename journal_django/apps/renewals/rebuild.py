"""
Пересбор сделок продления из истории посещаемости («правда из данных»).

Чистая раскладка (plan_for_student / target_open_stage_key) отделена от записи в
БД (rebuild_all ниже) — раскладку покрываем юнит-тестами без БД.

Модель: цикл = 4 суммарных урока (LESSONS_PER_CYCLE), half-lesson уже в units.
Каждый пройденный рубеж i×4 → закрытая «Продлён»; хвост — одна открытая сделка
(или «Ушёл» для покинувшего). Посещение сверх рубежа = продление по факту
продолжения занятий (решение 2026-07-19). Ровно на рубеже (активный) → последний
цикл открыт на «Ждём продление».
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from apps.renewals.cycle import LESSONS_PER_CYCLE


@dataclass
class ClosedCycle:
    cycle_no: int
    kind: str            # 'renewed' | 'churned'
    date: date


@dataclass
class OpenCycle:
    cycle_no: int
    stage_key: str       # awaiting_renewal | awaiting_payment | no_lesson_yet | lesson_N
    due_date: date | None  # день 4-го урока для awaiting_renewal, иначе None
    # Реальная дата попадания на текущую авто-стадию (из посещаемости):
    #   awaiting_renewal → день 4-го урока цикла (= due_date);
    #   прогресс/ждём-оплату → день последнего отработанного урока цикла;
    #   None → сигнала нет (нет уроков в цикле) → оставляем дефолт stage_entered_at.
    entered: date | None = None


@dataclass
class StudentPlan:
    closed: list[ClosedCycle]
    open: OpenCycle | None


def target_open_stage_key(into: float, debt: bool, progress_keys: list[str]) -> str:
    """Ключ авто-стадии открытой сделки — по правилу engine._target_auto_stage:
    цикл отработан (into>=4) → awaiting_renewal (приоритетнее оплаты); долг →
    awaiting_payment; иначе прогресс-стадия по числу отработанных уроков цикла."""
    if into >= LESSONS_PER_CYCLE:
        return 'awaiting_renewal'
    if debt:
        return 'awaiting_payment'
    idx = min(max(int(into), 0), len(progress_keys) - 1)
    return progress_keys[idx]


def plan_for_student(visits: list[tuple[date, float]], *, is_active: bool,
                     balance: float, progress_keys: list[str]) -> StudentPlan:
    """visits — посещения (present=true) в хронологии [(date, units)]."""
    total = 0.0
    boundary = float(LESSONS_PER_CYCLE)
    cycle_i = 1
    closed: list[ClosedCycle] = []
    for day, units in visits:
        total += float(units)
        while total >= boundary:
            closed.append(ClosedCycle(cycle_no=cycle_i, kind='renewed', date=day))
            cycle_i += 1
            boundary += LESSONS_PER_CYCLE
    completed_full = cycle_i - 1
    rem = total - completed_full * LESSONS_PER_CYCLE
    debt = balance <= 0

    if is_active:
        if rem == 0 and completed_full >= 1:
            # цикл завершён ровно — это не «Продлён», а «Ждём продление»:
            # откатываем последний won, делаем его открытой сделкой-решением.
            last = closed.pop()
            open_cycle = OpenCycle(cycle_no=last.cycle_no,
                                   stage_key='awaiting_renewal', due_date=last.date,
                                   entered=last.date)  # встал на «Ждём продление» в день 4-го урока
        else:
            key = target_open_stage_key(rem, debt, progress_keys)
            # На прогресс-/ждём-оплату-стадию сделка «встаёт» в день последнего
            # отработанного урока цикла (при rem>0 это последний визит вообще).
            entered = visits[-1][0] if (rem > 0 and visits) else None
            open_cycle = OpenCycle(cycle_no=completed_full + 1, stage_key=key,
                                   due_date=None, entered=entered)
        return StudentPlan(closed=closed, open=open_cycle)

    # Покинувший с неполным последним циклом → «Ушёл»: занятия брошены на середине.
    if rem > 0 and visits:
        closed.append(ClosedCycle(cycle_no=completed_full + 1, kind='churned',
                                  date=visits[-1][0]))
        return StudentPlan(closed=closed, open=None)

    # Ровно на рубеже цикла решение о продлении не принято — ни «Продлён», ни
    # «Ушёл». Раньше такой ученик оставался со всеми циклами won и без открытой
    # сделки, то есть пропадал из воронки: менеджер его не видел и связаться было
    # неоткуда. Правило рубежа одно для всех (см. cycle.open_cycle_no): последний
    # won откатывается в открытую «Ждём продление» независимо от того, числится ли
    # ученик в активной группе (решение 2026-07-30).
    if completed_full >= 1:
        last = closed.pop()
        return StudentPlan(closed=closed, open=OpenCycle(
            cycle_no=last.cycle_no, stage_key='awaiting_renewal',
            due_date=last.date, entered=last.date))
    return StudentPlan(closed=closed, open=None)


# --- Оркестратор: чтение источников правды + атомарная перезапись сделок ---

from datetime import datetime, time  # noqa: E402

from django.db import connection, transaction  # noqa: E402
from django.utils import timezone as tz  # noqa: E402

from apps.finances.repository import balances_for_students  # noqa: E402
from apps.renewals.models import (  # noqa: E402
    RenewalActivity, RenewalDeal, RenewalPipeline, RenewalStage)


def _dt(day: date):
    """date → aware datetime (полдень; важен день, не время)."""
    return tz.make_aware(datetime.combine(day, time(12, 0)))


def _load_attendance() -> dict[int, list[tuple[date, float]]]:
    """Вся посещаемость present=true в хронологии, сгруппированная по ученику."""
    by_student: dict[int, list] = {}
    with connection.cursor() as cur:
        cur.execute("""
            SELECT la.student_id, l.lesson_date,
                   CASE WHEN l.lesson_duration_minutes = 45 THEN 0.5 ELSE 1 END AS units
            FROM lesson_attendance la
            JOIN lessons l ON l.id = la.lesson_id
            WHERE la.present = true
            ORDER BY la.student_id, l.lesson_date, l.id
        """)
        for sid, day, units in cur.fetchall():
            by_student.setdefault(sid, []).append((day, float(units)))
    return by_student


def _active_students() -> set[int]:
    with connection.cursor() as cur:
        cur.execute("SELECT DISTINCT student_id FROM group_memberships WHERE active = true")
        return {r[0] for r in cur.fetchall()}


def _stage_context(pipe) -> tuple[dict, list[str]]:
    stages = {s.key: s for s in RenewalStage.objects.filter(pipeline=pipe)}
    progress_keys = [s.key for s in RenewalStage.objects
                     .filter(pipeline=pipe, kind='progress', is_auto=True)
                     .order_by('sort_order')]
    return stages, progress_keys


def _current_open_labels() -> dict[int, tuple[str, str]]:
    """student_id → (имя, текущая стадия открытой сделки) для превью в dry-run."""
    out: dict[int, tuple[str, str]] = {}
    with connection.cursor() as cur:
        cur.execute("""
            SELECT d.student_id, s.full_name, st.label
            FROM renewal_deal d
            JOIN students s ON s.id = d.student_id
            JOIN renewal_stage st ON st.id = d.stage_id
            WHERE d.outcome_at IS NULL
        """)
        for sid, name, label in cur.fetchall():
            out[sid] = (name, label)
    return out


def _write_plan(sid: int, plan: StudentPlan, pipe, stages: dict) -> None:
    # Пересбор сносит все сделки (rebuild_all) и создаёт заново, поэтому снимки
    # направлений он обязан проставить сам — иначе они терялись бы при каждом
    # запуске «Пересобрать сделки». Источник тот же, что и в штатном пути:
    # направления по урокам цикла, которые пересбор и так читает.
    from apps.renewals.repository import cycle_direction_ids

    for c in plan.closed:
        stage = stages['renewed'] if c.kind == 'renewed' else stages['churned']
        deal = RenewalDeal.objects.create(
            student_id=sid, cycle_no=c.cycle_no, pipeline=pipe, stage=stage,
            due_at=(c.date if c.kind == 'renewed' else None),
            outcome_at=_dt(c.date),
            directions_snapshot=cycle_direction_ids(sid, c.cycle_no),
            reason_code=('unknown' if c.kind == 'churned' else None))
        RenewalDeal.objects.filter(id=deal.id).update(
            stage_entered_at=_dt(c.date), created_at=_dt(c.date))
        RenewalActivity.objects.create(
            deal=deal, kind='system', to_stage=stage,
            body=('Пересобрано из истории посещений' if c.kind == 'renewed'
                  else 'Пересобрано из истории: ученик прекратил занятия'))
    if plan.open is not None:
        stage = stages[plan.open.stage_key]
        deal = RenewalDeal.objects.create(
            student_id=sid, cycle_no=plan.open.cycle_no, pipeline=pipe, stage=stage,
            due_at=plan.open.due_date)
        # Реальная дата входа на авто-стадию из посещаемости (иначе — дефолтный
        # now(): у сделки без уроков в цикле исторического сигнала нет).
        if plan.open.entered is not None:
            RenewalDeal.objects.filter(id=deal.id).update(
                stage_entered_at=_dt(plan.open.entered), created_at=_dt(plan.open.entered))
        RenewalActivity.objects.create(
            deal=deal, kind='system', to_stage=stage, body='Сделка пересобрана')


def rebuild_all(dry_run: bool = False) -> dict:
    """Пересобрать сделки всех учеников из истории посещаемости. dry_run — только
    план (ничего не пишет). apply — атомарно снести все сделки и записать заново."""
    pipe = RenewalPipeline.objects.get(is_default=True)
    stages, progress_keys = _stage_context(pipe)
    by_student = _load_attendance()
    active = _active_students()
    student_ids = set(by_student) | active
    balances = balances_for_students(list(student_ids))

    plans: dict[int, StudentPlan] = {}
    created_won = created_lost = created_open = 0
    for sid in student_ids:
        plan = plan_for_student(by_student.get(sid, []), is_active=sid in active,
                                balance=float(balances.get(sid, 0)),
                                progress_keys=progress_keys)
        plans[sid] = plan
        created_won += sum(1 for c in plan.closed if c.kind == 'renewed')
        created_lost += sum(1 for c in plan.closed if c.kind == 'churned')
        created_open += 1 if plan.open is not None else 0

    deals_deleted = RenewalDeal.objects.count()

    # Превью: до 10 учеников с открытой сделкой — текущая стадия → планируемая.
    current = _current_open_labels()
    samples = []
    for sid, plan in plans.items():
        if plan.open is None or sid not in current:
            continue
        name, cur_label = current[sid]
        samples.append({'student': name, 'from': cur_label,
                        'to': stages[plan.open.stage_key].label})
        if len(samples) >= 10:
            break

    if not dry_run:
        with transaction.atomic():
            RenewalDeal.objects.all().delete()  # каскадит renewal_activity, аудит в pghistory
            for sid, plan in plans.items():
                _write_plan(sid, plan, pipe, stages)

    return {
        'entity': 'renewals-rebuild', 'dry_run': dry_run,
        'deals_deleted': deals_deleted,
        'created_won': created_won, 'created_lost': created_lost,
        'created_open': created_open, 'students': len(student_ids),
        'samples': samples,
    }


def _open_deals_for_dates() -> list[tuple[int, int, int, bool, str, date | None]]:
    """Открытые сделки: (student_id, deal_id, cycle_no, stage_is_auto, stage_key, entered_date).

    entered_date — по МСК, не по сессионному часовому поясу PostgreSQL (UTC):
    stage_entered_at — timestamptz, ::date без AT TIME ZONE уезжает на день назад
    в окне 00:00-02:59 по Москве (тот же класс бага, что в analytics.py/engine.py)."""
    with connection.cursor() as cur:
        cur.execute("""
            SELECT d.student_id, d.id, d.cycle_no, st.is_auto, st.key,
                   (d.stage_entered_at AT TIME ZONE 'Europe/Moscow')::date
            FROM renewal_deal d
            JOIN renewal_stage st ON st.id = d.stage_id
            WHERE d.outcome_at IS NULL
        """)
        return [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in cur.fetchall()]


def backfill_open_dates(dry_run: bool = False) -> dict:
    """
    НЕДЕСТРУКТИВНО восстановить реальную `stage_entered_at` открытых сделок из
    истории посещаемости — НЕ трогая стадию, ответственного, комментарии (в
    отличие от rebuild_all, который сносит все сделки).

    Чиним только открытые сделки на АВТО-стадии (прогресс/ждём-оплату/ждём-
    продление/нет-урока), чей номер цикла совпадает с расчётным по посещаемости:
    для них дата входа на стадию выводится из уроков. Сделки, вручную уведённые
    в decision-стадию («Думает» и т.п.), пропускаем — дату ручного перехода из
    посещаемости не восстановить, менеджерский ввод не перетираем. Закрытые
    сделки не трогаем: их outcome_at уже реальный (пересбор/ручное закрытие).
    """
    pipe = RenewalPipeline.objects.get(is_default=True)
    _stages, progress_keys = _stage_context(pipe)
    by_student = _load_attendance()
    active = _active_students()
    balances = balances_for_students(list(set(by_student) | active))

    updates: list[tuple[int, date, date | None]] = []  # (deal_id, entered, due)
    skipped_manual = skipped_no_signal = unchanged = 0
    samples: list[dict] = []

    for sid, did, cyc, is_auto, stage_key, cur_entered in _open_deals_for_dates():
        plan = plan_for_student(by_student.get(sid, []), is_active=sid in active,
                                balance=float(balances.get(sid, 0)),
                                progress_keys=progress_keys)
        # Чиним, только если сделка стоит РОВНО на расчётной по посещаемости
        # авто-стадии того же цикла — тогда выведенная дата ей соответствует.
        # Иначе (ручной перенос, расхождение с движком) не трогаем.
        if (not is_auto or plan.open is None
                or plan.open.cycle_no != cyc or plan.open.stage_key != stage_key):
            skipped_manual += 1
            continue
        if plan.open.entered is None:
            skipped_no_signal += 1
            continue
        if cur_entered == plan.open.entered:
            unchanged += 1
            continue
        updates.append((did, plan.open.entered, plan.open.due_date))
        if len(samples) < 10:
            samples.append({'deal_id': did,
                            'from': cur_entered.isoformat() if cur_entered else None,
                            'to': plan.open.entered.isoformat()})

    if not dry_run:
        with transaction.atomic():
            for did, entered, due in updates:
                fields = {'stage_entered_at': _dt(entered)}
                if due is not None:
                    fields['due_at'] = due
                RenewalDeal.objects.filter(id=did).update(**fields)

    return {
        'entity': 'renewals-open-dates', 'dry_run': dry_run,
        'updated': len(updates), 'unchanged': unchanged,
        'skipped_manual': skipped_manual, 'skipped_no_signal': skipped_no_signal,
        'samples': samples,
    }
