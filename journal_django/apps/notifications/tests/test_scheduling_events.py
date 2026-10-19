"""
Точечные уведомления из расписания: перенос, отмена и замена преподавателя
ставят сообщение в очередь той же транзакцией, что и сама доменная операция.

План строим РЕАЛЬНОЙ фикстурой раздела (apps/scheduling/tests/conftest.py:
sched_setup — два преподавателя, группа со стартом и слотом) плюс штатной
генерацией repository.generate_for_group. Своих фабрик не изобретаем: иначе тест
проверял бы выдуманный объектный граф, а не тот, что приходит из продакшена.
Фикстура импортирована поимённо (conftest соседнего пакета pytest сам не
подхватывает); `django_db_setup` scheduling не переопределяет — тестовая БД общая.

_request_dispatch патчим: очередь нас интересует, поход в Celery — нет.
"""
from __future__ import annotations

import datetime

import pytest
from django.test import override_settings

from apps.notifications import services as notif_services
from apps.notifications.constants import (
    CHANNEL_DM, CHANNEL_GROUP, KIND_LESSON_CANCELLED, KIND_LESSON_MOVED,
    KIND_SUBSTITUTE_ASSIGNED, KIND_SUBSTITUTE_REMOVED,
)
from apps.notifications.models import NotificationMessage, TelegramRecipient, TelegramUser
from apps.scheduling import repository as sched_repository
from apps.scheduling import services as sched_services
from apps.scheduling.models import PlannedLesson
from apps.scheduling.tests.conftest import sched_setup  # noqa: F401 — фикстура pytest

_CHAT_A = 5151
_CHAT_B = 5252
_GENERAL_CHAT_ID = -100500


@pytest.fixture
def linked_teachers(sched_setup):  # noqa: F811 — фикстура из scheduling
    """Привязки Telegram обоим преподавателям — чтобы появилась личка у каждого.

    За собой убирает раньше, чем sched_setup удалит преподавателей: и привязки,
    и поставленные сообщения ссылаются на teachers реальным FK, а сырое DELETE
    в фикстуре преподавателей ничего не каскадит.
    """
    teacher_ids = [sched_setup['teacher_a'], sched_setup['teacher_b']]
    users = []
    for teacher_id, chat_id, nick in zip(teacher_ids, (_CHAT_A, _CHAT_B), ('a', 'b')):
        tg = TelegramUser.objects.create(
            chat_id=chat_id, username=f'sched_{nick}', full_name=f'Препод {nick}')
        TelegramRecipient.objects.create(teacher_id=teacher_id, telegram_user=tg)
        users.append(tg)
    yield sched_setup
    NotificationMessage.objects.filter(recipient_teacher_id__in=teacher_ids).delete()
    NotificationMessage.objects.filter(source_kind='planned_lesson').delete()
    TelegramRecipient.objects.filter(teacher_id__in=teacher_ids).delete()
    TelegramUser.objects.filter(id__in=[u.id for u in users]).delete()


@pytest.fixture
def no_dispatch(monkeypatch):
    """Очередь проверяем, воркер не дёргаем."""
    monkeypatch.setattr(notif_services, '_request_dispatch', lambda: None)


