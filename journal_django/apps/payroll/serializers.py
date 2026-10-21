"""
Serializers for payroll.

PayrollUpdateSerializer — вход для PATCH (ручная правка суммы урока): payment/penalty
необязательны, Decimal≥0 с точностью до копеек; прочие поля — 400.

PayrollReadSerializer — справочное представление вывода (views возвращают сырые
dict из repository, Decimal/date приводит DateSafeJSONRenderer).
"""
from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from apps.core.fields import DateStringField
from apps.payroll import motivation  # тексты ошибок живут в домене


def _money_field(**kwargs) -> serializers.DecimalField:
    # Деньги — DecimalField, не Float: сумма доходит до движка точным Decimal,
    # а лишние знаки после копеек отклоняются валидацией, а не округляются молча.
    return serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0'),
                                     **kwargs)


class PayrollUpdateSerializer(serializers.Serializer):
    """Вход для PATCH /api/admin/payroll/:id: только суммы, обе необязательны.

    total_students/present_count не принимаются: их выводит пересчёт из
    посещаемости и перезаписал бы правку при ближайшем пересчёте урока. Неизвестные
    поля — 400, а не молчаливый игнор: клиент, приславший headcount, должен узнать,
    что правка не применится (как StrictSerializer в scheduling/extra_lessons)."""

    # Ручная правка суммы урока — те же правила денег, что у правки отмены.
    payment = _money_field(required=False)
    penalty = _money_field(required=False)

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({k: 'Неизвестное поле.' for k in sorted(unknown)})
        return attrs


class PayrollReadSerializer(serializers.Serializer):
    """Справочное представление строки payroll + контекст из JOIN (не применяется во views)."""

    id = serializers.IntegerField()
    lesson_id = serializers.IntegerField()
    teacher_id = serializers.IntegerField()
    total_students = serializers.IntegerField()
    present_count = serializers.IntegerField()
    payment = serializers.CharField()
    penalty = serializers.CharField()
    lesson_date = DateStringField(required=False)
    lesson_number = serializers.CharField(required=False)
    group_id = serializers.IntegerField(required=False)
    group_name = serializers.CharField(required=False)
    teacher_name = serializers.CharField(required=False)


# ---------------------------------------------------------------------------
# «Настройки → Мотивация» (спека 2026-10-20-eduson-motivation-payroll-design, §4)
# ---------------------------------------------------------------------------

class RateDirectionInputSerializer(serializers.Serializer):
    """Ячейки направления в версии. null (или отсутствие ключа) — «как по умолчанию»."""

    direction_id = serializers.IntegerField(min_value=1)
    group_rate = _money_field(allow_null=True, required=False, default=None)
    individual_rate = _money_field(allow_null=True, required=False, default=None)


class RateVersionWriteSerializer(serializers.Serializer):
    """Вход POST/PATCH версии ставок. PATCH — partial=True, instance — текущая версия."""

    effective_from = serializers.DateField()
    group_rate = _money_field()
    individual_rate = _money_field()
    cancel_mode = serializers.ChoiceField(choices=['percent', 'fixed'])
    cancel_value = _money_field()
    penalty_enabled = serializers.BooleanField(required=False)
    penalty_mode = serializers.ChoiceField(choices=['per_lesson', 'per_student'], required=False)
    penalty_amount = _money_field(required=False)
    directions = RateDirectionInputSerializer(many=True, required=False)

    def validate_effective_from(self, value):
        from apps.payroll.models import PayRateVersion
        qs = PayRateVersion.objects.filter(effective_from=value)
        if self.instance is not None:
            qs = qs.exclude(id=self.instance.id)
        if qs.exists():
            raise serializers.ValidationError(motivation.DUPLICATE_VERSION_DATE)
        return value

    def validate_directions(self, items):
        from apps.directions.models import Direction
        ids = [item['direction_id'] for item in items]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError(motivation.DUPLICATE_DIRECTION)
        found = set(Direction.objects.filter(id__in=ids).values_list('id', flat=True))
        missing = sorted(set(ids) - found)
        if missing:
            raise serializers.ValidationError(f'Нет направлений с id: {missing}.')
        return items

    def validate(self, attrs):
        # Потолок 100 — только у процента. При PATCH режим и значение могут прийти
        # порознь, поэтому проверяем их вместе с текущей версией.
        inst = self.instance
        mode = attrs.get('cancel_mode', getattr(inst, 'cancel_mode', None))
        value = attrs.get('cancel_value', getattr(inst, 'cancel_value', None))
        if mode == 'percent' and value is not None and value > 100:
            raise serializers.ValidationError(
                {'cancel_value': [motivation.CANCEL_PERCENT_LIMIT]})
        return attrs



