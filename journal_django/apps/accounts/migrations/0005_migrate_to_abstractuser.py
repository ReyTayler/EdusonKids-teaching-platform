from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_invite_created_by_nullable'),  # последняя миграция accounts
        ('auth', '0012_alter_user_first_name_max_length'), # последняя миграция auth
    ]

    operations = [
        # 1. Переименовываем поля под AbstractUser
        migrations.RenameField(
            model_name='account',
            old_name='password_hash',
            new_name='password',
        ),
        migrations.RenameField(
            model_name='account',
            old_name='active',
            new_name='is_active',
        ),
        migrations.RenameField(
            model_name='account',
            old_name='last_login_at',
            new_name='last_login',
        ),
        migrations.RenameField(
            model_name='account',
            old_name='created_at',
            new_name='date_joined',
        ),
        
        # 2. Добавляем поля AbstractUser
        migrations.AddField(
            model_name='account',
            name='is_staff',
            field=models.BooleanField(default=False, verbose_name='staff status'),
        ),
        migrations.AddField(
            model_name='account',
            name='is_superuser',
            field=models.BooleanField(default=False, verbose_name='superuser status'),
        ),
        migrations.AddField(
            model_name='account',
            name='first_name',
            field=models.CharField(max_length=150, blank=True, verbose_name='first name'),
        ),
        migrations.AddField(
            model_name='account',
            name='last_name',
            field=models.CharField(max_length=150, blank=True, verbose_name='last name'),
        ),
        
        # 3. Меняем role на CharField с choices
        migrations.AlterField(
            model_name='account',
            name='role',
            field=models.CharField(
                choices=[('teacher', 'Учитель'), ('manager', 'Менеджер'), ('admin', 'Администратор')],
                max_length=20,
                verbose_name='role',
            ),
        ),
        
        # 4. Обновляем CHECK-ограничения (старые имена → новые)
        migrations.RemoveConstraint(
            model_name='account',
            name='accounts_check',
        ),
        migrations.RemoveConstraint(
            model_name='account',
            name='accounts_check1',
        ),
        migrations.AddConstraint(
            model_name='account',
            constraint=models.CheckConstraint(
                name='accounts_teacher_role_check',
                condition=(
                    models.Q(('role', 'teacher'), ('teacher__isnull', False))
                    | (~models.Q(('role', 'teacher')) & models.Q(('teacher__isnull', True)))
                ),
            ),
        ),
        migrations.AddConstraint(
            model_name='account',
            constraint=models.CheckConstraint(
                name='accounts_totp_secret_check',
                condition=(
                    ~models.Q(('twofa_method', 'totp'))
                    | models.Q(('twofa_secret__isnull', False))
                ),
            ),
        ),
        
        # 5. Меняем password с TextField на CharField(128)
        migrations.AlterField(
            model_name='account',
            name='password',
            field=models.CharField(max_length=128, verbose_name='password'),
        ),
    ]