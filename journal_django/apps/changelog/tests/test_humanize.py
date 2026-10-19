"""Очеловечивание событий журнала изменений (apps/changelog/humanize.py)."""
from __future__ import annotations

import pytest

from apps.changelog import humanize
from apps.changelog.summary import Lookups

_LK = Lookups(
    groups={7: 'ПИ1012'}, students={3: 'Иван Тестов'},
    teachers={1: 'Пётр', 2: 'Мария'}, directions={5: 'Робототехника'},
)

_N = humanize._NARROW  # узкий неразрывный пробел (разделитель тысяч и перед ₽)


# ---------------------------------------------------------------------------
# Чистые форматтеры значений (без БД).
# ---------------------------------------------------------------------------

def test_money_formats_with_ruble_and_thousands():
    assert humanize._money(3600) == f'3{_N}600{_N}₽'
    assert humanize._money('3600.50') == f'3{_N}600,50{_N}₽'
    assert humanize._money(0) == f'0{_N}₽'


def test_date_and_datetime_to_ru():
    assert humanize._fmt_date('2026-07-09') == '09.07.2026'
    assert humanize._fmt_datetime('2026-07-09T12:00:00+00:00') == '09.07.2026 12:00'


def test_day_of_week_sunday_is_zero():
    assert humanize._day_of_week(0) == 'Вс'
    assert humanize._day_of_week(1) == 'Пн'
    assert humanize._day_of_week(6) == 'Сб'


def test_not_revertable_reason_priority():
    assert humanize.not_revertable_reason(
        has_events=False, has_forbidden=False, is_revert=False, reverted=False,
    ) == 'она не содержит изменений данных'
    assert 'учётки' in humanize.not_revertable_reason(
        has_events=True, has_forbidden=True, is_revert=False, reverted=False,
    )
    assert humanize.not_revertable_reason(
        has_events=True, has_forbidden=False, is_revert=True, reverted=False,
    ) == 'операции отката не откатываются'
    assert humanize.not_revertable_reason(
        has_events=True, has_forbidden=False, is_revert=False, reverted=True,
    ) == 'она уже откачена'
    # придаточное-фрагмент: без завершающей точки (её ставит фронт-шаблон)
    assert not humanize.not_revertable_reason(
        has_events=False, has_forbidden=False, is_revert=False, reverted=False,
    ).endswith('.')
    assert humanize.not_revertable_reason(
        has_events=True, has_forbidden=False, is_revert=False, reverted=False,
    ) is None


# ---------------------------------------------------------------------------
# format_value по типу поля (интроспекция реальной модели → нужна БД).
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_format_value_by_field_type():
    fv = humanize.format_value
    # BooleanField
    assert fv('groups.Group', 'is_individual', True, _LK) == 'да'
    assert fv('groups.Group', 'is_individual', False, _LK) == 'нет'
    # DateField / TimeField
    assert fv('scheduling.PlannedLesson', 'scheduled_date', '2026-07-09', _LK) == '09.07.2026'
    assert fv('scheduling.PlannedLesson', 'scheduled_time', '12:00:00', _LK) == '12:00'
    # DecimalField (деньги) + FK по имени
    assert fv('payments.Payment', 'total_amount', 3600, _LK) == f'3{_N}600{_N}₽'
    assert fv('payments.Payment', 'student_id', 3, _LK) == 'Иван Тестов'
    # день недели + пустое значение
    assert fv('groups.GroupScheduleSlot', 'day_of_week', 0, _LK) == 'Вс'
    assert fv('groups.Group', 'name', None, _LK) == '—'


@pytest.mark.django_db
def test_format_value_fk_without_name_falls_back_to_id():
    assert humanize.format_value('payments.Payment', 'student_id', 999, _LK) == '#999'


# ---------------------------------------------------------------------------
# changes / title.
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_changes_update_humanizes_old_and_new():
    ev = {'entity': 'planned_lesson', 'pgh_obj_model': 'scheduling.PlannedLesson',
          'pgh_label': 'update', 'pgh_obj_id': '5',
          'pgh_data': {'group_id': 7},
          'pgh_diff': {'status': ['pending', 'done'],
                       'scheduled_date': ['2026-07-09', '2026-07-07']}}
    changes = {c['label']: (c['old'], c['new']) for c in humanize.humanize_changes(ev, _LK)}
    assert changes['статус'] == ('запланирован', 'проведён')
    assert changes['плановая дата'] == ('09.07.2026', '07.07.2026')


@pytest.mark.django_db
def test_changes_insert_hides_noise_and_title_fk():
    ev = {'entity': 'payment', 'pgh_obj_model': 'payments.Payment',
          'pgh_label': 'insert', 'pgh_obj_id': '8',
          'pgh_data': {'id': 8, 'student_id': 3, 'total_amount': 3600,
                       'created_at': '2026-07-01T10:00:00+00:00', 'note': None},
          'pgh_diff': {}}
    changes = humanize.humanize_changes(ev, _LK)
    labels = {c['label'] for c in changes}
    # student_id вынесен в title, created_at/id — шум, note=None опущено
    assert 'ученик' not in labels
    assert 'создано' not in labels
    money = next(c for c in changes if c['label'] == 'сумма')
    assert money['old'] is None and money['new'] == f'3{_N}600{_N}₽'


@pytest.mark.django_db
def test_title_uses_human_names():
    ev_pay = {'entity': 'payment', 'pgh_obj_model': 'payments.Payment',
              'pgh_label': 'insert', 'pgh_obj_id': '8',
              'pgh_data': {'student_id': 3, 'total_amount': 3600}, 'pgh_diff': {}}
    assert humanize.humanize_title(ev_pay, _LK) == f'Оплата 3{_N}600{_N}₽ — Иван Тестов'

    ev_lesson = {'entity': 'lesson', 'pgh_obj_model': 'lessons.Lesson',
                 'pgh_label': 'insert', 'pgh_obj_id': '1',
                 'pgh_data': {'group_id': 7, 'lesson_number': 3}, 'pgh_diff': {}}
    assert humanize.humanize_title(ev_lesson, _LK) == 'Урок «ПИ1012» №3'


@pytest.mark.django_db
def test_humanize_event_shape():
    ev = {'entity': 'direction', 'pgh_obj_model': 'directions.Direction',
          'pgh_label': 'update', 'pgh_obj_id': '2',
          'pgh_data': {'name': 'Робо'},
          'pgh_diff': {'name': ['Робо', 'Робототехника']}}
    human = humanize.humanize_event(ev, _LK)
    assert set(human) == {'title', 'text', 'changes'}
    assert human['title'] and human['text']
    assert human['changes'][0]['label'] == 'название'
    assert human['changes'][0]['new'] == 'Робототехника'
