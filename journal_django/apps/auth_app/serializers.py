"""
serializers.py — DRF Serializer-ы для auth-эндпоинтов.

Порт Zod-схем из shared/schemas.js (строки 18-47):
  loginSchema, login2faSchema, twofaSetupSchema, twofaEnableSchema,
  emailSendSchema, twofaDisableSchema.

EmailField автоматически валидирует формат; в validate() приводим к lowercase+strip
(Zod: z.string().trim().toLowerCase().email()).
"""
from __future__ import annotations

from rest_framework import serializers
from apps.accounts.models import Account


class MeSerializer(serializers.ModelSerializer):
    """Данные текущего пользователя для GET /me."""
    
    account_id = serializers.IntegerField(source='id', read_only=True)
    teacher_id = serializers.IntegerField(read_only=True, allow_null=True)
    name = serializers.SerializerMethodField()
    
    class Meta:
        model = Account
        fields = [
            'account_id',
            'email',
            'role',
            'teacher_id',
            'name',
            'twofa_enabled',
        ]
        read_only_fields = fields
    
    def get_name(self, obj) -> str:
        teacher_name = getattr(obj, 'teacher_name', None)
        return obj.full_name or teacher_name or obj.email


class LoginSerializer(serializers.Serializer):
    """Порт loginSchema: {email, password, role:'teacher'|'admin'}."""

    email = serializers.EmailField()
    password = serializers.CharField(min_length=1)
    role = serializers.ChoiceField(choices=['teacher', 'admin'])

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class Login2faSerializer(serializers.Serializer):
    """Порт login2faSchema: {challenge_token, code}."""

    challenge_token = serializers.CharField(min_length=1)
    code = serializers.CharField(min_length=1, trim_whitespace=True)


class TwofaSetupSerializer(serializers.Serializer):
    """Порт twofaSetupSchema: {challenge_token?, method:'totp'|'email'}."""

    challenge_token = serializers.CharField(min_length=1, required=False, allow_null=True)
    method = serializers.ChoiceField(choices=['totp', 'email'])


class TwofaEnableSerializer(serializers.Serializer):
    """Порт twofaEnableSchema: {challenge_token?, code}."""

    challenge_token = serializers.CharField(min_length=1, required=False, allow_null=True)
    code = serializers.CharField(min_length=1, trim_whitespace=True)


class EmailSendSerializer(serializers.Serializer):
    """Порт emailSendSchema: {challenge_token}."""

    challenge_token = serializers.CharField(min_length=1)


class TwofaDisableSerializer(serializers.Serializer):
    """Порт twofaDisableSchema: {password}."""

    password = serializers.CharField(min_length=1)


class InviteAcceptSerializer(serializers.Serializer):
    """
    Инвайт-accept: {token, password}. password 8..72 символа.

    max_length=72: bcrypt молча обрезает байты после 72-го → ограничиваем явно
    для детерминированного поведения (M3 ИБ-аудита).
    """

    token = serializers.CharField(min_length=1)
    password = serializers.CharField(min_length=8, max_length=72)
