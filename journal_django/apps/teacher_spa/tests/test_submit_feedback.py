"""
Отзыв по уроку при записи урока преподавателем (этап 3 Eduson, раздел A).

Урок без отзыва преподаватель сохранить не может: блок feedback обязателен,
каждое поле валидируется сервером, а урок и отзыв пишутся одной транзакцией.
Повторная отправка того же занятия второго отзыва не создаёт.
"""
from __future__ import annotations

import datetime

import pytest
from django.db import IntegrityError, connection
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.feedback.models import LessonFeedback
from apps.lessons.models import Lesson
from apps.teacher_spa.tests.payloads import VALID_FEEDBACK, VALID_RECORD_URL

pytestmark = pytest.mark.django_db

URL = '/api/submitLesson'
GROUP = '__spa_test_group__ пн 10:00'
STUDENT = '__spa_test_student__'
DATE = '2026-06-10'


def _client(account_id: int) -> APIClient:
    from apps.accounts.models import Account

    user = Account.objects.get(pk=account_id)
    refresh = RefreshToken.for_user(user)
    refresh['token_version'] = user.token_version
    client = APIClient()
    client.cookies['access'] = str(refresh.access_token)
    return client


def _payload(feedback=..., **extra) -> dict:
    """Тело запроса; feedback=... — валидный отзыв, None — без ключа feedback.
    recordUrl по умолчанию валидный; recordUrl=None — без ключа."""
    body = {
        'group': GROUP,
        'date': DATE,
        'students': [{'name': STUDENT, 'present': True}],
        'recordUrl': VALID_RECORD_URL,
        **extra,
    }
    if body['recordUrl'] is None:
        del body['recordUrl']
    if feedback is ...:
        body['feedback'] = dict(VALID_FEEDBACK)
    elif feedback is not None:
        body['feedback'] = feedback
    return body


def _feedback_with(**override) -> dict:
    """Валидный отзыв с подменой полей; значение ... — убрать поле совсем."""
    fb = dict(VALID_FEEDBACK)
    for key, value in override.items():
        if value is ...:
            fb.pop(key)
        else:
            fb[key] = value
    return fb


def _group_lessons(group_id: int):
    return Lesson.objects.filter(group_id=group_id)


@pytest.fixture
def cleanup(group_fixture, membership_fixture):
    """Уборка за тестом: отзывы, уроки и план группы, прогресс ученика.

    Отзыв удаляется ДО урока: lesson_feedback.lesson_id ссылается на lessons."""
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


# ---------------------------------------------------------------------------
# Успешная запись
# ---------------------------------------------------------------------------

def test_lesson_and_feedback_created_together(
    teacher_fixture, account_fixture, group_fixture, student_fixture, cleanup,
):
    teacher_id, _ = teacher_fixture
    before = timezone.now()
    resp = _client(account_fixture).post(URL, _payload(feedback={
        'understanding': 3, 'interest': 10, 'techProblems': True,
        'comment': '  Ребёнок отвлекался, к концу втянулся  ',
    }), format='json')
    assert resp.status_code == 200, resp.json()
    assert resp.json()['success'] is True

    lesson = _group_lessons(group_fixture).get()
    fb = LessonFeedback.objects.get(lesson=lesson)
    assert fb.group_id == group_fixture
    assert fb.lesson_date == datetime.date(2026, 6, 10) == lesson.lesson_date
    assert fb.teacher_id == teacher_id == lesson.teacher_id
    assert fb.took_place is True
    assert (fb.understanding, fb.interest, fb.tech_problems) == (3, 10, True)
    # Пробелы по краям — мусор ввода, в отзыв не попадают.
    assert fb.comment == 'Ребёнок отвлекался, к концу втянулся'
    assert before <= fb.submitted_at <= timezone.now()


def test_tech_problems_false_is_stored_as_false(
    account_fixture, group_fixture, student_fixture, cleanup,
):
    resp = _client(account_fixture).post(
        URL, _payload(feedback=_feedback_with(techProblems=False, understanding=1, interest=1)),
        format='json')
    assert resp.status_code == 200, resp.json()
    fb = LessonFeedback.objects.get(group_id=group_fixture)
    assert (fb.understanding, fb.interest, fb.tech_problems) == (1, 1, False)


