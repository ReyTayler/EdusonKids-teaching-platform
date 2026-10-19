# Добавляет статус 'burned' в CHECK-констрейнт absence_resolutions (Фаза 1c-2).
# Меняется ТОЛЬКО CHECK (swap old→new), как в 0006. Данные/uniq не трогаем.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('extra_lessons', '0008_revert_historical_makeups'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='absenceresolution',
            name='absence_resolutions_status_check',
        ),
        migrations.AddConstraint(
            model_name='absenceresolution',
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ('status__in', ['pending', 'makeup_scheduled', 'makeup_done', 'burned'])),
                name='absence_resolutions_status_check',
            ),
        ),
    ]
