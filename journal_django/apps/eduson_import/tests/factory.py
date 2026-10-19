"""Выдуманная SQLite в формате Eduson — только для тестов импорта.

Схема — дословный DDL legacy (fixtures/eduson_schema.sql), строки пишутся теми же
колонками, что пишет сервер Eduson. Все имена, телефоны и почты придуманы; реальную
копию okk.db тесты не открывают никогда.

Помимо «голых» вставок фабрика повторяет действия Eduson, которые меняют уроки и
оставляют след в audit_log: перенос, отработка, доп. занятие, «не состоится» с
сохранением тотала, удаление, правка карточки, смена расписания, заморозка,
«Исправить прогресс», смена статуса урока, отправка отчёта. Отметка
sessions.teacher_id ставится так же, как в legacy: голый session() и createGroup —
NULL, генерация «Исправить прогресс»/заморозка/доп. занятие — преподаватель группы,
смена расписания — переданный преподаватель или NULL. Каждый
метод делает с таблицами то же, что legacy (ссылка «как legacy X (файл:строка)» —
на legacy/server/src/), включая побочные эффекты: хвостовой урок, перенумерацию
scheduled, пересоздание участников с новыми id. Иначе тесты шагов импорта проверяли
бы формат, который придумали мы, а не тот, что лежит в боевой базе.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import sqlite3
from pathlib import Path

import bcrypt

SCHEMA_PATH = Path(__file__).parent / 'fixtures' / 'eduson_schema.sql'

# Eduson работает на bcryptjs: префикс $2a$. Боевой cost 10; в тестах 4 (минимум,
# который принимает wrap_eduson_bcrypt) — чтобы не тратить секунды на хэширование.
TEST_BCRYPT_COST = 4

# services/schedule.js:6-7
REGULAR_DURATION = 60
MAKEUP_DURATION = 45

_NOW = '2026-07-01T10:00:00.000Z'
_TS_START = dt.datetime(2026, 7, 1, 10, 0, 0)
_DAY = dt.timedelta(days=1)


def bcrypt_hash(password: str, cost: int = TEST_BCRYPT_COST) -> str:
    """Настоящий bcrypt-хэш в формате bcryptjs ($2a$) от выдуманного пароля."""
    salt = bcrypt.gensalt(rounds=cost, prefix=b'2a')
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('ascii')


def _ident(name: str) -> str:
    # имена колонок приходят только из кода тестов, но кавычки экранируем всё равно
    return '"' + name.replace('"', '""') + '"'


def _js_dow(day: dt.date) -> int:
    """День недели как getUTCDay() в JS: 0=Вс … 6=Сб."""
    return (day.weekday() + 1) % 7


def _parse_slots(group: dict) -> list[dict] | None:
    # как legacy: try { JSON.parse(g.schedule_slots) } catch { null }
    try:
        slots = json.loads(group['schedule_slots']) if group['schedule_slots'] else None
    except (TypeError, ValueError):
        slots = None
    return slots or None


def _by_dow(slots: list[dict], default_time: str) -> dict[int, list[str]]:
    by_dow: dict[int, list[str]] = {}
    for s in slots:
        by_dow.setdefault(int(s['dow']), []).append(s.get('time') or default_time)
    for times in by_dow.values():
        times.sort()
    return by_dow


def generate_session_descriptors(start_date: str, slots: list[dict], count: int,
                                 start_lesson_no: int = 1) -> list[dict]:
    """Как legacy generateSessionDescriptors (services/schedule.js:30-58), вариант со slots:
    count уроков подряд по слотам начиная с start_date включительно."""
    by_dow = _by_dow(slots, '')
    out: list[dict] = []
    cursor = dt.date.fromisoformat(start_date)
    lesson_no = start_lesson_no
    for _ in range(count * 14 + 400):  # тот же предохранитель, что в legacy
        if len(out) >= count:
            break
        for t in by_dow.get(_js_dow(cursor), ()):
            if len(out) >= count:
                break
            out.append({'date': cursor.isoformat(), 'time': t, 'lesson_no': lesson_no})
            lesson_no += 1
        cursor += _DAY
    return out


class EdusonDB:
    """Построитель базы. Использование:

        with EdusonDB(tmp_path / 'okk.db') as db:
            db.teacher(1, 'Анна Тестова')
        # после выхода база закоммичена и закрыта, db.path — путь к файлу

    wal=True включает журнал WAL, как у боевого Eduson (db.js: journal_mode = WAL).
    """

    def __init__(self, path: Path, *, wal: bool = False):
        self.path = Path(path)
        self.con = sqlite3.connect(self.path)
        self.con.row_factory = sqlite3.Row
        if wal:
            self.con.execute('PRAGMA journal_mode = WAL')
        self.con.executescript(SCHEMA_PATH.read_text(encoding='utf-8'))
        # Метки времени audit_log строго возрастают: импорт берёт «последнюю» запись
        # по порядку, и одинаковые ts сделали бы тесты зависимыми от rowid.
        self._ts = itertools.count()

    # ── контекст ─────────────────────────────────────────────────────────────
    def __enter__(self) -> EdusonDB:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> Path:
        self.con.commit()
        self.con.close()
        return self.path

    def commit(self) -> None:
        self.con.commit()

    def next_ts(self) -> str:
        """Следующая метка в формате new Date().toISOString(), шаг — секунда."""
        moment = _TS_START + dt.timedelta(seconds=next(self._ts))
        return moment.strftime('%Y-%m-%dT%H:%M:%S.000Z')

    def _insert(self, table: str, values: dict) -> int:
        # table — только из кода фабрики, значения — параметрами
        cols = ','.join(_ident(c) for c in values)
        marks = ','.join('?' * len(values))
        cur = self.con.execute(f'INSERT INTO {table} ({cols}) VALUES ({marks})', tuple(values.values()))
        return cur.lastrowid

    def _row(self, table: str, row_id: int) -> dict | None:
        r = self.con.execute(f'SELECT * FROM {table} WHERE id=?', (row_id,)).fetchone()
        return dict(r) if r else None

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.con.execute(sql, params)]

    # ── справочники ─────────────────────────────────────────────────────────
    def teacher(self, id: int, name: str, *, role: str = 'teacher', email: str = '', phone: str = '',
                tg: str = '', telemost: str = '', vk: str = '', subjects: str = '', active: int = 1) -> int:
        return self._insert('teachers', {
            'id': id, 'name': name, 'tg': tg, 'email': email, 'phone': phone, 'telemost': telemost,
            'vk': vk, 'subjects': subjects, 'role': role, 'active': active,
        })

    def user(self, email: str, role: str, *, teacher_id: int | None = None, password: str = 'test-pass',
             password_hash: str | None = None, name: str = '', active: int = 1) -> int:
        """password_hash задаёт хэш напрямую (например, битый); иначе — bcrypt от password."""
        return self._insert('users', {
            'email': email, 'password_hash': password_hash if password_hash is not None else bcrypt_hash(password),
            'role': role, 'teacher_id': teacher_id, 'name': name, 'active': active,
        })

    # ── группы и участники ──────────────────────────────────────────────────
    def group(self, id: int, code: str, course: str = 'scratch', teacher_id: int | None = None, *,
              fmt: str = 'individual', package: int | None = 12, done: int = 0, left: int | None = None,
              dow: int | None = 1, time: str | None = '15:00', slots: list[dict] | None = None,
              start: str | None = '2026-06-01', subject_label: str | None = None, amo_url: str = '',
              methodics_url: str = '', notes: str = '', active: int = 1,
              created_at: str = '2026-06-01T00:00:00.000Z') -> int:
        """Строка student_groups (колонки — как legacy createGroup, services/schedule.js:79-82).
        slots — список {dow, time} (0=Вс), пишется JSON-строкой."""
        return self._insert('student_groups', {
            'id': id, 'code': code, 'course': course,
            'subject_label': course if subject_label is None else subject_label,
            'teacher_id': teacher_id, 'format': fmt, 'package_size': package, 'lessons_done': done,
            'lessons_left': package if left is None else left, 'default_time': time, 'default_dow': dow,
            'start_date': start, 'amo_url': amo_url, 'methodics_url': methodics_url, 'notes': notes,
            'active': active, 'created_at': created_at,
            'schedule_slots': json.dumps(slots) if slots else None,
        })

    def participant(self, group_id: int, child_name: str, parent_name: str = '', parent_phone: str = '',
                    age: int | None = 10, *, id: int | None = None, child_tg: str = '', child_phone: str = '',
                    child_email: str = '', parent_tg: str = '', parent_email: str = '', amo_url: str = '',
                    notes: str = '') -> int:
        """Строка participants (колонки — как legacy createGroup, services/schedule.js:84-92)."""
        values = {
            'group_id': group_id, 'child_name': child_name, 'child_tg': child_tg, 'child_phone': child_phone,
            'child_email': child_email, 'parent_name': parent_name, 'parent_phone': parent_phone,
            'parent_tg': parent_tg, 'parent_email': parent_email, 'age': age, 'amo_url': amo_url, 'notes': notes,
        }
        if id is not None:
            values = {'id': id, **values}
        return self._insert('participants', values)

    def empty_participant(self, group_id: int, *, amo_url: str = '', id: int | None = None) -> int:
        """Пустая строка, как legacy createGroup при пустом списке участников
        (services/schedule.js:89: `participants.length ? participants : [{}]`): все поля
        ребёнка и родителя '', возраст NULL; amo_url берётся у группы (`p.amoUrl || amoUrl`)."""
        return self.participant(group_id, '', '', '', None, id=id, amo_url=amo_url)

    # ── уроки и отзывы ──────────────────────────────────────────────────────
    def session(self, id: int | None, group_id: int, date: str, *, time: str = '15:00', status: str = 'held',
                type: str = 'regular', duration: int = REGULAR_DURATION, lesson_no: int | None = None,
                teacher_id: int | None = None, recording_url: str = '', note: str | None = None,
                created_at: str = _NOW, updated_at: str = _NOW) -> int:
        values = {
            'group_id': group_id, 'date': date, 'time': time, 'duration_min': duration, 'type': type,
            'status': status, 'lesson_no': lesson_no, 'recording_url': recording_url, 'teacher_id': teacher_id,
            'note': note, 'created_at': created_at, 'updated_at': updated_at,
        }
        if id is not None:
            values = {'id': id, **values}
        return self._insert('sessions', values)

    def report(self, session_id: int | None, group_id: int, teacher_id: int | None, *, took_place: str = 'yes',
               understanding: int | None = 8, interest: int | None = 9, tech_problems: str = 'no',
               comment: str = '', status: str = 'submitted', lesson_no: int | None = None,
               recording_url: str = '', created_at: str = _NOW, submitted_at: str | None = _NOW) -> int:
        return self._insert('lesson_reports', {
            'session_id': session_id, 'group_id': group_id, 'teacher_id': teacher_id, 'lesson_no': lesson_no,
            'recording_url': recording_url, 'took_place': took_place, 'understanding': understanding,
            'interest': interest, 'tech_problems': tech_problems, 'comment': comment, 'status': status,
            'created_at': created_at, 'submitted_at': submitted_at if status == 'submitted' else None,
        })

    def renewal(self, group_id: int, status: str = 'new', *, note: str = '',
                updated_at: str = _NOW, updated_by: str = 'Тестовый админ') -> None:
        self._insert('renewals', {'group_id': group_id, 'status': status, 'note': note,
                                  'updated_at': updated_at, 'updated_by': updated_by})

    def notification(self, kind: str = 'report_reminder', *, channel: str = 'email', to_addr: str = '',
                     session_id: int | None = None, group_id: int | None = None,
                     teacher_id: int | None = None, status: str = 'queued') -> int:
        return self._insert('notifications', {
            'kind': kind, 'channel': channel, 'to_addr': to_addr, 'subject': '', 'body': '',
            'session_id': session_id, 'group_id': group_id, 'teacher_id': teacher_id,
            'scheduled_for': _NOW, 'status': status, 'created_at': _NOW,
        })

    # ── журнал Eduson ───────────────────────────────────────────────────────
    def audit(self, action: str, *, entity_type: str | None = None, entity_id: int | None = None,
              code: str = '', summary: str = '', before=None, after=None, ts: str | None = None,
              actor_id: int | None = None, actor_name: str = 'система', reverted: int = 0,
              revert_of: int | None = None) -> int:
        """Сырая запись, как legacy record (services/audit.js:24-32): before/after — JSON."""
        return self._insert('audit_log', {
            'ts': ts or self.next_ts(), 'actor_id': actor_id, 'actor_name': actor_name, 'action': action,
            'entity_type': entity_type, 'entity_id': entity_id, 'code': code, 'summary': summary or action,
            'before_json': json.dumps(before, ensure_ascii=False) if before is not None else None,
            'after_json': json.dumps(after, ensure_ascii=False) if after is not None else None,
            'reverted': reverted, 'revert_of': revert_of,
        })

    def _group(self, group_id: int) -> dict:
        return self._row('student_groups', group_id) or {}

    def _code_of(self, group_id: int) -> str:
        return self._group(group_id).get('code', '')

    def _next_slot_after(self, group: dict, after_date: str) -> dict | None:
        """Как legacy nextSlotAfter (services/schedule.js:185-203): ближайший слот строго после даты."""
        slots = _parse_slots(group)
        default_time = group.get('default_time') or '17:00'
        cursor = dt.date.fromisoformat(after_date)
        if not slots:
            dow = group['default_dow'] if group.get('default_dow') is not None else _js_dow(cursor)
            slots = [{'dow': dow, 'time': default_time}]
        by_dow = _by_dow(slots, default_time)
        cursor += _DAY
        for _ in range(400):
            if _js_dow(cursor) in by_dow:
                return {'date': cursor.isoformat(), 'time': by_dow[_js_dow(cursor)][0]}
            cursor += _DAY
        return None

    def _renumber_scheduled(self, group_id: int) -> None:
        """Как legacy renumberScheduled (services/schedule.js:206-213): будущие regular/moved
        получают номера lessons_done + held + 1, … по date, time. В audit не пишет.
        Тай-брейк по id добавлен ради детерминизма тестов (в legacy его нет)."""
        group = self._group(group_id)
        held = self.con.execute("SELECT COUNT(*) FROM sessions WHERE group_id=? AND status='held'",
                                (group_id,)).fetchone()[0]
        n = (group.get('lessons_done') or 0) + held + 1
        for r in self._rows("SELECT id FROM sessions WHERE group_id=? AND status='scheduled' "
                            "AND type IN ('regular','moved') ORDER BY date, time, id", (group_id,)):
            self.con.execute('UPDATE sessions SET lesson_no=? WHERE id=?', (n, r['id']))
            n += 1

    def _replace_future(self, group_id: int, from_date: str, descriptors: list[dict],
                        teacher_id: int | None) -> None:
        self.con.execute("DELETE FROM sessions WHERE group_id=? AND status='scheduled' AND date>=?",
                         (group_id, from_date))
        for d in descriptors:
            self.session(None, group_id, d['date'], time=d['time'], status='scheduled', lesson_no=d['lesson_no'],
                         teacher_id=teacher_id)

    def _start_lesson_no(self, group_id: int, future: list[dict]) -> int:
        # как legacy: min(lesson_no) заменяемых, иначе MAX(lesson_no)+1 по группе
        nums = [s['lesson_no'] for s in future if s['lesson_no'] is not None]
        if nums:
            return min(nums)
        return self.con.execute('SELECT COALESCE(MAX(lesson_no),0)+1 FROM sessions WHERE group_id=?',
                                (group_id,)).fetchone()[0]

    def move_session(self, session_id: int, date: str | None = None, time: str | None = None,
                     teacher_id: int | None = None, **audit_kw) -> int:
        """Как legacy POST /sessions/:id/move (routes/sessions.js:109-120): дата/время/
        преподаватель, тип → moved (кроме makeup), before {date, time, teacher_id, type}."""
        s = self._row('sessions', session_id)
        new_date, new_time = date or s['date'], time or s['time']
        self.con.execute(
            "UPDATE sessions SET date=?, time=?, teacher_id=?, "
            "type=CASE WHEN type='makeup' THEN type ELSE 'moved' END, updated_at=? WHERE id=?",
            (new_date, new_time, teacher_id if teacher_id is not None else s['teacher_id'], _NOW, session_id))
        code = self._code_of(s['group_id'])
        no = s['lesson_no'] if s['lesson_no'] is not None else '—'
        return self.audit('move_session', entity_type='session', entity_id=session_id, code=code,
                          summary=f"Перенос {code} №{no}: {s['date']} {s['time']} → {new_date} {new_time}",
                          before={'date': s['date'], 'time': s['time'], 'teacher_id': s['teacher_id'],
                                  'type': s['type']}, **audit_kw)

    def patch_session(self, session_id: int, **fields) -> int:
        """Как legacy PATCH /sessions/:id (routes/sessions.js:76-93): правка переданных полей
        урока — date, time, recording_url, note, teacher_id, duration_min, lesson_no, type
        (тип меняется, только если передан; перенос карточкой тип в 'moved' сам не ставит);
        в журнал — полный снимок до правки."""
        before = self._row('sessions', session_id)
        sets = ', '.join(f'{_ident(col)}=?' for col in fields)
        self.con.execute(f'UPDATE sessions SET {sets}, updated_at=? WHERE id=?',
                         (*fields.values(), _NOW, session_id))
        code = self._code_of(before['group_id'])
        no = before['lesson_no'] if before['lesson_no'] is not None else '—'
        return self.audit('session_patch', entity_type='session', entity_id=session_id, code=code,
                          summary=f'Правка урока {code} №{no}', before=before)

    def makeup(self, group_id: int, date: str, time: str, *, teacher_id: int | None = None,
               lesson_no: int | None = None, **audit_kw) -> int:
        """Как legacy POST /sessions/makeup (routes/sessions.js:123-136): новый урок makeup
        45 мин, scheduled; в журнал — after {id}. Возвращает id урока."""
        g = self._group(group_id)
        new_id = self.session(None, group_id, date, time=time, status='scheduled', type='makeup',
                              duration=MAKEUP_DURATION, lesson_no=lesson_no,
                              teacher_id=teacher_id if teacher_id is not None else g.get('teacher_id'))
        self.audit('makeup', entity_type='session', entity_id=new_id, code=g['code'],
                   summary=f"Поставлена отработка {g['code']} (45 мин) на {date} {time}",
                   after={'id': new_id}, **audit_kw)
        return new_id

    def extra_lesson(self, group_id: int, date: str, time: str | None = None, *,
                     teacher_id: int | None = None, **audit_kw) -> int:
        """Как legacy POST /sessions/extra (routes/sessions.js:139-150) → addExtraLesson
        (services/schedule.js:217-238): новый regular scheduled без номера, самый дальний
        scheduled regular/moved удаляется (тотал сохранён), перенумерация. В журнал —
        только summary, без before/after. Возвращает id нового урока."""
        g = self._group(group_id)
        t = time or g.get('default_time') or '17:00'
        new_id = self.session(None, group_id, date, time=t, status='scheduled', lesson_no=None,
                              teacher_id=teacher_id if teacher_id is not None else g.get('teacher_id'))
        tail = self._rows("SELECT id, date, time FROM sessions WHERE group_id=? AND status='scheduled' "
                          "AND type IN ('regular','moved') AND id<>? ORDER BY date DESC, time DESC LIMIT 1",
                          (group_id, new_id))
        trimmed = None
        if tail:
            self.con.execute('DELETE FROM sessions WHERE id=?', (tail[0]['id'],))
            trimmed = f"{tail[0]['date']} {tail[0]['time']}"
        self._renumber_scheduled(group_id)
        suffix = f' (тотал сохранён: снят хвост {trimmed})' if trimmed else ''
        self.audit('extra_lesson', entity_type='group', entity_id=group_id, code=g['code'],
                   summary=f"Доп. занятие {g['code']} на {date} {t}{suffix}", **audit_kw)
        return new_id

    def cancel_keep_total(self, session_id: int, **audit_kw) -> int:
        """Как legacy POST /sessions/:id/cancel-keep-total (routes/sessions.js:153-164) →
        deleteLessonKeepTotal (services/schedule.js:240-262): урок удаляется, в хвост после
        последнего scheduled (или после даты урока) встаёт regular scheduled без номера с
        преподавателем группы, затем перенумерация. В журнал — полный снимок строки.
        Отзыв на урок остаётся с висячим session_id, как в боевой базе."""
        before = self._row('sessions', session_id)
        g = self._group(before['group_id'])
        self.con.execute('DELETE FROM sessions WHERE id=?', (session_id,))
        last = self._rows("SELECT date FROM sessions WHERE group_id=? AND status='scheduled' "
                          "ORDER BY date DESC, time DESC LIMIT 1", (before['group_id'],))
        slot = self._next_slot_after(g, last[0]['date'] if last else before['date'])
        appended = None
        if slot:
            self.session(None, before['group_id'], slot['date'], time=slot['time'], status='scheduled',
                         lesson_no=None, teacher_id=g.get('teacher_id'))
            appended = f"{slot['date']} {slot['time']}"
        self._renumber_scheduled(before['group_id'])
        no = before['lesson_no'] if before['lesson_no'] is not None else '—'
        suffix = f', перенесён в конец → {appended} (тотал сохранён)' if appended else ''
        return self.audit('cancel_keep_total', entity_type='session', entity_id=session_id, code=g['code'],
                          summary=f"Урок {g['code']} №{no} ({before['date']} {before['time']}) не состоится{suffix}",
                          before=before, **audit_kw)

    def delete_session(self, session_id: int, **audit_kw) -> int:
        """Как legacy DELETE /sessions/:id (routes/sessions.js:166-174): строка удаляется
        без хвоста и перенумерации, в журнал — полный снимок (snapshotSession = SELECT *)."""
        before = self._row('sessions', session_id)
        code = self._code_of(before['group_id'])
        self.con.execute('DELETE FROM sessions WHERE id=?', (session_id,))
        no = before['lesson_no'] if before['lesson_no'] is not None else '—'
        return self.audit('delete_session', entity_type='session', entity_id=session_id, code=code,
                          summary=f"Удалён урок {code} №{no} ({before['date']} {before['time']})",
                          before=before, **audit_kw)

    def update_group(self, group_id: int, *, participants: list[dict] | None = None,
                     audit_kw: dict | None = None, **fields) -> int | None:
        """Как legacy PATCH /groups/:id (routes/groups.js:95-119): before = {group: полный
        снимок}; при participants — ещё before.participants, а участники удаляются и
        вставляются заново (новые id!). participants — kwargs для participant() (без group_id).
        Ничего не меняется — записи в журнале нет, как в legacy."""
        g = self._group(group_id)
        before: dict = {'group': g}
        if participants is not None:
            before['participants'] = self._rows('SELECT * FROM participants WHERE group_id=?', (group_id,))
        if fields:
            sets = ', '.join(f'{_ident(col)}=?' for col in fields)
            self.con.execute(f'UPDATE student_groups SET {sets} WHERE id=?', (*fields.values(), group_id))
        if participants is not None:
            self.con.execute('DELETE FROM participants WHERE group_id=?', (group_id,))
            for p in participants:
                self.participant(group_id, **p)
        if not fields and participants is None:
            return None
        return self.audit('update_group', entity_type='group', entity_id=group_id, code=g['code'],
                          summary=f"Изменена карточка {g['code']}", before=before, **(audit_kw or {}))

    def reschedule_group(self, group_id: int, *, from_date: str, slots: list[dict] | None = None,
                         dows: list[int] | None = None, time: str | None = None,
                         teacher_id: int | None = None, **audit_kw) -> int:
        """Как legacy POST /groups/:id/reschedule (routes/groups.js:122-142) → rescheduleFuture
        (services/schedule.js:144-180): будущие scheduled с from_date заменяются тем же
        числом уроков по новым слотам (teacher_id новых = переданный или NULL), правило и
        преподаватель группы обновляются. after = {slots} либо {dows, time}.
        Отличие от legacy (справка): пустой набор слотов legacy отвергает с 400, фабрика —
        нет (будущие уроки просто удаляются); тесты так её не вызывают."""
        g = self._group(group_id)
        future = self._rows("SELECT * FROM sessions WHERE group_id=? AND status='scheduled' AND date>=? "
                            "ORDER BY date, time", (group_id, from_date))
        slot_list = ([{'dow': int(s['dow']), 'time': s.get('time') or time or ''} for s in slots] if slots
                     else [{'dow': int(d), 'time': time or ''} for d in (dows or [])])
        descriptors = generate_session_descriptors(from_date, slot_list, len(future),
                                                   self._start_lesson_no(group_id, future))
        self._replace_future(group_id, from_date, descriptors, teacher_id or None)
        if slot_list:
            self.con.execute('UPDATE student_groups SET default_dow=?, default_time=?, schedule_slots=? WHERE id=?',
                             (slot_list[0]['dow'], slot_list[0]['time'], json.dumps(slot_list), group_id))
        if teacher_id:
            self.con.execute('UPDATE student_groups SET teacher_id=? WHERE id=?', (teacher_id, group_id))
        before = {'fromDate': from_date, 'oldSessions': future,
                  'group': {'default_dow': g['default_dow'], 'default_time': g['default_time'],
                            'schedule_slots': g['schedule_slots'], 'teacher_id': g['teacher_id']}}
        return self.audit('reschedule', entity_type='group', entity_id=group_id, code=g['code'],
                          summary=f"Изменено расписание {g['code']} (заменено {len(future)} уроков)",
                          before=before, after={'slots': slots} if slots else {'dows': dows, 'time': time},
                          **audit_kw)

    def freeze_group(self, group_id: int, *, freeze_from: str, resume_date: str, **audit_kw) -> int:
        """Как legacy POST /groups/:id/freeze (routes/groups.js:145-162) → freezeResume
        (services/schedule.js:265-294): scheduled с freeze_from переносятся тем же числом
        на слоты группы начиная с resume_date (преподаватель — группы).
        after = {freezeFrom, resumeDate}."""
        g = self._group(group_id)
        future = self._rows("SELECT * FROM sessions WHERE group_id=? AND status='scheduled' AND date>=? "
                            "ORDER BY date, time", (group_id, freeze_from))
        slots = _parse_slots(g)
        if not slots:
            dow = g['default_dow'] if g['default_dow'] is not None else _js_dow(dt.date.fromisoformat(resume_date))
            slots = [{'dow': dow, 'time': g['default_time'] or '17:00'}]
        descriptors = generate_session_descriptors(resume_date, slots, len(future),
                                                   self._start_lesson_no(group_id, future))
        self._replace_future(group_id, freeze_from, descriptors, g['teacher_id'])
        before = {'fromDate': freeze_from, 'oldSessions': future,
                  'group': {'default_dow': g['default_dow'], 'default_time': g['default_time'],
                            'schedule_slots': g['schedule_slots'], 'teacher_id': g['teacher_id']}}
        return self.audit('freeze', entity_type='group', entity_id=group_id, code=g['code'],
                          summary=(f"Заморозка {g['code']}: {len(future)} уроков с {freeze_from} → "
                                   f"возобновление с {resume_date}"),
                          before=before, after={'freezeFrom': freeze_from, 'resumeDate': resume_date},
                          **audit_kw)

    def update_progress(self, group_id: int, *, lessons_done: int, from_date: str, package: int | None = None,
                        regenerate: bool = True, **audit_kw) -> int:
        """Как legacy POST /groups/:id/progress (routes/groups.js:165-182) → setProgress
        (services/schedule.js:106-139): «проведено»/пакет группы переписываются, будущие
        scheduled с from_date (в legacy — «сегодня» по МСК) заменяются package − done
        уроками №done+1… по слотам группы. Новые уроки получают teacher_id ГРУППЫ — это
        и есть «отметка генерации»: смена преподавателя группы её потом не трогает.
        before = {group, fromDate, oldSessions}, after = {done, packageSize}."""
        g = self._group(group_id)
        future = self._rows("SELECT * FROM sessions WHERE group_id=? AND status='scheduled' AND date>=?",
                            (group_id, from_date))
        pkg = package if package is not None else g['package_size']
        done = max(0, min(lessons_done, pkg) if pkg is not None else lessons_done)
        left = max(0, pkg - done) if pkg is not None else None
        self.con.execute('UPDATE student_groups SET lessons_done=?, package_size=?, lessons_left=? WHERE id=?',
                         (done, pkg, left, group_id))
        if regenerate and left is not None:
            slots = _parse_slots(g)
            if not slots:
                dow = g['default_dow'] if g['default_dow'] is not None else _js_dow(dt.date.fromisoformat(from_date))
                slots = [{'dow': dow, 'time': g['default_time'] or '17:00'}]
            descriptors = generate_session_descriptors(from_date, slots, left, done + 1)
            self._replace_future(group_id, from_date, descriptors, g['teacher_id'])
        return self.audit('update_progress', entity_type='group', entity_id=group_id, code=g['code'],
                          summary=f"Прогресс {g['code']}: проведено {done}/{pkg if pkg is not None else '—'}",
                          before={'group': g, 'fromDate': from_date, 'oldSessions': future},
                          after={'done': done, 'packageSize': pkg}, **audit_kw)

    def set_status(self, session_id: int, status: str, **audit_kw) -> int | None:
        """Как legacy POST /sessions/:id/status (routes/sessions.js:62-75): смена статуса,
        в журнал — status_change с before {status} и after {status}. Тот же статус —
        ничего не пишется, как в legacy."""
        s = self._row('sessions', session_id)
        if status == s['status']:
            return None
        self.con.execute('UPDATE sessions SET status=?, updated_at=? WHERE id=?', (status, _NOW, session_id))
        code = self._code_of(s['group_id'])
        no = s['lesson_no'] if s['lesson_no'] is not None else '—'
        return self.audit('status_change', entity_type='session', entity_id=session_id, code=code,
                          summary=f'Статус {code} №{no}: {s["status"]} → {status}',
                          before={'status': s['status']}, after={'status': status}, **audit_kw)

    def submit_report(self, session_id: int, *, took_place: str = 'yes', understanding: int | None = 8,
                      interest: int | None = 9, tech_problems: str = 'no', comment: str = '',
                      submitted_at: str = _NOW) -> None:
        """Как legacy POST /reports/session/:id (routes/reports.js:54-85): отчёт
        (новый или поверх pending-заготовки) с teacher_id = эффективный преподаватель
        урока (отметка урока, иначе группы), submitted_at = момент отправки; урок
        становится held (took_place=yes) или cancelled. В audit_log не пишет."""
        s = self._row('sessions', session_id)
        eff_teacher = s['teacher_id'] if s['teacher_id'] is not None else self._group(s['group_id']).get('teacher_id')
        existing = self.con.execute('SELECT id FROM lesson_reports WHERE session_id=?', (session_id,)).fetchone()
        values = (eff_teacher, s['lesson_no'], took_place, understanding, interest, tech_problems, comment)
        if existing:
            self.con.execute(
                "UPDATE lesson_reports SET teacher_id=?, lesson_no=?, took_place=?, understanding=?, interest=?, "
                "tech_problems=?, comment=?, status='submitted', submitted_at=? WHERE session_id=?",
                (*values, submitted_at, session_id))
        else:
            self.con.execute(
                "INSERT INTO lesson_reports (session_id, group_id, teacher_id, lesson_no, took_place, understanding, "
                "interest, tech_problems, comment, status, created_at, submitted_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,'submitted',?,?)",
                (session_id, s['group_id'], *values, submitted_at, submitted_at))
        self.con.execute('UPDATE sessions SET status=?, updated_at=? WHERE id=?',
                         ('held' if took_place == 'yes' else 'cancelled', submitted_at, session_id))

    def _insert_or_replace(self, table: str, row: dict) -> None:
        """Как legacy insertRow (services/audit.js:17-21): INSERT OR REPLACE с исходными id."""
        cols = ','.join(_ident(c) for c in row)
        marks = ','.join('?' * len(row))
        self.con.execute(f'INSERT OR REPLACE INTO {table} ({cols}) VALUES ({marks})', tuple(row.values()))

    def revert(self, audit_id: int, **audit_kw) -> int:
        """Как legacy revert (services/audit.js:37-115) для действий, которые фабрика умеет
        делать: состояние из before восстанавливается, исходная запись помечается
        reverted=1, пишется запись 'revert' с revert_of и БЕЗ before/after — новое
        значение откат не сохраняет, его видно только по исходной записи."""
        a = self._row('audit_log', audit_id)
        before = json.loads(a['before_json']) if a['before_json'] else None
        action, entity_id = a['action'], a['entity_id']
        if action == 'update_group':
            if before.get('group'):
                self._insert_or_replace('student_groups', before['group'])
            if before.get('participants') is not None:
                self.con.execute('DELETE FROM participants WHERE group_id=?', (entity_id,))
                for p in before['participants']:
                    self._insert_or_replace('participants', p)
        elif action in ('reschedule', 'freeze'):
            self.con.execute("DELETE FROM sessions WHERE group_id=? AND status='scheduled' AND date>=?",
                             (entity_id, before['fromDate']))
            for s in before.get('oldSessions') or []:
                self._insert_or_replace('sessions', s)
            g = before.get('group')
            if g:
                self.con.execute('UPDATE student_groups SET default_dow=?, default_time=?, schedule_slots=?, '
                                 'teacher_id=? WHERE id=?',
                                 (g['default_dow'], g['default_time'], g.get('schedule_slots'), g['teacher_id'],
                                  entity_id))
        elif action == 'move_session':
            self.con.execute('UPDATE sessions SET date=?, time=?, teacher_id=?, type=?, updated_at=? WHERE id=?',
                             (before['date'], before['time'], before['teacher_id'], before['type'], _NOW,
                              entity_id))
        elif action == 'delete_session':
            self._insert_or_replace('sessions', before)
        elif action == 'status_change':
            self.con.execute('UPDATE sessions SET status=?, updated_at=? WHERE id=?',
                             (before['status'], _NOW, entity_id))
        else:
            raise NotImplementedError(f'фабрика не умеет откатывать {action}')
        self.con.execute('UPDATE audit_log SET reverted=1 WHERE id=?', (audit_id,))
        return self.audit('revert', entity_type=a['entity_type'], entity_id=entity_id, code=a['code'],
                          summary='Откат: ' + a['summary'], revert_of=audit_id, **audit_kw)
