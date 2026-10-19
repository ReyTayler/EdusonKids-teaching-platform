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

    # Teacher нужен всем, на кого ссылаются данные, всем с ролью teacher и всем, к кому
    # привязана учётка преподавателя; администратор без ссылок — только учётка (§5.4).
    needed = referenced | {i for i, t in teachers.items() if t['role'] == 'teacher'}
    needed |= {u['teacher_id'] for u in users if u['role'] == 'teacher' and u['teacher_id'] in teachers}
    _check_unique_names(teachers, needed)

    for tid in sorted(needed):
        t = teachers[tid]
        ctx.teacher_by_eduson[tid] = Teacher.objects.create(
            name=t['name'].strip(), email=_text(t['email']), phone=_text(t['phone']),
            active=bool(t['active']), created_at=now,
        ).id
    for tid, t in teachers.items():
        extra = [k for k in _TEACHER_EXTRA if _text(t.get(k))]
        if extra:
            where = '' if tid in needed else ' (администратор без Teacher)'
            ctx.report.append(f'преподаватель #{tid}{where}: в Eduson есть {", ".join(extra)} — '
                              'полей в модели KOTOKOD нет (этап 3), значения остаются в архиве')
        elif tid not in needed:
            ctx.report.append(f'преподаватель #{tid}: администратор без ссылок — только учётка')

    _import_accounts(users, referenced, ctx, now)


def _import_directions(src: EdusonSource, ctx: ImportContext) -> None:
    groups = src.rows('student_groups')
    unknown = sorted({g['code'] for g in groups if g['course'] not in DIRECTIONS and g['course'] != GROUPS_COURSE})
    if unknown:
        raise ImportDataError('неизвестный курс у групп: ' + ', '.join(unknown))
    # Курс каждой группы решается до первой записи: ошибка в коде группы не должна
    # оставлять за собой созданные направления (хоть транзакция их и откатит).
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

    for course, (name, color) in DIRECTIONS.items():
        ctx.direction_by_course[course] = Direction.objects.create(name=name, color=color, active=True).id
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
        ctx.account_by_user[uid] = Account.objects.create(
            email=email, password=password, role=role,
            teacher_id=ctx.teacher_by_eduson[teacher_eduson] if is_teacher else None,
            # У преподавателя имя берётся из Teacher (как в KOTOKOD), у админа — своё.
            full_name=None if is_teacher else _text(u['name']),
            is_active=bool(u['active']), date_joined=now,
        ).id
