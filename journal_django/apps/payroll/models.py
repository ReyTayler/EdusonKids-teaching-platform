"""
Модели зарплат преподавателей.

`Payroll` — начисление за проведённый урок (1:1 с lessons), исторически
managed=False поверх таблицы `payroll` (db/migrations/001_initial_schema.sql),
сейчас managed=True. numeric-поля рендерятся строкой с масштабом ('500.00') —
DateSafeJSONRenderer.

Слой «Мотивации» (спека docs/specs/2026-10-20-eduson-motivation-payroll-design.md, §2):
  PayRateVersion / PayRateDirection — датированные ставки школы;
  PersonalRate / PersonalRateTerm — персональные ставки «преподаватель × группа»
    и их периоды действия с параметрами (поправка §8: правки не меняют прошлое);
  CancellationPay — начисление за отменённое занятие (1:1 с маркером отмены).
Правил расчёта здесь нет — только данные и их инварианты в БД: движок пишет
строки bulk-операциями мимо full_clean, поэтому деньги ≥ 0, диапазоны и режимы
держат CHECK-ограничения PostgreSQL.
"""
from __future__ import annotations

import pghistory
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import (
    DateRangeField,
    DecimalRangeField,
    RangeBoundary,
    RangeOperators,
)
from django.db import models
from django.utils import timezone

# Имена ограничений персональных ставок — по ним сервис узнаёт нарушение
# (apps/payroll/motivation.py) и отвечает 400 с понятным текстом.
PERSONAL_ALL_EXCLUDE = 'payroll_personal_terms_all_excl'
PERSONAL_RANGES_EXCLUDE = 'payroll_personal_terms_ranges_excl'
PERSONAL_RANGE_CHECK = 'payroll_personal_terms_range_check'
PERSONAL_PERIOD_CHECK = 'payroll_personal_terms_period_check'


class NumRange(models.Func):
    """numrange(from, to, '[]') — замкнутый диапазон номеров уроков."""

    function = 'NUMRANGE'
    output_field = DecimalRangeField()


