"""created_by в account_invites → nullable (NULL для bootstrap первого admin)."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_account_invites'),
    ]

    operations = [
        migrations.AlterField(
            model_name='accountinvite',
            name='created_by',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.RunSQL(
            sql="ALTER TABLE account_invites ALTER COLUMN created_by DROP NOT NULL;",
            reverse_sql="ALTER TABLE account_invites ALTER COLUMN created_by SET NOT NULL;",
        ),
    ]
