"""
Стадия «Ждём продление» (awaiting_renewal, авто) после «Ждём оплату»;
awaiting_payment становится авто-стадией; backfill due_at закрытым сделкам.

Открытые сделки по новым правилам разводит НЕ миграция, а движок по событиям
посещаемости/оплат (балансовая логика живёт в apps.finances).
"""
from django.db import migrations


def forward(apps, schema_editor):
    RenewalPipeline = apps.get_model('renewals', 'RenewalPipeline')
    RenewalStage = apps.get_model('renewals', 'RenewalStage')
    RenewalDeal = apps.get_model('renewals', 'RenewalDeal')

    pipe = RenewalPipeline.objects.filter(is_default=True).first()
    if pipe is None:
        return
    ap = RenewalStage.objects.filter(pipeline=pipe, key='awaiting_payment').first()
    if ap is not None and not ap.is_auto:
        ap.is_auto = True
        ap.save(update_fields=['is_auto'])

    if not RenewalStage.objects.filter(pipeline=pipe, key='awaiting_renewal').exists():
        anchor = ap.sort_order if ap is not None else 4
        # сдвигаем хвост в обратном порядке — не столкнуться по sort_order
        for st in (RenewalStage.objects.filter(pipeline=pipe, sort_order__gt=anchor)
                   .order_by('-sort_order')):
            st.sort_order += 1
            st.save(update_fields=['sort_order'])
        RenewalStage.objects.create(
            pipeline=pipe, key='awaiting_renewal', label='Ждём продление',
            color='#F97316', kind='decision', is_auto=True, sort_order=anchor + 1)

    # закрытым сделкам месяц когорты = месяц закрытия
    for deal in RenewalDeal.objects.filter(outcome_at__isnull=False, due_at__isnull=True):
        deal.due_at = deal.outcome_at.date()
        deal.save(update_fields=['due_at'])


def backward(apps, schema_editor):
    RenewalPipeline = apps.get_model('renewals', 'RenewalPipeline')
    RenewalStage = apps.get_model('renewals', 'RenewalStage')
    pipe = RenewalPipeline.objects.filter(is_default=True).first()
    if pipe is None:
        return
    st = RenewalStage.objects.filter(pipeline=pipe, key='awaiting_renewal').first()
    if st is not None and not st.deals.exists():
        st.delete()


class Migration(migrations.Migration):
    dependencies = [('renewals', '0004_remove_renewaldeal_insert_insert_and_more')]
    operations = [migrations.RunPython(forward, backward)]
