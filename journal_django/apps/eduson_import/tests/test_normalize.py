"""Нормализация признаков ученика: что считается «тем же» именем, телефоном, почтой, ником."""
from __future__ import annotations

import pytest

from apps.eduson_import.normalize import norm_email, norm_name, norm_phone, norm_tg, phone_is_usable


@pytest.mark.parametrize('raw, expected', [
    ('  Ёлка   Тестова ', 'елка тестова'),   # регистр, ё→е, лишние пробелы
    ('ЁЖИК', 'ежик'),
    ('Миша Тест', 'миша тест'),         # неразрывный пробел из копипасты
    ('\tМиша\n', 'миша'),
    ('', ''),
    (None, ''),
])
def test_norm_name(raw, expected):
    assert norm_name(raw) == expected


def test_norm_name_keeps_other_letters_distinct():
    # й и и — разные буквы; склеивать «Майя» и «Маия» нормализация не должна
    assert norm_name('Майя') != norm_name('Маия')
    assert norm_name('Миша') != norm_name('Маша')


@pytest.mark.parametrize('raw, expected', [
    ('8 (999) 000-00-00', '79990000000'),
    ('+7 999 000 00 00', '79990000000'),
    ('79990000000', '79990000000'),
    ('9990000000', '79990000000'),          # без кода страны
    ('+375 29 000-00-00', '375290000000'),  # чужой код — только цифры, без подмены
    ('81234567890123', '81234567890123'),   # не 11 цифр — 8 не трогаем
    ('', ''),
    (None, ''),
    ('нет', ''),
])
def test_norm_phone(raw, expected):
    assert norm_phone(raw) == expected


def test_norm_phone_8_and_7_are_same_parent():
    assert norm_phone('8-999-111-22-33') == norm_phone('+7 (999) 111 22 33')


@pytest.mark.parametrize('raw, expected', [
    (' Test.Parent@Example.RU ', 'test.parent@example.ru'),
    ('', ''),
    (None, ''),
])
def test_norm_email(raw, expected):
    assert norm_email(raw) == expected


@pytest.mark.parametrize('raw, expected', [
    ('@Test_Kid', 'test_kid'),
    (' test_kid ', 'test_kid'),
    ('https://t.me/Test_Kid', 'test_kid'),
    ('t.me/test_kid', 'test_kid'),
    ('', ''),
    (None, ''),
])
def test_norm_tg(raw, expected):
    assert norm_tg(raw) == expected


@pytest.mark.parametrize('raw, usable', [
    ('8 (999) 123-45-67', True),
    ('9161234567', True),
    ('375290000001', True),     # чужой код страны — годен
    ('79999999999', False),     # одна цифра после кода
    ('70000000000', False),     # 7 + нули
    ('88888888888', False),     # одна цифра целиком (8→7, остаток — восьмёрки)
    ('71234567890', False),     # последовательность
    ('9876543210', False),      # обратная последовательность
    ('0123456789', False),
    ('799912345', False),       # меньше 10 цифр
    ('', False),
])
def test_phone_is_usable(raw, usable):
    assert phone_is_usable(norm_phone(raw)) is usable
