"""Общие куски тела POST /api/submitLesson для тестов teacher_spa.

Отзыв по уроку обязателен (этап 3 Eduson, раздел A), а ссылка на запись обязательна по
правилу школы по умолчанию (apps.lessons.record_url_rule, спека 2026-11-16), поэтому каждый
тест, которому нужен успешно записанный урок, присылает оба. Тесты отзыва — в
test_submit_feedback.py, правила ссылки — в test_record_url_rule_submit.py.
"""
from __future__ import annotations

VALID_FEEDBACK = {
    'understanding': 7,
    'interest': 8,
    'techProblems': False,
    'comment': 'Тестовый комментарий по уроку',
}


# Ссылка на запись урока тоже обязательна — без неё урок не записывается.
VALID_RECORD_URL = 'https://video.example.test/rec/1'


def with_feedback(payload: dict) -> dict:
    """Тело запроса с валидными отзывом и ссылкой на запись (свои значения в
    payload не перетираются)."""
    return {'feedback': dict(VALID_FEEDBACK), 'recordUrl': VALID_RECORD_URL, **payload}
