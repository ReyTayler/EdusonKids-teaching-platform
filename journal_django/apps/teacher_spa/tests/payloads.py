"""Общие куски тела POST /api/submitLesson для тестов teacher_spa.

Отзыв по уроку и ссылка на запись обязательны (этап 3 Eduson, раздел A), поэтому каждый тест,
которому нужен успешно записанный урок, обязан его прислать. Тесты, которые
проверяют сам отзыв, лежат в test_submit_feedback.py.
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
