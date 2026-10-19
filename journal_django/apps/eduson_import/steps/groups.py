"""Шаг «группы»: student_groups → Group + GroupScheduleSlot; participants → Student
(склейка по связке признаков) + GroupMembership; заметки → StudentComment.

ПОЧЕМУ ORM, А НЕ СЕРВИСЫ KOTOKOD.
  * groups.services.create_group после записи строит план занятий (автоплан) от
    слотов — у импорта план целиком строится шагом уроков из сессий Eduson, второй
    план задвоил бы позиции. groups.repository.create_group не принимает active,
    lesson_number_offset и created_at — пришлось бы догонять UPDATE'ом, а это лишнее
    событие в журнале изменений на каждую группу.
  * memberships.add_membership всегда ставит active=True и считает баланс на ответ;
    у импорта членство архивной группы — архивное.
Поэтому — Model.objects.create с полями, которые заполняют эти репозитории, а их
инварианты проверяются здесь явно: CHECK модели (длительность 60, 1..7 занятий в
неделю, lessons_total > 0, день недели 0..6), уникальность слота и членства,
«≤1 активного ученика в индивидуальной группе» (тот же инвариант, что держит
apps.memberships.repository._assert_individual_capacity при add/update/place).

Пройденное до учёта (спека §5.4, «продолжение курса» как memberships._seed_continuation):
  Group.lesson_number_offset = N; GroupMembership.lessons_done = N + Σ вес проведённых held
группы — настоящих и с исправленным годом (вкладка «Июнь 2027», ImportContext.year_fixed),
то есть всех, кроме фиктивных (вес — apps.lessons.weights.lesson_weight: 45 мин = 0.5),
плюс уроки из таблиц Eduson (--sheets-dir, ImportContext.sheet_weight).
N — из ImportContext (уже за вычетом уроков из таблиц).
"""
from __future__ import annotations

import datetime
import json
import re
from collections import defaultdict
from decimal import Decimal

from django.utils import timezone

from apps.eduson_import.context import EDUSON_LESSON_MINUTES, ImportContext, ImportDataError
from apps.eduson_import.identity import REASON_AMBIGUOUS, ResolvedStudent, resolve_students
from apps.eduson_import.normalize import norm_email, norm_name, norm_phone, norm_tg
from apps.eduson_import.sqlite_source import EdusonSource
from apps.eduson_import.steps import require_atomic
from apps.groups.models import Group, GroupScheduleSlot
from apps.lessons.weights import lesson_weight
from apps.memberships.models import GroupMembership
from apps.students.models import Student, StudentComment

# Урок Eduson — 60 минут (legacy services/schedule.js:6 REGULAR_DURATION). Отработки по
# 45 минут — свойство урока, не группы: их длительность пишет шаг уроков.
GROUP_DURATION_MINUTES = EDUSON_LESSON_MINUTES
FORMATS = {'individual': True, 'group': False}     # format → is_individual

# Поле ученика ← колонка participants, и как сравнивать значения при склейке.
_STUDENT_FIELDS = (
    ('parent1_name', 'parent_name', norm_name),
    ('parent1_phone', 'parent_phone', norm_phone),
    ('parent1_email', 'parent_email', norm_email),
    ('parent1_telegram', 'parent_tg', norm_tg),
    ('phone', 'child_phone', norm_phone),
    ('email', 'child_email', norm_email),
    ('telegram', 'child_tg', norm_tg),
    ('crm_link', 'amo_url', str.strip),
)
_TIME_RE = re.compile(r'^(\d{1,2}):(\d{2})(?::\d{2})?$')


def _text(value) -> str:
    return str(value or '').strip()


def _time(value: str, code: str) -> datetime.time:
    m = _TIME_RE.fullmatch(_text(value))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ImportDataError(f'группа {code}: время слота {value!r} не в формате ЧЧ:ММ')
    return datetime.time(int(m.group(1)), int(m.group(2)))


def _slot_rule(g: dict) -> list[tuple[object, str]]:
    """(день, время) по правилу legacy: schedule_slots, если это непустой JSON-список
    (битый JSON legacy считает пустым), иначе default_dow + default_time. Время слота
    без своего — default_time группы, как в legacy генераторе."""
    try:
        slots = json.loads(g['schedule_slots']) if g['schedule_slots'] else None
    except (TypeError, ValueError):
        slots = None
    default_time = _text(g['default_time'])
    if isinstance(slots, list) and slots:
        return [(s.get('dow'), _text(s.get('time')) or default_time) for s in slots if isinstance(s, dict)]
    if g['default_dow'] is not None:
        return [(g['default_dow'], default_time)]
    return []


