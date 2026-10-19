"""API-тесты чтения renewals: RBAC + форма ответов board/list."""
import pytest

from apps.renewals import engine

BASE = '/api/admin/renewals'


@pytest.mark.django_db
def test_no_cookie_401(anon_client):
    assert anon_client.get(BASE).status_code == 401


@pytest.mark.django_db
def test_teacher_403(teacher_client):
    assert teacher_client.get(BASE).status_code == 403


@pytest.mark.django_db
def test_manager_board_200(manager_client, make_student, make_direction):
    sid, did = make_student(), make_direction()
    engine.ensure_deal(sid, cycle_no=1)
    resp = manager_client.get(f'{BASE}?view=board')
    assert resp.status_code == 200
    assert 'columns' in resp.json()


@pytest.mark.django_db
def test_manager_list_shape(manager_client, make_student, make_direction):
    sid, did = make_student(), make_direction()
    engine.ensure_deal(sid, cycle_no=1)
    resp = manager_client.get(f'{BASE}?view=list&page=1&page_size=10')
    body = resp.json()
    assert set(body) >= {'rows', 'total', 'page', 'page_size'}


@pytest.mark.django_db
def test_invalid_sort_400(manager_client):
    assert manager_client.get(f'{BASE}?view=list&sort_by=hax').status_code == 400


@pytest.mark.django_db
def test_non_numeric_page_400(manager_client):
    """Нечисловой page не должен ронять 500 — валидируем на входе → 400."""
    assert manager_client.get(f'{BASE}?view=list&page=abc').status_code == 400


@pytest.mark.django_db
def test_non_numeric_filter_400(manager_client):
    """Нечисловой числовой фильтр (уходит в SQL как int) → 400, не 500."""
    assert manager_client.get(
        f'{BASE}?view=board&filter[direction_id]=abc').status_code == 400


@pytest.mark.django_db
def test_column_cards_show_more(manager_client, make_student):
    """«Показать ещё»: GET /columns/:stage_id?offset= догружает карточки колонки."""
    from apps.renewals.models import RenewalDeal
    deal = engine.ensure_deal(make_student('__renew_test_student_a__'), cycle_no=1)
    engine.ensure_deal(make_student('__renew_test_student_b__'), cycle_no=1)
    stage_id = RenewalDeal.objects.get(id=deal.id).stage_id

    resp = manager_client.get(f'{BASE}/columns/{stage_id}?offset=1&filter[student]=__renew_test_student_')
    assert resp.status_code == 200
    data = resp.json()
    assert data['count'] == 2  # обе сделки в этой стадии
    assert len(data['cards']) == 1  # offset=1 → вторая карточка


@pytest.mark.django_db
def test_column_cards_student_search(manager_client, make_student):
    """filter[student] фильтрует карточки колонки по имени ученика (ILIKE)."""
    from apps.renewals.models import RenewalDeal
    deal = engine.ensure_deal(make_student('__renew_search_Иванов__'), cycle_no=1)
    engine.ensure_deal(make_student('__renew_search_Петров__'), cycle_no=1)
    stage_id = RenewalDeal.objects.get(id=deal.id).stage_id

    resp = manager_client.get(
        f'{BASE}/columns/{stage_id}?filter[student]=иванов')
    assert resp.status_code == 200
    data = resp.json()
    assert data['count'] == 1
    assert len(data['cards']) == 1
    assert 'Иванов' in data['cards'][0]['student_name']


@pytest.mark.django_db
def test_column_cards_teacher_403(teacher_client):
    assert teacher_client.get(f'{BASE}/columns/1').status_code == 403


@pytest.mark.django_db
def test_column_cards_non_numeric_offset_400(manager_client):
    assert manager_client.get(f'{BASE}/columns/1?offset=abc').status_code == 400


@pytest.mark.django_db
def test_board_excludes_terminal_columns(manager_client, make_student, make_direction):
    """Won/lost-колонки на доске не нужны: закрытие — через зоны, архив — списком."""
    sid, did = make_student(), make_direction()
    engine.ensure_deal(sid, cycle_no=1)
    resp = manager_client.get(f'{BASE}?view=board')
    kinds = {c['kind'] for c in resp.json()['columns']}
    assert 'won' not in kinds and 'lost' not in kinds


@pytest.mark.django_db
def test_board_card_has_ids_and_debt(manager_client, make_student, make_direction,
                                     make_teacher):
    """Карточке нужны student_id (форма оплаты), directions (справочно) и debt."""
    from django.db import connection
    sid, did, tid = make_student(), make_direction('__renew_card_dir__'), make_teacher()
    with connection.cursor() as cur:
        cur.execute("INSERT INTO groups (name, direction_id, teacher_id, is_individual, active, created_at, "
                    "lesson_number_offset) "
                    "VALUES ('__bc_group__', %s, %s, false, true, now(), 0) RETURNING id", [did, tid])
        gid = cur.fetchone()[0]
        cur.execute("INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                    "VALUES (%s,%s,0,true)", [gid, sid])
    try:
        engine.ensure_deal(sid, cycle_no=1)
        resp = manager_client.get(f'{BASE}?view=board')
        cards = [c for col in resp.json()['columns'] for c in col['cards']
                 if c['id'] and c['student_name'] == '__renew_test_student__']
        assert cards, 'карточка сделки не попала на доску'
        card = cards[0]
        assert card['student_id'] == sid
        # направления ученика — справочный список активных membership
        assert [d['name'] for d in card['directions']] == ['__renew_card_dir__']
        assert card['debt'] is False  # оплат и посещений нет — баланс ровно 0, долга нет
        # 0 уроков цикла — цикл не завершён (нужно фронту, чтобы скрыть
        # зону «Продлён» при drag'е, см. RenewalBoard.tsx)
        assert card['cycle_completed'] is False
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM renewal_activity WHERE deal_id IN '
                        '(SELECT id FROM renewal_deal WHERE student_id = %s)', [sid])
            cur.execute('DELETE FROM renewal_deal WHERE student_id = %s', [sid])
            cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [gid])
            cur.execute('DELETE FROM groups WHERE id = %s', [gid])


