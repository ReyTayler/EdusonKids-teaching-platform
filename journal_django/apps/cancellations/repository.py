"""
Запросы справочника причин отмены и запись об отмене урока.

Справочник маленький (единицы строк на сторону) и ограничен экраном настроек,
поэтому список читается целиком одним запросом, без пагинации — осознанное
исключение, как у стадий продлений.
"""
from __future__ import annotations

from django.db.models import Max, Q, Value
from django.db.models.functions import Lower
from django.utils import timezone

from apps.cancellations.models import CancellationReason, CancellationSide, LessonCancellation
from apps.cancellations.rules import CancellationDraft, is_compensated
from apps.payroll.engine import CancellationDecision

# Поля строки справочника для API; compensation вырезает services.serialize_reason
# для не-superadmin.
ROW_FIELDS = ('id', 'side', 'name', 'position', 'compensation')


def _active():
    return CancellationReason.objects.filter(deleted_at__isnull=True)


def list_active() -> dict[str, list[CancellationReason]]:
    """Активные причины по сторонам, ORDER BY position, id — один запрос.

    id — детерминированный тай-брейк: позиции двух причин могут совпасть
    (параллельное создание или ручной PATCH position)."""
    out: dict[str, list[CancellationReason]] = {s.value: [] for s in CancellationSide}
    for reason in _active().only(*ROW_FIELDS).order_by('position', 'id'):
        out[reason.side].append(reason)
    return out


def get_active(pk: int) -> CancellationReason | None:
    return _active().filter(pk=pk).first()


def get_active_for_update(pk: int) -> CancellationReason | None:
    """Под локом строки: правки одной причины сериализуются, проверка дубля
    имени и запись видят одно и то же состояние этой строки.

    FOR NO KEY UPDATE: ключ причины не меняется, а INSERT записи об отмене
    берёт на строку причины FOR KEY SHARE (FK) — с NO KEY UPDATE они
    совместимы, и правка справочника не тормозит отмены уроков."""
    return _active().select_for_update(no_key=True).filter(pk=pk).first()


def active_name_taken(side: str, name: str, *, exclude_id: int | None = None) -> bool:
    """Есть ли среди активных причин стороны такое же имя без учёта регистра.

    Предпроверка ради понятного текста ошибки; гонку двух одинаковых вставок
    ловит частичный UNIQUE cancel_reason_active_name_uq (services переводит
    его в тот же текст)."""
    # Lower(), а не iexact (UPPER): сравнение обязано совпадать с выражением
    # UNIQUE (models.py) — для символов, где UPPER и LOWER расходятся, иначе
    # предпроверка и БД давали бы разные ответы.
    qs = (_active().annotate(_lname=Lower('name'))
          .filter(side=side, _lname=Lower(Value(name))))
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    return qs.exists()


def next_position(side: str) -> int:
    """Позиция новой причины — в конец стороны (0 для пустой).

    Две параллельные вставки могут получить одну позицию — это безвредно:
    порядок списка добивается тай-брейком по id."""
    top = _active().filter(side=side).aggregate(top=Max('position'))['top']
    return 0 if top is None else top + 1


def create(*, side: str, name: str, compensation: bool) -> CancellationReason:
    return CancellationReason.objects.create(
        side=side, name=name, compensation=compensation, position=next_position(side),
    )


def create_for_marker(marker, draft: CancellationDraft) -> LessonCancellation:
    """Запись об отмене для только что вставленного маркера.

    Вызывается из scheduling.repository.cancel_lesson ВНУТРИ его транзакции:
    после лока курса и INSERT маркера, до пересчёта начисления — порядок
    «план → запись отмены → деньги». compensated — снимок решения по дате
    маркера (спека §4); teacher — эффективный преподаватель маркера."""
    return LessonCancellation.objects.create(
        group_id=marker.group_id, lesson_date=marker.scheduled_date,
        scheduled_time=marker.scheduled_time, teacher_id=marker.teacher_id,
        planned_lesson=marker, side=draft.side, reason_id=draft.reason_id,
        reason_text=draft.other_text if draft.is_other else draft.reason_name,
        is_other=draft.is_other, notified_on=draft.notified_on,
        compensated=is_compensated(
            side=draft.side, is_other=draft.is_other,
            reason_compensation=draft.reason_compensation,
            notified_on=draft.notified_on, lesson_date=marker.scheduled_date,
        ),
        created_by_id=draft.created_by_id,
    )


# Поля записи, нужные привязке и решению; остальное пересчёту не нужно.
_RESOLVE_FIELDS = ('id', 'planned_lesson_id', 'group_id', 'lesson_date', 'scheduled_time',
                   'side', 'is_other', 'reason_text', 'compensated')

