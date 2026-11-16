"""
Ссылка на запись урока: обязательность по правилу школы с исключениями
(спека 2026-11-16).

обязательна = правило XOR (группа в исключениях ИЛИ ЗАПИСЫВАЮЩИЙ преподаватель
в исключениях). Проверяет сервис до записи: при отказе ни урок, ни отзыв не
пишутся. Непустая ссылка проверяется на формат всегда — и когда она необязательна.
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.feedback.models import LessonFeedback
from apps.lessons.exceptions import RECORD_URL_REQUIRED_MSG
from apps.lessons.record_url_rule import replace_rule
from apps.teacher_spa.tests.test_submit_feedback import (
    DATE, GROUP, URL, _client, _group_lessons, _payload,
)

pytestmark = pytest.mark.django_db

RECORD_URL_INVALID = 'Нужна ссылка на запись: адрес вида https://…'


@pytest.fixture
def cleanup(group_fixture, membership_fixture):
    """Уборка за тестом (как в test_submit_feedback): отзыв — до урока."""
    yield
    with connection.cursor() as cur:
        cur.execute('DELETE FROM lesson_feedback WHERE group_id = %s', [group_fixture])
        cur.execute(
            'DELETE FROM payroll WHERE lesson_id IN (SELECT id FROM lessons WHERE group_id = %s)',
            [group_fixture])
        cur.execute(
            'DELETE FROM lesson_attendance WHERE lesson_id IN '
            '(SELECT id FROM lessons WHERE group_id = %s)', [group_fixture])
        cur.execute('DELETE FROM planned_lessons WHERE group_id = %s', [group_fixture])
        cur.execute('DELETE FROM lessons WHERE group_id = %s', [group_fixture])
        cur.execute('UPDATE group_memberships SET lessons_done = 0 WHERE id = %s',
                    [membership_fixture])


@pytest.fixture(autouse=True)
def reset_rule(group_fixture, teacher_fixture, sub_teacher_fixture):
    """Исключения ссылаются на группу и преподавателей фикстур: сырой DELETE в их
    уборке упал бы на FK. Зависимость от фикстур гарантирует, что правило
    сбрасывается РАНЬШЕ, чем они удаляются."""
    yield
    _rule(required=True)


@pytest.fixture
def substitution(teacher_fixture, sub_teacher_fixture, group_fixture):
    """Плановое занятие группы на DATE, назначенное заменяющему преподавателю."""
    owner_id, _ = teacher_fixture
    sub_id, _ = sub_teacher_fixture
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
            'scheduled_time, teacher_id, substitute_teacher_id, status, created_at, updated_at) '
            "VALUES (%s, 1, 1, %s, '10:00', %s, %s, 'pending', NOW(), NOW())",
            [group_fixture, DATE, owner_id, sub_id],
        )
    return owner_id, sub_id


def _rule(required=True, teacher_ids=(), group_ids=()):
    replace_rule(required=required, teacher_ids=teacher_ids, group_ids=group_ids,
                 updated_by=None)


def _assert_url_required(resp, group_id):
    assert resp.status_code == 400, resp.json()
    assert resp.json() == {'error': 'Validation failed',
                           'details': {'recordUrl': [RECORD_URL_REQUIRED_MSG]}}
    assert not _group_lessons(group_id).exists()
    assert not LessonFeedback.objects.filter(group_id=group_id).exists()


def _assert_recorded_without_url(resp, group_id):
    assert resp.status_code == 200, resp.json()
    assert resp.json()['success'] is True
    assert _group_lessons(group_id).get().record_url is None


# ---------------------------------------------------------------------------
# Правило по умолчанию: ссылка обязательна
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('record_url', [None, '', '   '])
def test_default_rule_missing_url_400(
    record_url, account_fixture, group_fixture, student_fixture, cleanup,
):
    """Нет ключа, пустая строка, одни пробелы — одна и та же ошибка поля recordUrl."""
    resp = _client(account_fixture).post(URL, _payload(recordUrl=record_url), format='json')
    _assert_url_required(resp, group_fixture)


def test_default_rule_explicit_null_400(account_fixture, group_fixture, student_fixture, cleanup):
    body = _payload()
    body['recordUrl'] = None
    resp = _client(account_fixture).post(URL, body, format='json')
    _assert_url_required(resp, group_fixture)


# ---------------------------------------------------------------------------
# Правило и исключения
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('record_url', [None, '', '   '])
def test_rule_off_missing_url_records_lesson(
    record_url, account_fixture, group_fixture, student_fixture, cleanup,
):
    _rule(required=False)
    resp = _client(account_fixture).post(URL, _payload(recordUrl=record_url), format='json')
    _assert_recorded_without_url(resp, group_fixture)
    # Отзыв пишется как обычно — необязательность ссылки ничего больше не снимает.
    assert LessonFeedback.objects.filter(group_id=group_fixture).exists()


def test_rule_off_explicit_null_records_lesson(
    account_fixture, group_fixture, student_fixture, cleanup,
):
    _rule(required=False)
    body = _payload()
    body['recordUrl'] = None
    resp = _client(account_fixture).post(URL, body, format='json')
    _assert_recorded_without_url(resp, group_fixture)


def test_rule_off_url_still_saved(account_fixture, group_fixture, student_fixture, cleanup):
    """Необязательная ссылка, если её прислали, сохраняется (срезанная по краям)."""
    _rule(required=False)
    resp = _client(account_fixture).post(
        URL, _payload(recordUrl='  https://vk.com/video-1_2 '), format='json')
    assert resp.status_code == 200, resp.json()
    assert _group_lessons(group_fixture).get().record_url == 'https://vk.com/video-1_2'


def test_group_exception_lifts_requirement(
    account_fixture, group_fixture, student_fixture, cleanup,
):
    _rule(required=True, group_ids=[group_fixture])
    resp = _client(account_fixture).post(URL, _payload(recordUrl=None), format='json')
    _assert_recorded_without_url(resp, group_fixture)


def test_teacher_exception_lifts_requirement(
    teacher_fixture, account_fixture, group_fixture, student_fixture, cleanup,
):
    teacher_id, _ = teacher_fixture
    _rule(required=True, teacher_ids=[teacher_id])
    resp = _client(account_fixture).post(URL, _payload(recordUrl=None), format='json')
    _assert_recorded_without_url(resp, group_fixture)


def test_both_exceptions_are_one_exception(
    teacher_fixture, account_fixture, group_fixture, student_fixture, cleanup,
):
    """Совпадение и по группе, и по преподавателю не гасит исключение."""
    teacher_id, _ = teacher_fixture
    _rule(required=True, teacher_ids=[teacher_id], group_ids=[group_fixture])
    resp = _client(account_fixture).post(URL, _payload(recordUrl=None), format='json')
    _assert_recorded_without_url(resp, group_fixture)


def test_rule_off_group_exception_requires(
    account_fixture, group_fixture, student_fixture, cleanup,
):
    _rule(required=False, group_ids=[group_fixture])
    resp = _client(account_fixture).post(URL, _payload(recordUrl=None), format='json')
    _assert_url_required(resp, group_fixture)


def test_rule_off_teacher_exception_requires(
    teacher_fixture, account_fixture, group_fixture, student_fixture, cleanup,
):
    teacher_id, _ = teacher_fixture
    _rule(required=False, teacher_ids=[teacher_id])
    resp = _client(account_fixture).post(URL, _payload(recordUrl=None), format='json')
    _assert_url_required(resp, group_fixture)


def test_other_teacher_exception_does_not_apply(
    sub_teacher_fixture, account_fixture, group_fixture, student_fixture, cleanup,
):
    sub_id, _ = sub_teacher_fixture
    _rule(required=True, teacher_ids=[sub_id])
    resp = _client(account_fixture).post(URL, _payload(recordUrl=None), format='json')
    _assert_url_required(resp, group_fixture)


@pytest.mark.parametrize('record_url', ['не ссылка', 'vk.com/video-1_2', 'http://localhost'])
def test_invalid_url_rejected_even_when_optional(
    record_url, account_fixture, group_fixture, student_fixture, cleanup,
):
    _rule(required=False)
    resp = _client(account_fixture).post(URL, _payload(recordUrl=record_url), format='json')
    assert resp.status_code == 400
    assert resp.json()['details']['recordUrl'] == [RECORD_URL_INVALID]
    assert not _group_lessons(group_fixture).exists()


# ---------------------------------------------------------------------------
# Замена: правило смотрит на ЗАПИСЫВАЮЩЕГО
# ---------------------------------------------------------------------------

def test_substitute_teacher_exception_applies(
    substitution, sub_account_fixture, group_fixture, student_fixture, cleanup,
):
    _owner_id, sub_id = substitution
    _rule(required=True, teacher_ids=[sub_id])
    resp = _client(sub_account_fixture).post(URL, _payload(recordUrl=None), format='json')
    _assert_recorded_without_url(resp, group_fixture)
    assert _group_lessons(group_fixture).get().teacher_id == sub_id


def test_owner_exception_does_not_cover_substitute(
    substitution, sub_account_fixture, group_fixture, student_fixture, cleanup,
):
    owner_id, _sub_id = substitution
    _rule(required=True, teacher_ids=[owner_id])
    resp = _client(sub_account_fixture).post(URL, _payload(recordUrl=None), format='json')
    _assert_url_required(resp, group_fixture)


def test_unassigned_teacher_gets_403_not_url_error(
    sub_teacher_fixture, sub_account_fixture, group_fixture, student_fixture, cleanup,
):
    """Чужая группа без назначения — 403 «не назначено», а не 400 про ссылку:
    правило проверяется только для того, кому урок вообще можно записать."""
    resp = _client(sub_account_fixture).post(URL, _payload(recordUrl=None), format='json')
    assert resp.status_code == 403, resp.json()
    assert 'recordUrl' not in str(resp.json())
    assert not _group_lessons(group_fixture).exists()


# ---------------------------------------------------------------------------
# Выдача групп преподавателю: recordUrlRequired
# ---------------------------------------------------------------------------

def _get_data(account_id):
    resp = _client(account_id).post('/api/getData', {}, format='json')
    assert resp.status_code == 200, resp.json()
    return resp.json()['data']


def test_get_data_default_required(
    account_fixture, group_fixture, student_fixture, membership_fixture,
):
    assert _get_data(account_fixture)[GROUP]['recordUrlRequired'] is True


def test_get_data_teacher_exception(
    teacher_fixture, account_fixture, group_fixture, student_fixture, membership_fixture,
):
    teacher_id, _ = teacher_fixture
    _rule(required=True, teacher_ids=[teacher_id])
    assert _get_data(account_fixture)[GROUP]['recordUrlRequired'] is False


def test_get_data_rule_off_group_exception(
    account_fixture, group_fixture, student_fixture, membership_fixture,
):
    _rule(required=False, group_ids=[group_fixture])
    assert _get_data(account_fixture)[GROUP]['recordUrlRequired'] is True
    _rule(required=False)
    assert _get_data(account_fixture)[GROUP]['recordUrlRequired'] is False


def test_get_all_data_computed_for_substitute(
    substitution, teacher_fixture, sub_account_fixture,
    group_fixture, student_fixture, cleanup,
):
    """В чужой группе поле посчитано для СМОТРЯЩЕГО (он же запишет урок), а не
    для владельца группы."""
    owner_id, sub_id = substitution
    _, owner_name = teacher_fixture
    _rule(required=True, teacher_ids=[sub_id])
    resp = _client(sub_account_fixture).post('/api/getAllData', {}, format='json')
    assert resp.status_code == 200, resp.json()
    assert resp.json()['data'][owner_name][GROUP]['recordUrlRequired'] is False

    _rule(required=True, teacher_ids=[owner_id])
    resp = _client(sub_account_fixture).post('/api/getAllData', {}, format='json')
    assert resp.json()['data'][owner_name][GROUP]['recordUrlRequired'] is True
