"""API-тесты записи renewals: move (валидация переходов), patch, comment, activity."""
import pytest

from apps.renewals import engine
from apps.renewals.models import RenewalStage

BASE = '/api/admin/renewals'


def _stage_id(key):
    return RenewalStage.objects.get(key=key, pipeline__is_default=True).id


@pytest.mark.django_db
def test_move_off_auto_stage_blocked(admin_client, make_student, make_direction):
    """Свежая сделка стоит на авто-стадии `no_lesson_yet` — увести её руками
    (даже в ручную decision «Думает») теперь нельзя: авто-стадии двигает
    только движок (решение пользователя 2026-07-17)."""
    sid, did = make_student(), make_direction()
    deal = engine.ensure_deal(sid, cycle_no=1)
    assert deal.stage.key == 'no_lesson_yet'
    resp = admin_client.post(f'{BASE}/{deal.id}/move',
                             {'to_stage_id': _stage_id('thinking')}, format='json')
    assert resp.status_code == 409


@pytest.mark.django_db
def test_move_from_terminal_409(admin_client, make_student, make_direction):
    """С терминальной стадии (lost) руками никуда — 409. Доводим сделку до
    lost напрямую через ORM: руками на lost с авто-стадии не встать, а штатный
    путь закрытия (move_deal с причиной) сюда не нужен — проверяем сам запрет."""
    from django.utils import timezone
    from apps.renewals.models import RenewalDeal
    sid, did = make_student(), make_direction()
    deal = engine.ensure_deal(sid, cycle_no=1)
    deal.stage = RenewalStage.objects.get(key='churned', pipeline=deal.pipeline)
    deal.outcome_at = timezone.now()
    deal.save(update_fields=['stage', 'outcome_at'])
    resp = admin_client.post(f'{BASE}/{deal.id}/move',
                             {'to_stage_id': _stage_id('thinking')}, format='json')
    assert resp.status_code == 409


@pytest.mark.django_db
def test_move_onto_progress_stage_409(admin_client, make_student, make_direction):
    """Прогресс-стадии («Не было урока»/«Урок N») двигает только движок по
    событиям — ручной move на них запрещён, даже суперадмином."""
    sid, did = make_student(), make_direction()
    deal = engine.ensure_deal(sid, cycle_no=1)
    resp = admin_client.post(f'{BASE}/{deal.id}/move',
                             {'to_stage_id': _stage_id('lesson_1')}, format='json')
    assert resp.status_code == 409


@pytest.mark.django_db
def test_move_off_progress_stage_to_manual_decision_409(admin_client, make_student, make_direction):
    """А вот в РУЧНУЮ decision-стадию («Думает» и т.п.) со свежесозданной
    (цикл не завершён) сделки перевести нельзя — 409 (решение пользователя
    2026-07-17). Авто-стадии (в т.ч. «Ждём оплату», is_auto=True) руками не
    достижимы вовсе — их двигает только движок."""
    sid, did = make_student(), make_direction()
    deal = engine.ensure_deal(sid, cycle_no=1)
    resp = admin_client.post(f'{BASE}/{deal.id}/move',
                             {'to_stage_id': _stage_id('thinking')}, format='json')
    assert resp.status_code == 409


@pytest.mark.django_db
def test_move_to_won_before_cycle_completed_409(admin_client, make_student, make_direction):
    """Нельзя вручную закрыть сделку как «Продлён», пока цикл (4 урока)
    не отработан — ни с progress-стадии, ни с «Ждём оплату» (решение
    пользователя 2026-07-17)."""
    sid, did = make_student(), make_direction()
    deal = engine.ensure_deal(sid, cycle_no=1)
    resp = admin_client.post(f'{BASE}/{deal.id}/move',
                             {'to_stage_id': _stage_id('renewed')}, format='json')
    assert resp.status_code == 409

    # Ставим сделку на 'Ждём оплату' напрямую через ORM (руками туда больше
    # не попасть — is_auto=True): проверяем, что и оттуда 'Продлён' до
    # завершения цикла запрещён.
    deal.stage = RenewalStage.objects.get(key='awaiting_payment', pipeline=deal.pipeline)
    deal.save(update_fields=['stage'])
    resp = admin_client.post(f'{BASE}/{deal.id}/move',
                             {'to_stage_id': _stage_id('renewed')}, format='json')
    assert resp.status_code == 409


