"""
Models for accounts — стандартный Django AbstractUser + 2FA + инвайты.

Роли: teacher, manager, admin (через choices).
2FA: totp / email.
Инварианты на уровне БД сохраняем через CheckConstraint.
"""
from __future__ import annotations

import pghistory
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone


class AccountManager(BaseUserManager):
    """Менеджер учёток — email как основной идентификатор."""
    
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError('Email обязателен')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', 'admin')
        return self._create_user(email, password, **extra_fields)

    def get_by_natural_key(self, email):
        return self.get(email=email)


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
    exclude=[
        'password', 'twofa_secret',           # секреты — НИКОГДА в журнал
        'token_version', 'last_login',        # технический шум (меняются при каждом входе)
        'failed_login_count', 'locked_until',
    ],
)
class Account(AbstractUser):
    """Учётка пользователя (teacher | manager | admin)."""
    
    # Отключаем username — используем email
    username = None
    
    # Email как основной идентификатор
    email = models.EmailField(unique=True, verbose_name='email address')
    
    # Роль пользователя
    class Role(models.TextChoices):
        TEACHER = 'teacher', 'Учитель'
        MANAGER = 'manager', 'Менеджер'
        ADMIN = 'admin', 'Администратор'
        SUPERADMIN = 'superadmin', 'Суперадминистратор'

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        verbose_name='role',
    )

    # Отображаемое имя (для manager/admin/superadmin; у teacher берётся из преподавателя)
    full_name = models.CharField(max_length=200, null=True, blank=True, verbose_name='full name')

    # FK на учителя (только для роли teacher)
    teacher = models.ForeignKey(
        'teachers.Teacher',
        on_delete=models.DO_NOTHING,
        related_name='accounts',
        null=True,
        blank=True,
        verbose_name='teacher',
    )
    
    # 2FA поля
    twofa_method = models.CharField(
        max_length=10,
        null=True,
        blank=True,
        choices=[('totp', 'TOTP'), ('email', 'Email')],
        verbose_name='2FA method',
    )
    twofa_secret = models.TextField(null=True, blank=True, verbose_name='2FA secret')
    twofa_enabled = models.BooleanField(default=False, verbose_name='2FA enabled')
    twofa_confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name='2FA confirmed at')
    
    # Локаут при неудачных попытках входа
    failed_login_count = models.IntegerField(default=0, verbose_name='failed login count')
    locked_until = models.DateTimeField(null=True, blank=True, verbose_name='locked until')
    
    # Версия токена для инвалидации сессий
    token_version = models.IntegerField(default=0, verbose_name='token version')
    
    # Переопределяем last_login — уже есть в AbstractUser
    # created_at — используем date_joined из AbstractUser
    
    objects = AccountManager()
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []
    
    class Meta:
        db_table = 'accounts'
        verbose_name = 'account'
        verbose_name_plural = 'accounts'
        constraints = [
            models.CheckConstraint(
                name='accounts_role_check',
                condition=models.Q(role__in=['teacher', 'manager', 'admin', 'superadmin']),
            ),
            models.CheckConstraint(
                name='accounts_twofa_method_check',
                condition=models.Q(twofa_method__in=['totp', 'email']),
            ),
            # teacher_id IS NOT NULL ↔ role = 'teacher'
            models.CheckConstraint(
                name='accounts_teacher_role_check',
                condition=(
                    (models.Q(role='teacher') & models.Q(teacher__isnull=False))
                    | (~models.Q(role='teacher') & models.Q(teacher__isnull=True))
                ),
            ),
            # twofa_method='totp' → twofa_secret IS NOT NULL
            models.CheckConstraint(
                name='accounts_totp_secret_check',
                condition=~models.Q(twofa_method='totp') | models.Q(twofa_secret__isnull=False),
            ),
            # Уникальный teacher (только для не-null)
            models.UniqueConstraint(
                fields=['teacher'],
                name='accounts_teacher_id_uq',
                condition=models.Q(teacher__isnull=False),
            ),
        ]
    
    def __str__(self):
        return self.email
    
    @property
    def is_teacher(self):
        return self.role == self.Role.TEACHER
    
    @property
    def is_manager(self):
        return self.role == self.Role.MANAGER
    
    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN

    @property
    def is_superadmin(self):
        return self.role == self.Role.SUPERADMIN

    def has_role(self, *roles):
        """Проверить, что пользователь имеет одну из указанных ролей."""
        return self.role in roles


# AccountInvite и AccountRecoveryCode — без изменений, 
# только обновляем related_name если нужно
class AccountInvite(models.Model):
    """Invite-токен для первоначальной установки пароля."""

    id = models.AutoField(primary_key=True)
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name='invites',
    )
    token_hash = models.TextField()
    created_by = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = True
        db_table = 'account_invites'
        indexes = [
            models.Index(fields=['token_hash'], name='ai_token_hash_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['account'],
                name='ai_one_active_per_account',
                condition=models.Q(used_at__isnull=True, revoked_at__isnull=True),
            ),
        ]


class AccountRecoveryCode(models.Model):
    """Recovery-код для 2FA-сброса."""

    id = models.AutoField(primary_key=True)
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name='recovery_codes',
    )
    code_hash = models.TextField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = True
        db_table = 'account_recovery_codes'
        indexes = [
            models.Index(fields=['account'], name='arc_account_idx'),
        ]