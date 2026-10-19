"""
AccountsRepository — единственное место доступа к данным раздела accounts.

После миграции на AbstractUser:
  • password_hash → password
  • active → is_active
  • last_login_at → last_login
  • created_at → date_joined
"""
from __future__ import annotations

from typing import Any, Optional

from django.db import connection, transaction
from django.db.models import Exists, F, OuterRef, Q
from django.db.models.functions import Now
from django.utils import timezone

from apps.core.utils.orm import dictrow, dictrows

from .models import Account, AccountInvite, AccountRecoveryCode


# ---------------------------------------------------------------------------
# Pagination-конфиг
# ---------------------------------------------------------------------------

_LIST_FIELDS = (
    'id', 'email', 'role', 'teacher_id', 'is_active', 'twofa_enabled',
    'twofa_method', 'last_login', 'full_name',
)

_SORTABLE: dict[str, str] = {
    'email':      'email',
    'role':       'role',
    'is_active':  'is_active',
    'date_joined': 'date_joined',
}

_DEFAULT_SORT_BY = 'email'
_DEFAULT_SORT_DIR = 'asc'


def _active_invite_subquery():
    return AccountInvite.objects.filter(
        account=OuterRef('pk'),
        used_at__isnull=True,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    )


def _account_status(row: dict) -> str:
    if not row.get('is_active', True):
        return 'disabled'
    if row.get('last_login') is not None:
        return 'active'
    if row.get('has_active_invite'):
        return 'invited'
    return 'expired'


def _apply_filters(qs, filters: dict[str, Any]):
    email = filters.get('email')
    if email not in (None, ''):
        qs = qs.filter(email__icontains=str(email))

    role = filters.get('role')
    if role not in (None, ''):
        qs = qs.filter(role=role)

    active = filters.get('active')
    if active not in (None, ''):
        qs = qs.filter(is_active=(active is True or str(active).lower() == 'true'))

    teacher_name = filters.get('teacher_name')
    if teacher_name not in (None, ''):
        qs = qs.filter(teacher__name__icontains=str(teacher_name))

    return qs


# ---------------------------------------------------------------------------
# Repository functions
# ---------------------------------------------------------------------------

def list_accounts(
    page: int = 1,
    page_size: int = 50,
    sort_by: str = _DEFAULT_SORT_BY,
    sort_dir: str = _DEFAULT_SORT_DIR,
    filters: Optional[dict] = None,
) -> dict:
    if filters is None:
        filters = {}

    sort_field = _SORTABLE.get(sort_by) or _SORTABLE[_DEFAULT_SORT_BY]
    order_prefix = '' if sort_dir == 'asc' else '-'

    qs = _apply_filters(Account.objects.all(), filters)

    total = qs.count()

    offset = max(0, (page - 1) * page_size)
    ordered = qs.annotate(
        has_active_invite=Exists(_active_invite_subquery()),
    ).order_by(f'{order_prefix}{sort_field}', '-id')

    rows = dictrows(
        ordered[offset:offset + page_size].values(
            *_LIST_FIELDS,
            'has_active_invite',
            teacher_name=F('teacher__name'),
        )
    )

    for row in rows:
        row['status'] = _account_status(row)
        row['name'] = row.get('full_name') or row.get('teacher_name') or row['email']

    return {'rows': rows, 'total': total, 'page': page, 'page_size': page_size}


def find_by_email(email: str) -> Optional[dict]:
    return dictrow(Account.objects.filter(email=email).values())


def get_by_id(account_id: int) -> Optional[dict]:
    return dictrow(Account.objects.filter(id=account_id).values())


def get_by_id_with_teacher(account_id: int) -> Optional[dict]:
    return dictrow(
        Account.objects.filter(id=account_id).values(
            *_account_full_fields(), teacher_name=F('teacher__name'),
        )
    )


def create_account(email: str, role: str, teacher_id=None, password: str = None, full_name: str = None) -> dict:
    obj = Account.objects.create(
        email=email,
        password=password or '',  # AbstractUser требует не-null password
        role=role,
        teacher_id=teacher_id,
        full_name=full_name,
        date_joined=Now(),
    )
    return dictrow(Account.objects.filter(pk=obj.pk).values())


def update_account(account_id: int, email=None, role=None, active=None, full_name=None) -> Optional[dict]:
    obj = Account.objects.filter(id=account_id).first()
    if obj is None:
        return None
    if email is not None:
        obj.email = email
    if role is not None:
        obj.role = role
    if active is not None:
        obj.is_active = active
    if full_name is not None:
        obj.full_name = full_name
    obj.save()
    return dictrow(Account.objects.filter(id=account_id).values())


def update_full_name(account_id: int, full_name: Optional[str]) -> bool:
    return Account.objects.filter(id=account_id).update(full_name=full_name) > 0


def set_password(account_id: int, password: Optional[str]) -> bool:
    if password is None:
        password = '!'  # Невозможный хеш — вход никогда не сработает
    return Account.objects.filter(id=account_id).update(password=password) > 0


