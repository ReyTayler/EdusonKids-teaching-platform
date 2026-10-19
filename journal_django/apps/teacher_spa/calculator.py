"""
calculator.py — вспомогательные функции teacher SPA, специфичные для этого
приложения. Расчёт payment/penalty теперь в apps.payroll.calculator (общий для
teacher_spa и apps.lessons — см. apps.lessons.services.record_lesson).
"""
from __future__ import annotations

from apps.core.utils.dates import msk_today


def format_msk_date() -> str:
    """
    Порт formatMskDate() из calculator.js — сегодняшняя дата в МСК как 'YYYY-MM-DD'.

    Переиспользует apps.core.utils.dates.msk_today().
    """
    return msk_today()