def _slots(g: dict, ctx: ImportContext) -> list[tuple[int, datetime.time]]:
    out: list[tuple[int, datetime.time]] = []
    for dow, hhmm in _slot_rule(g):
        try:
            day = int(dow)
        except (TypeError, ValueError):
            day = -1
        if not 0 <= day <= 6:
            raise ImportDataError(f'группа {g["code"]}: день недели слота {dow!r} вне 0..6')
        if not hhmm:
            ctx.report.append(f'группа {g["code"]}: слот без времени (день {day}) не перенесён')
            continue
        slot = (day, _time(hhmm, g['code']))
        if slot not in out:  # legacy допускал повтор слота; в KOTOKOD он уникален
            out.append(slot)
    return out


def _date(value, code: str) -> datetime.date | None:
    if not _text(value):
        return None
    try:
        return datetime.date.fromisoformat(_text(value))
    except ValueError:
        raise ImportDataError(f'группа {code}: дата старта {value!r} не в формате ГГГГ-ММ-ДД') from None


def _created_at(value, now: datetime.datetime) -> datetime.datetime:
    try:
        moment = datetime.datetime.fromisoformat(_text(value))
    except ValueError:
        return now  # у групп из первичной загрузки метки может не быть — это не ошибка данных
    return moment if moment.tzinfo else moment.replace(tzinfo=datetime.timezone.utc)


def import_groups(src: EdusonSource, ctx: ImportContext) -> None:
    require_atomic('группы')
    now = timezone.now()
    groups = {g['id']: g for g in src.rows('student_groups')}
    _import_group_rows(groups, ctx, now)
    members = _import_students(src, groups, ctx, now)
    _import_memberships(src, groups, members, ctx)


# ── группы и слоты ──────────────────────────────────────────────────────────

def _import_group_rows(groups: dict[int, dict], ctx: ImportContext, now) -> None:
    for gid, g in groups.items():
        code = _text(g['code'])
        if g['format'] not in FORMATS:
            raise ImportDataError(f'группа {code}: неизвестный формат {g["format"]!r}')
        if g['teacher_id'] not in ctx.teacher_by_eduson:
            raise ImportDataError(f'группа {code}: нет преподавателя (в KOTOKOD он обязателен)')
        slots = _slots(g, ctx)
        if not slots:
            ctx.report.append(f'группа {code}: нет расписания (слотов) — план строится по урокам Eduson')
        if len(slots) > 7:
            raise ImportDataError(f'группа {code}: {len(slots)} занятий в неделю, в KOTOKOD не больше 7')
        n = ctx.pre_system.get(gid, Decimal(0))
        group = Group.objects.create(
            name=code, direction_id=ctx.direction_by_course[ctx.course_by_group[gid]],
            teacher_id=ctx.teacher_by_eduson[g['teacher_id']], is_individual=FORMATS[g['format']],
            lesson_duration_minutes=GROUP_DURATION_MINUTES, lessons_per_week=max(1, len(slots)),
            # lessons_total не заполняется: длина курса — из направления (см. _report_packages).
            group_start_date=_date(g['start_date'], code), lessons_total=None,
            lesson_number_offset=n, active=bool(g['active']), created_at=_created_at(g['created_at'], now),
        )
        ctx.group_by_eduson[gid] = group.id
        GroupScheduleSlot.objects.bulk_create(
            [GroupScheduleSlot(group=group, day_of_week=d, start_time=t) for d, t in slots])
    _report_packages(groups, ctx)


def _report_packages(groups: dict[int, dict], ctx: ImportContext) -> None:
    """Решение владельца 2026-10-19: package_size Eduson — размер купленного пакета,
    а не длина курса. Group.lessons_total остаётся NULL («как в направлении»), иначе
    повторный импорт затирал бы длину курса, заданную направлением, и прогресс курса
    считался бы от пакета. В отчёт — только сколько пакетов не перенесено."""
    with_package = sum(1 for g in groups.values() if g['package_size'] is not None)
    if with_package:
        ctx.report.append(f'package_size Eduson не переносится в длину курса (lessons_total пуст, длина '
                          f'из направления), групп с пакетом: {with_package}')


# ── ученики ─────────────────────────────────────────────────────────────────

def _ids(ids) -> str:
    return ', '.join(f'#{i}' for i in ids)


def _first(rows: list[dict], column: str) -> str | None:
    return next((_text(r[column]) for r in rows if _text(r[column])), None)


