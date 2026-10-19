"""
Помечает служебную запись «Архив (импорт истории)» флагом is_service.

Это синтетический «преподаватель», на котором висят архивные группы вида
«Python — архив» — туда сложили историю, импортированную до перехода на журнал.
Живым человеком он не является, но на нём ~80 % всех сделок продления школы,
поэтому в сравнениях преподавателей между собой он забивает всех остальных.

Ищем по точному имени: `teachers.name` уникален, а другого признака у записи
нет. Если записи нет (чистая БД, тестовое окружение) — миграция ничего не
делает и не падает.
"""
from __future__ import annotations

from django.db import migrations

SERVICE_TEACHER_NAME = 'Архив (импорт истории)'


def mark(apps, schema_editor):
    Teacher = apps.get_model('teachers', 'Teacher')
    Teacher.objects.filter(name=SERVICE_TEACHER_NAME).update(is_service=True)


def unmark(apps, schema_editor):
    Teacher = apps.get_model('teachers', 'Teacher')
    Teacher.objects.filter(name=SERVICE_TEACHER_NAME).update(is_service=False)


class Migration(migrations.Migration):

    dependencies = [
        ('teachers', '0003_teacher_is_service'),
    ]

    operations = [
        migrations.RunPython(mark, unmark),
    ]
