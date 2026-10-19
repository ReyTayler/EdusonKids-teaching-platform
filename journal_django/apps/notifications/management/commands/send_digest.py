"""
Ручная проверка дайджестов: посмотреть текст или отправить его конкретному
преподавателю, не дожидаясь 8:00 и 21:00.

Основной сценарий — предпросмотр перед тем, как включать рассылку на всех:

    manage.py send_digest morning --teacher-id 12 --dry-run   # показать текст
    manage.py send_digest morning --teacher-id 12             # отправить одному
    manage.py send_digest fill --date 2026-08-03 --dry-run    # за другой день

Без --teacher-id команда рассылает ВСЕМ привязанным — то же, что сделает beat.
Дайджест идемпотентен в пределах дня: повторный запуск ничего не продублирует.
Для повторной отправки в тот же день есть --force.
"""
from __future__ import annotations

import datetime

from django.core.management.base import BaseCommand, CommandError

from apps.core.utils.dates import msk_now
from apps.notifications import digests
from apps.teachers.models import Teacher

MORNING = 'morning'
FILL = 'fill'


class Command(BaseCommand):
    help = 'Показать или отправить дайджест (утренний / по незаполненным отчётам).'

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            'kind', choices=[MORNING, FILL],
            help='morning — расписание на день; fill — незаполненные отчёты.',
        )
        parser.add_argument(
            '--teacher-id', type=int, default=None,
            help='Отправить только этому преподавателю. Без него — всем привязанным.',
        )
        parser.add_argument(
            '--date', default=None,
            help='Дата в формате ГГГГ-ММ-ДД. По умолчанию сегодня (МСК).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Показать тексты, ничего не отправляя.',
        )
        parser.add_argument(
            '--force', action='store_true',
            help='Отправить повторно, даже если сегодня уже отправляли.',
        )

    def handle(self, *args, **options) -> None:
        day = self._parse_day(options['date'])
        kind = options['kind']
        teacher_id = options['teacher_id']

        if teacher_id is not None and not Teacher.objects.filter(id=teacher_id).exists():
            raise CommandError(f'Преподаватель id={teacher_id} не найден.')

        build = digests.morning_payloads if kind == MORNING else digests.fill_payloads
        payloads = build(day, only_teacher_id=teacher_id)

        if not payloads:
            self.stdout.write(self.style.WARNING(self._explain_empty(kind, teacher_id)))
            return

        for payload in payloads:
            self.stdout.write(self.style.HTTP_INFO(
                f'\n— {payload["teacher_name"]} (id={payload["teacher_id"]}, '
                f'chat_id={payload["chat_id"]}) —'
            ))
            if payload['error']:
                # Письмо не собралось. В боевом прогоне это попадёт в журнал
                # уведомлений строкой «Не доставлено»; здесь — сразу на глаза.
                self.stdout.write(self.style.ERROR(
                    f'Письмо не собрано: {payload["error"]}'))
            else:
                self.stdout.write(payload['text'])

        if options['dry_run']:
            self.stdout.write(self.style.SUCCESS(
                f'\n[dry-run] к отправке: {len(payloads)}. Ничего не отправлено.'))
            return

        # Обход идемпотентности: ключ дайджеста содержит дату, поэтому повторная
        # отправка в тот же день без суффикса молча не создаст сообщений.
        suffix = f':force:{msk_now().strftime("%H%M%S")}' if options['force'] else ''
        send = digests.send_morning_digest if kind == MORNING else digests.send_fill_digest
        queued = send(day, only_teacher_id=teacher_id, dedup_suffix=suffix)

        if queued:
            self.stdout.write(self.style.SUCCESS(
                f'\nПоставлено в очередь: {queued}. Уйдёт в течение минуты '
                '(или сразу, если Celery-воркер запущен).'))
        else:
            self.stdout.write(self.style.WARNING(
                '\nНичего не поставлено: за этот день дайджест уже отправляли. '
                'Повторить — с флагом --force.'))

    @staticmethod
    def _parse_day(raw: str | None) -> datetime.date:
        if not raw:
            return msk_now().date()
        try:
            return datetime.date.fromisoformat(raw)
        except ValueError as exc:
            raise CommandError(f'Дата должна быть в формате ГГГГ-ММ-ДД: {exc}') from exc

    @staticmethod
    def _explain_empty(kind: str, teacher_id: int | None) -> str:
        """Пустой результат — норма, но причин несколько; подсказываем какая."""
        who = f'преподавателя id={teacher_id}' if teacher_id else 'привязанных преподавателей'
        if kind == MORNING:
            return (f'Отправлять нечего: у {who} на эту дату нет занятий '
                    f'либо нет активной привязки Telegram.')
        return (f'Отправлять нечего: у {who} нет незаполненных отчётов '
                f'либо нет активной привязки Telegram.')
