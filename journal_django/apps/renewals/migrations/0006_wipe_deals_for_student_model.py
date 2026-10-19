"""
Ревизия №2: сделка продления становится сущностью УЧЕНИКА (ученик × cycle_no),
direction уходит из идентичности. Существующие сделки (ученик × направление)
несовместимы с новым UNIQUE(student, cycle_no) — вычищаем их ПЕРЕД схемной
миграцией. Ручных CRM-данных в таблицах нет (проверено 2026-07-12: 0 заполненных
assignee/next_touch/expected/reason, 2 тестовые ручные записи активности).

История восстанавливается из реальных дат посещений командой
`manage.py backfill_renewal_history` (запустить после migrate).
"""
from django.db import migrations


def wipe(apps, schema_editor):
    RenewalActivity = apps.get_model('renewals', 'RenewalActivity')
    RenewalDeal = apps.get_model('renewals', 'RenewalDeal')
    RenewalActivity.objects.all().delete()
    RenewalDeal.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [('renewals', '0005_seed_awaiting_renewal')]
    operations = [migrations.RunPython(wipe, migrations.RunPython.noop)]
