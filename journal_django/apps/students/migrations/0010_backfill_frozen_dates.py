"""Конвертация frozen_until_month → frozen_from/frozen_until для существующих
замороженных учеников.

frozen_until — 1-е число ближайшего наступления месяца (_frozen_backfill_util).
frozen_from — best-effort = сегодня по МСК (точная дата начала паузы в старой
модели не хранилась). ПРОД: значения требуют ручной выверки после миграции
(см. docs/superpowers/specs/2026-07-17-student-status-lifecycle-design.md §2.1).
Обратимо: unwind обнуляет обе даты (frozen_until_month ещё существует)."""
from django.db import migrations

from apps.core.utils.dates import msk_now
from apps.students.migrations._frozen_backfill_util import (
    clamp_frozen_from,
    infer_frozen_until,
)


def forwards(apps, schema_editor):
    Student = apps.get_model('students', 'Student')
    today = msk_now().date()
    for s in Student.objects.filter(enrollment_status='frozen',
                                    frozen_until_month__isnull=False):
        until = infer_frozen_until(s.frozen_until_month, today)
        s.frozen_until = until
        # Клампим frozen_from <= frozen_until: если месяц заморозки == текущему,
        # а сегодня уже не 1-е число, until окажется РАНЬШЕ today (1-е числа этого
        # месяца). Инвариант frozen_from <= frozen_until (CHECK на модели) обязан
        # держаться на всех путях.
        s.frozen_from = clamp_frozen_from(today, until)
        s.save(update_fields=['frozen_until', 'frozen_from'])


def backwards(apps, schema_editor):
    Student = apps.get_model('students', 'Student')
    Student.objects.filter(enrollment_status='frozen',
                           frozen_until_month__isnull=False).update(
        frozen_from=None, frozen_until=None)


class Migration(migrations.Migration):

    dependencies = [
        ('students', '0009_add_frozen_dates'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
