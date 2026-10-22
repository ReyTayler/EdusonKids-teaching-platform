"""
«Настройки → Мотивация»: версии ставок школы и персональные ставки
(спека docs/specs/2026-10-20-eduson-motivation-payroll-design.md, §4, §6).

Чтение для API и доменные операции. Правил расчёта здесь нет — они в
apps/payroll/engine.py; каждая мутация в той же транзакции пересчитывает
затронутые начисления (apps/payroll/recalc.py) и отдаёт их число
(`recalculated` = changed пересчёта): фронт показывает тост «Пересчитано: N».

Поправка владельца 2026-10-22 (§8): изменения ставок не меняют прошлое.
  * Версию школы создают только с сегодняшнего дня или позже; исправить или
    удалить можно только будущую (effective_from > сегодня).
  * Персональная ставка хранит историю периодов (PersonalRateTerm): правка,
    выключение и удаление закрывают текущий период сегодняшним днём.
  * Пересчёт после мутаций — с сегодняшнего дня (по МСК).

Конкурентность:
  * версии ставок — все мутации сериализованы транзакционным advisory-замком
    (_lock_rate_versions) ДО чтения соседних версий: период пересчёта зависит
    от соседей, и две параллельные правки иначе пересчитали бы каждая свой
    устаревший отрезок. Строчный FOR UPDATE по всем версиям не годится: он не
    видит вставку новой версии (в т.ч. в пустую таблицу), а замок — видит.
    Пересчёт этот замок не берёт, поэтому цикла с записью урока нет;
  * персональные ставки — непересечение периодов держит БД (EXCLUDE, см.
    PersonalRateTerm); сервис переводит нарушение в 400 по имени ограничения.
    Мутации одной ставки сериализованы локом её строки.

Тексты ошибок — здесь, в домене; сериализаторы берут их отсюда.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Callable, Optional

from django.db import IntegrityError, connection, transaction
from django.db.models import (
    BooleanField,
    Case,
    Count,
    F,
    Min,
    OuterRef,
    Q,
    Subquery,
    TextField,
    Value,
    When,
)
from django.db.models.functions import Coalesce, NullIf
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.core.utils.dates import msk_now
from apps.payroll.models import (
    PERSONAL_ALL_EXCLUDE,
    PERSONAL_RANGE_CHECK,
    PERSONAL_RANGES_EXCLUDE,
    PayRateDirection,
    PayRateVersion,
    PersonalRate,
    PersonalRateTerm,
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
GROUP_NOT_OF_TEACHER = 'Эту группу ведёт другой преподаватель — персональную ставку можно задать только в его группе.'
RANGES_OVERLAP = 'Диапазон уроков пересекается с уже заданным у этого преподавателя в этой группе.'
VERSION_IN_PAST = 'Новая версия — только с сегодняшнего дня или позже.'
VERSION_STARTED = ('Версия действует с прошлой даты — по ней уже посчитано прошлое. '
                   'Задайте новую версию с сегодняшнего дня или позже.')
PAIR_IMMUTABLE = ('Преподавателя и группу у ставки не меняют — создайте новую ставку '
                  '(у прежней остаётся история).')
INACTIVE_EDIT = 'Ставка выключена — включите её, чтобы изменить уроки, вид или значение.'

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
    PERSONAL_ALL_EXCLUDE: ('non_field_errors', ALL_LESSONS_TAKEN),
    PERSONAL_RANGE_CHECK: ('lesson_to', RANGE_ORDER),
    'payroll_personal_terms_kind_check': ('kind', UNKNOWN_MODE),
    'payroll_personal_terms_value_check': ('value', NEGATIVE_AMOUNT),
}

_VERSION_FIELDS = ('effective_from', 'group_rate', 'individual_rate', 'cancel_mode',
                   'cancel_value', 'penalty_enabled', 'penalty_mode', 'penalty_amount')

# Сгорание не оплачивается при любой ставке (engine.BURNED) — в «уроках без
# ставки» оно только пугало бы лишним числом.
_BURNED = 'burned'

# Ключ транзакционного advisory-замка мутаций версий ставок. Произвольная
# константа, уникальная в проекте (других advisory-замков в журнале нет).
_RATE_VERSIONS_LOCK = 7_310_052_026


def _today() -> datetime.date:
    """«Сегодня» по МСК — граница, с которой действуют изменения ставок (§8)."""
    return msk_now().date()


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


def _current_version_id(today: datetime.date, versions=None) -> Optional[int]:
    """Версия, действующая сегодня по МСК: последняя с effective_from ≤ сегодня."""
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
                  current_id: Optional[int], today: datetime.date) -> dict:
    cells = {d.direction_id: d for d in version.directions.all()}
    row = {'id': version.id}
    row.update({name: getattr(version, name) for name in _VERSION_FIELDS})
    row.update({
        'is_current': version.id == current_id,
        # Исправить/удалить можно версию с сегодня или позже (§8 + решение
        # координатора: начатую сегодня правят весь день, как период ставки).
        'editable': version.effective_from >= today,
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
    today = _today()  # одно на is_current и editable
    current_id = _current_version_id(today, versions)
    return {
        'results': [_version_dict(v, directions, current_id, today) for v in versions],
        'no_rate': _no_rate(versions[-1].effective_from if versions else None),
        'directions': directions,
    }


def _get_version(version_id: int) -> dict:
    version = _versions_qs().get(id=version_id)
    used = {d.direction_id for d in version.directions.all()}
    today = _today()
    return _version_dict(version, _table_directions(used), _current_version_id(today), today)


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


def _check_not_past(effective_from: datetime.date, today: datetime.date) -> None:
    if effective_from < today:
        raise ValidationError({'effective_from': [VERSION_IN_PAST]})


def _check_not_started(version: PayRateVersion, today: datetime.date) -> None:
    """Версию, действующую с прошлой даты, не трогают: по ней уже посчитано
    прошлое (§8). Начатую сегодня — можно: прошлых дат у неё ещё нет."""
    if version.effective_from < today:
        raise ValidationError({'non_field_errors': [VERSION_STARTED]})


def create_version(data: dict, account) -> dict:
    directions = data.pop('directions', None) or []
    with transaction.atomic():
        _lock_rate_versions()
        # «Сегодня» — один раз и под замком: проверка даты и граница пересчёта
        # не разойдутся через полночь.
        today = _today()
        _check_not_past(data['effective_from'], today)
        version = PayRateVersion(created_by=account, **data)

        def write():
            version.save()
            _write_directions(version, directions)
        _guarded(write, _version_error)
        # max(…, сегодня) — страховка: прошлое пересчётом ставок не трогаем.
        stats = recalculate(date_from=max(version.effective_from, today),
                            date_to=_period_end(version.effective_from, version.id),
                            today=today)
    return {**_get_version(version.id), 'recalculated': stats['changed']}


def update_version(version_id: int, data: dict) -> Optional[dict]:
    """Исправление будущей версии, в т.ч. даты (не раньше сегодня). Пересчёт — от
    min(старая, новая дата) до следующей версии после max(старая, новая): перенос
    даты в любую сторону меняет версию, действующую на этом отрезке (лишнее
    пересчитать безопасно — пересчёт идемпотентен)."""
    directions = data.pop('directions', None)
    with transaction.atomic():
        _lock_rate_versions()
        today = _today()
        version = PayRateVersion.objects.filter(id=version_id).first()
        if version is None:
            return None
        _check_not_started(version, today)
        if 'effective_from' in data:
            _check_not_past(data['effective_from'], today)
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
        stats = recalculate(date_from=max(min(old_from, new_from), today),
                            date_to=_period_end(max(old_from, new_from), version.id),
                            today=today)
    return {**_get_version(version.id), 'recalculated': stats['changed']}


def delete_version(version_id: int) -> Optional[dict]:
    """Удаление будущей версии: её период пересчитывается по предыдущей версии
    (или «нет ставки», если предыдущей нет) — §6, §8."""
    with transaction.atomic():
        _lock_rate_versions()
        today = _today()
        version = PayRateVersion.objects.filter(id=version_id).first()
        if version is None:
            return None
        _check_not_started(version, today)
        date_from = max(version.effective_from, today)
        date_to = _period_end(version.effective_from, version.id)
        version.delete()
        stats = recalculate(date_from=date_from, date_to=date_to, today=today)
    return {'recalculated': stats['changed']}


# ---------------------------------------------------------------------------
# Персональные ставки
# ---------------------------------------------------------------------------
#
# Поправка §8: ставка — сущность, параметры — в периодах PersonalRateTerm.
# Любое действие (создать, изменить параметры, выключить, включить, удалить)
# действует с сегодняшнего дня по МСК: текущий период закрывается сегодняшней
# датой (valid_until не включительно), новый начинается сегодня. Период,
# начатый сегодня, правится на месте — пустых периодов от правок не плодим.
# Пересчёт после мутации — с сегодняшнего дня: прошлое по определению то же.

# Параметры ставки, живущие в периоде.
TERM_PARAMS = ('lesson_from', 'lesson_to', 'kind', 'value')

# Whitelist сортировки: ключ API → поле ORDER BY (вторичные ключи и id —
# детерминированный тай-брейк, иначе страницы поедут).
PERSONAL_SORTS = {
    'teacher': 'teacher__name',
    'group': 'group__name',
    'lesson_from': 'lesson_from',
}
PERSONAL_DEFAULT_SORT = 'teacher'

_TERM_FIELDS = ('id', 'valid_from', 'valid_until') + TERM_PARAMS


def _latest_terms():
    """Периоды ставки от последнего к первому. Новые периоды начинаются только
    «сегодня», поэтому последний по valid_from — действующий (у включённой
    ставки) или последний действовавший (у выключенной). NULL — «с начала»,
    то есть самый ранний."""
    return PersonalRateTerm.objects.order_by(F('valid_from').desc(nulls_last=True), '-id')


def _shown_term(field: str) -> Subquery:
    return Subquery(_latest_terms().filter(rate=OuterRef('pk')).values(field)[:1])


def personal_rates_queryset(*, teacher_id: Optional[int], group_id: Optional[int],
                            sort_by: str, sort_dir: str):
    """Неудалённые ставки с параметрами текущего (у выключенной — последнего)
    периода. Сам список периодов — attach_terms (одним запросом на страницу)."""
    qs = PersonalRate.objects.filter(deleted_at__isnull=True)
    if teacher_id is not None:
        qs = qs.filter(teacher_id=teacher_id)
    if group_id is not None:
        qs = qs.filter(group_id=group_id)
    qs = qs.annotate(
        term_id=_shown_term('id'),
        **{name: _shown_term(name) for name in ('valid_from', 'valid_until') + TERM_PARAMS},
    )
    primary = F(PERSONAL_SORTS[sort_by])
    # «Все уроки» (NULL) — первыми: это база, диапазоны её уточняют.
    primary = (primary.desc(nulls_last=True) if sort_dir == 'desc'
               else primary.asc(nulls_first=True))
    return qs.values(
        'id', 'teacher_id', 'group_id', 'note', 'is_active', 'created_by_id', 'created_at',
        'term_id', 'valid_from', 'valid_until', *TERM_PARAMS,
        teacher_name=F('teacher__name'),
        group_name=F('group__name'),
        group_teacher_id=F('group__teacher_id'),
        # Пометка «группу ведёт другой преподаватель» (§8): ставка действует,
        # только когда урок ведёт её преподаватель, — у чужой группы она спит.
        # Группа без преподавателя — тоже «не его».
        group_of_other_teacher=Case(When(group__teacher_id=F('teacher_id'), then=Value(False)),
                                    default=Value(True), output_field=BooleanField()),
        direction_id=F('group__direction_id'),
        direction_name=F('group__direction__name'),
        direction_color=F('group__direction__color'),
        created_by_name=Coalesce(NullIf('created_by__full_name', Value('')),
                                 'created_by__email', output_field=TextField()),
    ).order_by(primary, 'teacher__name', 'group__name',
               F('lesson_from').asc(nulls_first=True), 'id')


def attach_terms(rows: list[dict]) -> list[dict]:
    """История периодов к строкам ставок — один запрос на все строки.
    Периоды — от первого к последнему (как читается история)."""
    by_rate: dict[int, list[dict]] = {row['id']: [] for row in rows}
    if by_rate:
        terms = (PersonalRateTerm.objects.filter(rate_id__in=list(by_rate))
                 .order_by('rate_id', F('valid_from').asc(nulls_first=True), 'id')
                 .values('rate_id', *_TERM_FIELDS))
        for term in terms:
            by_rate[term.pop('rate_id')].append(term)
    for row in rows:
        row['terms'] = by_rate[row['id']]
    return rows


def _personal_dict(rate_id: int) -> dict:
    row = personal_rates_queryset(teacher_id=None, group_id=None,
                                  sort_by=PERSONAL_DEFAULT_SORT, sort_dir='asc'
                                  ).get(id=rate_id)
    return attach_terms([row])[0]


def _lesson_no(value: Decimal) -> str:
    # 10.0 → «10», 10.5 → «10.5»; normalize() без 'f' дал бы «1E+1».
    return format(value.normalize(), 'f')


def _personal_error(term: PersonalRateTerm,
                    today: datetime.date) -> Callable[[Optional[str]], Optional[dict]]:
    def translate(name: Optional[str]) -> Optional[dict]:
        if name == PERSONAL_RANGES_EXCLUDE:
            # Только для текста: сам запрет уже сделала БД. Записываемый период
            # всегда открыт с сегодня или раньше (новый с сегодня, правка начатого
            # сегодня, открытый обратно при включении), а до сегодня он ни с чем
            # не пересекался — значит, конфликт с периодом, действующим после
            # сегодняшнего дня.
            other = (PersonalRateTerm.objects
                     .filter(teacher_id=term.teacher_id, group_id=term.group_id,
                             lesson_from__lte=term.lesson_to, lesson_to__gte=term.lesson_from)
                     .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=today))
                     .exclude(id=term.id or 0).values('lesson_from', 'lesson_to').first())
            if other is None:
                return {'non_field_errors': [RANGES_OVERLAP]}
            return {'non_field_errors': [
                f'Уроки {_lesson_no(term.lesson_from)}–{_lesson_no(term.lesson_to)} '
                f'пересекаются с уже заданными {_lesson_no(other["lesson_from"])}–'
                f'{_lesson_no(other["lesson_to"])} у этого преподавателя в этой группе.']}
        hit = _PERSONAL_CONSTRAINTS.get(name)
        return {hit[0]: [hit[1]]} if hit else None
    return translate


def _recalc_rate_group(group_id: int, today: datetime.date) -> int:
    return recalculate(group_ids=[group_id], date_from=today, today=today)['changed']


def _live_rate_for_update(rate_id: int) -> Optional[PersonalRate]:
    """Неудалённая ставка под локом строки: мутации одной ставки сериализуются,
    и «текущий период» не поменяется между чтением и записью."""
    return (PersonalRate.objects.select_for_update()
            .filter(id=rate_id, deleted_at__isnull=True).first())


def _last_term(rate: PersonalRate) -> PersonalRateTerm:
    # Лок ставки уже взят — периоды этой ставки никто параллельно не пишет.
    return _latest_terms().filter(rate=rate).first()


def _close_today(term: PersonalRateTerm, today: datetime.date) -> None:
    """Закрыть открытый период сегодняшним днём. Период, начатый сегодня,
    становится пустым [сегодня, сегодня): не действует ни на одну дату, но
    хранит параметры для включения."""
    if term.valid_until is None:
        term.valid_until = today
        term.save(update_fields=['valid_until'])


def _start_today(rate: PersonalRate, params: dict, today: datetime.date) -> PersonalRateTerm:
    term = PersonalRateTerm(rate=rate, teacher_id=rate.teacher_id, group_id=rate.group_id,
                            valid_from=today, valid_until=None, **params)
    _guarded(term.save, _personal_error(term, today))
    return term


def create_personal_rate(data: dict, account) -> dict:
    """Новая ставка с первым периодом «с сегодняшнего дня»."""
    params = {name: data.get(name) for name in TERM_PARAMS}
    with transaction.atomic():
        # Лочить нечего (строки ещё нет), непересечение держит EXCLUDE; «сегодня»
        # — одно на запись периода и пересчёт.
        today = _today()
        rate = PersonalRate.objects.create(
            teacher_id=data['teacher_id'], group_id=data['group_id'],
            note=data.get('note', ''), created_by=account)
        _start_today(rate, params, today)
        recalculated = _recalc_rate_group(rate.group_id, today)
    return {**_personal_dict(rate.id), 'recalculated': recalculated}


def update_personal_rate(rate_id: int, data: dict) -> Optional[dict]:
    """Правка примечания — на месте; правка параметров — с сегодняшнего дня
    (текущий период закрывается, новый начинается сегодня; начатый сегодня
    правится на месте). Пару преподаватель × группа сериализатор не пускает."""
    with transaction.atomic():
        rate = _live_rate_for_update(rate_id)
        if rate is None:
            return None
        today = _today()  # под локом ставки — одно на всю операцию
        if 'note' in data and data['note'] != rate.note:
            rate.note = data['note']
            rate.save(update_fields=['note'])

        last = _last_term(rate)
        changed = {name: data[name] for name in TERM_PARAMS
                   if name in data and data[name] != getattr(last, name)}
        recalculated = 0
        if changed:
            if not rate.is_active:
                raise ValidationError({'non_field_errors': [INACTIVE_EDIT]})
            if last.valid_from == today:
                for name, value in changed.items():
                    setattr(last, name, value)
                _guarded(lambda: last.save(update_fields=list(changed)),
                         _personal_error(last, today))
            else:
                params = {name: getattr(last, name) for name in TERM_PARAMS} | changed

                # Отказ БД по пересечению (400) откатывает и закрытие — вместе
                # с внешней транзакцией.
                _close_today(last, today)
                _start_today(rate, params, today)
            recalculated = _recalc_rate_group(rate.group_id, today)
    return {**_personal_dict(rate.id), 'recalculated': recalculated}


def deactivate_personal_rate(rate_id: int) -> Optional[dict]:
    """Выключить с сегодняшнего дня: текущий период закрывается сегодня, уроки
    с сегодня — по ставке школы. Повтор — без изменений (идемпотентно)."""
    with transaction.atomic():
        rate = _live_rate_for_update(rate_id)
        if rate is None:
            return None
        today = _today()
        recalculated = 0
        if rate.is_active:
            _close_today(_last_term(rate), today)
            rate.is_active = False
            rate.save(update_fields=['is_active'])
            recalculated = _recalc_rate_group(rate.group_id, today)
    return {**_personal_dict(rate.id), 'recalculated': recalculated}


def activate_personal_rate(rate_id: int) -> Optional[dict]:
    """Включить с сегодняшнего дня с последними параметрами. Выключили сегодня же —
    закрытый сегодня период открывается обратно (перерыва не было, пустых
    периодов не плодим). Повтор — без изменений (идемпотентно)."""
    with transaction.atomic():
        rate = _live_rate_for_update(rate_id)
        if rate is None:
            return None
        today = _today()
        recalculated = 0
        if not rate.is_active:
            last = _last_term(rate)
            if last.valid_until == today:
                last.valid_until = None
                _guarded(lambda: last.save(update_fields=['valid_until']),
                         _personal_error(last, today))
            else:
                _start_today(rate, {name: getattr(last, name) for name in TERM_PARAMS}, today)
            rate.is_active = True
            rate.save(update_fields=['is_active'])
            recalculated = _recalc_rate_group(rate.group_id, today)
    return {**_personal_dict(rate.id), 'recalculated': recalculated}


def delete_personal_rate(rate_id: int) -> Optional[dict]:
    """Удаление — как выключение плюс deleted_at: ставка уходит из списка, её
    закрытые периоды продолжают считать прошлое."""
    with transaction.atomic():
        rate = _live_rate_for_update(rate_id)
        if rate is None:
            return None
        today = _today()
        if rate.is_active:
            _close_today(_last_term(rate), today)
        rate.is_active = False
        rate.deleted_at = timezone.now()
        rate.save(update_fields=['is_active', 'deleted_at'])
        recalculated = _recalc_rate_group(rate.group_id, today)
    return {'recalculated': recalculated}


def personal_rate_state(rate_id: int) -> Optional[dict]:
    """Пара и параметры последнего периода неудалённой ставки — instance для
    валидации PATCH (диапазон проверяется вместе с текущими значениями)."""
    rate = (PersonalRate.objects.filter(id=rate_id, deleted_at__isnull=True)
            .values('teacher_id', 'group_id').first())
    if rate is None:
        return None
    term = _latest_terms().filter(rate_id=rate_id).values(*TERM_PARAMS).first() or {}
    return {**rate, **term}
