"""
Разбивает единую авто-стадию «Урок 1–4» на 4 отдельные авто-стадии
(«Урок 1».."Урок 4"), по одной на каждый урок цикла.

Существующие открытые сделки на старой стадии переносятся на соответствующую
новую стадию по фактической посещаемости (та же формула, что и в
apps.renewals.repository.deal_computed / apps.renewals.cycle).
"""
from __future__ import annotations

from django.db import migrations, connection

LESSONS_PER_CYCLE = 4


def _attended_lessons(student_id: int, direction_id: int) -> float:
    with connection.cursor() as cur:
        cur.execute("""
            SELECT COALESCE(SUM(m.lessons_done), 0)
            FROM group_memberships m
            JOIN groups g ON g.id = m.group_id
            WHERE m.student_id = %s AND g.direction_id = %s AND m.active = true
        """, [student_id, direction_id])
        return float(cur.fetchone()[0] or 0)


def split_stage(apps, schema_editor):
    RenewalPipeline = apps.get_model('renewals', 'RenewalPipeline')
    RenewalStage = apps.get_model('renewals', 'RenewalStage')
    RenewalDeal = apps.get_model('renewals', 'RenewalDeal')

    pipe = RenewalPipeline.objects.get(is_default=True)
    old_stage = RenewalStage.objects.filter(pipeline=pipe, key='lesson_progress').first()
    color = old_stage.color if old_stage else '#6366F1'

    # Освобождаем sort_order 0..3 под уроки — остальные стадии сдвигаем следом.
    others = list(RenewalStage.objects.filter(pipeline=pipe)
                  .exclude(key='lesson_progress').order_by('sort_order'))
    for i, st in enumerate(others):
        st.sort_order = i + LESSONS_PER_CYCLE
        st.save()

    new_stages = []
    for i in range(LESSONS_PER_CYCLE):
        st, _ = RenewalStage.objects.get_or_create(
            pipeline=pipe, key=f'lesson_{i + 1}',
            defaults={'label': f'Урок {i + 1}', 'color': color,
                      'kind': 'progress', 'is_auto': True, 'sort_order': i})
        new_stages.append(st)

    if old_stage is not None:
        for deal in RenewalDeal.objects.filter(stage=old_stage):
            attended = _attended_lessons(deal.student_id, deal.direction_id)
            idx = int(attended % LESSONS_PER_CYCLE)
            deal.stage = new_stages[idx]
            deal.save()
        old_stage.delete()


def merge_stage(apps, schema_editor):
    RenewalPipeline = apps.get_model('renewals', 'RenewalPipeline')
    RenewalStage = apps.get_model('renewals', 'RenewalStage')
    RenewalDeal = apps.get_model('renewals', 'RenewalDeal')

    pipe = RenewalPipeline.objects.get(is_default=True)
    lesson_stages = list(RenewalStage.objects.filter(
        pipeline=pipe, key__in=[f'lesson_{i + 1}' for i in range(LESSONS_PER_CYCLE)]))
    if not lesson_stages:
        return
    color = lesson_stages[0].color

    merged, _ = RenewalStage.objects.get_or_create(
        pipeline=pipe, key='lesson_progress',
        defaults={'label': 'Урок 1–4', 'color': color,
                  'kind': 'progress', 'is_auto': True, 'sort_order': 0})

    RenewalDeal.objects.filter(stage__in=lesson_stages).update(stage=merged)
    for st in lesson_stages:
        st.delete()

    others = list(RenewalStage.objects.filter(pipeline=pipe)
                  .exclude(key='lesson_progress').order_by('sort_order'))
    for i, st in enumerate(others):
        st.sort_order = i + 1
        st.save()


class Migration(migrations.Migration):
    dependencies = [('renewals', '0002_seed_default_pipeline')]
    operations = [migrations.RunPython(split_stage, merge_stage)]
