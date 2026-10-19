"""
Фаза 1c-1: перевод исторических проведённых доп.уроков (makeup_done) на новую
модель потребления — исходный урок возвращается в present=false, длительность
extra-факта приравнивается к длительности исходного урока (вес потребления).
lessons_done не трогается (старый apply_makeup уже дал верный инкремент). См.
apps.extra_lessons._migration_helpers.revert_historical_makeups.

Reverse — noop: восстановление исторических present=true/длительностей вручную
(деньги-критично). На dev/journal_test записей makeup_done нет → фактический
no-op; миграция нужна для корректного прогона на боевой БД.
"""
from django.db import migrations

from apps.extra_lessons._migration_helpers import revert_historical_makeups


def _forward(apps, schema_editor):
    revert_historical_makeups(schema_editor.connection)


class Migration(migrations.Migration):

    dependencies = [
        ('extra_lessons', '0007_missed_lesson_db_cascade'),
    ]

    operations = [
        migrations.RunPython(_forward, migrations.RunPython.noop),
    ]
