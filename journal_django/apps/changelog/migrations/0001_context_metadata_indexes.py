"""
Expression-индексы на JSONB-колонке pghistory_context.metadata.

Журнал изменений фильтрует контексты по ключам внутри metadata. Без индекса
каждый такой фильтр — seq scan по всей таблице контекстов (растёт линейно с
историей). Django генерирует предикаты вида `(metadata -> 'ключ') = '"..."'::jsonb`
(проверено), поэтому обычный GIN тут не подходит — нужен btree-индекс ровно по
выражению `(metadata -> 'ключ')`.

Индексируем два горячих ключа:
  * revert_of — проверка «операция уже откачена?» на каждом открытии карточки,
    странице ленты и попытке отката (repository._reverted_context_ids,
    revert.revert_context);
  * operation — фильтр ленты по операции «Откат» (metadata__operation).

Оба partial (WHERE ключ присутствует): ключи есть лишь у части контекстов
(revert_of — только у откатов), поэтому индексы остаются компактными.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('pghistory', '0007_auto_20250421_0444'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "CREATE INDEX IF NOT EXISTS changelog_ctx_revert_of "
                "ON pghistory_context ((metadata -> 'revert_of')) "
                "WHERE (metadata -> 'revert_of') IS NOT NULL;",
                "CREATE INDEX IF NOT EXISTS changelog_ctx_operation "
                "ON pghistory_context ((metadata -> 'operation')) "
                "WHERE (metadata -> 'operation') IS NOT NULL;",
            ],
            reverse_sql=[
                "DROP INDEX IF EXISTS changelog_ctx_revert_of;",
                "DROP INDEX IF EXISTS changelog_ctx_operation;",
            ],
        ),
    ]
