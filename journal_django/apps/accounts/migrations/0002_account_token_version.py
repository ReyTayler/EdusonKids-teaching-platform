from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='account',
            name='token_version',
            field=models.IntegerField(default=0),
        ),
        migrations.RunSQL(
            sql="ALTER TABLE accounts ALTER COLUMN token_version SET DEFAULT 0;",
            reverse_sql="ALTER TABLE accounts ALTER COLUMN token_version DROP DEFAULT;",
        ),
    ]
