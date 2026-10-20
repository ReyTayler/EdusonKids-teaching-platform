"""Шаг «люди»: направления, преподаватели, учётки (спека §5.1, поправки §5.4).

ПОЧЕМУ ORM, А НЕ СЕРВИСЫ KOTOKOD. accounts.services.create_account выпускает
invite-ссылку и пишет событие аудита от имени администратора — импорт переносит
готовые учётки с паролями, ни то ни другое здесь не к месту. Teacher/Direction
у KOTOKOD создаются тонкими репозиториями без инвариантов сверх модели. Поэтому
— Model.objects.create с теми же обязательными полями, что у репозиториев
(Teacher.created_at, Account.date_joined), а ограничения модели (уникальное имя
преподавателя, teacher_id ⇔ роль teacher, один аккаунт на преподавателя,
уникальная почта) проверяются ДО записи, чтобы ошибка называла запись Eduson,
а не падала IntegrityError без адреса.
"""
from __future__ import annotations

from django.utils import timezone

from apps.accounts.hashers import wrap_eduson_bcrypt
from apps.accounts.models import Account
from apps.directions.models import Direction
from apps.eduson_import.context import ImportContext, ImportDataError
from apps.eduson_import.links import Kind, load_links
from apps.eduson_import.normalize import norm_email, norm_name
from apps.eduson_import.sqlite_source import EdusonSource
from apps.eduson_import.steps import require_atomic
from apps.teachers.models import Teacher

# Курс Eduson → (название направления, цвет из дизайн-системы Eduson).
DIRECTIONS = {
    'scratch': ('Scratch', '#c2410c'),
    'python': ('Python', '#1a56db'),
    'vibe': ('Вайб-кодинг', '#067a57'),
}
# Курс 'groups' в Eduson — это формат («групповая группа»), а не предмет. Решение
# владельца 2026-10-19: направления «Группа» нет, предмет групповой группы — первая
# буква её кода (ПГ… — Python, СГ… — Scratch, ВГ… — Вайб-кодинг), формат по-прежнему
# берётся из поля format. Буквы кириллические, как в кодах Eduson.
GROUPS_COURSE = 'groups'
# Длина курса направления (уроков). Стандартный полный пакет Eduson — 48 (legacy
# services/renewals.js: рекомендуемый пакет 48). Группы берут длину из направления
# (lessons_total не заполняется — решение владельца 2026-10-19), поэтому без неё
# у всех групп пропали бы план, остаток курса и сигналы реестра.
COURSE_LESSONS = 48
GROUP_CODE_LETTER = {'П': 'python', 'С': 'scratch', 'В': 'vibe'}

# Контакты преподавателя, которым в модели KOTOKOD пока нет полей (этап 3).
_TEACHER_EXTRA = ('tg', 'telemost', 'vk', 'subjects')


def _text(value) -> str | None:
    """Строка как в Eduson без краевых пробелов; пустая → NULL."""
    value = str(value or '').strip()
    return value or None


def referenced_teacher_ids(src: EdusonSource, ctx: ImportContext) -> set[int]:
    """Преподаватели, на которых ссылаются данные: группы (сейчас и по истории
    журнала — нужны как «преподаватель группы на дату» урока), уроки и отзывы."""
    ids = {g['teacher_id'] for g in src.rows('student_groups')}
    ids |= {s['teacher_id'] for s in src.rows('sessions')}
    ids |= {r['teacher_id'] for r in src.rows('lesson_reports')}
    ids |= ctx.teacher_timeline.teacher_ids()
    ids.discard(None)
    return ids


def import_people(src: EdusonSource, ctx: ImportContext) -> None:
    require_atomic('люди')
    now = timezone.now()
    teachers = {t['id']: t for t in src.rows('teachers')}
    users = src.rows('users')
    referenced = referenced_teacher_ids(src, ctx)
    _check_superadmins(users, referenced, ctx)       # до первой записи
    _import_directions(src, ctx)

    missing = sorted(referenced - teachers.keys())
    if missing:
        raise ImportDataError('ссылки на несуществующих преподавателей: '
                              + ', '.join(f'#{i}' for i in missing))

    needed = _needed_teachers(teachers, users, referenced)
    _check_unique_names(teachers, needed)

    for tid in sorted(needed):
        ctx.teacher_by_eduson[tid] = Teacher.objects.create(**_teacher_fields(teachers[tid]), created_at=now).id
    _report_teachers(teachers, needed, ctx)
    _import_accounts(users, referenced, ctx, now)


