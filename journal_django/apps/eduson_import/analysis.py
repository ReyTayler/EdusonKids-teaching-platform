"""Анализ данных Eduson — чистые функции над EdusonSource, без БД Django.

Единственный источник правды для шагов импорта (группы, уроки) и сверки: N
«пройдено до учёта», held с ошибочным годом и фиктивные held, преподаватель группы на дату, дата «до»
переноса, дата удалённой сессии. Правила — спека §5.4; здесь — как они
выводятся из того, что лежит в SQLite. Шаги берут результаты из ImportContext
(считаются один раз), а не зовут функции сами.

Журнал Eduson (audit_log) пишет legacy record() (services/audit.js:24-32):
ts — new Date().toISOString() (UTC), before/after — JSON-снимки. Порядок записей —
(ts, id): ts в legacy монотонен, id — тай-брейк, чтобы результат не зависел от
порядка строк в файле.

РАЗБОР ОДИН РАЗ. student_groups, sessions и audit_log (с JSON) читаются и
разбираются один раз на источник (_parsed) и дальше переиспользуются всеми
функциями. Кэш держится, пока жив объект EdusonSource: база открыта только на
чтение и за время импорта не меняется.
"""
from __future__ import annotations

import bisect
import datetime
import json
import weakref
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from zoneinfo import ZoneInfo

from apps.eduson_import.context import ImportDataError
from apps.eduson_import.sqlite_source import EdusonSource

# Даты уроков Eduson — по Москве (legacy mskDateStr), метки журнала — UTC.
MSK = ZoneInfo('Europe/Moscow')

# Действия над уроком, чей before хранит дату урока на момент записи.
_SESSION_DATE_ACTIONS = frozenset({'move_session', 'session_patch', 'cancel_keep_total', 'delete_session'})
_DELETE_ACTIONS = frozenset({'cancel_keep_total', 'delete_session'})
# Действия группы, которые удаляют будущие scheduled и кладут их снимки в
# before.oldSessions (legacy routes/groups.js:133,152,171; revert — audit.js:76,84).
_REPLACE_ACTIONS = frozenset({'reschedule', 'freeze', 'update_progress'})