class PersonalRateWriteSerializer(serializers.Serializer):
    """Вход POST/PATCH персональной ставки. PATCH — partial=True, instance — текущая."""

    teacher_id = serializers.IntegerField(min_value=1)
    group_id = serializers.IntegerField(min_value=1)
    # Номера уроков курса дробные у 45-минутных групп (numeric 5,1, как Lesson.lesson_number).
    lesson_from = serializers.DecimalField(max_digits=5, decimal_places=1, min_value=Decimal('0'),
                                           allow_null=True, required=False, default=None)
    lesson_to = serializers.DecimalField(max_digits=5, decimal_places=1, min_value=Decimal('0'),
                                         allow_null=True, required=False, default=None)
    kind = serializers.ChoiceField(choices=['fixed', 'plus_fixed', 'plus_percent'])
    value = _money_field()
    note = serializers.CharField(allow_blank=True, required=False, max_length=500)

    def validate_teacher_id(self, value):
        from apps.teachers.models import Teacher
        if not Teacher.objects.filter(id=value).exists():
            raise serializers.ValidationError('Преподаватель не найден.')
        return value

    def validate_group_id(self, value):
        from apps.groups.models import Group
        if not Group.objects.filter(id=value).exists():
            raise serializers.ValidationError('Группа не найдена.')
        return value

    def validate(self, attrs):
        inst = self.instance
        # При partial default=None не подставляется — берём текущие значения.
        lesson_from = attrs['lesson_from'] if 'lesson_from' in attrs else getattr(
            inst, 'lesson_from', None)
        lesson_to = attrs['lesson_to'] if 'lesson_to' in attrs else getattr(
            inst, 'lesson_to', None)
        if (lesson_from is None) != (lesson_to is None):
            raise serializers.ValidationError({'lesson_to': [motivation.RANGE_BOTH_OR_NONE]})
        if lesson_from is not None and lesson_from > lesson_to:
            raise serializers.ValidationError({'lesson_to': [motivation.RANGE_ORDER]})
        self._check_group_of_teacher(attrs)
        return attrs

    def _check_group_of_teacher(self, attrs):
        """Персональная ставка — только в группе, которую ведёт преподаватель
        (решение владельца 2026-10-21; фронт сужает список, это — авторитетная
        проверка). При правке проверяется, только если меняется преподаватель или
        группа: ставка, чью группу позже передали другому, правится как есть."""
        from apps.groups.models import Group
        inst = self.instance
        teacher_id = attrs.get('teacher_id', getattr(inst, 'teacher_id', None))
        group_id = attrs.get('group_id', getattr(inst, 'group_id', None))
        if inst is not None and teacher_id == inst.teacher_id and group_id == inst.group_id:
            return
        if not Group.objects.filter(id=group_id, teacher_id=teacher_id).exists():
            raise serializers.ValidationError({'group_id': [motivation.GROUP_NOT_OF_TEACHER]})


class CancellationPayUpdateSerializer(serializers.Serializer):
    """Вход PATCH начисления за отмену: только сумма (ручная правка)."""

    payment = _money_field()
