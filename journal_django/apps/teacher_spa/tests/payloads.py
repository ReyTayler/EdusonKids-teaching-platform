"""Общие куски тела POST /api/submitLesson для тестов teacher_spa.

Отзыв по уроку обязателен (этап 3 Eduson, раздел A), поэтому каждый тест,
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


def with_feedback(payload: dict) -> dict:
    """Тело запроса с валидным отзывом (свой отзыв в payload не перетирается)."""
    return {'feedback': dict(VALID_FEEDBACK), **payload}
