"""
Сериализаторы-валидаторы admin-операций плана (planned_lessons).

Строгая валидация входа каждой мутации (шаг 4). Стиль зеркалит
apps/groups/serializers.py (ScheduleChangeSerializer):
даты — DateStringField ('YYYY-MM-DD'), время — HH:MM(:SS), day_of_week 0..6 (Вс=0).

Лишние поля не принимаются молча — StrictSerializer.validate отклоняет неизвестные
ключи (чтобы опечатка в имени поля не проходила тихо мимо валидации).
"""
from __future__ import annotations

import datetime
import re

from rest_framework import serializers

from apps.core.fields import DateStringField
from apps.core.utils.dates import msk_now

# Время слота/занятия: HH:MM или HH:MM:SS (как VALID_SLOT_TIME_RE в groups).
_TIME_RE = re.compile(r'^\d{2}:\d{2}(:\d{2})?$')


def _validate_time(value):
    """Проверить формат HH:MM(:SS); None/'' пропускаем (optional-поля)."""
    if value in (None, ''):
        return value
    if not _TIME_RE.match(value):
        raise serializers.ValidationError('Время должно быть в формате HH:MM или HH:MM:SS.')
    return value


class StrictSerializer(serializers.Serializer):
    """Базовый сериализатор, отклоняющий неизвестные поля (не «глотать» опечатки)."""

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(
                {k: 'Неизвестное поле.' for k in sorted(unknown)}
            )
        return attrs


class PlanRescheduleSerializer(StrictSerializer):
    """POST /plan/<lid>/reschedule — разовый перенос (+опц. время/преподаватель)."""

    new_date = DateStringField()
    new_time = serializers.CharField(required=False, allow_null=True)
    new_teacher_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def validate_new_time(self, value):
        return _validate_time(value)


class PlanSlotSerializer(serializers.Serializer):
    """Один слот целевого набора (перенос навсегда мультислотовой группы)."""

    day_of_week = serializers.IntegerField(min_value=0, max_value=6)
    start_time = serializers.CharField()

    def validate_start_time(self, value):
        if not _TIME_RE.match(value or ''):
            raise serializers.ValidationError('Время должно быть в формате HH:MM или HH:MM:SS.')
        return value


class PlanPermanentChangeSerializer(StrictSerializer):
    """POST /plan/permanent-change — «изменить расписание» с позиции from_seq.

    Единый контракт (никакого легаси-скаляра): клиент явно передаёт
      - effective_from — дату, с которой действует новый набор слотов;
      - new_slots — целевой НАБОР слотов (1..N, {'day_of_week', 'start_time'}).
    Хвост (seq>=from_seq) чисто перегенерируется от effective_from по new_slots
    (см. repository.permanent_change) — не переносится относительно старых дат."""

    from_seq = serializers.IntegerField(min_value=1)
    effective_from = DateStringField()
    new_slots = PlanSlotSerializer(many=True, required=True, allow_empty=False)
    new_teacher_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    # Дран-режим: клиент всё равно шлёт полный целевой payload — preview лишь
    # просит вернуть repository.preview_affected (что БУДЕТ сброшено) вместо
    # реальной записи. Не делает new_slots/effective_from опциональными.
    preview = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)  # отклонить неизвестные поля
        seen = set()
        for s in attrs['new_slots']:
            key = (s['day_of_week'], s['start_time'][:5])
            if key in seen:
                raise serializers.ValidationError('Дублирующийся слот в new_slots.')
            seen.add(key)
        return attrs


class PlanChangeTeacherSerializer(StrictSerializer):
    """POST /plan/<lid>/change-teacher — разовая смена преподавателя одной строки."""

    new_teacher_id = serializers.IntegerField(min_value=1)


