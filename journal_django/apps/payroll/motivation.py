"""
«Настройки → Мотивация»: версии ставок школы и персональные ставки
(спека docs/specs/2026-10-20-eduson-motivation-payroll-design.md, §4, §6).

Чтение для API и доменные операции. Правил расчёта здесь нет — они в
apps/payroll/engine.py; каждая мутация в той же транзакции пересчитывает
затронутые начисления (apps/payroll/recalc.py) и отдаёт их число
(`recalculated` = changed пересчёта): фронт показывает тост «Пересчитано: N».

Конкурентность:
  * версии ставок — все мутации сериализованы транзакционным advisory-замком
    (_lock_rate_versions) ДО чтения соседних версий: период пересчёта зависит
    от соседей, и две параллельные правки иначе пересчитали бы каждая свой
    устаревший отрезок. Строчный FOR UPDATE по всем версиям не годится: он не
    видит вставку новой версии (в т.ч. в пустую таблицу), а замок — видит.
    Пересчёт этот замок не берёт, поэтому цикла с записью урока нет;
  * персональные ставки — непересечение держит БД (частичный UNIQUE и EXCLUDE,
    см. PersonalRate); сервис переводит нарушение в 400 по имени ограничения.

Тексты ошибок — здесь, в домене; сериализаторы берут их отсюда.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Callable, Optional

from django.db import IntegrityError, connection, transaction
from django.db.models import Count, F, Min, Q, TextField, Value
from django.db.models.functions import Coalesce, NullIf
from rest_framework.exceptions import ValidationError

from apps.core.utils.dates import msk_now
from apps.payroll.models import (
    PERSONAL_ALL_LESSONS_UNIQUE,
    PERSONAL_RANGE_CHECK,
    PERSONAL_RANGES_EXCLUDE,
    PayRateDirection,
    PayRateVersion,
    PersonalRate,
)
from apps.payroll.recalc import recalculate

# ---------------------------------------------------------------------------
# Тексты ошибок
# ---------------------------------------------------------------------------

DUPLICATE_VERSION_DATE = ('Версия ставок с этой даты уже есть — исправьте её '
                          'или выберите другую дату.')
CANCEL_PERCENT_LIMIT = 'Процент за отмену — не больше 100.'
NEGATIVE_AMOUNT = 'Сумма не может быть отрицательной.'
UNKNOWN_MODE = 'Недопустимое значение.'
DUPLICATE_DIRECTION = 'Направление указано дважды.'
RANGE_BOTH_OR_NONE = 'Укажите оба номера урока («с» и «по») или ни одного — «все уроки».'
RANGE_ORDER = 'Номер «по» не может быть меньше номера «с».'
ALL_LESSONS_TAKEN = 'У преподавателя уже есть ставка «все уроки» в этой группе.'
RANGES_OVERLAP = 'Диапазон уроков пересекается с уже заданным у этого преподавателя в этой группе.'

# Нарушение ограничения БД → (поле ответа, текст). Ключ — имя ограничения или,
# для UNIQUE даты, его хвост: имя автоматическое (<таблица>_effective_from_key).
_VERSION_CONSTRAINTS = {
    'payroll_rate_versions_rates_check': ('group_rate', NEGATIVE_AMOUNT),
    'payroll_rate_versions_cancel_mode_check': ('cancel_mode', UNKNOWN_MODE),
    'payroll_rate_versions_cancel_value_check': ('cancel_value', CANCEL_PERCENT_LIMIT),
    'payroll_rate_versions_penalty_mode_check': ('penalty_mode', UNKNOWN_MODE),
    'payroll_rate_versions_penalty_amount_check': ('penalty_amount', NEGATIVE_AMOUNT),
    'payroll_rate_directions_rates_check': ('directions', NEGATIVE_AMOUNT),
    'payroll_rate_directions_version_direction_uniq': ('directions', DUPLICATE_DIRECTION),
}
_PERSONAL_CONSTRAINTS = {
    PERSONAL_ALL_LESSONS_UNIQUE: ('non_field_errors', ALL_LESSONS_TAKEN),
    PERSONAL_RANGE_CHECK: ('lesson_to', RANGE_ORDER),
    'payroll_personal_rates_kind_check': ('kind', UNKNOWN_MODE),
    'payroll_personal_rates_value_check': ('value', NEGATIVE_AMOUNT),
}

_VERSION_FIELDS = ('effective_from', 'group_rate', 'individual_rate', 'cancel_mode',
                   'cancel_value', 'penalty_enabled', 'penalty_mode', 'penalty_amount')

# Сгорание не оплачивается при любой ставке (engine.BURNED) — в «уроках без
# ставки» оно только пугало бы лишним числом.
_BURNED = 'burned'

# Ключ транзакционного advisory-замка мутаций версий ставок. Произвольная
# константа, уникальная в проекте (других advisory-замков в журнале нет).
_RATE_VERSIONS_LOCK = 7_310_052_026


def _constraint_name(exc: IntegrityError) -> Optional[str]:
    diag = getattr(exc.__cause__, 'diag', None)
    return getattr(diag, 'constraint_name', None)


def _guarded(write: Callable[[], None], translate: Callable[[Optional[str]], Optional[dict]]):
    """Запись под savepoint: нарушение известного ограничения → 400 с текстом,
    неизвестное — дальше (500 честнее, чем неверное объяснение)."""
    try:
        with transaction.atomic():
            write()
    except IntegrityError as exc:
        detail = translate(_constraint_name(exc))
        if detail is None:
            raise
        raise ValidationError(detail) from exc


def _creator_name(account) -> Optional[str]:
    if account is None:
        return None
    return account.full_name or account.email


# ---------------------------------------------------------------------------
# Версии ставок
# ---------------------------------------------------------------------------

def _lock_rate_versions() -> None:
    with connection.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(%s)', [_RATE_VERSIONS_LOCK])


def _version_error(name: Optional[str]) -> Optional[dict]:
    if name is None:
        return None
    if 'effective_from' in name:
        return {'effective_from': [DUPLICATE_VERSION_DATE]}
    hit = _VERSION_CONSTRAINTS.get(name)
    return {hit[0]: [hit[1]]} if hit else None


def _current_version_id(versions=None) -> Optional[int]:
    """Версия, действующая сегодня по МСК: последняя с effective_from ≤ сегодня."""
    today = msk_now().date()
    if versions is not None:
        current = [v for v in versions if v.effective_from <= today]
        return max(current, key=lambda v: v.effective_from).id if current else None
    return (PayRateVersion.objects.filter(effective_from__lte=today)
            .order_by('-effective_from').values_list('id', flat=True).first())


def _table_directions(used_ids: set[int]) -> list[dict]:
    """Строки таблицы ставок: активные направления + архивные, у которых в
    версиях есть своя ставка (иначе она была бы невидима, но продолжала считать)."""
    from apps.directions.models import Direction
    return list(
        Direction.objects.filter(Q(active=True) | Q(id__in=used_ids))
        .order_by('name', 'id').values('id', 'name', 'color')
    )


def _version_dict(version: PayRateVersion, directions: list[dict],
                  current_id: Optional[int]) -> dict:
    cells = {d.direction_id: d for d in version.directions.all()}
    row = {'id': version.id}
    row.update({name: getattr(version, name) for name in _VERSION_FIELDS})
    row.update({
        'is_current': version.id == current_id,
        'created_by': version.created_by_id,
        'created_by_name': _creator_name(version.created_by),
        'created_at': version.created_at,
        'directions': [
            {
                'direction_id': d['id'],
                'direction_name': d['name'],
                'group_rate': cells[d['id']].group_rate if d['id'] in cells else None,
                'individual_rate': cells[d['id']].individual_rate if d['id'] in cells else None,
            }
            for d in directions
        ],
    })
    return row


def _versions_qs():
    return PayRateVersion.objects.select_related('created_by').prefetch_related('directions')


def _no_rate(earliest_version: Optional[datetime.date]) -> dict:
    """Уроки раньше первой версии (или все, если версий нет) — одним агрегатом."""
    from apps.lessons.models import Lesson
    qs = Lesson.objects.exclude(lesson_type=_BURNED)
    if earliest_version is not None:
        qs = qs.filter(lesson_date__lt=earliest_version)
    agg = qs.aggregate(lessons=Count('id'), earliest=Min('lesson_date'))
    return {'lessons': agg['lessons'], 'earliest': agg['earliest']}


def list_versions() -> dict:
    """Все версии (их единицы — без пагинации), новые сверху; таблица направлений;
    предупреждение «уроков без ставки» (§6)."""
    versions = list(_versions_qs().order_by('-effective_from'))
    used = {d.direction_id for v in versions for d in v.directions.all()}
    directions = _table_directions(used)
    current_id = _current_version_id(versions)
    return {
        'results': [_version_dict(v, directions, current_id) for v in versions],
        'no_rate': _no_rate(versions[-1].effective_from if versions else None),
        'directions': directions,
    }


def _get_version(version_id: int) -> dict:
    version = _versions_qs().get(id=version_id)
    used = {d.direction_id for d in version.directions.all()}
    return _version_dict(version, _table_directions(used), _current_version_id())


def _write_directions(version: PayRateVersion, items: list[dict]) -> None:
    """Upsert ячеек по direction_id. Пара null/null — «как по умолчанию», строку
    удаляем (отсутствие строки значит то же самое). Направления, которых нет во
    входе, не трогаем: частичный PATCH не должен стирать чужие ячейки."""
    existing = {d.direction_id: d for d in version.directions.all()}
    to_create, to_update, to_delete = [], [], []
    for item in items:
        group_rate, individual_rate = item.get('group_rate'), item.get('individual_rate')
        current = existing.get(item['direction_id'])
        if group_rate is None and individual_rate is None:
            if current is not None:
                to_delete.append(current.id)
        elif current is None:
            to_create.append(PayRateDirection(
                version=version, direction_id=item['direction_id'],
                group_rate=group_rate, individual_rate=individual_rate))
        elif (current.group_rate, current.individual_rate) != (group_rate, individual_rate):
            current.group_rate, current.individual_rate = group_rate, individual_rate
            to_update.append(current)
    if to_delete:
        PayRateDirection.objects.filter(id__in=to_delete).delete()
    PayRateDirection.objects.bulk_create(to_create)
    PayRateDirection.objects.bulk_update(to_update, ['group_rate', 'individual_rate'])


def _period_end(after: datetime.date, exclude_id: int) -> Optional[datetime.date]:
    """Последний день периода версии: день перед следующей версией после `after`
    (сама версия не в счёт); None — следующей нет, период открыт.
    Зовётся только под _lock_rate_versions."""
    nxt = (PayRateVersion.objects.filter(effective_from__gt=after).exclude(id=exclude_id)
           .order_by('effective_from').values_list('effective_from', flat=True).first())
    return nxt - datetime.timedelta(days=1) if nxt else None


def create_version(data: dict, account) -> dict:
    directions = data.pop('directions', None) or []
    with transaction.atomic():
        _lock_rate_versions()
        version = PayRateVersion(created_by=account, **data)

        def write():
            version.save()
            _write_directions(version, directions)
        _guarded(write, _version_error)
        stats = recalculate(date_from=version.effective_from,
                            date_to=_period_end(version.effective_from, version.id))
    return {**_get_version(version.id), 'recalculated': stats['changed']}


def update_version(version_id: int, data: dict) -> Optional[dict]:
    """Исправление версии, в т.ч. даты. Пересчёт — от min(старая, новая дата) до
    следующей версии после max(старая, новая): перенос даты в любую сторону
    меняет версию, действующую на этом отрезке (лишнее пересчитать безопасно —
    пересчёт идемпотентен)."""
    directions = data.pop('directions', None)
    with transaction.atomic():
        _lock_rate_versions()
        version = PayRateVersion.objects.filter(id=version_id).first()
        if version is None:
            return None
        old_from = version.effective_from
        for name, value in data.items():
            setattr(version, name, value)

        def write():
            if data:
                version.save(update_fields=list(data))
            if directions is not None:
                _write_directions(version, directions)
        _guarded(write, _version_error)
        new_from = version.effective_from
        stats = recalculate(date_from=min(old_from, new_from),
                            date_to=_period_end(max(old_from, new_from), version.id))
    return {**_get_version(version.id), 'recalculated': stats['changed']}


def delete_version(version_id: int) -> Optional[dict]:
    """Удаление версии: её период пересчитывается по предыдущей версии (или
    «нет ставки», если предыдущей нет) — §6."""
    with transaction.atomic():
        _lock_rate_versions()
        version = PayRateVersion.objects.filter(id=version_id).first()
        if version is None:
            return None
        date_from = version.effective_from
        date_to = _period_end(date_from, version.id)
        version.delete()
        stats = recalculate(date_from=date_from, date_to=date_to)
    return {'recalculated': stats['changed']}


# ---------------------------------------------------------------------------
# Персональные ставки
# ---------------------------------------------------------------------------

# Whitelist сортировки: ключ API → поле ORDER BY (вторичные ключи и id —
# детерминированный тай-брейк, иначе страницы поедут).
PERSONAL_SORTS = {
    'teacher': 'teacher__name',
    'group': 'group__name',
    'lesson_from': 'lesson_from',
}
PERSONAL_DEFAULT_SORT = 'teacher'


def personal_rates_queryset(*, teacher_id: Optional[int], group_id: Optional[int],
                            sort_by: str, sort_dir: str):
    qs = PersonalRate.objects.all()
    if teacher_id is not None:
        qs = qs.filter(teacher_id=teacher_id)
    if group_id is not None:
        qs = qs.filter(group_id=group_id)
    primary = F(PERSONAL_SORTS[sort_by])
    # «Все уроки» (NULL) — первыми: это база, диапазоны её уточняют.
    primary = (primary.desc(nulls_last=True) if sort_dir == 'desc'
               else primary.asc(nulls_first=True))
    return qs.values(
        'id', 'teacher_id', 'group_id', 'lesson_from', 'lesson_to', 'kind', 'value',
        'note', 'created_by_id', 'created_at',
        teacher_name=F('teacher__name'),
        group_name=F('group__name'),
        direction_id=F('group__direction_id'),
        direction_name=F('group__direction__name'),
        direction_color=F('group__direction__color'),
        created_by_name=Coalesce(NullIf('created_by__full_name', Value('')),
                                 'created_by__email', output_field=TextField()),
    ).order_by(primary, 'teacher__name', 'group__name',
               F('lesson_from').asc(nulls_first=True), 'id')


def _personal_dict(rate_id: int) -> dict:
    return personal_rates_queryset(teacher_id=None, group_id=None,
                                   sort_by=PERSONAL_DEFAULT_SORT, sort_dir='asc'
                                   ).get(id=rate_id)


def _lesson_no(value: Decimal) -> str:
    # 10.0 → «10», 10.5 → «10.5»; normalize() без 'f' дал бы «1E+1».
    return format(value.normalize(), 'f')


def _personal_error(rate: PersonalRate) -> Callable[[Optional[str]], Optional[dict]]:
    def translate(name: Optional[str]) -> Optional[dict]:
        if name == PERSONAL_RANGES_EXCLUDE:
            # Только для текста: сам запрет уже сделала БД.
            other = (PersonalRate.objects
                     .filter(teacher_id=rate.teacher_id, group_id=rate.group_id,
                             lesson_from__lte=rate.lesson_to, lesson_to__gte=rate.lesson_from)
                     .exclude(id=rate.id or 0).values('lesson_from', 'lesson_to').first())
            if other is None:
                return {'non_field_errors': [RANGES_OVERLAP]}
            return {'non_field_errors': [
                f'Уроки {_lesson_no(rate.lesson_from)}–{_lesson_no(rate.lesson_to)} '
                f'пересекаются с уже заданными {_lesson_no(other["lesson_from"])}–'
                f'{_lesson_no(other["lesson_to"])} у этого преподавателя в этой группе.']}
        hit = _PERSONAL_CONSTRAINTS.get(name)
        return {hit[0]: [hit[1]]} if hit else None
    return translate


def create_personal_rate(data: dict, account) -> dict:
    with transaction.atomic():
        rate = PersonalRate(created_by=account, **data)
        _guarded(rate.save, _personal_error(rate))
        stats = recalculate(group_ids=[rate.group_id])
    return {**_personal_dict(rate.id), 'recalculated': stats['changed']}


def update_personal_rate(rate_id: int, data: dict) -> Optional[dict]:
    with transaction.atomic():
        # Старую группу читаем под локом строки: иначе параллельная правка могла
        # сменить её между чтением и записью, и старая группа осталась бы
        # непересчитанной.
        rate = PersonalRate.objects.select_for_update().filter(id=rate_id).first()
        if rate is None:
            return None
        old_group = rate.group_id
        for name, value in data.items():
            setattr(rate, name, value)
        if data:
            _guarded(lambda: rate.save(update_fields=list(data)), _personal_error(rate))
        stats = recalculate(group_ids=sorted({old_group, rate.group_id}))
    return {**_personal_dict(rate.id), 'recalculated': stats['changed']}


def delete_personal_rate(rate_id: int) -> Optional[dict]:
    with transaction.atomic():
        rate = PersonalRate.objects.select_for_update().filter(id=rate_id).first()
        if rate is None:
            return None
        group_id = rate.group_id
        rate.delete()
        stats = recalculate(group_ids=[group_id])
    return {'recalculated': stats['changed']}