def soft_delete(account_id: int) -> bool:
    return Account.objects.filter(id=account_id).update(is_active=False) > 0


def hard_delete(account_id: int) -> bool:
    return Account.objects.filter(id=account_id).delete()[0] > 0


def set_active(account_id: int, active: bool) -> bool:
    return Account.objects.filter(id=account_id).update(is_active=active) > 0


def reset_twofa(account_id: int) -> Optional[dict]:
    with transaction.atomic():
        AccountRecoveryCode.objects.filter(account_id=account_id).delete()
        Account.objects.filter(id=account_id).update(
            twofa_method=None, twofa_secret=None,
            twofa_enabled=False, twofa_confirmed_at=None,
        )
        return dictrow(Account.objects.filter(id=account_id).values())


# ---------------------------------------------------------------------------
# Auth functions
# ---------------------------------------------------------------------------

def set_twofa(
    account_id: int,
    method: Optional[str],
    secret: Optional[str],
    enabled: bool,
    confirmed: bool,
) -> Optional[dict]:
    updated = Account.objects.filter(id=account_id).update(
        twofa_method=method,
        twofa_secret=secret,
        twofa_enabled=bool(enabled),
        twofa_confirmed_at=Now() if confirmed else F('twofa_confirmed_at'),
    )
    if not updated:
        return None
    return dictrow(Account.objects.filter(id=account_id).values())


def bump_token_version(account_id: int) -> None:
    Account.objects.filter(id=account_id).update(token_version=F('token_version') + 1)


def get_auth_state(account_id: int) -> Optional[dict]:
    return dictrow(
        Account.objects.filter(id=account_id).values('token_version', 'is_active')
    )


def register_login_success(account_id: int) -> None:
    Account.objects.filter(id=account_id).update(
        failed_login_count=0, locked_until=None, last_login=Now(),
    )


def register_login_failure(
    account_id: int,
    max_fails: int = 5,
    lock_ms: int = 15 * 60 * 1000,
) -> Optional[dict]:
    with connection.cursor() as cur:
        cur.execute(
            'UPDATE accounts SET '
            'failed_login_count = failed_login_count + 1, '
            'locked_until = CASE WHEN failed_login_count + 1 >= %s '
            "THEN now() + (%s || ' milliseconds')::interval "
            'ELSE locked_until END '
            'WHERE id=%s RETURNING failed_login_count, locked_until',
            [max_fails, str(lock_ms), account_id],
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {'failed_login_count': row[0], 'locked_until': row[1]}


def replace_recovery_codes(account_id: int, hashes: list[str]) -> None:
    with transaction.atomic():
        AccountRecoveryCode.objects.filter(account_id=account_id).delete()
        if hashes:
            AccountRecoveryCode.objects.bulk_create([
                AccountRecoveryCode(account_id=account_id, code_hash=h) for h in hashes
            ])


def list_recovery_codes(account_id: int) -> list[dict]:
    return dictrows(
        AccountRecoveryCode.objects.filter(account_id=account_id)
        .order_by('id')
        .values('id', 'account_id', 'code_hash', 'used_at')
    )


def mark_recovery_used(rc_id: int) -> None:
    AccountRecoveryCode.objects.filter(id=rc_id).update(used_at=Now())


# ---------------------------------------------------------------------------
# Invites repository
# ---------------------------------------------------------------------------

def create_invite(account_id: int, token_hash: str, created_by: int, expires_at) -> dict:
    obj = AccountInvite.objects.create(
        account_id=account_id,
        token_hash=token_hash,
        created_by=created_by,
        created_at=Now(),
        expires_at=expires_at,
    )
    return dictrow(AccountInvite.objects.filter(pk=obj.pk).values())


def find_active_by_hash(token_hash: str) -> Optional[dict]:
    return dictrow(
        AccountInvite.objects.filter(
            token_hash=token_hash,
            used_at__isnull=True,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).values()
    )


def revoke_active_for_account(account_id: int) -> int:
    return AccountInvite.objects.filter(
        account_id=account_id,
        used_at__isnull=True,
        revoked_at__isnull=True,
    ).update(revoked_at=Now())


def accept_invite(invite_id: int, password_hash: str) -> Optional[dict]:
    with transaction.atomic():
        invite = (
            AccountInvite.objects.select_for_update()
            .filter(id=invite_id, used_at__isnull=True, revoked_at__isnull=True)
            .first()
        )
        if invite is None:
            return None
        if invite.expires_at < timezone.now():
            return None

        AccountInvite.objects.filter(pk=invite_id).update(used_at=Now())
        Account.objects.filter(pk=invite.account_id).update(password=password_hash)
        Account.objects.filter(pk=invite.account_id).update(
            token_version=F('token_version') + 1
        )
        return dictrow(AccountInvite.objects.filter(pk=invite_id).values())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def admin_exists() -> bool:
    return Account.objects.filter(role='superadmin').exists()


def _account_full_fields() -> tuple[str, ...]:
    return tuple(f.attname for f in Account._meta.concrete_fields)