class TermPeriod(models.Func):
    """daterange(valid_from, valid_until, '[)') — период действия терма;
    NULL-граница бесконечна. Без аргументов: выражение одно на оба EXCLUDE."""

    function = 'DATERANGE'
    output_field = DateRangeField()

    def __init__(self, **extra):
        super().__init__('valid_from', 'valid_until', RangeBoundary(
            inclusive_lower=True, inclusive_upper=False), **extra)


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class Payroll(models.Model):
    """Запись расчётного листа по уроку (1:1 с lessons)."""

    id = models.AutoField(primary_key=True)
    # FK → lessons(id), UNIQUE в БД → 1:1 (OneToOne). NO ACTION → DO_NOTHING.
    lesson = models.OneToOneField(
        'lessons.Lesson',
        on_delete=models.DO_NOTHING,
        db_column='lesson_id',
        related_name='payroll',
    )
    # FK → teachers(id). NO ACTION в БД → DO_NOTHING (managed=False).
    teacher = models.ForeignKey(
        'teachers.Teacher',
        on_delete=models.DO_NOTHING,
        db_column='teacher_id',
        related_name='payroll_entries',
    )
    total_students = models.IntegerField()
    present_count = models.IntegerField()
    payment = models.DecimalField(max_digits=10, decimal_places=2)
    penalty = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Ручная правка суммы (PATCH из админки): пересчёт такую строку не трогает,
    # обновляет только calc_* — чтобы рядом с правкой была видна расчётная сумма.
    # db_default у всех четырёх полей: таблица уже с данными, NOT NULL-колонке
    # нужен DEFAULT в самой БД (его же получат сырые INSERT старых путей).
    is_manual = models.BooleanField(default=False, db_default=False)
    calc_payment = models.DecimalField(max_digits=10, decimal_places=2,
                                       default=0, db_default=0)
    calc_penalty = models.DecimalField(max_digits=10, decimal_places=2,
                                       default=0, db_default=0)
    # Какое правило сработало («ставка группового, направление X», «нет ставки
    # на дату», …) — показывается в админке и кабинете вместо расшифровки ставок.
    rule_note = models.TextField(default='', db_default='')

    class Meta:
        managed = True
        db_table = 'payroll'
        indexes = [
            models.Index(fields=['teacher', 'lesson'], name='payroll_teacher_lesson_idx'),
            models.Index(fields=['lesson'], name='payroll_lesson_id_idx'),
        ]


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class PayRateVersion(models.Model):
    """
    Версия ставок школы, действующая с effective_from до следующей версии.

    Урок и отмена считаются по версии, действовавшей на их дату (§1.5). Сами
    поля — строка «По умолчанию»; ставки направлений — PayRateDirection.
    Отмена и штраф общие для школы, по направлениям не делятся (§1.1).
    """

    class CancelMode(models.TextChoices):
        PERCENT = 'percent', 'Процент от ставки'
        FIXED = 'fixed', 'Фиксированная сумма'

    class PenaltyMode(models.TextChoices):
        PER_LESSON = 'per_lesson', 'За урок'
        PER_STUDENT = 'per_student', 'За каждого присутствовавшего'

    id = models.AutoField(primary_key=True)
    # UNIQUE: две версии с одной датой сделали бы выбор «действующей» неоднозначным.
    # Индекс уникальности заодно обслуживает поиск «последняя версия ≤ даты».
    effective_from = models.DateField(unique=True)
    group_rate = models.DecimalField(max_digits=10, decimal_places=2)
    individual_rate = models.DecimalField(max_digits=10, decimal_places=2)
    cancel_mode = models.TextField(choices=CancelMode.choices)
    # percent — проценты (0..100) от ставки урока; fixed — рубли.
    cancel_value = models.DecimalField(max_digits=10, decimal_places=2)
    penalty_enabled = models.BooleanField(default=False)
    penalty_mode = models.TextField(choices=PenaltyMode.choices,
                                    default=PenaltyMode.PER_LESSON)
    penalty_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # SET_NULL: удаление учётки не должно уносить ставки, по которым считаны деньги.
    created_by = models.ForeignKey(
        'accounts.Account', on_delete=models.SET_NULL, null=True, blank=True,
        db_column='created_by_id', related_name='+',
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = True
        db_table = 'payroll_rate_versions'
        ordering = ['-effective_from']
        constraints = [
            models.CheckConstraint(
                name='payroll_rate_versions_rates_check',
                condition=models.Q(group_rate__gte=0) & models.Q(individual_rate__gte=0),
            ),
            models.CheckConstraint(
                name='payroll_rate_versions_cancel_mode_check',
                condition=models.Q(cancel_mode__in=['percent', 'fixed']),
            ),
            # Потолок 100 — только у процента: фиксированная сумма в рублях
            # законно больше 100.
            models.CheckConstraint(
                name='payroll_rate_versions_cancel_value_check',
                condition=models.Q(cancel_value__gte=0) & (
                    ~models.Q(cancel_mode='percent') | models.Q(cancel_value__lte=100)
                ),
            ),
            models.CheckConstraint(
                name='payroll_rate_versions_penalty_mode_check',
                condition=models.Q(penalty_mode__in=['per_lesson', 'per_student']),
            ),
            models.CheckConstraint(
                name='payroll_rate_versions_penalty_amount_check',
                condition=models.Q(penalty_amount__gte=0),
            ),
        ]

    def __str__(self) -> str:
        return f'Ставки с {self.effective_from}'


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class PayRateDirection(models.Model):
    """
    Ставки направления внутри версии. NULL в ячейке = «как по умолчанию»:
    так новое направление сразу считается по ставке версии, без строки здесь.
    """

    id = models.AutoField(primary_key=True)
    version = models.ForeignKey(
        PayRateVersion, on_delete=models.CASCADE,
        db_column='version_id', related_name='directions',
        # Покрыт префиксом UNIQUE(version, direction).
        db_index=False,
    )
    # PROTECT: направление со ставкой в истории версий молча не удаляется —
    # пересчёт прошлых периодов потерял бы ставку. Направления и так архивируют.
    direction = models.ForeignKey(
        'directions.Direction', on_delete=models.PROTECT,
        db_column='direction_id', related_name='pay_rates',
    )
    group_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    individual_rate = models.DecimalField(max_digits=10, decimal_places=2,
                                          null=True, blank=True)

    class Meta:
        managed = True
        db_table = 'payroll_rate_directions'
        constraints = [
            models.UniqueConstraint(fields=['version', 'direction'],
                                    name='payroll_rate_directions_version_direction_uniq'),
            # NULL проходит CHECK сам (NULL >= 0 → unknown → не нарушение).
            models.CheckConstraint(
                name='payroll_rate_directions_rates_check',
                condition=models.Q(group_rate__gte=0) & models.Q(individual_rate__gte=0),
            ),
        ]


class PersonalRateKind(models.TextChoices):
    FIXED = 'fixed', 'Вместо ставки'
    PLUS_FIXED = 'plus_fixed', 'Надбавка, ₽'
    PLUS_PERCENT = 'plus_percent', 'Надбавка, %'


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class PersonalRate(models.Model):
    """
    Персональная ставка — сущность «преподаватель × группа» (§1.6, поправка §8).

    Сами параметры (уроки, вид, значение) живут в периодах PersonalRateTerm:
    правка, выключение и удаление ставки действуют только с сегодняшнего дня,
    поэтому прежние параметры не перезаписываются, а закрываются датой — прошлые
    начисления при любом последующем пересчёте считаются по ним же. Колонок
    параметров здесь нет намеренно: два источника правды разошлись бы.

    is_active — переключатель «включена / выключена» (у включённой последний
    период открыт, у выключенной закрыт). deleted_at — мягкое удаление: ставка
    скрыта из списка, а её закрытые периоды продолжают считать прошлое.
    Преподаватель и группа неизменны: у истории другой смысл, для другой пары
    заводят новую ставку.
    """

    Kind = PersonalRateKind

    id = models.AutoField(primary_key=True)
    # CASCADE: жёсткое удаление преподавателя уносит его персональные ставки —
    # начислений в них нет (деньги живут в Payroll/CancellationPay под PROTECT),
    # а ставка без преподавателя бессмысленна.
    teacher = models.ForeignKey(
        'teachers.Teacher', on_delete=models.CASCADE,
        db_column='teacher_id', related_name='personal_rates',
    )
    group = models.ForeignKey(
        'groups.Group', on_delete=models.CASCADE,
        db_column='group_id', related_name='personal_rates',
        # Покрыт префиксом составного payroll_personal_group_tch_idx.
        db_index=False,
    )
    note = models.TextField(blank=True, default='')
    # db_default: таблица уже с данными — NOT NULL-колонке нужен DEFAULT в БД.
    is_active = models.BooleanField(default=True, db_default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        'accounts.Account', on_delete=models.SET_NULL, null=True, blank=True,
        db_column='created_by_id', related_name='+',
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = True
        db_table = 'payroll_personal_rates'
        indexes = [
            # Список фильтрует по группе и преподавателю.
            models.Index(fields=['group', 'teacher'],
                         name='payroll_personal_group_tch_idx'),
        ]


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class PersonalRateTerm(models.Model):
    """
    Период действия персональной ставки (поправка владельца §8).

    Действует на дату d, если valid_from ≤ d < valid_until; NULL-граница —
    без границы (valid_from NULL — «с начала», так мигрированы ставки до
    поправки). valid_until НЕ включительно: закрытие «сегодня» значит, что
    сегодняшние уроки уже считаются без этого периода. Пустой период
    (valid_from = valid_until) допустим: так хранятся параметры ставки,
    созданной и выключенной в один день, — иначе при включении их неоткуда
    было бы взять. Пустой период не действует ни на одну дату и ни с чем не
    пересекается.

    lesson_from/lesson_to — диапазон номеров урока курса (numeric 5,1, как
    Lesson.lesson_number); оба NULL — «все уроки группы».

    teacher и group — копия пары из ставки: по ним БД держит непересечение
    периодов пары преподаватель × группа (EXCLUDE видит только свою строку).
    Копию пишет только сервис (apps/payroll/motivation.py), пара у ставки
    неизменна, поэтому расхождению взяться неоткуда. Непересечение:
      * два периода «все уроки» не пересекаются по датам — EXCLUDE …_all_excl;
      * два периода с диапазоном не пересекаются одновременно по урокам и по
        датам — EXCLUDE …_ranges_excl. Так возможна замена «с даты — другая
        ставка» на те же уроки.
    «Все уроки» + диапазон на одни даты — законно: диапазон точнее и побеждает
    (§1.6), поэтому «все уроки» с диапазоном одним ограничением не сравниваются.
    """

    Kind = PersonalRateKind

    id = models.AutoField(primary_key=True)
    rate = models.ForeignKey(
        PersonalRate, on_delete=models.CASCADE,
        db_column='rate_id', related_name='terms',
    )
    teacher = models.ForeignKey(
        'teachers.Teacher', on_delete=models.CASCADE,
        db_column='teacher_id', related_name='+',
        # Покрыт gist-индексами ограничений непересечения.
        db_index=False,
    )
    group = models.ForeignKey(
        'groups.Group', on_delete=models.CASCADE,
        db_column='group_id', related_name='+',
        # Покрыт префиксом составного payroll_personal_term_grp_idx.
        db_index=False,
    )
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    lesson_from = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    lesson_to = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    kind = models.TextField(choices=PersonalRateKind.choices)
    # fixed / plus_fixed — рубли; plus_percent — проценты от ставки формата.
    value = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        managed = True
        db_table = 'payroll_personal_rate_terms'
        indexes = [
            # Движок грузит периоды по группам пересчёта.
            models.Index(fields=['group', 'teacher'],
                         name='payroll_personal_term_grp_idx'),
        ]
        constraints = [
            # «Все уроки» (оба NULL) либо замкнутый диапазон from ≤ to, from ≥ 0.
            # Полуоткрытый диапазон движок трактовал бы произвольно — запрещаем.
            models.CheckConstraint(
                name=PERSONAL_RANGE_CHECK,
                condition=(
                    models.Q(lesson_from__isnull=True, lesson_to__isnull=True)
                    | models.Q(lesson_from__isnull=False, lesson_to__isnull=False,
                               lesson_from__gte=0, lesson_from__lte=models.F('lesson_to'))
                ),
            ),
            # ≤, а не <: пустой период законен (см. docstring).
            models.CheckConstraint(
                name=PERSONAL_PERIOD_CHECK,
                condition=(models.Q(valid_from__isnull=True) | models.Q(valid_until__isnull=True)
                           | models.Q(valid_from__lte=models.F('valid_until'))),
            ),
            models.CheckConstraint(
                name='payroll_personal_terms_kind_check',
                condition=models.Q(kind__in=['fixed', 'plus_fixed', 'plus_percent']),
            ),
            models.CheckConstraint(
                name='payroll_personal_terms_value_check',
                condition=models.Q(value__gte=0),
            ),
            # Равенство по teacher/group в gist-индексе — расширение btree_gist
            # (миграция 0007). daterange(NULL, …) — граница бесконечна.
            ExclusionConstraint(
                name=PERSONAL_ALL_EXCLUDE,
                expressions=[
                    ('teacher', RangeOperators.EQUAL),
                    ('group', RangeOperators.EQUAL),
                    (TermPeriod(), RangeOperators.OVERLAPS),
                ],
                condition=models.Q(lesson_from__isnull=True),
            ),
            ExclusionConstraint(
                name=PERSONAL_RANGES_EXCLUDE,
                expressions=[
                    ('teacher', RangeOperators.EQUAL),
                    ('group', RangeOperators.EQUAL),
                    (NumRange('lesson_from', 'lesson_to', RangeBoundary(
                        inclusive_lower=True, inclusive_upper=True)),
                     RangeOperators.OVERLAPS),
                    (TermPeriod(), RangeOperators.OVERLAPS),
                ],
                condition=models.Q(lesson_from__isnull=False),
            ),
        ]


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class CancellationPay(models.Model):
    """
    Начисление за отменённое занятие (§1.3), привязанное к маркеру отмены
    (PlannedLesson status='cancelled', seq IS NULL).

    Маркеры не вечны: их удаляют пересборка плана (reset_plan — перевод ученика),
    wipe_one_offs (смена расписания) и мягкое обновление, в том числе прошлые.
    Поэтому у начисления свой естественный ключ — группа + lesson_date +
    scheduled_time: по нему движок заново привязывает «сироту» к новому маркеру
    (ручная правка переживает пересборку), а прошлую сироту без маркера не
    трогает — выплаченный месяц не должен меняться задним числом.

    teacher — копия PlannedLesson.teacher маркера (он уже эффективный: замена
    учтена при отмене).
    """

    id = models.AutoField(primary_key=True)
    # SET_NULL, не CASCADE: удаление маркера не должно молча уносить оплату за
    # уже выплаченный месяц (§2). UNIQUE остаётся — NULL в нём не конфликтуют.
    # ON DELETE SET NULL продублирован на уровне БД (миграция 0006, RunSQL):
    # Django ставит FK без ON DELETE, и сырой DELETE FROM planned_lessons
    # (teardown'ы тестов) упёрся бы в FK.
    # ⚠️ AlterField этого поля пересоздаст FK без ON DELETE — после него нужна
    # повторная RunSQL (тот же случай — extra_lessons/0012).
    planned_lesson = models.OneToOneField(
        'scheduling.PlannedLesson', on_delete=models.SET_NULL, null=True, blank=True,
        db_column='planned_lesson_id', related_name='cancellation_pay',
    )
    # PROTECT, а не DO_NOTHING как у Payroll. Смысл тот же — жёсткое удаление
    # преподавателя с начислениями запрещено (их удаляют мягко, active=false), —
    # но DO_NOTHING у Payroll — наследие managed=False-схемы (NO ACTION в SQL,
    # отказ всплывает IntegrityError'ом лишь на коммите). Новые managed-модели
    # проекта (feedback, extra_lessons) берут PROTECT: отказ сразу и понятным
    # ProtectedError, в БД при этом тот же FK без каскада.
    teacher = models.ForeignKey(
        'teachers.Teacher', on_delete=models.PROTECT,
        db_column='teacher_id', related_name='cancellation_pays',
        # Покрыт префиксом составного payroll_cancel_tch_date_idx.
        db_index=False,
    )
    group = models.ForeignKey(
        'groups.Group', on_delete=models.CASCADE,
        db_column='group_id', related_name='cancellation_pays',
        # Покрыт префиксом составного payroll_cancel_key_idx.
        db_index=False,
    )
    lesson_date = models.DateField()
    # Время отменённого занятия: в один день у группы может быть два слота,
    # без времени ключ не различил бы их отмены.
    scheduled_time = models.TimeField()
    payment = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    calc_payment = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_manual = models.BooleanField(default=False)
    rule_note = models.TextField(default='')

    class Meta:
        managed = True
        db_table = 'payroll_cancellations'
        indexes = [
            # Сводка/кабинет: начисления преподавателя за период.
            models.Index(fields=['teacher', 'lesson_date'],
                         name='payroll_cancel_tch_date_idx'),
            # Естественный ключ: привязка сирот к маркерам и пересчёт по группам.
            # Индекс, не UNIQUE: сопоставление «сирота ↔ маркер» по ключу — правило
            # движка (§2), в БД его не фиксируем.
            models.Index(fields=['group', 'lesson_date', 'scheduled_time'],
                         name='payroll_cancel_key_idx'),
        ]
        constraints = [
            # Только расчётная сумма: движок не даёт отрицательных начислений.
            # payment не ограничен — ручная правка отмены ведёт себя как правка
            # урока (у Payroll.payment CHECK нет), решение за человеком.
            models.CheckConstraint(
                name='payroll_cancellations_calc_payment_check',
                condition=models.Q(calc_payment__gte=0),
            ),
        ]