class PlanCancelSerializer(StrictSerializer):
    """POST /plan/<lid>/cancel — отмена с причиной (спека 2026-10-23, §5).

    lesson_date — дата занятия, как её видел клиент: сервис сверяет её с датой
    строки под локом (повторный POST того же lid → 409, а не отмена следующего
    урока). Существование и сторону причины проверяет cancellations.services
    (нужна БД), здесь — только форма тела."""

    lesson_date = DateStringField()
    # Строки, а не CancellationSide: scheduling зависит от cancellations только
    # локальными импортами (обратная зависимость — FK записи на маркер). Набор
    # совпадает с CHECK lesson_cancel_side_check; рассинхрон ловит build_draft
    # (сторона причины) и сама БД.
    side = serializers.ChoiceField(choices=[('teacher', 'Преподаватель'),
                                            ('client', 'Клиент')])
    reason_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    # trim_whitespace: текст из одних пробелов становится пустым и не считается
    # причиной; max_length — по обрезанному тексту, как его и сохранят.
    other_text = serializers.CharField(required=False, allow_null=True, allow_blank=True,
                                       max_length=1000, trim_whitespace=True)
    notified_on = DateStringField(required=False, allow_null=True)

    def validate_other_text(self, value):
        """Невидимые символы форматирования (Cf: ​, ﻿, ⁠…) вырезаются — иначе
        «причина» из одного такого символа прошла бы как непустая. Переводы
        строк сохраняются: описание бывает многострочным."""
        if not value:
            return value
        from apps.cancellations.serializers import drop_format_chars
        cleaned = drop_format_chars(value).strip()
        if not cleaned:
            raise serializers.ValidationError(
                'Описание причины не может состоять из невидимых символов.')
        return cleaned

    def validate(self, attrs):
        attrs = super().validate(attrs)
        has_reason = attrs.get('reason_id') is not None
        has_other = bool(attrs.get('other_text'))
        if has_reason == has_other:
            raise serializers.ValidationError(
                'Укажите причину из списка или опишите другую причину.')
        attrs.setdefault('reason_id', None)
        attrs['other_text'] = attrs.get('other_text') or None
        if attrs['side'] == 'teacher':
            # В7а: для стороны «Преподаватель» дата уведомления не нужна —
            # игнорируем, а не отказываем: форма могла прислать значение по умолчанию.
            attrs['notified_on'] = None
        elif not attrs.get('notified_on'):
            raise serializers.ValidationError(
                {'notified_on': 'Укажите, когда сообщили об отмене.'})
        elif datetime.date.fromisoformat(attrs['notified_on']) > msk_now().date():
            raise serializers.ValidationError(
                {'notified_on': 'Дата уведомления не может быть в будущем.'})
        return attrs


class PlanChangeTeacherPermanentSerializer(StrictSerializer):
    """POST /plan/change-teacher-permanent — смена преподавателя хвоста (seq>=from_seq)."""

    from_seq = serializers.IntegerField(min_value=1)
    new_teacher_id = serializers.IntegerField(min_value=1)


_ISO_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


class PlanResyncSerializer(StrictSerializer):
    """
    POST /plan/resync — починка привязок план↔факт.

    expected — целевое состояние КАЖДОЙ меняющейся позиции из предпросмотра:
    [[position_id, fact_lesson_id|null, 'YYYY-MM-DD'], ...]. Клиент подтверждает
    ровно тот дифф, который ему показали; сервер сверяет его с диффом, посчитанным
    под локом, и при расхождении отдаёт 409 (состояние успело измениться).
    Пустой список — законный вход: «чинить нечего» после успешной починки.

    Тройки разбираем вручную: ListField с разнотипными позициями DRF не
    выражает, а форму надо проверить до сервиса (иначе 500 вместо 400).
    """

    expected = serializers.ListField(child=serializers.JSONField(), allow_empty=True)

    def validate_expected(self, value):
        normalized = []
        seen = set()
        for item in value:
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                raise serializers.ValidationError(
                    'Каждый элемент — [position_id, fact_lesson_id|null, "YYYY-MM-DD"].')
            position_id, fact_lesson_id, date_str = item
            if not isinstance(position_id, int) or isinstance(position_id, bool) or position_id < 1:
                raise serializers.ValidationError('position_id — целое число ≥ 1.')
            if fact_lesson_id is not None and (
                not isinstance(fact_lesson_id, int) or isinstance(fact_lesson_id, bool)
                or fact_lesson_id < 1
            ):
                raise serializers.ValidationError('fact_lesson_id — целое число ≥ 1 или null.')
            if not isinstance(date_str, str) or not _ISO_DATE_RE.match(date_str):
                raise serializers.ValidationError('Дата должна быть в формате YYYY-MM-DD.')
            if position_id in seen:
                raise serializers.ValidationError('Позиция указана дважды.')
            seen.add(position_id)
            normalized.append((position_id, fact_lesson_id, date_str))
        return normalized
