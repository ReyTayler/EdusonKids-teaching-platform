"""
Bootstrap первого суперадминистратора (замена legacy Node scripts/create-account.js).

  python manage.py bootstrap_admin --email=admin@example.com [--if-empty]

Создаёт superadmin-учётку в состоянии «приглашён» (БЕЗ пароля) и печатает
invite-ссылку в stdout — НЕ пароль. По ссылке суперадминистратор сам задаёт
пароль и настраивает 2FA.

Роль именно superadmin (не admin): доступ к разделам Учётки/Журнал ИБ/Зарплата
даётся только superadmin, поэтому после свежего разворачивания должна
существовать хотя бы одна такая учётка, иначе управлять доступами будет некому.

--if-empty: создавать только если в accounts нет ни одного superadmin
(idempotent, безопасно гонять в деплое).
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email

from apps.accounts import repository, services


class _NoReq:
    """Заглушка request для audit.log_event вне HTTP-контекста (нет ip/user-agent)."""

    META: dict = {}


class Command(BaseCommand):
    help = 'Создать первого суперадминистратора и напечатать invite-ссылку.'

    def add_arguments(self, parser):
        parser.add_argument('--email', required=True, help='Email суперадминистратора.')
        parser.add_argument(
            '--if-empty', action='store_true',
            help='Создавать только если нет ни одного superadmin (idempotent).',
        )

    def handle(self, *args, **opts):
        # Нормализация как в LoginSerializer (strip+lower), валидация — штатным Django.
        email = opts['email'].strip().lower()
        try:
            validate_email(email)
        except ValidationError:
            raise CommandError('Некорректный email.')

        if opts['if_empty'] and repository.admin_exists():
            self.stdout.write('Superadmin уже существует — пропуск (--if-empty).')
            return

        if repository.find_by_email(email) is not None:
            raise CommandError(f'Учётка {email} уже существует.')

        acc = repository.create_account(email=email, role='superadmin', teacher_id=None)
        invite = services.issue_invite(acc['id'], actor_account_id=None, request=_NoReq())

        self.stdout.write(self.style.SUCCESS(f'Создан суперадминистратор {email}.'))
        self.stdout.write(f"Invite-ссылка (48 ч): {invite['invite_url']}")
