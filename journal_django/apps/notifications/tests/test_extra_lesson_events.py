"""
Точечные уведомления из доп.уроков: назначение и отмена ставят сообщение
в очередь той же транзакцией, что и сама доменная операция.

Данные строим РЕАЛЬНЫМИ фикстурами раздела доп.уроков (apps/extra_lessons/
tests/conftest.py) — пропуск, членство, оплата и группа заводятся ровно так же,
как в его собственных тестах. Свои фабрики здесь не изобретаем: иначе тест
проверял бы выдуманный объектный граф, а не тот, что приходит из продакшена.
Фикстуры импортированы поимённо (conftest соседнего пакета pytest сам не
подхватывает); `django_db_setup` НЕ импортируем — уведомления живут в своей
тестовой БД.

_request_dispatch патчим: очередь нас интересует, поход в Celery — нет.
"""
from __future__ import annotations

import pytest
from django.test import override_settings

from apps.extra_lessons import services as extra_services
from apps.extra_lessons.models import AbsenceResolution
from apps.extra_lessons.tests.conftest import (  # noqa: F401 — фикстуры pytest
    direction_fixture, group_fixture, membership_fixture, missed_lesson_fixture,
    student_fixture, teacher_fixture,
)
from apps.notifications import services as notif_services
from apps.notifications.constants import (
    CHANNEL_DM, CHANNEL_GROUP, KIND_MAKEUP_ASSIGNED, KIND_MAKEUP_CANCELLED,
)
from apps.notifications.models import NotificationMessage, TelegramRecipient, TelegramUser

_CHAT_ID = 4242
_GENERAL_CHAT_ID = -100777


@pytest.fixture
def linked_recipient(teacher_fixture):  # noqa: F811 — фикстура из extra_lessons
    """Привязка Telegram для преподавателя доп.урока — чтобы появилась и личка.

    За собой убирает раньше, чем teacher_fixture удалит преподавателя: и привязка,
    и поставленные сообщения ссылаются на teachers по реальному FK, а сырое
    DELETE в фикстуре преподавателя ничего не каскадит.
    """
    tg = TelegramUser.objects.create(
        chat_id=_CHAT_ID, username='el_teacher', full_name='Преподаватель')
    TelegramRecipient.objects.create(teacher_id=teacher_fixture, telegram_user=tg)
    yield teacher_fixture
    NotificationMessage.objects.filter(recipient_teacher_id=teacher_fixture).delete()
    TelegramRecipient.objects.filter(teacher_id=teacher_fixture).delete()
    TelegramUser.objects.filter(id=tg.id).delete()


@pytest.fixture
def extra_resolutions_cleanup(student_fixture, group_fixture):  # noqa: F811
    """Резолюции kind='extra' не привязаны к пропуску, поэтому missed_lesson_fixture
    их не подчищает — сносим сами, раньше удаления группы и преподавателя."""
    yield
    AbsenceResolution.objects.filter(student_id=student_fixture).delete()