@pytest.mark.django_db
def test_move_to_won_blocked_when_balance_not_positive(manager_client, make_student, make_direction):
    """Цикл отработан, но баланс <= 0 (долг или ровно 0) — менеджеру «Продлён»
    запрещён: без положительного баланса продление не подкреплено оплатой на
    следующий цикл. Ставим сделку на ручную decision-стадию («Думает»), цикл
    мокаем завершённым — проверяем именно балансовый гейт, отдельно от
    cycle_completed. Admin/superadmin этот гейт обходят (тест ниже)."""
    from unittest.mock import patch
    sid, did = make_student(), make_direction()
    deal = engine.ensure_deal(sid, cycle_no=1)
    deal.stage = RenewalStage.objects.get(key='thinking', pipeline=deal.pipeline)
    deal.save(update_fields=['stage'])
    with patch('apps.renewals.engine.cycle_completed', return_value=True), \
         patch('apps.finances.repository.balance_for_student', return_value=0):
        resp = manager_client.post(f'{BASE}/{deal.id}/move',
                                 {'to_stage_id': _stage_id('renewed')}, format='json')
    assert resp.status_code == 409
    assert 'баланс' in resp.json()['error'].lower()

    with patch('apps.renewals.engine.cycle_completed', return_value=True), \
         patch('apps.finances.repository.balance_for_student', return_value=-3):
        resp = manager_client.post(f'{BASE}/{deal.id}/move',
                                 {'to_stage_id': _stage_id('renewed')}, format='json')
    assert resp.status_code == 409


@pytest.mark.django_db
@pytest.mark.parametrize('client_fixture', ['admin_client', 'superadmin_client'])
@pytest.mark.parametrize('balance', [0, -3])
def test_move_to_won_allowed_for_admins_when_balance_not_positive(
        request, client_fixture, balance, make_student, make_direction):
    """Admin и superadmin могут отметить «Продлён» при нулевом балансе или долге
    (решение пользователя 2026-09-28: продление договорено, оплата придёт позже).
    Гейт завершённого цикла для них остаётся — отдельный тест выше."""
    from unittest.mock import patch
    client = request.getfixturevalue(client_fixture)
    sid, did = make_student(), make_direction()
    deal = engine.ensure_deal(sid, cycle_no=1)
    deal.stage = RenewalStage.objects.get(key='thinking', pipeline=deal.pipeline)
    deal.save(update_fields=['stage'])
    with patch('apps.renewals.engine.cycle_completed', return_value=True),          patch('apps.finances.repository.balance_for_student', return_value=balance):
        resp = client.post(f'{BASE}/{deal.id}/move',
                           {'to_stage_id': _stage_id('renewed')}, format='json')
    assert resp.status_code == 200, resp.content
    deal.refresh_from_db()
    assert deal.stage.key == 'renewed'
    assert deal.outcome_at is not None


@pytest.mark.django_db
def test_move_to_stage_outside_pipeline_409(admin_client, make_student, make_direction):
    """to_stage_id, которого нет в воронке сделки → InvalidTransition → 409, не 500."""
    sid, did = make_student(), make_direction()
    deal = engine.ensure_deal(sid, cycle_no=1)
    resp = admin_client.post(f'{BASE}/{deal.id}/move',
                             {'to_stage_id': 999999999}, format='json')
    assert resp.status_code == 409


