"""Стадия-пауза (allow_mid_cycle) — обобщение механики «Заморожен» на любую стадию.

Решение пользователя 2026-08-06: школе нужна стадия «Закончил курс» с теми же
правилами, что у заморозки, — перевести можно в любой момент цикла, а выход
только «Вернуть в работу». Правило перестало быть привилегией ключа 'frozen'
и стало свойством стадии.
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.renewals.models import RenewalDeal, RenewalStage

STAGES_URL = '/api/admin/renewals/stages'
MOVE_URL = '/api/admin/renewals/{}/move'
UNFREEZE_URL = '/api/admin/renewals/{}/unfreeze'


@pytest.fixture
def pause_stage(db):
    """Ручная decision-стадия «Закончил курс» с allow_mid_cycle."""
    pipe_id, order = RenewalStage.objects.filter(
        pipeline__is_default=True).values_list('pipeline_id', 'sort_order').last()
    stage = RenewalStage.objects.create(
        pipeline_id=pipe_id, key='__test_course_finished__', label='Закончил курс',
        color='#8B5CF6', kind='decision', sort_order=order + 1,
        is_auto=False, allow_mid_cycle=True)
    yield stage
    # RenewalDeal.stage — FK RESTRICT, и финализация фикстур идёт в обратном
    # порядке: эта стадия убирается РАНЬШЕ, чем make_student успеет снести свои
    # сделки. Поэтому сначала явно чистим всё, что на неё ссылается.
    with connection.cursor() as cur:
        cur.execute('DELETE FROM renewal_activity WHERE deal_id IN '
                    '(SELECT id FROM renewal_deal WHERE stage_id = %s)', [stage.id])
        cur.execute('DELETE FROM renewal_activity '
                    'WHERE from_stage_id = %s OR to_stage_id = %s', [stage.id, stage.id])
        cur.execute('DELETE FROM renewal_deal WHERE stage_id = %s', [stage.id])
        cur.execute('DELETE FROM renewals_renewalstageevent WHERE pgh_obj_id = %s', [stage.id])
    RenewalStage.objects.filter(id=stage.id).delete()


@pytest.fixture
def deal_on_progress(db, make_student):
    """Открытая сделка на авто-стадии «Урок 1», посещаемости нет — цикл не отработан."""
    sid = make_student('__pause_stage_stud__')
    lesson_1 = RenewalStage.objects.get(pipeline__is_default=True, key='lesson_1')
    return RenewalDeal.objects.create(
        student_id=sid, cycle_no=1, pipeline_id=lesson_1.pipeline_id, stage=lesson_1)


@pytest.mark.django_db
def test_move_to_pause_stage_mid_cycle_from_auto(admin_client, deal_on_progress, pause_stage):
    """Главное требование: перевод с авто-стадии «Урок 1» посреди цикла проходит."""
    resp = admin_client.post(MOVE_URL.format(deal_on_progress.id),
                             {'to_stage_id': pause_stage.id}, format='json')
    assert resp.status_code == 200, resp.json()
    deal_on_progress.refresh_from_db()
    assert deal_on_progress.stage_id == pause_stage.id
    # Сделка ОСТАЁТСЯ открытой: это пауза, а не исход.
    assert deal_on_progress.outcome_at is None


@pytest.mark.django_db
def test_pause_stage_needs_no_month(admin_client, deal_on_progress, pause_stage):
    """Срок («до какого месяца») — обвязка заморозки, у прочих пауз его не спрашиваем."""
    resp = admin_client.post(MOVE_URL.format(deal_on_progress.id),
                             {'to_stage_id': pause_stage.id}, format='json')
    assert resp.status_code == 200
    deal_on_progress.refresh_from_db()
    assert deal_on_progress.frozen_until_month is None


@pytest.mark.django_db
def test_return_to_work_from_pause_stage(admin_client, deal_on_progress, pause_stage):
    """«Вернуть в работу» возвращает сделку на расчётную авто-стадию."""
    from apps.renewals import repository as repo
    repo.move_deal(deal_on_progress.id, pause_stage.id, None, None)

    resp = admin_client.post(UNFREEZE_URL.format(deal_on_progress.id))
    assert resp.status_code == 200, resp.json()
    deal_on_progress.refresh_from_db()
    returned = RenewalStage.objects.get(id=deal_on_progress.stage_id)
    assert returned.is_auto is True
    assert deal_on_progress.outcome_at is None


@pytest.mark.django_db
def test_return_to_work_rejected_on_ordinary_stage(admin_client, deal_on_progress):
    """С обычной стадии «Вернуть в работу» не работает — 409, стадия не меняется."""
    before = deal_on_progress.stage_id
    resp = admin_client.post(UNFREEZE_URL.format(deal_on_progress.id))
    assert resp.status_code == 409
    deal_on_progress.refresh_from_db()
    assert deal_on_progress.stage_id == before


@pytest.mark.django_db
def test_ordinary_decision_still_blocked_mid_cycle(admin_client, deal_on_progress):
    """Контроль: без флага та же попытка посреди цикла по-прежнему 409."""
    thinking = RenewalStage.objects.get(pipeline__is_default=True, key='thinking')
    assert thinking.allow_mid_cycle is False
    resp = admin_client.post(MOVE_URL.format(deal_on_progress.id),
                             {'to_stage_id': thinking.id}, format='json')
    assert resp.status_code == 409


@pytest.mark.django_db
def test_flag_does_not_open_terminal_shortcut(admin_client, deal_on_progress, pause_stage):
    """Флаг не должен давать закрыть сделку в обход ворот баланса и цикла.

    superadmin может сменить вид стадии-паузы на «Продлён»; проверка kind='decision'
    в transitions._is_pause_target — единственное, что не даёт так закрыть сделку
    посреди цикла с нулевым балансом.
    """
    RenewalStage.objects.filter(id=pause_stage.id).update(kind='won')
    resp = admin_client.post(MOVE_URL.format(deal_on_progress.id),
                             {'to_stage_id': pause_stage.id}, format='json')
    assert resp.status_code == 409
    deal_on_progress.refresh_from_db()
    assert deal_on_progress.outcome_at is None


@pytest.mark.django_db
def test_frozen_keeps_flag_after_migration():
    """Заморозке миграция 0016 проставила флаг — её поведение не изменилось."""
    frozen = RenewalStage.objects.get(pipeline__is_default=True, key='frozen')
    assert frozen.allow_mid_cycle is True


@pytest.mark.django_db
def test_stage_api_roundtrips_flag(superadmin_client):
    """Флаг ставится при создании, читается в списке и меняется PATCH-ем."""
    created = superadmin_client.post(
        STAGES_URL, {'label': 'Закончил курс', 'kind': 'decision',
                     'color': '#8B5CF6', 'allow_mid_cycle': True}, format='json')
    assert created.status_code == 201
    stage_id = created.json()['id']
    assert created.json()['allow_mid_cycle'] is True

    listed = next(s for s in superadmin_client.get(STAGES_URL).json() if s['id'] == stage_id)
    assert listed['allow_mid_cycle'] is True

    patched = superadmin_client.patch(f'{STAGES_URL}/{stage_id}',
                                      {'allow_mid_cycle': False}, format='json')
    assert patched.status_code == 200
    assert patched.json()['allow_mid_cycle'] is False

    assert superadmin_client.delete(f'{STAGES_URL}/{stage_id}').status_code == 204


@pytest.mark.django_db
def test_stage_api_defaults_flag_to_false(superadmin_client):
    """Без явного флага стадия обычная — послабления не раздаём по умолчанию."""
    created = superadmin_client.post(
        STAGES_URL, {'label': 'Перезвонить позже', 'kind': 'decision'}, format='json')
    assert created.status_code == 201
    assert created.json()['allow_mid_cycle'] is False
    superadmin_client.delete(f"{STAGES_URL}/{created.json()['id']}")