# «Другая причина» в пояснении начисления — без введённого текста: свободный
# текст может содержать ПДн (имя клиента), а пояснение видит преподаватель в
# кабинете. Полный текст — только во вкладке группы.
_OTHER_REASON_NOTE = 'Другая причина'

Key = tuple  # (group_id, lesson_date, scheduled_time) — естественный ключ отмены


def _decision(rec: LessonCancellation) -> CancellationDecision:
    return CancellationDecision(
        compensated=rec.compensated,
        side_label=CancellationSide(rec.side).label,
        reason=_OTHER_REASON_NOTE if rec.is_other else rec.reason_text,
    )


def lock_for_markers(marker_rows: list[dict]) -> list[LessonCancellation]:
    """Залочить записи об отменах, которые пересчёт может отдать маркерам.

    marker_rows — маркеры, УЖЕ залоченные пересчётом (payroll.recalc._lock_markers).
    Одним SELECT … FOR UPDATE: записи этих маркеров по FK и сироты их групп за
    их даты. Сирота-кандидат всегда той же даты, что маркер: и по ключу, и по
    дню начисление-сирота привязывается только к маркеру своей даты. Произведение
    групп × дат шире пар (группа, дата) — лишние просто ни к чему не привяжутся.

    Лок — до лока начислений (порядок «план → записи отмен → деньги»), а решение о
    привязке — позже, в bind_for_markers: запись идёт за начислением. Лок нужен
    сиротам: два пересчёта соседних маркеров группы иначе привязали бы одну запись
    дважды."""
    if not marker_rows:
        return []
    return list(
        LessonCancellation.objects.select_for_update(of=('self',))
        .filter(Q(planned_lesson_id__in=[r['id'] for r in marker_rows])
                | Q(planned_lesson__isnull=True,
                    group_id__in={r['group_id'] for r in marker_rows},
                    lesson_date__in={r['scheduled_date'] for r in marker_rows}))
        .only(*_RESOLVE_FIELDS)
        .order_by('id')
    )


def bind_for_markers(marker_rows: list[dict], records: list[LessonCancellation],
                     follow: dict[int, Key]) -> dict[int, CancellationDecision]:
    """Решение об отмене для маркеров пересчёта: {id маркера: решение}. Маркера без
    записи в ответе нет — он платится, как отмена до фичи причин.

    records — результат lock_for_markers. follow — {id маркера: исходный ключ
    начисления-сироты}, которое пересчёт только что привязал к этому маркеру (по
    ключу или по дню).

    Запись СЛЕДУЕТ ЗА начислением, своей привязки по дню у неё нет. Иначе множества
    сирот расходятся (будущее начисление удаляется, запись — никогда): запись
    снятой отмены цеплялась бы к чужому маркеру дня и обнуляла его, а прошлое
    начисление, привязанное по дню, теряло бы своё решение и менялось задним числом.
    Порядок для маркера:
      1. своя запись по FK;
      2. к маркеру привязалось начисление-сирота — запись-сирота с ИСХОДНЫМ ключом
         этого начисления (решение той же отмены);
      3. иначе — запись-сирота по точному ключу самого маркера.
    При нескольких сиротах одного ключа — самая новая: последнее решение человека
    (В7д). Привязанные записи получают ссылку одним bulk_update (planned_lesson
    исключён из журнала — событий нет)."""
    linked = {r.planned_lesson_id: r for r in records if r.planned_lesson_id is not None}
    orphans: dict[Key, list[LessonCancellation]] = {}
    for rec in reversed(records):  # новые первыми
        if rec.planned_lesson_id is None:
            orphans.setdefault((rec.group_id, rec.lesson_date, rec.scheduled_time),
                               []).append(rec)

    decisions: dict[int, CancellationDecision] = {}
    rebound: list[LessonCancellation] = []
    for row in marker_rows:
        rec = linked.get(row['id'])
        if rec is None:
            key = follow.get(row['id'],
                             (row['group_id'], row['scheduled_date'], row['scheduled_time']))
            bucket = orphans.get(key)
            if bucket:
                rec = bucket.pop(0)
                rec.planned_lesson_id = row['id']
                rebound.append(rec)
        if rec is not None:
            decisions[row['id']] = _decision(rec)
    if rebound:
        LessonCancellation.objects.bulk_update(rebound, ['planned_lesson'])
    return decisions


def soft_delete(pk: int) -> bool:
    """Мягкое удаление одним UPDATE; False — причины нет или она уже удалена.

    QuerySet.update() обходит auto_now, поэтому updated_at ставим явно."""
    now = timezone.now()
    return _active().filter(pk=pk).update(deleted_at=now, updated_at=now) > 0
