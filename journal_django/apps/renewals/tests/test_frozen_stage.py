"""Заморозка как обычная стадия воронки (спека 2026-07-25)."""

from datetime import date

import pytest
from django.db import connection

from apps.renewals.models import RenewalDeal, RenewalStage
from apps.students.models import Student

# Тест бэкфила (миграция 0013 читала students.frozen_until) удалён вместе с
# колонками ученика — миграция students/0016 их сняла, воспроизвести вход SQL-я
# больше нечем. Сам бэкфил историчен и уже отработал на dev/проде.


@pytest.fixture
def frozen_stage():
    return RenewalStage.objects.get(pipeline__is_default=True, key='frozen')


@pytest.fixture
def churned_stage():
    return RenewalStage.objects.get(pipeline__is_default=True, key='churned')


@pytest.fixture
def open_deal(frozen_stage):
    """Открытая сделка на «Ждём продление», посещаемости нет (цикл не отработан).

    Этого достаточно: в «Заморожен» валидатор пускает и посреди цикла (задача 2),
    а в «Ушёл» — всегда. Тесты намеренно не подкручивают посещаемость, чтобы не
    зависеть от механики начисления уроков."""
    student = Student.objects.create(
        full_name='__frozen_stage_stud__', created_at='2026-07-01T00:00:00Z')
    awaiting = RenewalStage.objects.get(
        pipeline__is_default=True, key='awaiting_renewal')
    deal = RenewalDeal.objects.create(
        student=student, cycle_no=1, pipeline=frozen_stage.pipeline, stage=awaiting)
    yield deal
    # renewal_activity.author_id → accounts FK — DEFERRABLE INITIALLY DEFERRED
    # (см. apps/renewals/tests/conftest.py:make_student): если тест двигал сделку
    # через admin_client, активность ссылается на временный admin-аккаунт, который
    # тот же admin_client удаляет в СВОЁМ teardown. Чистим activity/deal/student
    # явно и раньше, чтобы к моменту удаления аккаунта висячей ссылки не было.
    with connection.cursor() as cur:
        cur.execute('DELETE FROM renewal_activity WHERE deal_id = %s', [deal.id])
        cur.execute('DELETE FROM renewal_deal WHERE id = %s', [deal.id])
        cur.execute('DELETE FROM students WHERE id = %s', [student.id])


MOVE_URL = '/api/admin/renewals/{}/move'


@pytest.mark.django_db
def test_move_to_frozen_requires_month(admin_client, open_deal, frozen_stage):
    """Без месяца заморозки переход в «Заморожен» не проходит — 400."""
    resp = admin_client.post(MOVE_URL.format(open_deal.id),
                             {'to_stage_id': frozen_stage.id},
                             format='json')
    assert resp.status_code == 400
    # Глобальный custom_exception_handler оборачивает ValidationError в
    # {"error": "Validation failed", "details": {...}} (apps/core/exceptions.py).
    assert 'frozen_until_month' in resp.json()['details']


@pytest.mark.django_db
def test_move_to_frozen_normalizes_month(admin_client, open_deal, frozen_stage):
    """День из ввода отбрасывается: храним 1-е число месяца."""
    resp = admin_client.post(MOVE_URL.format(open_deal.id),
                             {'to_stage_id': frozen_stage.id,
                              'frozen_until_month': '2026-09-17'},
                             format='json')
    assert resp.status_code == 200
    open_deal.refresh_from_db()
    assert open_deal.frozen_until_month == date(2026, 9, 1)
    assert open_deal.stage_id == frozen_stage.id


@pytest.mark.django_db
def test_leaving_frozen_clears_month(open_deal, frozen_stage, churned_stage):
    """Уход со стадии «Заморожен» обнуляет месяц — иначе он «прилипает» мёртвым.

    Уходим в «Ушёл»: у сделки из фикстуры нет посещаемости, то есть цикл не
    отработан, и валидатор пускает только в lost (в «Думает» он бы отказал —
    это его штатное поведение, а не помеха тесту).
    """
    from apps.renewals import repository as repo
    repo.move_deal(open_deal.id, frozen_stage.id, None, None,
                   frozen_until_month=date(2026, 9, 1))
    repo.move_deal(open_deal.id, churned_stage.id, None, None)
    open_deal.refresh_from_db()
    assert open_deal.frozen_until_month is None
    assert open_deal.stage_id == churned_stage.id


@pytest.mark.django_db
def test_freeze_activity_mentions_month(open_deal, frozen_stage):
    """В таймлайне видно, до какого месяца заморозка."""
    from apps.renewals import repository as repo
    repo.move_deal(open_deal.id, frozen_stage.id, None, None,
                   frozen_until_month=date(2026, 9, 1))
    body = (open_deal.activities.filter(kind='stage_change')
            .order_by('-created_at').first().body)
    assert 'сентября 2026' in body


UNFREEZE_URL = '/api/admin/renewals/{}/unfreeze'


@pytest.mark.django_db
def test_unfreeze_returns_to_computed_auto_stage(admin_client, open_deal, frozen_stage):
    """«Вернуть в работу» ставит расчётную авто-стадию и гасит месяц."""
    from apps.renewals import repository as repo
    repo.move_deal(open_deal.id, frozen_stage.id, None, None,
                   frozen_until_month=date(2026, 9, 1))

    resp = admin_client.post(UNFREEZE_URL.format(open_deal.id))
    assert resp.status_code == 200

    open_deal.refresh_from_db()
    assert open_deal.stage.is_auto is True
    assert open_deal.stage_id != frozen_stage.id
    assert open_deal.frozen_until_month is None
    assert open_deal.activities.filter(kind='system').exists()


@pytest.mark.django_db
def test_unfreeze_is_noop_when_not_frozen(admin_client, open_deal):
    """Сделка не на «Заморожен» — 409, стадия не меняется."""
    before = open_deal.stage_id
    resp = admin_client.post(UNFREEZE_URL.format(open_deal.id))
    assert resp.status_code == 409
    open_deal.refresh_from_db()
    assert open_deal.stage_id == before


@pytest.mark.django_db
def test_unfreeze_forbidden_for_teacher(teacher_client, open_deal):
    """RBAC: учителю раздел продлений недоступен."""
    resp = teacher_client.post(UNFREEZE_URL.format(open_deal.id))
    assert resp.status_code in (401, 403)


@pytest.mark.django_db
def test_unfreeze_not_found_for_missing_deal(admin_client):
    """Несуществующая сделка — 404, а не 409 (отличаем «нет сделки» от «не заморожена»)."""
    resp = admin_client.post(UNFREEZE_URL.format(0))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_frozen_month_visible_in_api(admin_client, open_deal, frozen_stage):
    """Месяц заморозки видно в детали сделки, на карточке доски и в списке."""
    from apps.renewals import repository as repo
    repo.move_deal(open_deal.id, frozen_stage.id, None, None,
                   frozen_until_month=date(2026, 9, 1))

    detail = admin_client.get(f'/api/admin/renewals/{open_deal.id}').json()
    assert detail['frozen_until_month'] == '2026-09-01'

    board = admin_client.get('/api/admin/renewals?view=board').json()
    frozen_col = next(c for c in board['columns'] if c['key'] == 'frozen')
    assert frozen_col['cards'][0]['frozen_until_month'] == '2026-09-01'
