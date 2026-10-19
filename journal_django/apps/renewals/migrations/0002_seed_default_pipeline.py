"""Сид дефолтной воронки продлений и её стадий.

Идемпотентная data-миграция (get_or_create) — безопасна к повторному прогону.
Обратима: unseed удаляет стадии и дефолтный pipeline.
"""
from django.db import migrations

# (key, label, color, kind, is_auto)
STAGES = [
    ('lesson_progress', 'Урок 1–4',     '#6366F1', 'progress', True),
    ('awaiting_payment', 'Ждём оплату',  '#F59E0B', 'decision', False),
    ('thinking',        'Думает',         '#3B82F6', 'decision', False),
    ('frozen',          'Заморожен',      '#64748B', 'decision', False),
    ('ignoring',        'Игнорит',        '#EF4444', 'decision', False),
    ('renewed',         'Продлён',        '#22C55E', 'won',      False),
    ('churned',         'Ушёл',           '#9CA3AF', 'lost',     False),
]


def seed(apps, schema_editor):
    RenewalPipeline = apps.get_model('renewals', 'RenewalPipeline')
    RenewalStage = apps.get_model('renewals', 'RenewalStage')

    pipeline, _ = RenewalPipeline.objects.get_or_create(
        is_default=True,
        defaults={'name': 'Продления'},
    )
    for i, (key, label, color, kind, is_auto) in enumerate(STAGES):
        RenewalStage.objects.get_or_create(
            pipeline=pipeline,
            key=key,
            defaults={
                'label': label,
                'color': color,
                'kind': kind,
                'is_auto': is_auto,
                'sort_order': i,
            },
        )


def unseed(apps, schema_editor):
    RenewalPipeline = apps.get_model('renewals', 'RenewalPipeline')
    RenewalStage = apps.get_model('renewals', 'RenewalStage')

    pipeline = RenewalPipeline.objects.filter(is_default=True).first()
    if pipeline is None:
        return
    RenewalStage.objects.filter(pipeline=pipeline).delete()
    pipeline.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('renewals', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