def _date(value, what: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ImportDataError(f'{what}: дата не в формате ГГГГ-ММ-ДД') from None


def _int(value) -> int | None:
    """Целый номер урока; NULL, дробь, строка — «номера нет»."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass
class _Parsed:
    groups: list[dict]
    sessions: list[dict]
    session_dates: dict[int, datetime.date]   # sessions.id → дата (проверена)
    audit: list[dict]                          # по (ts, id), с ts-datetime и before-объектом
    audit_by_id: dict[int, dict] = field(default_factory=dict)


_CACHE: weakref.WeakKeyDictionary[EdusonSource, _Parsed] = weakref.WeakKeyDictionary()


def _parsed(src: EdusonSource) -> _Parsed:
    if src in _CACHE:
        return _CACHE[src]
    sessions = src.rows('sessions')
    dates = {s['id']: _date(s['date'], f'сессия #{s["id"]}') for s in sessions}
    audit = []
    for a in src.rows('audit_log'):
        try:
            ts = datetime.datetime.fromisoformat(a['ts'])
            before = json.loads(a['before_json']) if a['before_json'] else None
            after = json.loads(a['after_json']) if a['after_json'] else None
        except (TypeError, ValueError):
            raise ImportDataError(
                f'журнал Eduson, запись #{a["id"]}: не разбирается ts, before_json или after_json') from None
        if ts.tzinfo is None:  # toISOString всегда с 'Z'; без зоны — считаем UTC, как legacy
            ts = ts.replace(tzinfo=datetime.timezone.utc)
        audit.append({**a, 'ts': ts, 'before': before, 'after': after})
    audit.sort(key=lambda a: (a['ts'], a['id']))
    parsed = _Parsed(src.rows('student_groups'), sessions, dates, audit, {a['id']: a for a in audit})
    _CACHE[src] = parsed
    return parsed


# ── held с ошибочным годом, фиктивные held и пройденное до учёта ─────────────

def corrected_year(day: datetime.date, as_of: datetime.date) -> datetime.date | None:
    """Дата held, отмеченного в таблицах Eduson на вкладке следующего года (решение
    владельца 2026-10-19: все 544 held «2027-06-DD» — настоящие уроки июня 2026,
    отмеченные на вкладке «Июнь 2027»), или None.

    Год исправляется, только если held стоит в календарном году ПОЗЖЕ дня импорта и
    та же дата годом раньше уже наступила (≤ as_of). Требование «год позже» — защита
    от ложных срабатываний: held на завтра того же года — не «не та вкладка», и
    отправлять его на год назад значило бы выдумать урок. 29 февраля без пары в
    прошлом году — None: дату не придумываем.
    """
    if day <= as_of or day.year <= as_of.year:
        return None
    try:
        fixed = day.replace(year=day.year - 1)
    except ValueError:
        return None
    return fixed if fixed <= as_of else None


def _held_by_group(p: _Parsed, as_of: datetime.date) -> dict[int, tuple[list[dict], list[dict]]]:
    """group_id → (проведённые held — настоящие и с исправленным годом, фиктивные held)."""
    out: dict[int, tuple[list[dict], list[dict]]] = defaultdict(lambda: ([], []))
    for s in p.sessions:
        if s['status'] == 'held':
            real, fake = out[s['group_id']]
            day = p.session_dates[s['id']]
            (fake if day > as_of and corrected_year(day, as_of) is None else real).append(s)
    return out


def year_fixed_held(src: EdusonSource, as_of: datetime.date) -> dict[int, datetime.date]:
    """sessions.id → исправленная дата для held с ошибочным годом (corrected_year).
    Шаг уроков заводит их проведёнными уроками на эту дату, как настоящие held."""
    p = _parsed(src)
    out: dict[int, datetime.date] = {}
    for s in p.sessions:
        if s['status'] == 'held':
            fixed = corrected_year(p.session_dates[s['id']], as_of)
            if fixed is not None:
                out[s['id']] = fixed
    return out


def fake_held_ids(src: EdusonSource, as_of: datetime.date) -> set[int]:
    """held с датой позже дня импорта, год которой исправить нельзя (corrected_year
    вернул None): прошлые уроки без настоящей даты. В Lesson не превращаются, а идут
    в N своей группы (спека §5.4). На копии 2026-10-19 таких нет — все 544 held
    «2027» исправляются; правило оставлено на случай новых данных."""
    return {s['id'] for _, fake in _held_by_group(_parsed(src), as_of).values() for s in fake}


def pre_system_lessons(src: EdusonSource, as_of: datetime.date) -> dict[int, Decimal]:
    """N — сколько уроков группа прошла до начала учёта в Eduson, по каждой группе.

        N = max(0, L − 1) + F≥L

    L   — минимальный целый lesson_no среди ПРОВЕДЁННЫХ held: настоящих (дата ≤ as_of)
          и с исправленным годом (year_fixed_held) — последние тоже станут уроками;
          нет ни одного номера — L = 1. Уроки №1..L−1 пройдены до первого учтённого.
    F≥L — число оставшихся фиктивных held группы (fake_held_ids), у которых
          lesson_no ≥ L или номера нет: эти уроки были, но не входят в 1..L−1.

    Группа, у которой все заглушки «2027» перенесены уроками, получает N = L − 1
    (на копии 2026-10-19 у всех 66 таких групп L = 1, N = 0). Группа без заглушек с
    L > 1 (первый учтённый урок №13) — по-прежнему N = L − 1: о тех уроках данных нет.

    Почему не «все фиктивные + (L − 1)»: первичная загрузка часто ставила
    фиктивным held номера 1..F, а настоящие уроки продолжали с F+1 — тогда
    фиктивные уже сидят внутри 1..L−1, и простое сложение считало их дважды
    (7 групп на копии 2026-10-15). Фиктивный с номером < L — это как раз урок из
    1..L−1, отдельно он не считается.

    student_groups.lessons_done не используется вовсе — «Исправить прогресс» писал
    туда общее число, и Eduson прибавлял к нему held (двойной счёт, спека §5.4).
    Вес отработки к N не применяется: N считает уроки курса штуками, как Eduson.

    Результат — Decimal (как Group.lesson_number_offset), для всех групп, в т.ч. 0.
    """
    p = _parsed(src)
    held = _held_by_group(p, as_of)
    out: dict[int, Decimal] = {}
    for g in p.groups:
        real, fake = held.get(g['id'], ([], []))
        numbers = [n for n in (_int(s['lesson_no']) for s in real) if n is not None]
        first = min(numbers) if numbers else 1
        later_fake = sum(1 for s in fake if _int(s['lesson_no']) is None or _int(s['lesson_no']) >= first)
        out[g['id']] = Decimal(max(0, first - 1) + later_fake)
    return out


# ── преподаватель группы на дату ─────────────────────────────────────────────

@dataclass(frozen=True)
class TeacherTimeline:
    """Кусочно-постоянный «преподаватель группы» по времени.

    segments[group_id] — список (начало, teacher_id) по возрастанию начала; первое
    начало None («с начала времён»). Значение действует до начала следующего.
    Границы для bisect собираются один раз при создании, не на каждый запрос.
    """
    segments: dict[int, list[tuple[datetime.datetime | None, int | None]]]
    _starts: dict[int, list[datetime.datetime]] = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, '_starts', {
            gid: [start for start, _ in segs[1:]] for gid, segs in self.segments.items()})

    def group_teacher_on(self, group_id: int, day: datetime.date,
                         time: datetime.time | None = None) -> int | None:
        """teacher_id Eduson преподавателя группы в момент day+time по Москве.

        Без времени — на начало дня (00:00): действие в журнале в тот же день
        позже полуночи на такой запрос ещё не влияет. Неизвестная группа — KeyError:
        молча вернуть «никого» значило бы потерять преподавателя урока.

        Может вернуть None: legacy PATCH карточки принимает teacherId: null, и у
        группы в какой-то период преподавателя не было. Шаг уроков ОБЯЗАН это
        обработать (явный sessions.teacher_id урока или понятная ошибка/ручная
        проверка) — Lesson.teacher в KOTOKOD обязателен.
        """
        segs = self.segments[group_id]
        moment = datetime.datetime.combine(day, time or datetime.time(0, 0), tzinfo=MSK)
        return segs[bisect.bisect_right(self._starts[group_id], moment)][1]

    def teacher_ids(self) -> set[int]:
        """Все преподаватели, которые были у какой-либо группы (им нужен Teacher)."""
        return {t for segs in self.segments.values() for _, t in segs if t is not None}

    def changed_groups(self) -> set[int]:
        """Группы, у которых преподаватель менялся."""
        return {gid for gid, segs in self.segments.items() if len({t for _, t in segs}) > 1}


def group_teacher_timeline(src: EdusonSource) -> TeacherTimeline:
    """История преподавателя группы по журналу Eduson.

    ЧТО ЕСТЬ В ЖУРНАЛЕ. teacher_id группы меняют update_group (PATCH карточки),
    reschedule с teacherId и откаты. Новое значение не пишет никто: у
    update_group/sync/update_progress/delete_group before = {group: полный снимок},
    у reschedule/freeze before.group = {default_*, schedule_slots, teacher_id} —
    это преподаватель ДО действия. Откат (action='revert') пишется без before/after,
    но возвращает before.group.teacher_id откатываемой записи (legacy revert:
    insertRow(before.group) или UPDATE … teacher_id=before.group.teacher_id).

    RESCHEDULE СО СМЕНОЙ ПРЕПОДАВАТЕЛЯ. legacy rescheduleFuture переводит группу
    с fromDate (новые уроки с этой даты — у нового преподавателя), а запись в
    журнале появляется в момент действия — нередко позже fromDate. Поэтому у такой
    записи граница отрезка — min(ts, fromDate 00:00 МСК), но не раньше предыдущей
    границы (порядок отрезков сохраняется). Смена есть, только когда новое
    значение (следующего отрезка) отличается от before.group.teacher_id: у
    reschedule без смены преподавателя граница остаётся на ts.

    ТОЧКИ. По каждой группе в порядке (ts, id):
      «до»    — запись группы с before.group.teacher_id: значение действовало
                вплоть до этой записи;
      «после» — откат такой записи: значение действует с момента отката.
    Плюс текущее student_groups.teacher_id — значение после последней точки.

    ОТРЕЗКИ. До первой точки — её значение (первая точка всегда «до»: откат
    идёт после откатываемой записи). Между точками i и i+1:
      — i+1 «до» → его значение (прямое наблюдение);
      — иначе значение точки i: после отката — восстановленное; после действия
        без последующего наблюдения — прежнее (смена не доказана; если это и было
        действие, которое потом откатили, — окно ошибки отдаём прежнему
        преподавателю, раз откат признал его правильным).
    После последней точки — текущий преподаватель группы.

    Записи об уроках (move_session с teacher_id и т.п.) — разовые замены на уроке,
    не смена преподавателя группы; они здесь не участвуют. Удалённые группы (нет
    строки в student_groups) не выводятся.
    """
    p = _parsed(src)
    current = {g['id']: g['teacher_id'] for g in p.groups}

    def group_teacher(a: dict | None):
        """(group_id, teacher_id) из снимка before.group записи группы, иначе None."""
        if not a or a['entity_type'] != 'group' or not isinstance(a['before'], dict):
            return None
        snap = a['before'].get('group')
        if not isinstance(snap, dict) or 'teacher_id' not in snap:
            return None
        return a['entity_id'], snap['teacher_id']

    points: dict[int, list[tuple[datetime.datetime, str, int | None, dict]]] = defaultdict(list)
    for a in p.audit:
        if a['action'] == 'revert':
            seen = group_teacher(p.audit_by_id.get(a['revert_of']))
            if seen:
                points[seen[0]].append((a['ts'], 'after', seen[1], a))
        else:
            seen = group_teacher(a)
            if seen:
                points[seen[0]].append((a['ts'], 'before', seen[1], a))

    segments: dict[int, list[tuple[datetime.datetime | None, int | None]]] = {}
    for gid, teacher_now in current.items():
        pts = points.get(gid, [])
        if not pts:
            segments[gid] = [(None, teacher_now)]
            continue
        segs: list[tuple[datetime.datetime | None, int | None]] = [
            (None, pts[0][2] if pts[0][1] == 'before' else teacher_now)]
        for i, (ts, kind, value, a) in enumerate(pts):
            if i + 1 < len(pts):
                nxt = pts[i + 1]
                new_value = nxt[2] if nxt[1] == 'before' else value
            else:
                new_value = teacher_now
            if kind == 'before' and a['action'] == 'reschedule' and new_value != value:
                start = _reschedule_start(a)
                if start is not None:
                    floor = segs[-1][0]
                    ts = min(ts, start) if floor is None else max(floor, min(ts, start))
            segs.append((ts, new_value))
        segments[gid] = segs
    return TeacherTimeline(segments)


def _reschedule_start(a: dict) -> datetime.datetime | None:
    """fromDate записи reschedule как момент 00:00 МСК; нет или битая — None (граница по ts)."""
    try:
        day = datetime.date.fromisoformat(a['before'].get('fromDate'))
    except (AttributeError, TypeError, ValueError):
        return None
    return datetime.datetime.combine(day, datetime.time(0, 0), tzinfo=MSK)


# ── переносы и удалённые уроки ───────────────────────────────────────────────

def _session_chains(p: _Parsed) -> dict[int, list[dict]]:
    """session_id → записи журнала, чей before хранит дату урока, по (ts, id)."""
    chains: dict[int, list[dict]] = defaultdict(list)
    for a in p.audit:
        if (a['action'] in _SESSION_DATE_ACTIONS and a['entity_type'] == 'session'
                and isinstance(a['before'], dict) and a['before'].get('date')):
            chains[a['entity_id']].append(a)
    return chains


def moved_from_dates(src: EdusonSource) -> dict[int, datetime.date]:
    """session_id → дата «до» последнего переноса, сменившего дату (спека §5.4).

    move_session пишет только before {date, time, teacher_id, type}; дату ПОСЛЕ
    переноса берём на момент записи: это before.date следующей записи журнала об
    этом уроке (перенос, правка карточки, удаление), а если её нет — текущая дата
    урока. Перенос, у которого before.date совпал с датой после, менял только
    время — не в счёт. Откаченные переносы (reverted=1) не в счёт: откат вернул
    и дату, и тип. Сравнивать с ТЕКУЩЕЙ датой нельзя: перенос только времени, после
    которого дату поправили карточкой урока, выглядел бы переносом по дате.
    """
    p = _parsed(src)
    out: dict[int, datetime.date] = {}
    for sid, chain in _session_chains(p).items():
        for k, a in enumerate(chain):
            if a['action'] != 'move_session' or a['reverted']:
                continue
            if k + 1 < len(chain):
                after = _date(chain[k + 1]['before']['date'], f'журнал Eduson, запись #{chain[k + 1]["id"]}')
            elif sid in p.session_dates:
                after = p.session_dates[sid]
            else:
                continue  # урок удалён без снимка в журнале — даты «после» нет
            before = _date(a['before']['date'], f'журнал Eduson, запись #{a["id"]}')
            if before != after:
                out[sid] = before  # цикл по возрастанию — остаётся последний
    return out


def deleted_session_dates(src: EdusonSource) -> dict[int, tuple[int, datetime.date]]:
    """session_id → (group_id, date) уроков, которых больше нет в sessions.

    Нужна отзывам: отзыв остаётся с висячим session_id, когда строку урока удалили.
    Снимки удалённых строк лежат в журнале:
      — cancel_keep_total / delete_session: before = вся строка урока;
      — reschedule / freeze / update_progress: before.oldSessions = снятые будущие
        scheduled (их место заняли новые строки с новыми id).
    Урок, восстановленный откатом (insertRow с тем же id), снова есть в sessions —
    он не удалённый. Если строку снимали не раз, берётся последний снимок.

    КОНТРАКТ ДЛЯ ШАГА УРОКОВ. Отзыв, чей session_id нет ни в sessions, ни здесь
    (урок удалён без снимка — например, вместе с группой или до появления журнала),
    — не повод падать: шаг кладёт его в manual_review и идёт дальше.
    """
    p = _parsed(src)
    out: dict[int, tuple[int, datetime.date]] = {}

    def remember(row: dict, audit_id: int) -> None:
        sid = row.get('id')
        if sid is None or sid in p.session_dates or row.get('group_id') is None:
            return
        out[sid] = (row['group_id'], _date(row.get('date'), f'журнал Eduson, запись #{audit_id}'))

    for a in p.audit:  # по (ts, id): поздний снимок перезаписывает ранний
        before = a['before'] if isinstance(a['before'], dict) else None
        if not before:
            continue
        if a['action'] in _DELETE_ACTIONS and a['entity_type'] == 'session':
            remember({**before, 'id': a['entity_id']}, a['id'])
        elif a['action'] in _REPLACE_ACTIONS:
            for row in before.get('oldSessions') or []:
                if isinstance(row, dict):
                    remember(row, a['id'])
    return out


# ── замены на уроке и момент проведения ─────────────────────────────────────

_SUBSTITUTION_ACTIONS = frozenset({'move_session', 'session_patch'})


def substituted_teacher_by_session(src: EdusonSource) -> dict[int, int]:
    """session_id → teacher_id Eduson, если ТЕКУЩИЙ sessions.teacher_id урока выставило
    действие замены — не откаченный move_session/session_patch, реально сменивший
    преподавателя урока (спека §5.4).

    ЗАЧЕМ. sessions.teacher_id в legacy — прежде всего отметка генерации расписания:
    setProgress, freezeResume, доп. занятие и «не состоится» ставят новым урокам
    преподавателя группы (services/schedule.js:131,288,225,252), rescheduleFuture —
    переданного или NULL (:168), createGroup и seed — NULL. Смена преподавателя
    группы старые уроки не трогает, так что «отметка ≠ преподавателю группы» — ещё
    не замена (на копии это ~128 ложных замен). Замену доказывает только действие
    над самим уроком: перенос с teacherId или правка карточки урока.

    КАК ВИДНО СМЕНУ. after у этих действий legacy не пишет; before хранит teacher_id
    ДО действия. Значение ПОСЛЕ — before.teacher_id следующей записи журнала об этом
    уроке (перенос, правка, удаление), а у последней — текущее значение в sessions.
    Действие сменило преподавателя, если «до» ≠ «после» и «после» не NULL. Откаченные
    записи (reverted=1) не в счёт: откат вернул «до». Урок попадает в результат,
    только если последняя такая смена дала ровно его текущее значение.
    """
    p = _parsed(src)
    current = {s['id']: s['teacher_id'] for s in p.sessions}
    out: dict[int, int] = {}
    for sid, chain in _session_chains(p).items():
        if sid not in current:
            continue                        # урок удалён — факта нет, замена не нужна
        chain = [a for a in chain if 'teacher_id' in a['before']]
        last_change = None
        for k, a in enumerate(chain):
            after = chain[k + 1]['before']['teacher_id'] if k + 1 < len(chain) else current[sid]
            if (a['action'] in _SUBSTITUTION_ACTIONS and not a['reverted']
                    and after is not None and after != a['before']['teacher_id']):
                last_change = after
        if last_change is not None and last_change == current[sid]:
            out[sid] = last_change
    return out


def held_moments(src: EdusonSource) -> dict[int, datetime.datetime]:
    """session_id → момент, когда урок стал проведённым (для Lesson.submitted_at).

    В held урок переводят два пути legacy: POST /sessions/:id/status (запись
    status_change с after.status='held', routes/sessions.js:62-75) и отправка отчёта
    с took_place='yes' (routes/reports.js:54-85: status='held' без записи в журнал,
    момент — lesson_reports.submitted_at). Берётся последний не откаченный
    status_change → held (последний переход и есть действующий), иначе момент
    отправки отчёта «состоялся». sessions.updated_at не годится: его двигают и
    ссылка на запись урока, и любая правка. Ничего нет — урока в результате нет,
    шаг уроков берёт created_at или дату урока.
    """
    p = _parsed(src)
    out: dict[int, datetime.datetime] = {}
    for a in p.audit:   # по (ts, id): поздний переход перезаписывает ранний
        if (a['action'] == 'status_change' and a['entity_type'] == 'session' and not a['reverted']
                and isinstance(a['after'], dict) and a['after'].get('status') == 'held'):
            out[a['entity_id']] = a['ts']
    for r in src.rows('lesson_reports'):
        if r['session_id'] in out or r['status'] != 'submitted' or r['took_place'] != 'yes':
            continue
        moment = parse_moment(r['submitted_at'])
        if moment is not None:
            out[r['session_id']] = moment
    return out


def parse_moment(value) -> datetime.datetime | None:
    """Метка Eduson (toISOString, 'Z') → aware datetime; без зоны — UTC, как legacy;
    пустая или битая — None."""
    try:
        moment = datetime.datetime.fromisoformat(str(value or '').strip())
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=datetime.timezone.utc)
