"""
Снять историю версий документов — решение пользователя 2026-08-12.

Таблица удаляется вместе с содержимым: снимки хранились полными копиями
документа, и восстанавливать их больше нечем. Данные не переносятся никуда —
это осознанная потеря, а не упущение.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('knowledge', '0003_remove_knowledgedocument_views_count_and_more'),
    ]

    operations = [
        migrations.DeleteModel(
            name='KnowledgeDocumentVersion',
        ),
    ]
