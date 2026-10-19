from django.db import migrations
from apps.extra_lessons._migration_helpers import migrate_assignments_to_resolutions


def _forward(apps, schema_editor):
    migrate_assignments_to_resolutions(schema_editor.connection)


class Migration(migrations.Migration):
    dependencies = [('extra_lessons', '0002_absenceresolution_absenceresolutionevent_and_more')]
    operations = [
        migrations.RunPython(_forward, migrations.RunPython.noop),
    ]
