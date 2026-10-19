"""Стадия «Заморожен» снова становится РУЧНОЙ (is_auto=False) — откат 0010.

0010 сделала её авто-стадией только чтобы transitions.is_allowed блокировал
ручной вход/выход: войти можно было исключительно каскадом смены статуса
ученика (engine.freeze_deal). Статусы ученика удалены (спека 2026-07-25),
заморозка — обычная decision-стадия, которую менеджер ставит сам.
Идемпотентно; обратимо.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    RenewalStage = apps.get_model('renewals', 'RenewalStage')
    RenewalStage.objects.filter(
        pipeline__is_default=True, key='frozen').update(is_auto=False)


def backwards(apps, schema_editor):
    RenewalStage = apps.get_model('renewals', 'RenewalStage')
    RenewalStage.objects.filter(
        pipeline__is_default=True, key='frozen').update(is_auto=True)


class Migration(migrations.Migration):

    dependencies = [
        ('renewals', '0011_drop_next_touch_at'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
