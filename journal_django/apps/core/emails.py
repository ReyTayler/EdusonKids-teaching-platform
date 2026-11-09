"""
Оформленная (HTML) версия писем платформы — один шаблон на все письма.

Значения подставляются через шаблонизатор Django с автоэкранированием: имя
ученика или название направления из базы не могут стать разметкой письма.
Простой текст письма каждый отправитель собирает сам и шлёт рядом с этой версией —
его показывает почта, которая не умеет HTML.
"""
from __future__ import annotations

from collections.abc import Sequence

from django.template.loader import render_to_string


def render_email_html(
    *,
    preheader: str,
    kicker: str,
    title: str,
    lead: str,
    note: str,
    code: str | None = None,
    facts: Sequence[tuple[str, str]] = (),
) -> str:
    """HTML письма. code — крупный код на плашке, facts — строки «подпись: значение»."""
    return render_to_string('emails/message.html', {
        'preheader': preheader, 'kicker': kicker, 'title': title, 'lead': lead,
        'note': note, 'code': code, 'facts': facts,
    })