def test_record_url_saved_on_lesson(account_fixture, group_fixture, student_fixture, cleanup):
    resp = _client(account_fixture).post(
        URL, _payload(recordUrl='https://video.example.test/rec/2'), format='json')
    assert resp.status_code == 200, resp.json()
    assert _group_lessons(group_fixture).get().record_url == 'https://video.example.test/rec/2'


def test_record_url_is_trimmed(account_fixture, group_fixture, student_fixture, cleanup):
    resp = _client(account_fixture).post(
        URL, _payload(recordUrl='  https://vk.com/video-1_2  '), format='json')
    assert resp.status_code == 200, resp.json()
    assert _group_lessons(group_fixture).get().record_url == 'https://vk.com/video-1_2'


@pytest.mark.parametrize('record_url', [
    None,                      # ключа нет совсем
    '',
    '   ',
    'запись урока',
    'vk.com/video-1_2',        # без схемы — не ссылка
    'ftp://files.example.test/rec.mp4',
    'javascript:alert(1)',
    'https://',
    'http://localhost',        # без домена верхнего уровня
])
def test_record_url_required_and_must_be_link(
    record_url, account_fixture, group_fixture, student_fixture, cleanup,
):
    """Ссылка на запись обязательна и должна быть адресом http(s): иначе 400 с
    ошибкой по полю recordUrl, урок и отзыв не пишутся."""
    resp = _client(account_fixture).post(URL, _payload(recordUrl=record_url), format='json')
    assert resp.status_code == 400, resp.json()
    assert 'recordUrl' in resp.json().get('details', {}), resp.json()
    assert not _group_lessons(group_fixture).exists()
    assert not LessonFeedback.objects.filter(group_id=group_fixture).exists()


def test_substitute_is_feedback_author(
    teacher_fixture, sub_teacher_fixture, sub_account_fixture,
    group_fixture, student_fixture, cleanup,
):
    """Отзыв пишет проводящий урок (заменяющий), а не владелец группы."""
    owner_id, _ = teacher_fixture
    sub_id, _ = sub_teacher_fixture
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
            'scheduled_time, teacher_id, substitute_teacher_id, status, created_at, updated_at) '
            "VALUES (%s, 1, 1, %s, '10:00', %s, %s, 'pending', NOW(), NOW())",
            [group_fixture, DATE, owner_id, sub_id],
        )
    resp = _client(sub_account_fixture).post(URL, _payload(), format='json')
    assert resp.status_code == 200, resp.json()
    fb = LessonFeedback.objects.get(group_id=group_fixture)
    assert fb.teacher_id == sub_id
    assert fb.lesson.teacher_id == sub_id


# ---------------------------------------------------------------------------
# Валидация: 400, ошибки по полям, урок не записан
# ---------------------------------------------------------------------------

def _assert_rejected(resp, group_id: int, *path: str) -> None:
    assert resp.status_code == 400, resp.json()
    details = resp.json()['details']
    node = details
    for key in path:
        assert key in node, details
        node = node[key]
    assert not _group_lessons(group_id).exists()
    assert not LessonFeedback.objects.filter(group_id=group_id).exists()


def test_missing_feedback_rejected(account_fixture, group_fixture, student_fixture, cleanup):
    resp = _client(account_fixture).post(URL, _payload(feedback=None), format='json')
    _assert_rejected(resp, group_fixture, 'feedback')


@pytest.mark.parametrize('value', [None, 'отлично', ['x'], 5])
def test_feedback_not_an_object_rejected(
    account_fixture, group_fixture, student_fixture, cleanup, value,
):
    resp = _client(account_fixture).post(URL, _payload(feedback=value), format='json')
    _assert_rejected(resp, group_fixture, 'feedback')


@pytest.mark.parametrize('field', ['understanding', 'interest'])
@pytest.mark.parametrize('value', [..., None, 0, 11, -3, 5.5, 'abc', '', True])
def test_invalid_score_rejected(
    account_fixture, group_fixture, student_fixture, cleanup, field, value,
):
    resp = _client(account_fixture).post(
        URL, _payload(feedback=_feedback_with(**{field: value})), format='json')
    _assert_rejected(resp, group_fixture, 'feedback', field)


@pytest.mark.parametrize('value', [..., None, '', 'возможно', 2])
def test_tech_problems_required_without_default(
    account_fixture, group_fixture, student_fixture, cleanup, value,
):
    """Да/Нет без значения по умолчанию: пропуск поля — ошибка, а не «Нет»."""
    resp = _client(account_fixture).post(
        URL, _payload(feedback=_feedback_with(techProblems=value)), format='json')
    _assert_rejected(resp, group_fixture, 'feedback', 'techProblems')


