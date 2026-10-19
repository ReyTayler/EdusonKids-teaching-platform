# Фаза 2b: историч. burned_at-правки → штатные burned-Lesson + AbsenceResolution.
# ЧИТАЕТ LessonAttendance.burned_at / Payroll.burn_surcharge_* — ОБЯЗАНА пройти ДО
# их удаления (2c-миграции lessons/payroll). reverse=noop (деньги-критично,
# восстановление вручную при откате).

from django.db import migrations

from apps.extra_lessons._migration_helpers import convert_historical_burned_at


def _forward(apps, schema_editor):
    convert_historical_burned_at(schema_editor.connection)


class Migration(migrations.Migration):

    dependencies = [
        ('extra_lessons', '0009_add_burned_status'),
    ]

    operations = [
        migrations.RunPython(_forward, migrations.RunPython.noop),
    ]