@pytest.fixture
def planned_group(linked_teachers):
    """Сгенерированный план группы A. Возвращает (данные фикстуры, первая строка).

    Старт 2026-06-01 (пн), слот Пн 10:00, direction.total_lessons=8 → первое
    занятие 01.06 в 10:00.
    """
    group_id = linked_teachers['group_a']
    assert sched_repository.generate_for_group(group_id) is not None
    first = PlannedLesson.objects.filter(group_id=group_id).order_by('seq').first()
    return linked_teachers, first


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=_GENERAL_CHAT_ID)
def test_reschedule_enqueues_moved(no_dispatch, planned_group):
    """Разовый перенос → сообщение с ОБЕИМИ датами: было и стало."""
    setup, first = planned_group

    row = sched_services.reschedule(
        setup['group_a'], first.id,
        {'new_date': '2026-06-03', 'new_time': '11:00'}, None)
    assert row is not None

    rows = list(NotificationMessage.objects
                .filter(kind=KIND_LESSON_MOVED).order_by('channel'))
    assert [r.channel for r in rows] == [CHANNEL_DM, CHANNEL_GROUP]
    assert {r.chat_id for r in rows} == {_CHAT_A, _GENERAL_CHAT_ID}
    assert {r.source_kind for r in rows} == {'planned_lesson'}
    assert {r.source_id for r in rows} == {first.id}

    text = rows[0].text
    assert '__sched_group_A__' in text     # группа
    assert '__sched_dir__' in text         # направление
    assert '01.06' in text                 # было
    assert '03.06' in text and '11:00' in text  # стало
    assert 'урок №1' in text
    assert 'None' not in text


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=_GENERAL_CHAT_ID)
def test_cancel_enqueues_cancelled(no_dispatch, planned_group):
    """Отмена → сообщение об отменённом занятии с его исходной датой.

    Дата и номер берутся ДО мутации: cancel переносит строку в конец курса и
    перенумеровывает хвост, после операции они были бы уже другими.
    """
    setup, first = planned_group

    plan = sched_services.cancel(setup['group_a'], first.id, None)
    assert plan is not None

    rows = list(NotificationMessage.objects
                .filter(kind=KIND_LESSON_CANCELLED).order_by('channel'))
    assert [r.channel for r in rows] == [CHANNEL_DM, CHANNEL_GROUP]
    assert {r.chat_id for r in rows} == {_CHAT_A, _GENERAL_CHAT_ID}

    text = rows[0].text
    assert 'отменено' in text
    assert '__sched_group_A__' in text
    assert '01.06' in text and '10:00' in text
    assert 'None' not in text

    # Отмена не выбрасывает урок: он уезжает в конец курса, и у группы
    # появляется новое занятие. Без этой строки преподаватель видит только
    # половину последствия.
    course_dates = [r['scheduled_date'] for r in plan
                    if r['seq'] is not None and r['status'] != 'cancelled']
    expected_end = datetime.date.fromisoformat(max(course_dates))
    assert f'Курс продлён до {expected_end.strftime("%d.%m")}' in text


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=_GENERAL_CHAT_ID)
def test_change_teacher_notifies_both_sides(no_dispatch, planned_group):
    """Назначение замены → в личку пишем ОБОИМ, в общий чат — ровно один раз."""
    setup, first = planned_group

    row = sched_services.change_teacher(
        setup['group_a'], first.id, {'new_teacher_id': setup['teacher_b']}, None)
    assert row is not None

    dms = list(NotificationMessage.objects
               .filter(kind=KIND_SUBSTITUTE_ASSIGNED, channel=CHANNEL_DM))
    assert {r.recipient_teacher_id for r in dms} == {setup['teacher_a'], setup['teacher_b']}
    assert NotificationMessage.objects.filter(
        kind=KIND_SUBSTITUTE_ASSIGNED, channel=CHANNEL_GROUP).count() == 1

    by_teacher = {r.recipient_teacher_id: r.text for r in dms}
    # Тот, кого назначили заменой, и тот, кого сняли, читают РАЗНЫЕ формулировки.
    assert 'Вас назначили заменой' in by_teacher[setup['teacher_b']]
    assert '__sched_B__' in by_teacher[setup['teacher_a']]
    assert 'Вас назначили заменой' not in by_teacher[setup['teacher_a']]
    for text in by_teacher.values():
        assert '__sched_group_A__' in text
        assert '01.06' in text and '10:00' in text
        assert 'None' not in text


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=_GENERAL_CHAT_ID)
def test_change_teacher_back_removes_substitute(no_dispatch, planned_group):
    """Возврат занятия преподавателю контента = снятие замены, тоже обоим."""
    setup, first = planned_group

    sched_services.change_teacher(
        setup['group_a'], first.id, {'new_teacher_id': setup['teacher_b']}, None)
    sched_services.change_teacher(
        setup['group_a'], first.id, {'new_teacher_id': setup['teacher_a']}, None)

    dms = list(NotificationMessage.objects
               .filter(kind=KIND_SUBSTITUTE_REMOVED, channel=CHANNEL_DM))
    assert {r.recipient_teacher_id for r in dms} == {setup['teacher_a'], setup['teacher_b']}
    assert NotificationMessage.objects.filter(
        kind=KIND_SUBSTITUTE_REMOVED, channel=CHANNEL_GROUP).count() == 1

    by_teacher = {r.recipient_teacher_id: r.text for r in dms}
    assert 'ведёт основной преподаватель' in by_teacher[setup['teacher_b']]
    assert 'снова за вами' in by_teacher[setup['teacher_a']]


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=_GENERAL_CHAT_ID)
def test_change_teacher_to_same_teacher_is_silent(no_dispatch, planned_group):
    """Смена «на того же» ничего не меняет — и молчит: спам без события."""
    setup, first = planned_group

    sched_services.change_teacher(
        setup['group_a'], first.id, {'new_teacher_id': setup['teacher_a']}, None)

    assert not NotificationMessage.objects.filter(
        kind__in=[KIND_SUBSTITUTE_ASSIGNED, KIND_SUBSTITUTE_REMOVED]).exists()


@pytest.mark.django_db
@override_settings(TELEGRAM_GENERAL_CHAT_ID=_GENERAL_CHAT_ID)
def test_repeated_reschedule_sends_every_time(no_dispatch, planned_group):
    """Перенос туда и обратно — два разных события, оба должны дойти.

    Ключ идемпотентности содержал только новую дату, поэтому возврат занятия
    на дату, где оно уже было, выглядел повтором первой операции, и второе
    сообщение молча не уходило.
    """
    setup, first = planned_group

    sched_services.reschedule(
        setup['group_a'], first.id, {'new_date': '2026-06-03', 'new_time': '11:00'}, None)
    sched_services.reschedule(
        setup['group_a'], first.id, {'new_date': '2026-06-01', 'new_time': '10:00'}, None)
    sched_services.reschedule(
        setup['group_a'], first.id, {'new_date': '2026-06-03', 'new_time': '11:00'}, None)

    dms = list(NotificationMessage.objects
               .filter(kind=KIND_LESSON_MOVED, channel=CHANNEL_DM)
               .order_by('id'))
    assert len(dms) == 3, 'каждый перенос — отдельное событие для преподавателя'

    # Тексты честно отражают каждый шаг: было → стало.
    assert '01.06' in dms[0].text and '03.06' in dms[0].text
    assert '03.06' in dms[1].text and '01.06' in dms[1].text
    assert '01.06' in dms[2].text and '03.06' in dms[2].text
