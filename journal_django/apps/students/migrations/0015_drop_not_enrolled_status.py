"""Статус `not_enrolled` («Не учится») удалён из домена.

Вместе с ним удалён и механизм soft-delete ученика (DELETE /students/:id), который
был единственным его «производителем» в UI. Оставшиеся терминальные состояния —
`frozen` (пауза) и `declined` (отказ); `not_enrolled` дублировал их без своей
семантики.

Порядок операций критичен: существующие строки переводятся в `enrolled` ДО
AddConstraint — иначе новый CHECK не применится на боевой БД. frozen_from/until у
них уже NULL (это гарантировал прежний students_frozen_dates_presence_check для
любого не-frozen статуса), поэтому перевод в `enrolled` не нарушает его.

Историю pghistory (students_event) не трогаем: там CHECK нет, а прошлые значения
статуса — факт, который журнал изменений обязан сохранить как есть.
"""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('students', '0014_remove_student_insert_insert_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Необратимо в обратную сторону: какие именно `enrolled` были раньше
        # `not_enrolled`, после апдейта уже не отличить → reverse = noop.
        migrations.RunSQL(
            sql="UPDATE students SET enrollment_status = 'enrolled' "
                "WHERE enrollment_status = 'not_enrolled'",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RemoveConstraint(
            model_name='student',
            name='students_enrollment_status_check',
        ),
        migrations.AddConstraint(
            model_name='student',
            constraint=models.CheckConstraint(condition=models.Q(('enrollment_status__in', ['enrolled', 'frozen', 'declined'])), name='students_enrollment_status_check'),
        ),
    ]
