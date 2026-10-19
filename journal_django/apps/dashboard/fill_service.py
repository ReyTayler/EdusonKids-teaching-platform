"""
Сервис вкладки «Заполнить» — сводка просроченных незаполненных занятий по школе.

Мёржит два источника (плановые уроки `apps.scheduling` + доп.уроки-отработки
`apps.extra_lessons`), отсекает по точному порогу overdue (момент занятия в МСК
уже наступил — та же логика, что `scheduling.services._planned_status`), сортирует
по (date, time) в направлении sort_dir (по умолчанию 'desc' — новые просрочки
сверху; 'asc' — старые сверху). Пагинацию делает вьюха (StandardPagination поверх
списка).
"""
from __future__ import annotations

import datetime

from apps.core.utils.dates import MSK, msk_now
from apps.extra_lessons import repository as extra_repo
from apps.scheduling import repository as scheduling_repo


def _passed(d: datetime.date, t: datetime.time | None, now: datetime.datetime) -> bool:
    """Момент занятия (МСК) уже наступил — overdue-порог, как в _planned_status."""
    occ_dt = datetime.datetime.combine(d, t or datetime.time(0, 0), tzinfo=MSK)
    return now >= occ_dt


def _fmt_time(t: datetime.time | None) -> str | None:
    return t.strftime('%H:%M') if t else None


def unfilled_lessons(
    teacher_id: int | None = None,
    sort_dir: str = 'desc',
    now: datetime.datetime | None = None,
) -> list[dict]:
    """Плоский список overdue незаполненных уроков (план + доп.уроки), сортировка
    по (date, time). sort_dir='desc' (по умолчанию) — новые просрочки сверху;
    'asc' — старые сверху. now инжектируется в тестах; в проде — msk_now()."""
    now = now or msk_now()
    today = now.date()
    tnames = scheduling_repo.teacher_names()
    out: list[dict] = []

    for r in scheduling_repo.unfilled_planned_lessons(today, teacher_id):
        if not _passed(r['scheduled_date'], r['scheduled_time'], now):
            continue
        effective = r['substitute_teacher_id'] or r['teacher_id']
        out.append({
            'kind': 'planned',
            'id': r['id'],
            'group_id': r['group_pk'],
            'group_name': r['group_name'],
            'teacher_id': effective,
            'teacher_name': tnames.get(effective),
            'direction_name': r['direction_name'],
            'direction_color': r['direction_color'],
            'lesson_number': float(r['lesson_number']) if r['lesson_number'] is not None else None,
            # Порядковый номер занятия (не вес для денег) — нужен уведомлениям:
            # на 45-мин курсах lesson_number = seq * 0.5, а преподавателю
            # показывается seq (см. apps.notifications.digests).
            'seq': r['seq'],
            'date': r['scheduled_date'].isoformat(),
            'time': _fmt_time(r['scheduled_time']),
        })

    for r in extra_repo.unfilled_extra_lessons(today, teacher_id):
        if not _passed(r['scheduled_date'], r['scheduled_time'], now):
            continue
        out.append({
            'kind': 'extra',
            'id': r['id'],
            'group_id': r['group_id'],
            'group_name': r['group_name'],
            'teacher_id': r['assigned_teacher_id'],
            'teacher_name': tnames.get(r['assigned_teacher_id']),
            'direction_name': None,
            'direction_color': None,
            'lesson_number': None,
            'seq': None,
            'date': r['scheduled_date'].isoformat(),
            'time': _fmt_time(r['scheduled_time']),
        })

    out.sort(key=lambda x: (x['date'], x['time'] or ''), reverse=(sort_dir == 'desc'))
    return out
