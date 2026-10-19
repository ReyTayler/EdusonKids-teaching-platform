"""
Сделать FK absence_resolutions.missed_lesson реальным DB-level ON DELETE CASCADE.

Django на уровне БД НЕ эмитит ON DELETE (эмулирует CASCADE только в ORM), но
спека требует: удаление исходного урока сносит его pending/резолюции
(docs/superpowers/specs/2026-07-18-...: «on_delete=CASCADE (было PROTECT)»).
Без DB-cascade любой прямой `DELETE FROM lessons` (в т.ч. в тестовых teardown'ах)
падает по FK, т.к. record_lesson теперь авто-создаёт pending-резолюции.

Имя FK-констрейнта авто-генерируется Django (с хэш-суффиксом), поэтому находим
его интроспекцией, а не хардкодим. makemigrations это изменение не «замечает»
(Django не трекает ON DELETE в state), поэтому --check остаётся чистым.
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
        ADD CONSTRAINT absence_resolutions_missed_lesson_id_18a0fa1d_fk_lessons_id
        FOREIGN KEY (missed_lesson_id) REFERENCES lessons(id)
        DEFERRABLE INITIALLY DEFERRED;
END $$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('extra_lessons', '0006_rename_statuses'),
    ]

    operations = [
        migrations.RunSQL(_ADD_CASCADE, reverse_sql=_REVERT),
    ]