@pytest.mark.django_db
def test_move_to_won_respawns_next_cycle(admin_client, make_student, make_direction):
    """
    Ручное продление (drag/диалог → won) — единственный путь закрытия сделки
    (оплата больше не закрывает её сама, см. signals.py): ставит outcome_at и
    спавнит открытую сделку следующего цикла. Move→won разрешён только когда
    цикл отработан (см. test_move_to_won_before_cycle_completed_409) — здесь
    это не тестируем, поэтому мокаем cycle_completed, а не городим реальную
    посещаемость (группа/членство/4 урока) ради несвязанной проверки респавна.
    """
    from unittest.mock import patch
    sid, did = make_student(), make_direction()
    deal = engine.ensure_deal(sid, cycle_no=1)
    # На авто-стадии move→won запрещён (from_is_auto); реальный путь «Продлён» —
    # с ручной decision-стадии. Ставим 'Думает' напрямую через ORM.
    deal.stage = RenewalStage.objects.get(key='thinking', pipeline=deal.pipeline)
    deal.save(update_fields=['stage'])
    with patch('apps.renewals.engine.cycle_completed', return_value=True), \
         patch('apps.finances.repository.balance_for_student', return_value=4):
        resp = admin_client.post(f'{BASE}/{deal.id}/move',
                                 {'to_stage_id': _stage_id('renewed')}, format='json')
    assert resp.status_code == 200
    body = resp.json()
    assert body['stage_key'] == 'renewed'
    assert body['outcome_at'] is not None
    from apps.renewals.models import RenewalDeal
    assert RenewalDeal.objects.filter(
        student_id=sid, cycle_no=2, outcome_at__isnull=True).exists()


@pytest.mark.django_db
def test_move_to_won_syncs_spawned_cycle_stage(admin_client, make_student, make_direction):
    """F2: при «Продлён» спавненная сделка следующего цикла сразу встаёт на верную
    авто-стадию по фактической посещаемости, а не остаётся на «Не было урока».
    attended=5: цикл 1 отработан (move→won разрешён), в цикл 2 отработан 1 урок."""
    from unittest.mock import patch
    from apps.renewals.models import RenewalDeal
    sid, did = make_student(), make_direction()
    deal = engine.ensure_deal(sid, cycle_no=1)
    deal.stage = RenewalStage.objects.get(key='thinking', pipeline=deal.pipeline)
    deal.save(update_fields=['stage'])
    with patch('apps.renewals.engine._attended_total', return_value=5.0), \
         patch('apps.finances.repository.balance_for_student', return_value=10):
        resp = admin_client.post(f'{BASE}/{deal.id}/move',
                                 {'to_stage_id': _stage_id('renewed')}, format='json')
    assert resp.status_code == 200
    nxt = RenewalDeal.objects.get(student_id=sid, cycle_no=2, outcome_at__isnull=True)
    assert nxt.stage.key == 'lesson_1'


@pytest.mark.django_db
def test_move_to_won_skips_taken_closed_cycle(admin_client, make_student, make_direction):
    """Если цикл N+1 уже занят закрытой сделкой (после reopen/возврата),
    респавн перешагивает его — открытая сделка не теряется."""
    from unittest.mock import patch
    from django.utils import timezone
    from apps.renewals.models import RenewalDeal, RenewalPipeline
    sid, did = make_student(), make_direction()
    deal = engine.ensure_deal(sid, cycle_no=1)
    pipe = RenewalPipeline.objects.get(is_default=True)
    lost = RenewalStage.objects.filter(pipeline=pipe, kind='lost').first()
    RenewalDeal.objects.create(student_id=sid, cycle_no=2, pipeline=pipe,
                               stage=lost, outcome_at=timezone.now())

    # На авто-стадии move→won запрещён (from_is_auto) — ставим 'Думает' через ORM.
    deal.stage = RenewalStage.objects.get(key='thinking', pipeline=pipe)
    deal.save(update_fields=['stage'])
    with patch('apps.renewals.engine.cycle_completed', return_value=True), \
         patch('apps.finances.repository.balance_for_student', return_value=4):
        resp = admin_client.post(f'{BASE}/{deal.id}/move',
                                 {'to_stage_id': _stage_id('renewed')}, format='json')
    assert resp.status_code == 200
    assert RenewalDeal.objects.filter(
        student_id=sid, cycle_no=3, outcome_at__isnull=True).exists()


