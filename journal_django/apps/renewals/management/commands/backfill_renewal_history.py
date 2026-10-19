"""
Восстановление истории циклов продления из реальных дат посещений (LT/LTV).

Подписочная модель: цикл = абонемент = 4 суммарных урока по всей истории ученика.
Для каждого ученика с посещениями:
  • прошлые полные циклы i (сумма посещений пересекла i×4) → закрытая сделка
    «Продлён» с due_at = outcome_at = дата пересечения (день 4-го урока цикла);
  • у ученика БЕЗ активных membership последний неполный цикл → закрыт «Ушёл»
    датой последнего посещения (реальный отток для аналитики);
  • активному ученику текущий цикл создаёт менеджер через сводку
    «Ученики без сделок» (POST /api/admin/renewals).

Идемпотентна: существующие сделки (student, cycle_no) не пересоздаются.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from apps.renewals.cycle import LESSONS_PER_CYCLE
from apps.renewals.models import RenewalActivity, RenewalDeal, RenewalPipeline, RenewalStage


class Command(BaseCommand):
    help = 'Восстанавливает закрытые циклы продления из истории посещений (идемпотентно).'

    def handle(self, *args, **options):
        pipe = RenewalPipeline.objects.get(is_default=True)
        won = RenewalStage.objects.filter(pipeline=pipe, kind='won').order_by('sort_order').first()
        lost = RenewalStage.objects.filter(pipeline=pipe, kind='lost').order_by('sort_order').first()

        # Вся посещаемость по ученикам в хронологии (half-lesson 0.5).
        with connection.cursor() as cur:
            cur.execute("""
                SELECT la.student_id, l.lesson_date,
                       CASE WHEN l.lesson_duration_minutes = 45 THEN 0.5 ELSE 1 END AS units
                FROM lesson_attendance la
                JOIN lessons l ON l.id = la.lesson_id
                WHERE la.present = true
                ORDER BY la.student_id, l.lesson_date, l.id
            """)
            rows = cur.fetchall()
            cur.execute("""
                SELECT DISTINCT m.student_id FROM group_memberships m WHERE m.active = true
            """)
            active_students = {r[0] for r in cur.fetchall()}

        # student_id -> [(date, units), ...] в хронологии
        by_student: dict[int, list] = {}
        for sid, day, units in rows:
            by_student.setdefault(sid, []).append((day, float(units)))

        existing = set(RenewalDeal.objects.values_list('student_id', 'cycle_no'))

        created_won = created_lost = 0
        with transaction.atomic():
            for sid, visits in by_student.items():
                # даты пересечения границ i×4 → закрытые «Продлён»-циклы
                total = 0.0
                boundary = LESSONS_PER_CYCLE
                cycle_i = 1
                last_date = visits[-1][0]
                for day, units in visits:
                    total += units
                    while total >= boundary:
                        if (sid, cycle_i) not in existing:
                            deal = RenewalDeal.objects.create(
                                student_id=sid, cycle_no=cycle_i, pipeline=pipe,
                                stage=won, due_at=day, outcome_at=_dt(day))
                            RenewalDeal.objects.filter(id=deal.id).update(
                                stage_entered_at=_dt(day), created_at=_dt(day))
                            RenewalActivity.objects.create(
                                deal=deal, kind='system', to_stage=won,
                                body='Восстановлено из истории посещений')
                            created_won += 1
                            existing.add((sid, cycle_i))
                        cycle_i += 1
                        boundary += LESSONS_PER_CYCLE
                # ушедший ученик: неполный последний цикл закрываем «Ушёл»
                if sid not in active_students and total < boundary and total > (boundary - LESSONS_PER_CYCLE):
                    if (sid, cycle_i) not in existing:
                        deal = RenewalDeal.objects.create(
                            student_id=sid, cycle_no=cycle_i, pipeline=pipe,
                            stage=lost, outcome_at=_dt(last_date),
                            reason_code='unknown')
                        RenewalDeal.objects.filter(id=deal.id).update(
                            stage_entered_at=_dt(last_date), created_at=_dt(last_date))
                        RenewalActivity.objects.create(
                            deal=deal, kind='system', to_stage=lost,
                            body='Восстановлено из истории: ученик прекратил занятия')
                        created_lost += 1
                        existing.add((sid, cycle_i))

        self.stdout.write(self.style.SUCCESS(
            f'renewals: восстановлено циклов — продлён {created_won}, ушёл {created_lost}'))


def _dt(day):
    """date → aware datetime (полночь MSK-дня в UTC не критична — важен день)."""
    from datetime import datetime, time
    from django.utils import timezone as tz
    return tz.make_aware(datetime.combine(day, time(12, 0)))