@pytest.mark.django_db
def test_board_card_cycle_completed_true_after_4_lessons(
        manager_client, make_student, make_direction, make_teacher, make_attendance):
    """Как только 4 урока цикла отработаны — карточка несёт cycle_completed=True."""
    from django.db import connection
    sid, did, tid = make_student(), make_direction('__renew_card_dir2__'), make_teacher()
    with connection.cursor() as cur:
        cur.execute("INSERT INTO groups (name, direction_id, teacher_id, is_individual, active, created_at, "
                    "lesson_number_offset) "
                    "VALUES ('__bc_group2__', %s, %s, false, true, now(), 0) RETURNING id", [did, tid])
        gid = cur.fetchone()[0]
        cur.execute("INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                    "VALUES (%s,%s,0,true)", [gid, sid])
    try:
        from apps.renewals.models import RenewalDeal
        engine.ensure_deal(sid, cycle_no=1)
        make_attendance(sid, gid, tid, count=4)
        deal = RenewalDeal.objects.get(student_id=sid)
        engine.sync_lesson_stage(sid)
        resp = manager_client.get(f'{BASE}?view=board')
        cards = [c for col in resp.json()['columns'] for c in col['cards'] if c['id'] == deal.id]
        assert cards, 'карточка сделки не попала на доску'
        assert cards[0]['cycle_completed'] is True
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id IN '
                        '(SELECT id FROM lessons WHERE group_id = %s)', [gid])
            cur.execute('DELETE FROM lessons WHERE group_id = %s', [gid])
            cur.execute('DELETE FROM renewal_activity WHERE deal_id IN '
                        '(SELECT id FROM renewal_deal WHERE student_id = %s)', [sid])
            cur.execute('DELETE FROM renewal_deal WHERE student_id = %s', [sid])
            cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [gid])
            cur.execute('DELETE FROM groups WHERE id = %s', [gid])


@pytest.mark.django_db
def test_deal_computed_lesson_from_cycle(manager_client, make_student, make_direction,
                                         make_teacher, make_payment, make_attendance):
    """attended=3 при cycle_no=1 → «урок 4 из 4», а не (3 % 4)+1 без учёта цикла."""
    from django.db import connection
    sid, did, tid = make_student(), make_direction(), make_teacher()
    make_payment(sid, did, lessons=8)
    with connection.cursor() as cur:
        cur.execute("INSERT INTO groups (name, direction_id, teacher_id, is_individual, active, created_at, "
                    "lesson_number_offset) "
                    "VALUES ('__ar_group__', %s, %s, false, true, now(), 0) RETURNING id", [did, tid])
        gid = cur.fetchone()[0]
        cur.execute("INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                    "VALUES (%s,%s,0,true)", [gid, sid])
    try:
        make_attendance(sid, gid, tid, count=3)
        deal = engine.ensure_deal(sid, cycle_no=1)
        body = manager_client.get(f'{BASE}/{deal.id}').json()
        assert body['lesson_in_cycle'] == 4
        assert body['cycle_completed'] is False
        assert 'due_at' in body and 'debt' in body and 'directions' in body
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM renewal_activity WHERE deal_id IN '
                        '(SELECT id FROM renewal_deal WHERE student_id = %s)', [sid])
            cur.execute('DELETE FROM renewal_deal WHERE student_id = %s', [sid])
            cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [gid])
            cur.execute('DELETE FROM groups WHERE id = %s', [gid])


@pytest.mark.django_db
def test_board_card_has_balance_in_lessons(manager_client, make_student, make_direction,
                                           make_teacher, make_attendance):
    """
    Карточке нужен balance (в УРОКАХ, не в рублях) — из него карточка канбана
    строит бейдж «Долг N ур.». Два посещения без оплат = баланс −2.
    """
    from django.db import connection
    sid = make_student()
    did = make_direction('__renew_balance_dir__')
    tid = make_teacher()
    with connection.cursor() as cur:
        cur.execute("INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
                    "active, created_at, lesson_number_offset) "
                    "VALUES ('__bal_group__', %s, %s, false, true, now(), 0) RETURNING id",
                    [did, tid])
        gid = cur.fetchone()[0]
        cur.execute("INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                    "VALUES (%s,%s,0,true)", [gid, sid])
    try:
        make_attendance(sid, gid, tid, count=2)
        engine.ensure_deal(sid, cycle_no=1)

        resp = manager_client.get(f'{BASE}?view=board')
        cards = [c for col in resp.json()['columns'] for c in col['cards']
                 if c['student_id'] == sid]
        assert cards, 'карточка сделки не попала на доску'
        card = cards[0]
        assert card['balance'] == -2
        assert card['debt'] is True
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM renewal_activity WHERE deal_id IN '
                        '(SELECT id FROM renewal_deal WHERE student_id = %s)', [sid])
            cur.execute('DELETE FROM renewal_deal WHERE student_id = %s', [sid])
            cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [gid])
            cur.execute('DELETE FROM groups WHERE id = %s', [gid])