@pytest.mark.parametrize('value', [..., None, '', '   ', '\n\t '])
def test_comment_required_non_blank(
    account_fixture, group_fixture, student_fixture, cleanup, value,
):
    resp = _client(account_fixture).post(
        URL, _payload(feedback=_feedback_with(comment=value)), format='json')
    _assert_rejected(resp, group_fixture, 'feedback', 'comment')


def test_too_long_comment_rejected(account_fixture, group_fixture, student_fixture, cleanup):
    resp = _client(account_fixture).post(
        URL, _payload(feedback=_feedback_with(comment='а' * 5001)), format='json')
    _assert_rejected(resp, group_fixture, 'feedback', 'comment')


def test_all_errors_reported_at_once(account_fixture, group_fixture, student_fixture, cleanup):
    """Пустой отзыв → ошибка по каждому полю сразу, чтобы фронт подсветил все."""
    resp = _client(account_fixture).post(URL, _payload(feedback={}), format='json')
    assert resp.status_code == 400
    errors = resp.json()['details']['feedback']
    assert set(errors) == {'understanding', 'interest', 'techProblems', 'comment'}


def test_boundary_scores_accepted(account_fixture, group_fixture, student_fixture, cleanup):
    resp = _client(account_fixture).post(
        URL, _payload(feedback=_feedback_with(understanding=10, interest=1)), format='json')
    assert resp.status_code == 200, resp.json()
    fb = LessonFeedback.objects.get(group_id=group_fixture)
    assert (fb.understanding, fb.interest) == (10, 1)


# ---------------------------------------------------------------------------
# Атомарность
# ---------------------------------------------------------------------------

def test_feedback_db_failure_rolls_back_lesson(
    account_fixture, group_fixture, student_fixture, membership_fixture, lessons_done, cleanup,
):
    """Отзыв не лёг в БД → урока, посещаемости, зарплаты и прогресса тоже нет.

    Сериализатор обходим намеренно: проверяется транзакция, а не валидация.
    Оценку 11 отбивает CHECK lesson_feedback_understanding_range."""
    from apps.teacher_spa import services

    with pytest.raises(IntegrityError):
        services.submit_lesson(account_fixture, {
            'group': GROUP, 'date': DATE,
            'students': [{'name': STUDENT, 'present': True}],
            'feedback': {'understanding': 11, 'interest': 5,
                         'tech_problems': False, 'comment': 'x'},
        })

    assert not _group_lessons(group_fixture).exists()
    with connection.cursor() as cur:
        cur.execute(
            'SELECT COUNT(*) FROM payroll p JOIN lessons l ON l.id = p.lesson_id '
            'WHERE l.group_id = %s', [group_fixture])
        assert cur.fetchone()[0] == 0
    assert lessons_done(group_fixture, student_fixture) == 0.0


# ---------------------------------------------------------------------------
# Идемпотентность
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('explicit_position', [True, False])
def test_repeat_submit_does_not_duplicate_feedback(
    teacher_fixture, account_fixture, group_fixture, student_fixture, cleanup,
    explicit_position,
):
    """Повтор того же занятия → 409; отзыв один и остался первым."""
    teacher_id, _ = teacher_fixture
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
            'scheduled_time, teacher_id, status, created_at, updated_at) '
            "VALUES (%s, 1, 1, %s, '10:00', %s, 'pending', NOW(), NOW()) RETURNING id",
            [group_fixture, DATE, teacher_id],
        )
        planned_id = cur.fetchone()[0]
    extra = {'plannedLessonId': planned_id} if explicit_position else {}
    client = _client(account_fixture)

    first = client.post(URL, _payload(**extra), format='json')
    assert first.status_code == 200, first.json()
    second = client.post(
        URL, _payload(feedback=_feedback_with(comment='Другой текст', understanding=2), **extra),
        format='json')
    assert second.status_code == 409
    assert second.json()['code'] == 'lesson_already_recorded'

    assert _group_lessons(group_fixture).count() == 1
    fb = LessonFeedback.objects.get(group_id=group_fixture)
    assert fb.comment == VALID_FEEDBACK['comment']
    assert fb.understanding == VALID_FEEDBACK['understanding']