@pytest.fixture
def no_dispatch(monkeypatch):
    """Очередь проверяем, воркер не дёргаем."""
    monkeypatch.setattr(notif_services, '_request_dispatch', lambda: None)


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=_GENERAL_CHAT_ID)
def test_assign_makeup_enqueues_message(
    no_dispatch, linked_recipient, teacher_fixture, student_fixture,  # noqa: F811
    missed_lesson_fixture,  # noqa: F811
):
    """Назначение отработки → сообщение в очереди, с настоящими данными в тексте."""
    result = extra_services.create_assignment({
        'missed_lesson_id': missed_lesson_fixture,
        'teacher_id': teacher_fixture,
        'student_ids': [student_fixture],
        'scheduled_date': '2026-04-08',
        'scheduled_time': '14:30',
        'duration_minutes': 60,
    }, None)

    rows = NotificationMessage.objects.filter(kind=KIND_MAKEUP_ASSIGNED).order_by('channel')
    assert [r.channel for r in rows] == [CHANNEL_DM, CHANNEL_GROUP]
    assert {r.chat_id for r in rows} == {_CHAT_ID, _GENERAL_CHAT_ID}
    assert {r.source_kind for r in rows} == {'absence_resolution'}
    assert {r.source_id for r in rows} == {result['resolution_ids'][0]}

    text = rows[0].text
    assert '__el_test_group__' in text          # группа пропуска
    assert '__el_test_dir__' in text            # направление
    assert '__el_test_student__' in text        # ученик
    assert '__el_test_teacher__' in text        # преподаватель
    assert '08.04' in text and '14:30' in text  # дата и время
    assert 'None' not in text


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=_GENERAL_CHAT_ID)
def test_cancel_makeup_enqueues_message(
    no_dispatch, linked_recipient, teacher_fixture, student_fixture,  # noqa: F811
    missed_lesson_fixture,  # noqa: F811
):
    """Отмена → сообщение «отменён» с параметрами, которые отменой стираются."""
    result = extra_services.create_assignment({
        'missed_lesson_id': missed_lesson_fixture,
        'teacher_id': teacher_fixture,
        'student_ids': [student_fixture],
        'scheduled_date': '2026-04-08',
        'scheduled_time': '14:30',
        'duration_minutes': 60,
    }, None)
    resolution_id = result['resolution_ids'][0]

    extra_services.cancel_assignment(resolution_id, None)

    rows = NotificationMessage.objects.filter(kind=KIND_MAKEUP_CANCELLED).order_by('channel')
    assert [r.channel for r in rows] == [CHANNEL_DM, CHANNEL_GROUP]
    text = rows[0].text
    assert 'отменён' in text
    assert '__el_test_group__' in text
    assert '__el_test_student__' in text
    # Данные назначения собраны ДО перевода в pending (иначе тут были бы None).
    assert '08.04' in text and '14:30' in text
    assert 'None' not in text


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=_GENERAL_CHAT_ID)
def test_assign_extra_beyond_course_enqueues_message(
    no_dispatch, linked_recipient, teacher_fixture, student_fixture,  # noqa: F811
    group_fixture, membership_fixture, extra_resolutions_cleanup,  # noqa: F811
):
    """Доп.урок СВЕРХ курса (kind='extra'): группа берётся из самой резолюции,
    а текст обязан сказать «сверх курса», а не «отработка»."""
    result = extra_services.create_extra_assignment({
        'group_id': group_fixture,
        'teacher_id': teacher_fixture,
        'student_ids': [student_fixture],
        'scheduled_date': '2026-04-09',
        'scheduled_time': '16:00',
        'duration_minutes': 60,
    }, None)
    assert result['kind'] == 'extra'

    row = NotificationMessage.objects.get(
        kind=KIND_MAKEUP_ASSIGNED, channel=CHANNEL_DM)
    assert 'сверх курса' in row.text
    assert '__el_test_group__' in row.text
    assert '09.04' in row.text and '16:00' in row.text


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=_GENERAL_CHAT_ID)
def test_assignment_notification_is_idempotent_per_resolution(
    no_dispatch, linked_recipient, teacher_fixture, student_fixture,  # noqa: F811
    group_fixture, membership_fixture, extra_resolutions_cleanup,  # noqa: F811
):
    """Повторная постановка того же уведомления не плодит строк: dedup_key несёт
    id резолюции и дату назначения."""
    result = extra_services.create_extra_assignment({
        'group_id': group_fixture,
        'teacher_id': teacher_fixture,
        'student_ids': [student_fixture],
        'scheduled_date': '2026-04-09',
        'scheduled_time': '16:00',
        'duration_minutes': 60,
    }, None)
    resolution_id = result['resolution_ids'][0]

    extra_services._notify_assigned(resolution_id)

    assert NotificationMessage.objects.filter(kind=KIND_MAKEUP_ASSIGNED).count() == 2


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=_GENERAL_CHAT_ID)
def test_drop_extra_for_present_student_enqueues_cancelled(
    no_dispatch, linked_recipient, teacher_fixture, student_fixture,  # noqa: F811
    group_fixture, missed_lesson_fixture, extra_resolutions_cleanup,  # noqa: F811
):
    """Ученик пришёл на урок, за который заранее назначен доп.урок — назначение
    снимается. За ним стоит забронированное время преподавателя, поэтому снятие
    ОБЯЗАНО сопровождаться уведомлением: иначе человек придёт на несуществующий
    урок.

    Текст собирается из полей резолюции, которые удаление стирает, — проверяем,
    что в сообщении есть и дата, и время, и что 'None' туда не просочился.

    Назначение заводим через repository.create_extra_direct: урок уже проведён,
    поэтому штатный create_extra_assignment увёл бы его в makeup, а нам нужен
    именно extra за номер существующего урока.
    """
    from apps.extra_lessons import repository as extra_repo

    resolution_id = extra_repo.create_extra_direct(
        group_id=group_fixture, student_id=student_fixture,
        assigned_teacher_id=teacher_fixture,
        scheduled_date='2026-04-08', scheduled_time='14:30',
        duration_minutes=60, target_lesson_number=1)

    dropped = extra_services.drop_extra_for_present_students(
        missed_lesson_fixture, [student_fixture])

    assert dropped == 1
    assert not AbsenceResolution.objects.filter(id=resolution_id).exists()

    rows = NotificationMessage.objects.filter(kind=KIND_MAKEUP_CANCELLED).order_by('channel')
    assert [r.channel for r in rows] == [CHANNEL_DM, CHANNEL_GROUP]
    text = rows[0].text
    assert 'отменён' in text
    assert '__el_test_student__' in text
    # Поля взяты ДО удаления — иначе здесь были бы None.
    assert '08.04' in text and '14:30' in text
    assert 'None' not in text
