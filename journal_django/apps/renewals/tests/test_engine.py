from unittest.mock import patch

import pytest
from apps.renewals import engine, repository
from apps.renewals.models import RenewalActivity, RenewalDeal, RenewalStage


def _close_as_won(deal_id):
    """
    Продовый путь ручного закрытия «Продлён» — engine больше не закрывает
    сделки сам (см. signals.py), единственный путь — repository.move_deal.
    Move→won разрешён только с РУЧНОЙ decision-стадии и когда цикл отработан
    (решение пользователя 2026-07-17, см. test_api_write.py::
    test_move_to_won_before_cycle_completed_409). Свежая сделка стоит на авто-
    стадии, откуда руками уйти нельзя (from_is_auto) — поэтому сперва ставим её
    на ручную «Думает» напрямую через ORM (реальный путь «Продлён» идёт именно
    с ручной decision-стадии), а cycle_completed мокаем вместо реальной
    посещаемости на 4 урока (тесты этого файла — про респавн/reopen). Баланс
    тоже мокаем положительным — с 2026-07-22 «Продлён» дополнительно требует
    balance > 0 (не связано с тем, что тестируют файлы этого модуля).
    """
    deal = RenewalDeal.objects.get(id=deal_id)
    deal.stage = RenewalStage.objects.get(pipeline=deal.pipeline, key='thinking')
    deal.save(update_fields=['stage'])
    won_id = RenewalStage.objects.get(pipeline__is_default=True, kind='won').id
    with patch('apps.renewals.engine.cycle_completed', return_value=True), \
         patch('apps.finances.repository.balance_for_student', return_value=4):
        return repository.move_deal(deal_id, won_id, None, author_id=None)


@pytest.mark.django_db
def test_ensure_deal_is_idempotent(make_student):
    sid = make_student()
    d1 = engine.ensure_deal(sid, cycle_no=1)
    d2 = engine.ensure_deal(sid, cycle_no=1)
    assert d1.id == d2.id
    assert RenewalDeal.objects.filter(student_id=sid).count() == 1
    assert d1.stage.kind == 'progress'
    assert d1.outcome_at is None


@pytest.mark.django_db
def test_ensure_deal_picks_up_student_manager(make_student):
    """Новая сделка сразу получает assignee = текущий менеджер ученика (без
    передачи assignee_id вызывающим кодом — единый источник правды)."""
    from apps.students import services as student_services
    from apps.accounts.models import Account
    from django.contrib.auth.hashers import make_password
    import uuid

    sid = make_student()
    email = f'__test_engine_manager__{uuid.uuid4().hex[:8]}@test.local'
    manager = Account.objects.create(
        email=email, password=make_password('x'), role='manager',
        is_active=True, full_name='__Test Engine Manager__',
    )
    student_services.set_student_manager(sid, manager.id)

    deal = engine.ensure_deal(sid, cycle_no=1)
    assert deal.assignee_id == manager.id


@pytest.mark.django_db
def test_manual_close_won_respawns_next_cycle(make_student):
    """Ручное подтверждение продления (repository.move_deal → won) закрывает
    сделку и спавнит открытую сделку следующего цикла."""
    sid = make_student()
    deal = engine.ensure_deal(sid, cycle_no=1)
    _close_as_won(deal.id)
    open_deals = RenewalDeal.objects.filter(student_id=sid, outcome_at__isnull=True)
    closed = RenewalDeal.objects.filter(student_id=sid, outcome_at__isnull=False)
    assert closed.count() == 1
    assert closed.first().stage.kind == 'won'
    assert open_deals.count() == 1
    assert open_deals.first().cycle_no == 2


@pytest.mark.django_db
def test_manual_close_won_skips_taken_closed_cycle(make_student):
    """Респавн перешагивает занятый (закрытый) номер цикла — ученик не выпадает."""
    from django.utils import timezone
    from apps.renewals.models import RenewalPipeline
    sid = make_student()
    deal = engine.ensure_deal(sid, cycle_no=1)
    # имитируем «дыру»: цикл 2 уже существует и закрыт (переоткрытия/возвраты)
    pipe = RenewalPipeline.objects.get(is_default=True)
    lost = RenewalStage.objects.filter(pipeline=pipe, kind='lost').first()
    RenewalDeal.objects.create(student_id=sid, cycle_no=2, pipeline=pipe,
                               stage=lost, outcome_at=timezone.now())

    _close_as_won(deal.id)
    open_deals = RenewalDeal.objects.filter(student_id=sid, outcome_at__isnull=True)
    assert open_deals.count() == 1
    assert open_deals.first().cycle_no == 3  # перешагнули закрытый 2-й


@pytest.mark.django_db
def test_reopen_deletes_untouched_next_cycle(make_student):
    sid = make_student()
    deal = engine.ensure_deal(sid, cycle_no=1)
    _close_as_won(deal.id)
    assert RenewalDeal.objects.filter(student_id=sid, cycle_no=2).exists()

    reopened = engine.reopen_deal(deal.id)
    assert reopened.outcome_at is None
    assert reopened.stage.is_auto  # вернулась в вычисленную авто-стадию
    # порождённая нетронутая сделка цикла 2 удалена
    assert not RenewalDeal.objects.filter(student_id=sid, cycle_no=2).exists()
    # в таймлайне есть системная запись о переоткрытии
    assert RenewalActivity.objects.filter(
        deal_id=reopened.id, kind='system', body__icontains='переоткрыт').exists()


@pytest.mark.django_db
def test_reopen_keeps_touched_next_cycle(make_student):
    sid = make_student()
    deal = engine.ensure_deal(sid, cycle_no=1)
    _close_as_won(deal.id)
    nxt = RenewalDeal.objects.get(student_id=sid, cycle_no=2)
    RenewalActivity.objects.create(deal=nxt, kind='comment', body='тронуто руками')

    engine.reopen_deal(deal.id)
    assert RenewalDeal.objects.filter(id=nxt.id).exists()  # осталась
    assert RenewalActivity.objects.filter(
        deal_id=nxt.id, kind='system', body__icontains='переоткрыт').exists()


@pytest.mark.django_db
def test_reopen_open_deal_is_noop(make_student):
    sid = make_student()
    deal = engine.ensure_deal(sid, cycle_no=1)
    assert engine.reopen_deal(deal.id) is None


@pytest.mark.django_db
def test_next_open_cycle_no_skips_taken(make_student):
    sid = make_student()
    engine.ensure_deal(sid, cycle_no=1)
    assert engine.next_open_cycle_no(sid, 1) == 2  # цикл 1 занят открытой сделкой
    assert engine.next_open_cycle_no(sid, 5) == 5  # 5-й свободен
