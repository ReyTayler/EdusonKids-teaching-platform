"""Стадия последней сделки как «статус» ученика (спека 2026-07-25).

Открытая сделка → её стадия, stage_is_open=True. Все закрыты → стадия
последнего цикла, stage_is_open=False. Нет сделок → stage=None.
"""
from datetime import date

import pytest
from django.utils import timezone

from apps.renewals.models import RenewalDeal, RenewalStage
from apps.students import repository
from apps.students.models import Student


@pytest.fixture
def stages():
    pipe_filter = {'pipeline__is_default': True}
    return {
        'frozen': RenewalStage.objects.get(**pipe_filter, key='frozen'),
        'churned': RenewalStage.objects.get(**pipe_filter, key='churned'),
        'awaiting': RenewalStage.objects.get(**pipe_filter, key='awaiting_renewal'),
    }


def _student(name):
    return Student.objects.create(full_name=name, created_at=timezone.now())


@pytest.mark.django_db
def test_open_deal_stage_is_reported(stages):
    st = _student('__stage_open__')
    RenewalDeal.objects.create(student=st, cycle_no=1,
                               pipeline=stages['frozen'].pipeline,
                               stage=stages['frozen'],
                               frozen_until_month=date(2026, 9, 1))
    row = repository.get_student(st.id)
    assert row['stage']['key'] == 'frozen'
    assert row['stage']['label'] == 'Заморожен'
    assert row['stage_is_open'] is True
    assert row['stage_frozen_until_month'] == date(2026, 9, 1)


@pytest.mark.django_db
def test_closed_deal_stage_is_reported_as_not_open(stages):
    st = _student('__stage_closed__')
    RenewalDeal.objects.create(student=st, cycle_no=1,
                               pipeline=stages['churned'].pipeline,
                               stage=stages['churned'],
                               outcome_at=timezone.now())
    row = repository.get_student(st.id)
    assert row['stage']['key'] == 'churned'
    assert row['stage_is_open'] is False
    assert row['stage_frozen_until_month'] is None


@pytest.mark.django_db
def test_latest_cycle_wins(stages):
    """Показываем стадию последнего цикла, а не первого."""
    st = _student('__stage_latest__')
    RenewalDeal.objects.create(student=st, cycle_no=1,
                               pipeline=stages['churned'].pipeline,
                               stage=stages['churned'], outcome_at=timezone.now())
    RenewalDeal.objects.create(student=st, cycle_no=2,
                               pipeline=stages['awaiting'].pipeline,
                               stage=stages['awaiting'])
    row = repository.get_student(st.id)
    assert row['stage']['key'] == 'awaiting_renewal'
    assert row['stage_is_open'] is True


@pytest.mark.django_db
def test_student_without_deals_has_no_stage():
    st = _student('__stage_none__')
    row = repository.get_student(st.id)
    assert row['stage'] is None
    assert row['stage_is_open'] is False
    assert row['stage_frozen_until_month'] is None


@pytest.mark.django_db
def test_filter_by_stage_id(stages):
    st = _student('__stage_filter__')
    RenewalDeal.objects.create(student=st, cycle_no=1,
                               pipeline=stages['frozen'].pipeline,
                               stage=stages['frozen'],
                               frozen_until_month=date(2026, 9, 1))
    result = repository.list_students(
        page_size=500, filters={'stage_id': stages['frozen'].id})
    names = [r['full_name'] for r in result['rows']]
    assert '__stage_filter__' in names
    assert all(r['stage']['key'] == 'frozen' for r in result['rows'])


@pytest.mark.django_db
def test_sort_by_stage_desc_keeps_students_without_deals_last(stages):
    """Ученик без сделок — всегда в конце, при любом направлении сортировки.

    Postgres по умолчанию даёт NULLS LAST только для ASC; без явного nulls_last
    сортировка «по стадии по убыванию» начиналась бы с пачки прочерков.
    """
    with_deal = _student('__stage_nulls_with__')
    RenewalDeal.objects.create(student=with_deal, cycle_no=1,
                               pipeline=stages['frozen'].pipeline,
                               stage=stages['frozen'])
    _student('__stage_nulls_without__')

    for direction in ('asc', 'desc'):
        rows = repository.list_students(
            page_size=500, sort_by='stage', sort_dir=direction)['rows']
        names = [r['full_name'] for r in rows]
        assert names.index('__stage_nulls_with__') < names.index('__stage_nulls_without__'), (
            f'ученик без сделок должен идти позже при sort_dir={direction}')


@pytest.mark.django_db
def test_sort_by_stage(stages):
    """sort_by='stage' сортирует по sort_order стадии, не по её id/подписи.

    Ученики на стадиях с разным sort_order + один без сделок (стадия NULL —
    сортировка не должна на нём падать).
    """
    for name, stage in (('__stage_sort_frozen__', stages['frozen']),
                        ('__stage_sort_awaiting__', stages['awaiting'])):
        st = _student(name)
        RenewalDeal.objects.create(student=st, cycle_no=1,
                                   pipeline=stage.pipeline, stage=stage)
    _student('__stage_sort_none__')

    result = repository.list_students(page_size=500, sort_by='stage', sort_dir='asc')
    orders = [r['stage']['sort_order'] for r in result['rows'] if r['stage']]
    assert len(orders) >= 2
    assert orders == sorted(orders)
