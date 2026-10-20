"""
DB-level ON DELETE SET NULL на payroll_cancellations.planned_lesson_id.

on_delete=SET_NULL в Django работает только через ORM-коллектор, а FK в БД
создаётся без ON DELETE. Маркеры отмен живут в planned_lessons, которые чистят и
сырым SQL (teardown'ы тестов, разовые скрипты) — без правила в БД такой DELETE
упёрся бы в FK. SET NULL, а не CASCADE: начисление за отмену переживает
удаление маркера (спека §2 — выплаченный месяц не меняется задним числом).
Приём тот же, что extra_lessons/0007 и 0012.

Отдельной миграцией, а не RunSQL в 0005: FK из CreateModel/AddField Django
добавляет отложенным SQL в конце миграции — RunSQL внутри 0005 выполнился бы
раньше, и в таблице оказалось бы два FK. Снимаем ВСЕ FK на колонке циклом, а не
первый найденный: иначе при повторном прогоне/частичном откате мог бы остаться
лишний FK без ON DELETE. Django не трекает ON DELETE в state, поэтому
makemigrations этого не «замечает»; AlterField поля planned_lesson в будущем
затрёт правило — после него нужна повторная RunSQL.
"""
from django.db import migrations

_SET_FK = """
DO $$
DECLARE
    con_name text;
BEGIN
    FOR con_name IN
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = ANY(con.conkey)
        WHERE con.conrelid = 'payroll_cancellations'::regclass
          AND con.contype = 'f'
          AND a.attname = 'planned_lesson_id'
    LOOP
        EXECUTE format('ALTER TABLE payroll_cancellations DROP CONSTRAINT %%I', con_name);
    END LOOP;
    ALTER TABLE payroll_cancellations
        ADD CONSTRAINT %(name)s
        FOREIGN KEY (planned_lesson_id) REFERENCES planned_lessons(id)
        %(on_delete)s DEFERRABLE INITIALLY DEFERRED;
END $$;
"""

_ADD_SET_NULL = _SET_FK % {
    'name': 'payroll_cancellations_planned_lesson_fk_set_null',
    'on_delete': 'ON DELETE SET NULL',
}
# Откат — к FK в том виде, в каком его создаёт Django (без ON DELETE).
_REVERT = _SET_FK % {
    'name': 'payroll_cancellations_planned_lesson_id_fk_planned_lessons_id',
    'on_delete': '',
}


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0005_motivation_models'),
    ]

    operations = [
        migrations.RunSQL(_ADD_SET_NULL, reverse_sql=_REVERT),
    ]