@pytest.mark.django_db
def test_move_from_awaiting_renewal_to_won(admin_client, make_student, make_direction):
    """Со стадии «Ждём продление» продление подтверждается вручную (drag→won) —
    ключевой сценарий, ранее заблокированный тотальным from_is_auto (fix 2026-07-19).
    Ставим сделку на awaiting_renewal напрямую (движок ставит её сам по факту
    отработки цикла), cycle_completed мокаем — реальную посещаемость тут не строим."""
    from unittest.mock import patch
    sid, did = make_student(), make_direction()
    deal = engine.ensure_deal(sid, cycle_no=1)
    deal.stage = RenewalStage.objects.get(key='awaiting_renewal', pipeline=deal.pipeline)
    deal.save(update_fields=['stage'])
    with patch('apps.renewals.engine.cycle_completed', return_value=True), \
         patch('apps.finances.repository.balance_for_student', return_value=4):
        resp = admin_client.post(f'{BASE}/{deal.id}/move',
                                 {'to_stage_id': _stage_id('renewed')}, format='json')
    assert resp.status_code == 200
    assert resp.json()['stage_key'] == 'renewed'
    assert resp.json()['outcome_at'] is not None


@pytest.mark.django_db
def test_patch_reason_code(admin_client, make_student, make_direction):
    sid, did = make_student(), make_direction()
    deal = engine.ensure_deal(sid, cycle_no=1)
    resp = admin_client.patch(f'{BASE}/{deal.id}',
                              {'reason_code': 'звонил, думает'}, format='json')
    assert resp.status_code == 200
    assert resp.json()['reason_code'] == 'звонил, думает'


@pytest.mark.django_db
def test_patch_ignores_assignee_id(admin_client, make_student, make_direction):
    """assignee_id больше не патчится напрямую на сделке — единственный путь
    смены ответственного теперь через Student.manager (жёсткая синхронизация)."""
    from apps.accounts.models import Account
    from django.contrib.auth.hashers import make_password
    import uuid

    sid, did = make_student(), make_direction()
    deal = engine.ensure_deal(sid, cycle_no=1)
    email = f'__test_patch_ignore_assignee__{uuid.uuid4().hex[:8]}@test.local'
    acc = Account.objects.create(
        email=email, password=make_password('x'), role='manager', is_active=True)
    resp = admin_client.patch(f'{BASE}/{deal.id}', {'assignee_id': acc.id}, format='json')
    assert resp.status_code == 200
    assert resp.json()['assignee_id'] is None


@pytest.mark.django_db
def test_comment_then_activity(admin_client, make_student, make_direction):
    sid, did = make_student(), make_direction()
    deal = engine.ensure_deal(sid, cycle_no=1)
    admin_client.post(f'{BASE}/{deal.id}/comment', {'body': 'позвонил, думает'}, format='json')
    acts = admin_client.get(f'{BASE}/{deal.id}/activity').json()
    assert any(a['kind'] == 'comment' and a['body'] == 'позвонил, думает' for a in acts)


@pytest.mark.django_db
def test_reopen_closed_deal(admin_client, make_student, make_direction):
    """Закрытую сделку можно переоткрыть: outcome_at сбрасывается, стадия — авто."""
    from django.utils import timezone
    sid, did = make_student(), make_direction()
    deal = engine.ensure_deal(sid, cycle_no=1)
    # Закрываем сделку напрямую через ORM (на lost с авто-стадии руками больше не
    # встать — from_is_auto): проверяем именно переоткрытие закрытой сделки.
    deal.stage = RenewalStage.objects.get(key='churned', pipeline=deal.pipeline)
    deal.outcome_at = timezone.now()
    deal.save(update_fields=['stage', 'outcome_at'])
    resp = admin_client.post(f'{BASE}/{deal.id}/reopen')
    assert resp.status_code == 200
    assert resp.json()['outcome_at'] is None


@pytest.mark.django_db
def test_reopen_open_deal_409(admin_client, make_student, make_direction):
    sid, did = make_student(), make_direction()
    deal = engine.ensure_deal(sid, cycle_no=1)
    resp = admin_client.post(f'{BASE}/{deal.id}/reopen')
    assert resp.status_code == 409


@pytest.mark.django_db
def test_reopen_missing_deal_404(admin_client):
    resp = admin_client.post(f'{BASE}/999999999/reopen')
    assert resp.status_code == 404


@pytest.mark.django_db
def test_assignees_list(manager_client):
    resp = manager_client.get(f'{BASE}/assignees')
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert all({'id', 'full_name'} <= set(r) for r in rows)


@pytest.mark.django_db
def test_assignees_forbidden_for_teacher(teacher_client):
    assert teacher_client.get(f'{BASE}/assignees').status_code == 403