def _import_students(src: EdusonSource, groups: dict[int, dict], ctx: ImportContext,
                     now) -> dict[int, list[tuple[int, ResolvedStudent]]]:
    """Ученики + комментарии; → group_id Eduson → [(Student.id, ученик)] в порядке участников."""
    participants = src.rows('participants')
    for p in participants:
        if p['group_id'] not in groups:
            raise ImportDataError(f'участник #{p["id"]}: группа #{p["group_id"]} не найдена')
    code_of = {gid: _text(g['code']) for gid, g in groups.items()}

    resolution = resolve_students(participants)
    ctx.skipped_participants = list(resolution.skipped_empty)
    if resolution.skipped_empty:
        ctx.report.append('пропущены пустые строки участников (legacy createGroup без участников): '
                          + _ids(resolution.skipped_empty))
    for item in resolution.manual_review:
        ctx.review_participants += len(item.participant_ids)
        if item.reason == REASON_AMBIGUOUS:
            what = 'запись подходит нескольким ученикам — заведена отдельным учеником'
        else:
            what = f'совпали имя ребёнка и телефон родителя, но расходится {item.reason} — заведены разными учениками'
        ctx.manual_review.append(f'участники {_ids(item.participant_ids)}: {what}')

    members: dict[int, list[tuple[int, ResolvedStudent]]] = defaultdict(list)
    for rs in resolution.students:
        rows = rs.rows
        codes = list(dict.fromkeys(code_of[r['group_id']] for r in rows))
        name = _first(rows, 'child_name')
        if name is None:
            # full_name обязателен, а строка Eduson без имени (только возраст/контакт) —
            # всё же ребёнок группы. Код группы в заглушке отличает таких учеников в
            # списках друг от друга и ведёт к карточке, где имя надо дописать.
            name = 'Без имени · ' + ', '.join(codes)
            ctx.manual_review.append(f'участники {_ids(rs.participant_ids)}: нет имени ребёнка — '
                                     'ученик заведён с именем-заглушкой, впишите имя')
        _report_conflicts(rs, ctx)
        student = Student.objects.create(
            full_name=name, created_at=now,
            **{field: _first(rows, column) for field, column, _ in _STUDENT_FIELDS},
        )
        for pid in rs.participant_ids:
            ctx.student_by_participant[pid] = student.id
        for r in rows:
            members[r['group_id']].append((student.id, rs))
        _student_comments(student, rows, code_of, ctx)
    return members


def _report_conflicts(rs: ResolvedStudent, ctx: ImportContext) -> None:
    """У склеенного ученика в разных записях бывают разные контакты — берётся первая
    запись, остальные значения остаются в архиве. В отчёт — только названия полей."""
    if len(rs.rows) < 2:
        return
    differ = [field for field, column, norm in _STUDENT_FIELDS
              if len({norm(_text(r[column])) for r in rs.rows if _text(r[column])}) > 1]
    if differ:
        ctx.report.append(f'ученик из участников {_ids(rs.participant_ids)}: разные значения '
                          f'{", ".join(differ)} — взяты из первой записи')


def _student_comments(student: Student, rows: list[dict], code_of: dict[int, str], ctx: ImportContext) -> None:
    ages = sorted({r['age'] for r in rows if isinstance(r['age'], int) and not isinstance(r['age'], bool)})
    bodies = []
    if ages:
        # Возраст числом KOTOKOD не хранит (вычисляет из birth_date) — спека §4.2.
        bodies.append(f'Возраст на {ctx.as_of:%d.%m.%Y} по данным Eduson: {"/".join(map(str, ages))}')
    bodies += [f'[{code_of[r["group_id"]]}] {_text(r["notes"])}' for r in rows if _text(r['notes'])]
    StudentComment.objects.bulk_create([StudentComment(student=student, body=b) for b in bodies])


# ── членства и заметки групп ────────────────────────────────────────────────

def _import_memberships(src: EdusonSource, groups: dict[int, dict],
                        members: dict[int, list[tuple[int, ResolvedStudent]]], ctx: ImportContext) -> None:
    held_weight: dict[int, Decimal] = defaultdict(Decimal)
    for s in src.rows('sessions'):
        if s['status'] == 'held' and s['id'] not in ctx.fake_held:
            held_weight[s['group_id']] += lesson_weight(s['duration_min'])

    for gid, g in groups.items():
        code = _text(g['code'])
        # Одна и та же склейка может прийти двумя строками одной группы — членство одно.
        student_ids = list(dict.fromkeys(sid for sid, _ in members.get(gid, ())))
        if not student_ids:
            ctx.report.append(f'группа {code}: без учеников')
            if _text(g['notes']):
                ctx.report.append(f'группа {code}: заметка группы не перенесена — некому её приписать')
            continue
        if len(student_ids) < len(members[gid]):
            ctx.report.append(f'группа {code}: один ученик записан в группу дважды — членство одно')
        active = bool(g['active'])
        if active and FORMATS[g['format']] and len(student_ids) > 1:
            raise ImportDataError(f'группа {code}: индивидуальная, но в ней {len(student_ids)} ученика — '
                                  'в KOTOKOD у индивидуальной группы не больше одного активного ученика')
        done = ctx.pre_system.get(gid, Decimal(0)) + held_weight.get(gid, Decimal(0)) + ctx.sheet_weight(gid)
        GroupMembership.objects.bulk_create([
            GroupMembership(group_id=ctx.group_by_eduson[gid], student_id=sid, lessons_done=done,
                            start_date=_date(g['start_date'], code), active=active)
            for sid in student_ids
        ])
        if _text(g['notes']):
            StudentComment.objects.bulk_create([
                StudentComment(student_id=sid, body=f'[{code}] Заметка группы: {_text(g["notes"])}')
                for sid in student_ids
            ])
