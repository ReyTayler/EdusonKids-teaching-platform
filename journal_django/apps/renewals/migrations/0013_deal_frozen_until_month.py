"""Месяц окончания заморозки переезжает на сделку (спека 2026-07-25).

Раньше период заморозки жил на ученике (students.frozen_from/frozen_until) и
двигал расписание. Теперь заморозка — просто стадия воронки, и от периода
остаётся только «до какого месяца» — свойство сделки.

Бэкфил переносит месяц уже замороженных учеников, пока колонка
students.frozen_until ещё существует (её удаляет students/0016).
"""
from django.db import migrations, models

from apps.renewals.migrations._frozen_month_backfill import BACKFILL_SQL


def backfill(apps, schema_editor):
    schema_editor.execute(BACKFILL_SQL)


def noop(apps, schema_editor):
    """Откат не нужен: RemoveField ниже уносит колонку вместе с данными."""


class Migration(migrations.Migration):

    dependencies = [
        ('renewals', '0012_frozen_manual_stage'),
        ('students', '0015_drop_not_enrolled_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='renewaldeal',
            name='frozen_until_month',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name='renewaldeal',
            index=models.Index(fields=['student', '-cycle_no'],
                               name='renewal_deal_student_cycle_idx'),
        ),
        migrations.RunPython(backfill, noop),
    ]