def _needed_teachers(teachers: dict[int, dict], users: list[dict], referenced: set[int]) -> set[int]:
    """Teacher нужен всем, на кого ссылаются данные, всем с ролью teacher и всем, к кому
    привязана учётка преподавателя; администратор без ссылок — только учётка (§5.4)."""
    needed = referenced | {i for i, t in teachers.items() if t['role'] == 'teacher'}
    needed |= {u['teacher_id'] for u in users if u['role'] == 'teacher' and u['teacher_id'] in teachers}
    return needed


def _teacher_fields(t: dict) -> dict:
    return {'name': t['name'].strip(), 'email': _text(t['email']), 'phone': _text(t['phone']),
            'active': bool(t['active'])}


def _report_teachers(teachers: dict[int, dict], needed: set[int], ctx: ImportContext) -> None:
    for tid, t in teachers.items():
        extra = [k for k in _TEACHER_EXTRA if _text(t.get(k))]
        if extra:
            where = '' if tid in needed else ' (администратор без Teacher)'
            ctx.report.append(f'преподаватель #{tid}{where}: в Eduson есть {", ".join(extra)} — '
                              'полей в модели KOTOKOD нет (этап 3), значения остаются в архиве')
        elif tid not in needed:
            ctx.report.append(f'преподаватель #{tid}: администратор без ссылок — только учётка')


def _import_directions(src: EdusonSource, ctx: ImportContext) -> None:
    by_letter = _resolve_courses(src, ctx)
    for course, (name, color) in DIRECTIONS.items():
        ctx.direction_by_course[course] = Direction.objects.create(
            name=name, color=color, active=True, total_lessons=COURSE_LESSONS).id
    _report_courses(by_letter, ctx)


def _resolve_courses(src: EdusonSource, ctx: ImportContext) -> dict[str, int]:
    """Курс-направление каждой группы → ctx.course_by_group; → {курс: групп по букве кода}.
    Решается до первой записи: ошибка в коде группы не должна оставлять за собой
    созданные направления (хоть транзакция их и откатит)."""
    groups = src.rows('student_groups')
    unknown = sorted({g['code'] for g in groups if g['course'] not in DIRECTIONS and g['course'] != GROUPS_COURSE})
    if unknown:
        raise ImportDataError('неизвестный курс у групп: ' + ', '.join(unknown))
    by_letter: dict[str, int] = {}
    for g in groups:
        course = g['course']
        if course == GROUPS_COURSE:
            letter = str(g['code'] or '').strip()[:1].upper()
            course = GROUP_CODE_LETTER.get(letter)
            if course is None:
                # Код группы — не ПДн, но по правилу сообщения адресуем id Eduson.
                raise ImportDataError(
                    f'группа #{g["id"]}: курс {GROUPS_COURSE!r}, первая буква кода {letter or "—"!r} '
                    f'не задаёт направление (ожидается одна из {", ".join(GROUP_CODE_LETTER)})')
            by_letter[course] = by_letter.get(course, 0) + 1
        ctx.course_by_group[g['id']] = course
    return by_letter


def _report_courses(by_letter: dict[str, int], ctx: ImportContext) -> None:
    if by_letter:
        split = ', '.join(f'{DIRECTIONS[c][0]} {n}' for c, n in sorted(by_letter.items(),
                                                                         key=lambda kv: DIRECTIONS[kv[0]][0]))
        ctx.report.append(f'курс {GROUPS_COURSE!r} разнесён по направлениям по первой букве кода, '
                          f'групп {sum(by_letter.values())}: {split}')


def _check_unique_names(teachers: dict[int, dict], needed: set[int]) -> None:
    # В KOTOKOD имя преподавателя уникально. Сравнение — как у человека: без учёта
    # регистра, ё/е и лишних пробелов («Анна  Тестова» и «анна тестова» — одно имя).
    by_name: dict[str, list[int]] = {}
    for tid in sorted(needed):
        name = norm_name(teachers[tid]['name'])
        if not name:
            raise ImportDataError(f'преподаватель #{tid}: пустое имя')
        by_name.setdefault(name, []).append(tid)
    clashes = [ids for ids in by_name.values() if len(ids) > 1]
    if clashes:
        raise ImportDataError('одинаковые имена у преподавателей (в KOTOKOD имя уникально): '
                              + '; '.join(', '.join(f'#{i}' for i in ids) for ids in clashes))


