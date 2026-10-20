"""
Ограничения БД слоя данных «Мотивации» (спека 2026-10-20-eduson-motivation-payroll, §2).

Проверяем именно БД, а не валидацию сериализаторов: ставки и начисления будут
писать движок пересчёта (bulk_create/bulk_update — мимо full_clean) и сервисы,
поэтому последняя линия защиты от отрицательных денег и кривых диапазонов —
CHECK/UNIQUE в PostgreSQL. Каждое нарушение ловим в своём savepoint, иначе
первая же ошибка отравит транзакцию теста.

Данные — выдуманная минимальная школа (apps/core/tests/minimal_school.py).
"""
from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.core.tests.minimal_school import ensure_minimal_school
from apps.payroll.models import (
    CancellationPay,
    PayRateDirection,
    PayRateVersion,
    Payroll,
    PersonalRate,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def school():
    return ensure_minimal_school()


def _version(**overrides) -> PayRateVersion:
    fields = {
        'effective_from': datetime.date(2026, 9, 1),
        'group_rate': Decimal('800.00'),
        'individual_rate': Decimal('1000.00'),
        'cancel_mode': PayRateVersion.CancelMode.PERCENT,
        'cancel_value': Decimal('50.00'),
    }
    fields.update(overrides)
    return PayRateVersion.objects.create(**fields)


def _assert_db_rejects(factory, **kwargs) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        factory(**kwargs)


# ---------------------------------------------------------------------------
# PayRateVersion
# ---------------------------------------------------------------------------

def test_version_defaults_penalty_off():
    v = _version()
    v.refresh_from_db()
    # Штраф по умолчанию выключен (решение владельца, §1.4) — новая версия
    # без явного включения не должна начать штрафовать.
    assert v.penalty_enabled is False
    assert v.penalty_mode == PayRateVersion.PenaltyMode.PER_LESSON
    assert v.penalty_amount == Decimal('0.00')
    assert v.created_at is not None


@pytest.mark.parametrize('field', ['group_rate', 'individual_rate', 'cancel_value',
                                   'penalty_amount'])
def test_version_rejects_negative_money(field):
    _assert_db_rejects(_version, **{field: Decimal('-0.01')})


def test_version_percent_cancel_over_100_rejected():
    _assert_db_rejects(_version, cancel_mode=PayRateVersion.CancelMode.PERCENT,
                       cancel_value=Decimal('100.01'))


def test_version_percent_cancel_exactly_100_allowed():
    v = _version(cancel_mode=PayRateVersion.CancelMode.PERCENT, cancel_value=Decimal('100'))
    assert v.pk is not None


def test_version_fixed_cancel_over_100_allowed():
    # Фиксированная сумма в рублях: 100 — не потолок, потолок только у процента.
    v = _version(cancel_mode=PayRateVersion.CancelMode.FIXED, cancel_value=Decimal('500.00'))
    v.refresh_from_db()
    assert v.cancel_value == Decimal('500.00')


def test_version_unknown_modes_rejected():
    _assert_db_rejects(_version, cancel_mode='bogus')
    _assert_db_rejects(_version, penalty_mode='bogus')


def test_version_effective_from_unique():
    _version(effective_from=datetime.date(2026, 10, 1))
    _assert_db_rejects(_version, effective_from=datetime.date(2026, 10, 1))


# ---------------------------------------------------------------------------
# PayRateDirection
# ---------------------------------------------------------------------------

def test_direction_rate_null_means_default(school):
    v = _version()
    row = PayRateDirection.objects.create(version=v, direction_id=school['direction_id'])
    row.refresh_from_db()
    assert row.group_rate is None
    assert row.individual_rate is None
    assert list(v.directions.all()) == [row]


def test_direction_rate_unique_per_version(school):
    v = _version()
    PayRateDirection.objects.create(version=v, direction_id=school['direction_id'],
                                    group_rate=Decimal('900'))
    _assert_db_rejects(PayRateDirection.objects.create, version=v,
                       direction_id=school['direction_id'])


@pytest.mark.parametrize('field', ['group_rate', 'individual_rate'])
def test_direction_rate_rejects_negative(school, field):
    v = _version()
    _assert_db_rejects(PayRateDirection.objects.create, version=v,
                       direction_id=school['direction_id'], **{field: Decimal('-1')})


def test_direction_with_rates_cannot_be_deleted():
    # PROTECT: ставка направления в истории версий нужна для пересчёта прошлых
    # периодов — молча удалить направление вместе с ней нельзя. Направление
    # своё, без групп и оплат: иначе удаление запретили бы чужие FK
    # (payments — RESTRICT), и тест прошёл бы без PROTECT здесь.
    from django.db.models import ProtectedError

    from apps.directions.models import Direction
    direction = Direction.objects.create(name='Тест Направление со ставкой',
                                         total_lessons=16)
    PayRateDirection.objects.create(version=_version(), direction=direction)
    with pytest.raises(ProtectedError), transaction.atomic():
        direction.delete()
    assert Direction.objects.filter(pk=direction.pk).exists()


def test_direction_rows_cascade_with_version(school):
    v = _version()
    PayRateDirection.objects.create(version=v, direction_id=school['direction_id'])
    v.delete()
    assert not PayRateDirection.objects.filter(direction_id=school['direction_id']).exists()


# ---------------------------------------------------------------------------
# PersonalRate
# ---------------------------------------------------------------------------

def _personal(school, **overrides) -> PersonalRate:
    fields = {
        'teacher_id': school['teacher_id'],
        'group_id': school['group_id'],
        'kind': PersonalRate.Kind.FIXED,
        'value': Decimal('1200.00'),
    }
    fields.update(overrides)
    return PersonalRate.objects.create(**fields)


def test_personal_rate_all_lessons(school):
    r = _personal(school)
    r.refresh_from_db()
    assert r.lesson_from is None and r.lesson_to is None
    assert r.note == ''


def test_personal_rate_range_ok_including_single_lesson(school):
    r = _personal(school, lesson_from=Decimal('10'), lesson_to=Decimal('15.5'))
    r.refresh_from_db()
    assert (r.lesson_from, r.lesson_to) == (Decimal('10.0'), Decimal('15.5'))
    # Диапазон из одного урока (from == to) — допустим.
    assert _personal(school, lesson_from=Decimal('3'), lesson_to=Decimal('3')).pk


@pytest.mark.parametrize('bounds', [
    {'lesson_from': Decimal('5')},
    {'lesson_to': Decimal('5')},
])
def test_personal_rate_half_open_range_rejected(school, bounds):
    _assert_db_rejects(_personal, school=school, **bounds)


def test_personal_rate_inverted_range_rejected(school):
    _assert_db_rejects(_personal, school=school,
                       lesson_from=Decimal('15'), lesson_to=Decimal('10'))


def test_personal_rate_negative_from_rejected(school):
    _assert_db_rejects(_personal, school=school,
                       lesson_from=Decimal('-1'), lesson_to=Decimal('2'))


def test_personal_rate_deleted_with_group(school):
    from apps.groups.models import Group
    group = Group.objects.create(
        name='Тест Группа со ставкой', direction_id=school['direction_id'],
        teacher_id=school['teacher_id'], is_individual=False,
        lesson_duration_minutes=90, lessons_per_week=1, active=True,
        created_at=timezone.now(),
    )
    rate = _personal(school, group_id=group.pk)
    group.delete()
    assert not PersonalRate.objects.filter(pk=rate.pk).exists()


def test_personal_rate_deleted_with_teacher(school):
    # Ставка без преподавателя бессмысленна, а денег в ней нет — CASCADE.
    from apps.teachers.models import Teacher
    teacher = Teacher.objects.create(name='Тест Преподаватель со ставкой',
                                     created_at=timezone.now())
    rate = _personal(school, teacher_id=teacher.pk)
    teacher.delete()
    assert not PersonalRate.objects.filter(pk=rate.pk).exists()


def test_personal_rate_negative_value_and_unknown_kind_rejected(school):
    _assert_db_rejects(_personal, school=school, value=Decimal('-0.01'))
    _assert_db_rejects(_personal, school=school, kind='bogus')


# ---------------------------------------------------------------------------
# CancellationPay
# ---------------------------------------------------------------------------

def _cancel_marker(school):
    from apps.scheduling.models import PlannedLesson
    # Маркер отмены — как его создаёт scheduling.repository.cancel_lesson:
    # status='cancelled', без seq/номера урока.
    return PlannedLesson.objects.create(
        group_id=school['group_id'], teacher_id=school['teacher_id'],
        seq=None, lesson_number=None, status='cancelled',
        created_at=timezone.now(), updated_at=timezone.now(),
        scheduled_date=datetime.date(2026, 9, 14), scheduled_time=datetime.time(10, 0),
    )


def _cancellation(school, marker, **overrides) -> CancellationPay:
    fields = {
        'planned_lesson': marker,
        'teacher_id': school['teacher_id'],
        'group_id': school['group_id'],
        'lesson_date': marker.scheduled_date,
        'scheduled_time': marker.scheduled_time,
    }
    fields.update(overrides)
    return CancellationPay.objects.create(**fields)


def test_cancellation_defaults(school):
    c = _cancellation(school, _cancel_marker(school))
    c.refresh_from_db()
    assert c.payment == Decimal('0.00')
    assert c.calc_payment == Decimal('0.00')
    assert c.is_manual is False
    assert c.rule_note == ''


def test_cancellation_unique_per_marker(school):
    marker = _cancel_marker(school)
    _cancellation(school, marker)
    _assert_db_rejects(_cancellation, school=school, marker=marker)


def test_cancellation_survives_marker_delete_via_orm(school):
    # Пересборка плана (reset_plan, wipe_one_offs) удаляет маркеры, в том числе
    # прошлые. Начисление остаётся сиротой со своим ключом (группа + дата +
    # время): CASCADE молча унёс бы оплату за уже выплаченный месяц.
    marker = _cancel_marker(school)
    c = _cancellation(school, marker, payment=Decimal('400.00'))
    marker.delete()
    c.refresh_from_db()
    assert c.planned_lesson_id is None
    assert c.payment == Decimal('400.00')
    assert (c.group_id, c.lesson_date, c.scheduled_time) == (
        school['group_id'], datetime.date(2026, 9, 14), datetime.time(10, 0))


def test_cancellation_survives_marker_delete_via_raw_sql(school):
    # Сырой DELETE мимо ORM-коллектора (teardown'ы, скрипты): правило
    # ON DELETE SET NULL обязано стоять в самой БД (миграция 0006).
    marker = _cancel_marker(school)
    c = _cancellation(school, marker)
    with connection.cursor() as cur:
        cur.execute('DELETE FROM planned_lessons WHERE id = %s', [marker.pk])
    c.refresh_from_db()
    assert c.planned_lesson_id is None


def test_several_orphans_allowed(school):
    # UNIQUE(planned_lesson) не мешает нескольким сиротам: NULL не конфликтуют.
    for _ in range(2):
        _cancellation(school, _cancel_marker(school)).planned_lesson.delete()
    assert CancellationPay.objects.filter(planned_lesson__isnull=True,
                                          group_id=school['group_id']).count() == 2


def test_teacher_with_cancellation_pay_cannot_be_deleted(school):
    from django.db.models import ProtectedError

    from apps.teachers.models import Teacher
    teacher = Teacher.objects.create(name='Тест Преподаватель отмен',
                                     created_at=timezone.now())
    _cancellation(school, _cancel_marker(school), teacher_id=teacher.pk)
    with pytest.raises(ProtectedError), transaction.atomic():
        teacher.delete()
    assert Teacher.objects.filter(pk=teacher.pk).exists()


def test_cancellation_rejects_negative_calc_payment(school):
    _assert_db_rejects(_cancellation, school=school, marker=_cancel_marker(school),
                       calc_payment=Decimal('-1'))


def test_cancellation_manual_payment_not_limited_by_db(school):
    # Ручная правка отмены ведёт себя как правка урока: у Payroll.payment CHECK
    # нет, значит и здесь отрицательная сумма (удержание) решается человеком.
    c = _cancellation(school, _cancel_marker(school), payment=Decimal('-100.00'),
                      is_manual=True)
    c.refresh_from_db()
    assert c.payment == Decimal('-100.00')


# ---------------------------------------------------------------------------
# Payroll — новые поля
# ---------------------------------------------------------------------------

def test_payroll_new_fields_have_db_defaults_for_legacy_insert(school):
    # Строка, вставленная «по-старому» (без новых колонок), — модель уже
    # существующих в таблице записей: значения обязаны прийти из DEFAULT БД.
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO payroll (lesson_id, teacher_id, total_students, present_count, '
            'payment, penalty) VALUES (%s, %s, 1, 1, 650.00, 0) RETURNING id',
            [school['lesson_id'], school['teacher_id']],
        )
        payroll_id = cur.fetchone()[0]
    p = Payroll.objects.get(pk=payroll_id)
    assert p.is_manual is False
    assert p.calc_payment == Decimal('0.00')
    assert p.calc_penalty == Decimal('0.00')
    assert p.rule_note == ''
