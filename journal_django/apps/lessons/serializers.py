"""
Serializers for lessons.

Правила валидации — точный порт Zod-схем из shared/schemas.js:
  attendanceItemSchema / payrollPartSchema / createLessonSchema /
  updateLessonSchema / updateAttendanceSchema.

LessonReadSerializer — справочное представление вывода. Views возвращают сырые
dict из repository (как в apps/groups), а Decimal/date-поля приводит
DateSafeJSONRenderer — это обеспечивает байт-в-байт совпадение с Express.

Принятая допустимая разница с Express (не влияет на реальный фронт, который шлёт
JSON-числа): DRF FloatField/IntegerField коэрсят числовые строки ("1" → 1), тогда
как Zod z.number() их отвергает (400). Поля id (group_id/teacher_id/student_id/
original_teacher_id) у Zod — z.coerce.number(), поэтому там коэрсинг совпадает.
"""
from __future__ import annotations

from rest_framework import serializers

from apps.core.fields import DateStringField
from apps.lessons.models import COURSE_LESSON_TYPES

# ---- константы из Zod-схем (shared/schemas.js) ----
VALID_LESSON_DURATIONS = (45, 60, 90)
# Единый источник набора — apps.lessons.models (там же SYSTEM_LESSON_TYPES).
VALID_LESSON_TYPES = COURSE_LESSON_TYPES


class AttendanceItemSerializer(serializers.Serializer):
    """Элемент посещаемости (attendanceItemSchema): student_id + present + is_free.

    is_free — исход «бесплатное занятие» (present=true, баланс/FIFO ученика не
    трогаются, зарплата преподавателю начисляется, прогресс/продление идут). Опционален,
    по умолчанию False. См. lesson-outcomes-spec.
    """

    student_id = serializers.IntegerField(min_value=1)
    present = serializers.BooleanField()
    is_free = serializers.BooleanField(required=False, default=False)


class LessonCreateSerializer(serializers.Serializer):
    """
    Вход для POST /api/admin/lessons (createLessonSchema).

    Обязательные: lesson_date, group_id, teacher_id, lesson_number.
    """

    lesson_date = DateStringField()
    group_id = serializers.IntegerField(min_value=1)
    teacher_id = serializers.IntegerField(min_value=1)
    original_teacher_id = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    lesson_number = serializers.FloatField(min_value=0.5)
    lesson_duration_minutes = serializers.ChoiceField(
        choices=VALID_LESSON_DURATIONS, required=False
    )
    lesson_type = serializers.ChoiceField(choices=VALID_LESSON_TYPES, required=False)
    # Zod z.string() не триммит — отключаем DRF trim, чтобы значение совпадало с Express.
    record_url = serializers.CharField(
        allow_null=True, allow_blank=True, required=False, trim_whitespace=False
    )
    submitted_by_token = serializers.CharField(required=False, trim_whitespace=False)
    attendance = AttendanceItemSerializer(many=True, required=False)
    # allow_debt — «записать, невзирая на отрицательный баланс». Право проверяет
    # вьюха (только superadmin), здесь только приём значения. Занятие остаётся
    # ПЛАТНЫМ: спишется с баланса — это не is_free.
    allow_debt = serializers.BooleanField(required=False, default=False)


class LessonUpdateSerializer(serializers.Serializer):
    """
    Вход для PATCH /api/admin/lessons/:id (updateLessonSchema).

    Все поля необязательны. original_teacher_id nullable — различаем
    «не передано» и «явный null» по наличию ключа в validated_data.
    """

    lesson_date = DateStringField(required=False)
    teacher_id = serializers.IntegerField(min_value=1, required=False)
    lesson_number = serializers.FloatField(min_value=0.5, required=False)
    lesson_type = serializers.ChoiceField(choices=VALID_LESSON_TYPES, required=False)
    record_url = serializers.CharField(
        allow_null=True, allow_blank=True, required=False, trim_whitespace=False
    )
    original_teacher_id = serializers.IntegerField(min_value=1, allow_null=True, required=False)


class AttendanceUpdateSerializer(serializers.Serializer):
    """Вход для toggle посещаемости (updateAttendanceSchema): present + is_free.

    is_free — исход «бесплатное занятие» на уже проведённом уроке (present=true,
    с ученика не списывается). Опционален, по умолчанию False; при present=false игнорируется
    (отсутствовавший не может быть «бесплатным»). Позволяет проставить бесплатный
    урок постфактум — типовой результат разрешения спора после занятия.
    """

    present = serializers.BooleanField()
    is_free = serializers.BooleanField(required=False, default=False)
    # См. LessonCreateSerializer.allow_debt — то же право, тот же смысл:
    # «отметить присутствие, невзирая на долг». Право проверяет вьюха.
    allow_debt = serializers.BooleanField(required=False, default=False)


class UnpaidSkipUpdateSerializer(serializers.Serializer):
    """Вход для исхода «неоплачиваемый пропуск»: value=true поставить, false снять."""

    value = serializers.BooleanField()


class LessonReadSerializer(serializers.Serializer):
    """
    Справочное представление урока (вывод getLessonFull).

    Не применяется во views (там сырые dict + DateSafeJSONRenderer) — оставлено
    для документации полей и потенциального schema-генератора.
    """

    id = serializers.IntegerField()
    group_id = serializers.IntegerField()
    teacher_id = serializers.IntegerField()
    original_teacher_id = serializers.IntegerField(allow_null=True)
    lesson_date = DateStringField()
    lesson_number = serializers.CharField()
    lesson_duration_minutes = serializers.IntegerField()
    lesson_type = serializers.CharField()
    record_url = serializers.CharField(allow_null=True)
    submitted_at = serializers.DateTimeField()
    submitted_by_token = serializers.CharField()
    group_name = serializers.CharField(required=False)
    teacher_name = serializers.CharField(required=False)
    original_teacher_name = serializers.CharField(allow_null=True, required=False)