def _check_superadmins(users: list[dict], referenced: set[int], ctx: ImportContext) -> None:
    """Каждая почта --superadmin — учётка-admin Eduson, которая и в KOTOKOD останется
    админской (не ведёт занятий: такой админ переносится преподавателем, а роль
    teacher ⇔ teacher_id — CHECK accounts_teacher_role_check). Почту в сообщение
    не кладём — только её номер в параметрах и id учётки."""
    by_email = {norm_email(u['email']): u for u in users}
    for i, email in enumerate(ctx.superadmin_emails, start=1):
        u = by_email.get(email)
        if u is None:
            raise ImportDataError(f'--superadmin №{i}: такой почты нет среди учёток Eduson')
        if u['role'] != 'admin':
            raise ImportDataError(f'--superadmin №{i}: учётка #{u["id"]} не администратор (роль {u["role"]})')
        if not u['active']:
            # Отключённый суперадмин — та же ситуация «управлять некому», только скрытая.
            raise ImportDataError(f'--superadmin №{i}: учётка #{u["id"]} отключена в Eduson')
        if u['teacher_id'] in referenced:
            raise ImportDataError(f'--superadmin №{i}: учётка #{u["id"]} переносится преподавателем '
                                  '(роль teacher) — суперадмином быть не может')


def _import_accounts(users: list[dict], referenced: set[int], ctx: ImportContext, now) -> None:
    for uid, fields in _account_fields(users, referenced, ctx):
        ctx.account_by_user[uid] = Account.objects.create(**fields, date_joined=now).id


def _account_fields(users: list[dict], referenced: set[int], ctx: ImportContext) -> list[tuple[int, dict]]:
    """Проверка учёток Eduson и поля Account для каждой → [(users.id, поля)]. Общая для
    импорта и мягкого обновления: правила роли и связи с преподавателем одни."""
    out: list[tuple[int, dict]] = []
    seen_email: dict[str, int] = {}
    seen_teacher: dict[int, int] = {}
    for u in users:
        uid = u['id']
        email = norm_email(u['email'])
        if not email:
            raise ImportDataError(f'учётка #{uid}: пустая почта')
        if email in seen_email:
            raise ImportDataError(f'учётки #{seen_email[email]} и #{uid}: одна почта без учёта регистра')
        seen_email[email] = uid

        if u['role'] == 'teacher':
            if u['teacher_id'] not in ctx.teacher_by_eduson:
                raise ImportDataError(f'учётка #{uid}: роль teacher без преподавателя')
            teacher_eduson = u['teacher_id']
        elif u['role'] == 'admin':
            # Решение владельца (§5.4): администратор, привязанный к преподавателю,
            # который ведёт группы/уроки/отзывы, работает в кабинете преподавателя.
            # Остальным связь отбрасывается — в KOTOKOD она есть только у роли teacher.
            teacher_eduson = u['teacher_id'] if u['teacher_id'] in referenced else None
            if teacher_eduson is not None:
                ctx.report.append(f'учётка #{uid}: администратор Eduson ведёт занятия — перенесена с ролью teacher')
        else:
            raise ImportDataError(f'учётка #{uid}: неизвестная роль {u["role"]!r}')

        if teacher_eduson is not None:
            if teacher_eduson in seen_teacher:
                raise ImportDataError(f'учётки #{seen_teacher[teacher_eduson]} и #{uid} привязаны к одному '
                                      f'преподавателю #{teacher_eduson} — в KOTOKOD учётка у преподавателя одна')
            seen_teacher[teacher_eduson] = uid

        try:
            password = wrap_eduson_bcrypt(u['password_hash'])
        except ValueError:
            # Сам хэш в сообщение не кладём: это секрет, даже если он битый.
            raise ImportDataError(f'учётка #{uid}: пароль не в формате bcrypt Eduson') from None

        is_teacher = teacher_eduson is not None
        if is_teacher:
            role = Account.Role.TEACHER
        elif email in ctx.superadmin_emails:
            role = Account.Role.SUPERADMIN
            ctx.report.append(f'учётка #{uid}: назначена суперадмином (--superadmin)')
        else:
            role = Account.Role.ADMIN
        out.append((uid, {
            'email': email, 'password': password, 'role': role,
            'teacher_id': ctx.teacher_by_eduson[teacher_eduson] if is_teacher else None,
            # У преподавателя имя берётся из Teacher (как в KOTOKOD), у админа — своё.
            'full_name': None if is_teacher else _text(u['name']),
            'is_active': bool(u['active']),
        }))
    return out


# ── мягкое обновление (sync_eduson) ─────────────────────────────────────────

