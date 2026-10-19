from django.db import migrations


def promote_admins(apps, schema_editor):
    Account = apps.get_model('accounts', 'Account')
    Account.objects.filter(role='admin').update(role='superadmin')


def demote_noop(apps, schema_editor):
    # Обратный промоут небезопасен (нельзя отличить исходных admin от super) — no-op.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_remove_account_accounts_role_check_account_full_name_and_more'),
    ]

    operations = [
        migrations.RunPython(promote_admins, demote_noop),
    ]
