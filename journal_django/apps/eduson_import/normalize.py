"""Нормализация признаков ученика Eduson — только для СРАВНЕНИЯ записей при склейке.

В модели ученика значения пишутся как в Eduson; нормализованная форма нигде не
хранится, иначе импорт тихо переписал бы то, что вводили люди.
"""
from __future__ import annotations

import re

_SPACES = re.compile(r'\s+')          # \s в str-шаблоне ловит и неразрывный пробел
_NON_DIGITS = re.compile(r'\D')
_TG_LINK = re.compile(r'^(?:https?://)?(?:www\.)?t(?:elegram)?\.me/', re.IGNORECASE)


def norm_name(value: str | None) -> str:
    """Регистр, лишние пробелы, ё≡е. Остальные буквы не трогаем: «Майя» ≠ «Маия»."""
    return _SPACES.sub(' ', (value or '').lower().replace('ё', 'е')).strip()


def norm_phone(value: str | None) -> str:
    """Только цифры; российский номер приводится к 7XXXXXXXXXX (8… ≡ 7…, 10 цифр → 7+).
    Другие длины оставляем цифрами как есть: подменять код чужой страны нельзя."""
    digits = _NON_DIGITS.sub('', value or '')
    if len(digits) == 11 and digits[0] == '8':
        return '7' + digits[1:]
    if len(digits) == 10:
        return '7' + digits
    return digits


def norm_email(value: str | None) -> str:
    return (value or '').strip().lower()


def norm_tg(value: str | None) -> str:
    """Ник Telegram без @ и без ссылки t.me/, в нижнем регистре (ники регистронезависимы)."""
    nick = _TG_LINK.sub('', (value or '').strip())
    return nick.lstrip('@').lower()


_SEQUENCES = ('01234567890123456789', '98765432109876543210')


def phone_is_usable(normalized: str) -> bool:
    """Годится ли нормализованный телефон как КЛЮЧ склейки учеников.

    Заглушки, которые вбивают в обязательное поле («79999999999», «70000000000»,
    «71234567890»), у разных семей совпадают — по ним тёзки склеились бы в одного
    ребёнка. Поэтому: не меньше 10 цифр, и ни номер целиком, ни его часть после
    кода страны 7 не состоят из одной цифры и не идут подряд по возрастанию или
    убыванию.
    """
    if len(normalized) < 10:
        return False
    parts = [normalized]
    if len(normalized) == 11 and normalized[0] == '7':
        parts.append(normalized[1:])
    for part in parts:
        if len(set(part)) == 1 or any(part in seq for seq in _SEQUENCES):
            return False
    return True
