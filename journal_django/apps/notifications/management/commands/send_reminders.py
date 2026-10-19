"""
Ручной запуск email-напоминаний — то же, что делает Celery-beat по расписанию.

Локально без Redis beat не работает (см. config/settings/base.py, раздел Celery),
поэтому напоминания запускаются этой командой:

    manage.py send_reminders schedule              # «Расписание на сегодня»
    manage.py send_reminders unrecorded            # «Урок не записан» + повторы
    manage.py send_reminders parent-day            # родителям «урок завтра»
    manage.py send_reminders parent-hour           # родителям «урок через час»
    manage.py send_reminders unrecorded --dispatch # и сразу разгрести очередь

Повторный запуск безопасен: письмо в своё окно ставится один раз (dedup_key).
Выключатели раздела «Уведомления» действуют и здесь.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.notifications import dispatcher, reminders

JOBS = {
    'schedule': reminders.send_teacher_schedule,
    'unrecorded': reminders.send_unrecorded_reminders,
    'parent-day': reminders.send_parent_day_before,
    'parent-hour': reminders.send_parent_hour_before,
}


class Command(BaseCommand):
    help = 'Поставить email-напоминания в очередь (то же, что задачи по расписанию).'

    def add_arguments(self, parser) -> None:
        parser.add_argument('job', choices=list(JOBS), help='Какое напоминание.')
        parser.add_argument(
            '--dispatch', action='store_true',
            help='После постановки сразу отправить очередь (без Celery-воркера).',
        )

    def handle(self, *args, **options) -> None:
        queued = JOBS[options['job']]()
        self.stdout.write(self.style.SUCCESS(f'Поставлено в очередь: {queued}.'))
        if options['dispatch']:
            sent = dispatcher.dispatch()
            self.stdout.write(self.style.SUCCESS(f'Отправлено: {sent}.'))
