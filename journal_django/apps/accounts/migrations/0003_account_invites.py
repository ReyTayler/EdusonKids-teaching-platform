"""
0003_account_invites — таблица invite-токенов + password_hash nullable.

Операции:
  1. CreateModel AccountInvite
  2. AddIndex token_hash
  3. AddConstraint «один активный инвайт» (partial UNIQUE)
  4. RunSQL: created_at DEFAULT now()
  5. AlterField Account.password_hash → nullable
  6. RunSQL: ALTER COLUMN password_hash DROP NOT NULL (синхрон БД)
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_account_token_version'),
    ]

    operations = [
        migrations.CreateModel(
            name='AccountInvite',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('token_hash', models.TextField()),
                ('created_by', models.IntegerField()),
                ('created_at', models.DateTimeField()),
                ('expires_at', models.DateTimeField()),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                (
                    'account',
                    models.ForeignKey(
                        db_column='account_id',
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='invites',
                        to='accounts.account',
                    ),
                ),
            ],
            options={
                'db_table': 'account_invites',
                'managed': True,
            },
        ),
        migrations.AddIndex(
            model_name='accountinvite',
            index=models.Index(fields=['token_hash'], name='ai_token_hash_idx'),
        ),
        migrations.AddConstraint(
            model_name='accountinvite',
            constraint=models.UniqueConstraint(
                condition=models.Q(used_at__isnull=True, revoked_at__isnull=True),
                fields=('account',),
                name='ai_one_active_per_account',
            ),
        ),
        migrations.RunSQL(
            sql='ALTER TABLE account_invites ALTER COLUMN created_at SET DEFAULT now();',
            reverse_sql='ALTER TABLE account_invites ALTER COLUMN created_at DROP DEFAULT;',
        ),
        # password_hash → nullable (Django-сторона)
        migrations.AlterField(
            model_name='account',
            name='password_hash',
            field=models.TextField(blank=True, null=True),
        ),
        # Синхронизировать БД: снять NOT NULL с password_hash
        migrations.RunSQL(
            sql='ALTER TABLE accounts ALTER COLUMN password_hash DROP NOT NULL;',
            reverse_sql='ALTER TABLE accounts ALTER COLUMN password_hash SET NOT NULL;',
        ),
    ]
