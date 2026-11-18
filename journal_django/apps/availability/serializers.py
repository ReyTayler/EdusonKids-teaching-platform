"""
Валидация входа графика работы: 'HH:MM', шаг 30 мин, 08:00–22:00, start < end.

Содержимое days (ключи дней/дат и интервалы) разбирается в object-level
validate(), а не в child-поле DictField: эти ошибки уходят в non_field_errors —
фронт (lib/api.ts extractErrorDetail) показывает именно их; вложенная по дням
ошибка под ключом 'days' превратилась бы в безликое «Validation failed».
Ошибки ФОРМЫ полей (days отсутствует или не объект, monday не дата) остаются
под ключом поля — их шлёт только сломанный клиент, не пользовательский ввод.

В текст ошибки ввод попадает только строкой и обрезанным (_quote): ответ
не должен эхом возвращать произвольный объём/структуру из запроса.
Те же правила дублирует доменный services.validate_intervals — он защищает
команды и бэкфиллы, которые обходят сериализатор.
"""
from __future__ import annotations

import datetime
import re

from rest_framework import serializers

from apps.availability.models import GRID_END, GRID_START

_HHMM = re.compile(r'^([01]\d|2[0-3]):(00|30)$')
_DAY_KEYS = frozenset(str(i) for i in range(7))
_QUOTE_MAX = 20


def _quote(value) -> str | None:
    """Строка ввода для текста ошибки, обрезанная до _QUOTE_MAX; не строка → None."""
    if not isinstance(value, str):
        return None
    return value if len(value) <= _QUOTE_MAX else value[:_QUOTE_MAX] + '…'


def _parse_hhmm(value) -> datetime.time:
    # fullmatch, а не match: '$' в re пропускает завершающий перевод строки ('10:00\n').
    if not isinstance(value, str) or not _HHMM.fullmatch(value):
        quoted = _quote(value)
        prefix = f'Время «{quoted}»' if quoted is not None else 'Время'
        raise serializers.ValidationError(f'{prefix} — формат HH:MM с шагом 30 минут.')
    h, m = value.split(':')
    return datetime.time(int(h), int(m))


def parse_intervals(data) -> list[tuple[datetime.time, datetime.time]]:
    """[['10:00','14:00'], …] → [(time, time), …]."""
    if not isinstance(data, list):
        raise serializers.ValidationError('Ожидается список интервалов.')
    out = []
    for item in data:
        if not isinstance(item, list) or len(item) != 2:
            raise serializers.ValidationError('Интервал — пара [начало, конец].')
        start, end = _parse_hhmm(item[0]), _parse_hhmm(item[1])
        if start < GRID_START or end > GRID_END:
            raise serializers.ValidationError('Окна — в пределах 08:00–22:00.')
        if start >= end:
            raise serializers.ValidationError('Начало окна должно быть раньше конца.')
        out.append((start, end))
    return out


class TemplateSerializer(serializers.Serializer):
    """{'days': {'0'..'6': [[HH:MM, HH:MM], …]}} → {'days': {int: [(time, time)]}}. Вс=0."""

    days = serializers.DictField()

    def validate(self, attrs):
        out = {}
        for key, intervals in attrs['days'].items():
            if key not in _DAY_KEYS:
                raise serializers.ValidationError(f'День недели «{_quote(key)}» — ожидается 0–6 (Вс=0).')
            out[int(key)] = parse_intervals(intervals)
        return {'days': out}


class WeekSerializer(serializers.Serializer):
    """{'monday': date, 'days': {'YYYY-MM-DD': [[HH:MM, HH:MM], …]}}."""

    monday = serializers.DateField()
    days = serializers.DictField()

    def validate(self, attrs):
        out = {}
        for key, intervals in attrs['days'].items():
            try:
                d = datetime.date.fromisoformat(key)
            except ValueError:
                raise serializers.ValidationError(f'Дата «{_quote(key)}» — ожидается YYYY-MM-DD.')
            out[d] = parse_intervals(intervals)
        return {'monday': attrs['monday'], 'days': out}
