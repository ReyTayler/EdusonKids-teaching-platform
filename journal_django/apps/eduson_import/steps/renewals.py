"""Шаг «продления»: сделки KOTOKOD по посещаемости + статусы Eduson (спека §5.4).

ДВА ЭТАПА, ОБА — ШТАТНЫМИ ПУТЯМИ KOTOKOD, БЕЗ СВОЕЙ ЛОГИКИ ВОРОНКИ.
  1. apps.renewals.rebuild.rebuild_all — та же пересборка, что Синхро
     «rebuild-renewals»: циклы по 4 урока из посещаемости, закрытые «Продлён»/«Ушёл»,
     открытая сделка на авто-стадии. Свою транзакцию она открывает вложенной
     (savepoint внутри транзакции команды), уведомлений и on_commit-хуков не шлёт.
  2. Статус Eduson (renewals, одна строка на группу) → стадия открытой сделки
     каждого ученика группы через apps.renewals.services.move_deal — тот же вызов,
     что ручной перевод в интерфейсе: ворота переходов, снимок направлений,
     активность смены стадии, у «Продлён» — открытие следующего цикла.
     Дата закрытия won/lost — services.set_outcome_date (как ручная правка даты в
     карточке), заметка — services.add_comment. Автор — None («Система»).

ВОРОТА ВОРОНКИ НЕ ОБХОДЯТСЯ. move_deal отказывает, если правило KOTOKOD переход
запрещает: «Думает» и «Продлён» — только по отработанному циклу (4 урока) и с
«Ждём продление»; «Ждём продление» — авто-стадия, руками на неё не встают (её
ставит движок, когда цикл отработан). Отказ → сделка остаётся там, где её
поставила пересборка, заметка всё равно переносится, а в «на ручную проверку» —
группа, статус Eduson и стадия KOTOKOD. Пауза («Заморожен») и «Ушёл» разрешены в
любой момент цикла. Гейт «Продлён только при балансе > 0» снимается
(allow_nonpositive_balance — право admin в интерфейсе): оплаты импорт не
создаёт по решению владельца, баланс у всех ≤ 0, и без снятия ни одно продление
Eduson не перенеслось бы.

НЕТ ОТКРЫТОЙ СДЕЛКИ (архивная группа: пересборка закрыла цикл «Ушёл»; или у
ученика нет ни уроков, ни активного членства — сделок нет вовсе). Новую сделку
не создаём: в KOTOKOD номер цикла — функция посещаемости (services.create_deal
отказывает 'cycle_taken', если номер держит закрытая сделка), а переоткрывать
закрытую ради чужого статуса — менять историю, построенную по фактам. Если
последняя сделка уже стоит в целевой стадии (declined → «Ушёл») — ставим ей дату
закрытия Eduson и заметку; иначе заметка ложится на последнюю сделку (если она
есть), а случай — на ручную проверку.

⚠ Повторная Синхро «rebuild-renewals» сотрёт применённые здесь стадии (бэклог).

Отчёт и manual_review — без ПДн: коды групп, статусы, ключи стадий. Текст
заметки Eduson в отчёт не попадает (в нём бывают имена и телефоны).
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from decimal import Decimal

from apps.core.utils.dates import MSK
from apps.eduson_import.analysis import parse_moment
from apps.eduson_import.context import ImportContext, ImportDataError
from apps.eduson_import.sqlite_source import EdusonSource
from apps.eduson_import.steps import require_atomic
from apps.lessons.models import LessonAttendance
from apps.lessons.weights import lesson_weight
from apps.renewals import rebuild, services
from apps.renewals.cycle import LESSONS_PER_CYCLE
from apps.renewals.models import RenewalDeal, RenewalPipeline, RenewalStage
from apps.renewals.transitions import InvalidTransition

# Статус Eduson → ключ стадии дефолтной воронки KOTOKOD (спека §5.1/§5.4).
STAGE_BY_STATUS = {
    'new': 'awaiting_renewal',
    'contacted': 'thinking',
    'paused': 'frozen',
    'renewed': 'renewed',
    'declined': 'churned',
}
# Причина ухода в Eduson не хранится — тот же код, что ставит пересборка KOTOKOD.
LOST_REASON = 'unknown'
NOTE_PREFIX = 'Заметка Eduson: '


def import_renewals(src: EdusonSource, ctx: ImportContext) -> None:
    require_atomic('продления')
    summary = rebuild.rebuild_all(dry_run=False)
    # summary['samples'] не печатаем: там имена учеников.
    ctx.report.append(
        f'продления: пересборка KOTOKOD по посещаемости — учеников {summary["students"]}, '
        f'закрыто «Продлён» {summary["created_won"]}, «Ушёл» {summary["created_lost"]}, '
        f'открытых сделок {summary["created_open"]}')

    stages = {s.key: s for s in RenewalStage.objects.filter(
        pipeline=RenewalPipeline.objects.get(is_default=True))}
    missing = sorted(set(STAGE_BY_STATUS.values()) - set(stages))
    if missing:
        raise ImportDataError(f'в воронке продлений нет стадий {", ".join(missing)}')

    codes = {g['id']: str(g['code'] or '').strip() for g in src.rows('student_groups')}
    students = _students_by_group(src, ctx)
    seen: dict[int, str] = {}                       # Student.id → код группы первой записи
    applied = 0                                     # групп, где статус встал всем ученикам
    for r in src.rows('renewals'):                  # по group_id — детерминированно
        gid, status = r['group_id'], r['status']
        if gid not in ctx.group_by_eduson:
            raise ImportDataError(f'продление группы #{gid}: группа не найдена')
        if status not in STAGE_BY_STATUS:
            raise ImportDataError(f'продление группы {codes[gid]}: неизвестный статус {status!r}')
        code = codes[gid]
        if not students.get(gid):
            ctx.manual_review.append(f'продление группы {code} (статус {status}): в группе нет учеников — '
                                     'не перенесено')
            continue
        done = 0
        for sid in students[gid]:
            if sid in seen:
                ctx.manual_review.append(
                    f'продление группы {code} (статус {status}): у ученика уже применено продление группы '
                    f'{seen[sid]} — второй статус не применён')
                continue
            seen[sid] = code
            done += _apply(sid, r, stages[STAGE_BY_STATUS[status]], code, ctx)
        applied += done == len(students[gid])
    ctx.renewals_applied = applied


def _students_by_group(src: EdusonSource, ctx: ImportContext) -> dict[int, list[int]]:
    """group_id Eduson → Student.id (без пустых строк и без повторов склеенного ученика)."""
    out: dict[int, dict[int, None]] = defaultdict(dict)
    skipped = set(ctx.skipped_participants)
    for p in src.rows('participants'):
        if p['id'] not in skipped:
            out[p['group_id']][ctx.student_by_participant[p['id']]] = None
    return {gid: list(ids) for gid, ids in out.items()}


def _apply(student_id: int, r: dict, target: RenewalStage, code: str, ctx: ImportContext) -> int:
    """Статус Eduson → сделка ученика. 1 — статус применён (или уже стоял), 0 — на проверку."""
    status = r['status']
    where = f'продление группы {code} (статус {status})'
    note = str(r['note'] or '').strip()
    deal = (RenewalDeal.objects.select_related('stage')
            .filter(student_id=student_id, outcome_at__isnull=True).order_by('-cycle_no').first())

    if deal is None:
        last = (RenewalDeal.objects.select_related('stage')
                .filter(student_id=student_id).order_by('-cycle_no').first())
        if note and last is not None:
            services.add_comment(last.id, NOTE_PREFIX + note, None)
        if last is not None and last.stage.key == target.key:
            _set_outcome_date(last, r, where, ctx)
            return 1
        tail = '' if not note else (' Заметка добавлена к последней сделке.' if last else ' Заметка не перенесена.')
        state = f'последняя сделка — «{last.stage.key}»' if last else 'сделок нет (нет уроков и активной группы)'
        ctx.manual_review.append(f'{where}: открытой сделки нет, {state} — стадия «{target.key}» не применена.{tail}')
        return 0

    if note:
        services.add_comment(deal.id, NOTE_PREFIX + note, None)
    if deal.stage.key == target.key:
        return 1                                   # пересборка уже поставила ту же стадию
    try:
        services.move_deal(deal.id, target.id, LOST_REASON if target.kind == 'lost' else None, None,
                           frozen_until_month=None, allow_nonpositive_balance=True)
    except InvalidTransition:
        ctx.manual_review.append(
            f'{where}: правила воронки KOTOKOD не дают перевести сделку цикла {deal.cycle_no} '
            f'со стадии «{deal.stage.key}» в «{target.key}» (цикл не отработан или стадия автоматическая) — '
            'оставлена расчётная стадия, переведите вручную при необходимости')
        return 0
    if target.kind in ('won', 'lost'):
        _set_outcome_date(deal, r, where, ctx)
    return 1


def _set_outcome_date(deal: RenewalDeal, r: dict, where: str, ctx: ImportContext) -> None:
    """Дата закрытия — день updated_at Eduson по Москве (а не момент импорта):
    аналитика и «Переходимость» относят исход к месяцу outcome_at.

    Дата раньше начала цикла сделки (первого урока цикла) или позже дня импорта
    не ставится: исход до начала цикла или в будущем — заведомо не та дата (в
    legacy updated_at двигает любая правка записи), а отчёт по месяцам молча
    уехал бы. Остаётся момент закрытия сервисом, случай — на проверку."""
    moment = parse_moment(r['updated_at'])
    if moment is None:
        ctx.manual_review.append(f'{where}: у записи Eduson нет даты изменения — дата закрытия = момент импорта')
        return
    day = moment.astimezone(MSK).date()
    start = _cycle_start(deal.student_id, deal.cycle_no)
    if day > ctx.as_of or (start is not None and day < start):
        ctx.manual_review.append(
            f'{where}: дата изменения Eduson {day.isoformat()} вне цикла {deal.cycle_no} '
            f'(начало {start.isoformat() if start else "—"}, день импорта {ctx.as_of.isoformat()}) — '
            'дата закрытия = момент импорта, проверьте')
        return
    services.set_outcome_date(deal.id, day, None)


def _cycle_start(student_id: int, cycle_no: int) -> datetime.date | None:
    """День первого урока цикла: тот, после которого накопленный вес посещений
    (present=true, вес по канону lesson_weight) превысил (cycle_no−1)×4 — та же
    граница циклов, что у пересборки и apps.renewals.repository.cycle_direction_ids.
    None — уроков в цикле нет."""
    floor = Decimal((cycle_no - 1) * LESSONS_PER_CYCLE)
    total = Decimal(0)
    for day, minutes in (LessonAttendance.objects
                         .filter(student_id=student_id, present=True)
                         .order_by('lesson__lesson_date', 'lesson_id')
                         .values_list('lesson__lesson_date', 'lesson__lesson_duration_minutes')):
        total += lesson_weight(minutes)
        if total > floor:
            return day
    return None