def sync_people(src: EdusonSource, ctx: ImportContext) -> None:
    """Мягкое обновление людей: направления и преподаватели — по связям EdusonLink
    (обновить на месте, новых — завести), учётки — только новые. Существующие учётки
    не трогаются: пароль, 2FA, роль и активность, изменённые в платформе, важнее.
    Суперадмин мягким обновлением не назначается (ctx.superadmin_emails пуст)."""
    require_atomic('люди')
    now = timezone.now()
    teachers = {t['id']: t for t in src.rows('teachers')}
    users = src.rows('users')
    referenced = referenced_teacher_ids(src, ctx)
    _sync_directions(src, ctx, load_links(Kind.DIRECTION))

    missing = sorted(referenced - teachers.keys())
    if missing:
        raise ImportDataError('ссылки на несуществующих преподавателей: '
                              + ', '.join(f'#{i}' for i in missing))
    needed = _needed_teachers(teachers, users, referenced)
    _check_unique_names(teachers, needed)
    _sync_teachers(teachers, needed, load_links(Kind.TEACHER), ctx, now)
    _sync_accounts(users, referenced, load_links(Kind.ACCOUNT), ctx, now)


def _sync_directions(src: EdusonSource, ctx: ImportContext, linked: dict[str, int]) -> None:
    _resolve_courses(src, ctx)
    existing = set(Direction.objects.filter(id__in=linked.values()).values_list('id', flat=True))
    for course, (name, color) in DIRECTIONS.items():
        local = linked.get(course)
        if local in existing:
            ctx.direction_by_course[course] = local      # настройки направления — дело платформы
        else:
            ctx.direction_by_course[course] = Direction.objects.create(
                name=name, color=color, active=True, total_lessons=COURSE_LESSONS).id
            ctx.report.append(f'направление {name}: заведено')


def _sync_teachers(teachers: dict[int, dict], needed: set[int], linked: dict[str, int],
                   ctx: ImportContext, now) -> None:
    existing = Teacher.objects.in_bulk([linked[str(t)] for t in needed if str(t) in linked])
    # Имя преподавателя уникально в БД: занято преподавателем вне этого набора (заведён в
    # платформе или связан с другим id Eduson) — ошибка с адресом, а не IntegrityError.
    ours = set(existing)
    taken = {norm_name(name): pk for pk, name in Teacher.objects.exclude(id__in=ours).values_list('id', 'name')}
    created = updated = 0
    for tid in sorted(needed):
        fields = _teacher_fields(teachers[tid])
        if norm_name(fields['name']) in taken:
            raise ImportDataError(f'преподаватель #{tid}: имя уже занято преподавателем платформы '
                                  f'(id {taken[norm_name(fields["name"])]}) — в KOTOKOD имя уникально')
        teacher = existing.get(linked.get(str(tid)))
        if teacher is None:
            teacher = Teacher.objects.create(**fields, created_at=now)
            created += 1
        else:
            changed = [k for k, v in fields.items() if getattr(teacher, k) != v]
            if changed:
                for k in changed:
                    setattr(teacher, k, fields[k])
                teacher.save(update_fields=changed)
                updated += 1
        ctx.teacher_by_eduson[tid] = teacher.id
    ctx.report.append(f'преподаватели: новых {created}, обновлено {updated}')


def _sync_accounts(users: list[dict], referenced: set[int], linked: dict[str, int],
                   ctx: ImportContext, now) -> None:
    existing = set(Account.objects.filter(id__in=linked.values()).values_list('id', flat=True))
    taken_email = {norm_email(e) for e in Account.objects.values_list('email', flat=True)}
    taken_teacher = set(Account.objects.filter(teacher__isnull=False).values_list('teacher_id', flat=True))
    created = 0
    for uid, fields in _account_fields(users, referenced, ctx):
        local = linked.get(str(uid))
        if local in existing:
            ctx.account_by_user[uid] = local
            continue
        if fields['email'] in taken_email or fields['teacher_id'] in taken_teacher:
            # Учётку с этой почтой (или на этого преподавателя) уже завели в платформе —
            # она главнее; связь не пишется, пароль Eduson не переносится.
            ctx.report.append(f'учётка #{uid}: почта или преподаватель уже есть у учётки платформы — не перенесена')
            continue
        ctx.account_by_user[uid] = Account.objects.create(**fields, date_joined=now).id
        created += 1
    ctx.report.append(f'учётки: новых {created} (существующие не меняются)')
