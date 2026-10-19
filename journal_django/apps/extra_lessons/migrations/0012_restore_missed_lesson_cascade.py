"""
Восстановить DB-level ON DELETE CASCADE на absence_resolutions.missed_lesson.

Миграция 0011 сделала missed_lesson nullable (AlterField), и Django пересоздал
FK-констрейнт СВОИМ дефолтом — без ON DELETE, затерев каскад, который ставила
0007. Без него прямой `DELETE FROM lessons` (в т.ч. тестовые teardown'ы) снова
падает по FK, т.к. record_lesson авто-создаёт pending-резолюции. Возвращаем каскад
той же интроспекцией имени констрейнта, что и в 0007 (Django не трекает ON DELETE
в state, поэтому makemigrations это не «замечает» и --check остаётся чистым).
"""
from django.db import migrations

_ADD_CASCADE = """
DO $$
DECLARE
    con_name text;
BEGIN
    SELECT con.conname INTO con_name
    FROM pg_constraint con
    JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = ANY(con.conkey)
    WHERE con.conrelid = 'absence_resolutions'::regclass
      AND con.contype = 'f'
      AND a.attname = 'missed_lesson_id';
    IF con_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE absence_resolutions DROP CONSTRAINT %I', con_name);
    END IF;
    ALTER TABLE absence_resolutions
        ADD CONSTRAINT absence_resolutions_missed_lesson_fk_cascade
        FOREIGN KEY (missed_lesson_id) REFERENCES lessons(id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;
END $$;
"""

_REVERT = """
DO $$
DECLARE
    con_name text;
BEGIN
    SELECT con.conname INTO con_name
    FROM pg_constraint con
    JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = ANY(con.conkey)
    WHERE con.conrelid = 'absence_resolutions'::regclass
      AND con.contype = 'f'
      AND a.attname = 'missed_lesson_id';
    IF con_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE absence_resolutions DROP CONSTRAINT %I', con_name);
    END IF;
    ALTER TABLE absence_resolutions
        ADD CONSTRAINT absence_resolutions_missed_lesson_id_fk_lessons_id
        FOREIGN KEY (missed_lesson_id) REFERENCES lessons(id)
        DEFERRABLE INITIALLY DEFERRED;
END $$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('extra_lessons', '0011_remove_absenceresolution_insert_insert_and_more'),
    ]

    operations = [
        migrations.RunSQL(_ADD_CASCADE, reverse_sql=_REVERT),
    ]
