"""Стадия «Заморожен» (key='frozen') становится авто-стадией: в неё/из неё нельзя
войти вручную (transitions.is_allowed блокирует любые is_auto). Двигает её только
движок по смене статуса ученика (engine.freeze_deal / resume_from_freeze).
Идемпотентно; обратимо (is_auto=False)."""
from django.db import migrations


def forwards(apps, schema_editor):
    RenewalStage = apps.get_model('renewals', 'RenewalStage')
    RenewalStage.objects.filter(
        pipeline__is_default=True, key='frozen').update(is_auto=True)


def backwards(apps, schema_editor):
    RenewalStage = apps.get_model('renewals', 'RenewalStage')
    RenewalStage.objects.filter(
        pipeline__is_default=True, key='frozen').update(is_auto=False)


class Migration(migrations.Migration):

    dependencies = [
        ('renewals', '0009_rename_lesson_progress_stages'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
