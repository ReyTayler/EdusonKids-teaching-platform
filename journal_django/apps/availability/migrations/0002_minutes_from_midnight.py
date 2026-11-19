"""
График работы: время окон — минуты от полуночи вместо TimeField (спека
2026-11-19-schedule-gcal-and-drop-columns-design.md, часть 2).

TimeField не хранит «24:00», а сетка теперь — сутки 00:00–24:00.

Порядок операций выбран под откат и под журнал изменений:
1. Сначала снимаем триггеры pghistory: перенос данных — техническая правка,
   а не действие пользователя; иначе каждая строка породила бы событие update.
2. Новые поля добавляются nullable, старые rule-поля временно делаются
   nullable — тогда в обратную сторону RemoveField вернёт колонку без DEFAULT
   на непустую таблицу, а RunPython заполнит её до возврата NOT NULL.
3. Перенос значений идёт и по теневым таблицам событий: иначе история окон
   (pgh_data в «Журнале изменений») потеряла бы время.
4. NOT NULL у rule-полей, новые CHECK/UNIQUE и триггеры — в конце.

Откат возможен не всегда (ensure_reversible), и молча подгонять данные он не будет:
- окна графика должны укладываться в 08:00–22:00 — откат возвращает CHECK-и
  0001 с этой сеткой, и окно 07:00 или 23:00 уронило бы AddConstraint;
- в истории (таблицы событий pghistory) CHECK-ов нет, но конец 24:00 (1440)
  непредставим в TimeField, а обрезка до 23:30 исказила бы историю.
"""
import datetime

import django.db.models.functions.math
import django.db.models.lookups
import pgtrigger.compiler
import pgtrigger.migrations
from django.db import migrations, models

MINUTES_PER_DAY = 24 * 60

# Исходные модели и их теневые таблицы событий pghistory — перенос во всех четырёх.
_MODELS = ('AvailabilityRule', 'AvailabilityOverride',
            'AvailabilityRuleEvent', 'AvailabilityOverrideEvent')


def time_to_minute(t: datetime.time | None) -> int | None:
    """time → минута от полуночи. Секунд в данных нет: их запрещал CHECK 0001."""
    if t is None:
        return None
    return t.hour * 60 + t.minute


def minute_to_time(m: int | None) -> datetime.time | None:
    """Минута от полуночи → time. 1440 (24:00) в TimeField непредставима."""
    if m is None:
        return None
    if not 0 <= m < MINUTES_PER_DAY:
        raise ValueError(f'Минута {m} не помещается в TimeField (00:00–23:59).')
    return datetime.time(m // 60, m % 60)


# Сетка 0001 — к ней вернутся CHECK-и avail_*_in_grid при откате.
_LEGACY_GRID_START = 8 * 60
_LEGACY_GRID_END = 22 * 60
_MAIN_MODELS = ('AvailabilityRule', 'AvailabilityOverride')
_EVENT_MODELS = ('AvailabilityRuleEvent', 'AvailabilityOverrideEvent')


def ensure_reversible(apps) -> None:
    """
    Откат запрещён, пока в графике есть окна вне 08:00–22:00 или в истории
    журнала изменений — окна до 24:00. Пусто — можно откатывать.
    """
    def counts(names, condition):
        out = []
        for name in names:
            n = apps.get_model('availability', name).objects.filter(condition).count()
            if n:
                out.append(f'{name}: {n}')
        return out

    # NULL (день закрыт) под оба условия не попадает — сравнение с NULL ложно.
    main = counts(_MAIN_MODELS, models.Q(start_minute__lt=_LEGACY_GRID_START)
                  | models.Q(end_minute__gt=_LEGACY_GRID_END))
    history = counts(_EVENT_MODELS, models.Q(end_minute__gte=MINUTES_PER_DAY))
    if not (main or history):
        return
    parts = ['Откат availability.0002 невозможен.']
    if main:
        parts.append(
            'В графике есть окна вне 08:00–22:00 — старая схема хранит окна только '
            f'в пределах 08:00–22:00 ({", ".join(main)}). '
            'Измените или удалите эти окна через «График работы».')
    if history:
        lead = ('Откат блокирует только история журнала изменений: '
                if not main else 'Кроме того, в истории журнала изменений ')
        parts.append(
            f'{lead}есть события с окнами до 24:00, которые нельзя записать '
            f'в TimeField ({", ".join(history)}). Правка графика их не уберёт — '
            'это прошлые события. Если откат действительно нужен, удалите эти '
            'строки из таблиц событий availability_availability*event '
            '(история этих окон будет потеряна) — осознанно, после копии БД.')
    raise RuntimeError(' '.join(parts))


def _convert(apps, src: tuple[str, str], dst: tuple[str, str], fn) -> None:
    for name in _MODELS:
        model = apps.get_model('availability', name)
        rows = list(model.objects.exclude(**{f'{src[0]}__isnull': True}).only('pk', *src))
        for row in rows:
            setattr(row, dst[0], fn(getattr(row, src[0])))
            setattr(row, dst[1], fn(getattr(row, src[1])))
        # Строк — сотни (окна ~100 преподавателей), пачки по 500 хватает.
        model.objects.bulk_update(rows, list(dst), batch_size=500)


def forwards(apps, schema_editor):
    _convert(apps, ('start_time', 'end_time'), ('start_minute', 'end_minute'), time_to_minute)


def backwards(apps, schema_editor):
    ensure_reversible(apps)
    _convert(apps, ('start_minute', 'end_minute'), ('start_time', 'end_time'), minute_to_time)


class Migration(migrations.Migration):

    dependencies = [
        ('availability', '0001_initial'),
        ('teachers', '0006_teacher_vk_url'),
    ]

    operations = [
        pgtrigger.migrations.RemoveTrigger(
            model_name='availabilityoverride',
            name='insert_insert',
        ),
        pgtrigger.migrations.RemoveTrigger(
            model_name='availabilityoverride',
            name='update_update',
        ),
        pgtrigger.migrations.RemoveTrigger(
            model_name='availabilityoverride',
            name='delete_delete',
        ),
        pgtrigger.migrations.RemoveTrigger(
            model_name='availabilityrule',
            name='insert_insert',
        ),
        pgtrigger.migrations.RemoveTrigger(
            model_name='availabilityrule',
            name='update_update',
        ),
        pgtrigger.migrations.RemoveTrigger(
            model_name='availabilityrule',
            name='delete_delete',
        ),
        migrations.RemoveConstraint(
            model_name='availabilityoverride',
            name='avail_override_both_or_none',
        ),
        migrations.RemoveConstraint(
            model_name='availabilityoverride',
            name='avail_override_ordered',
        ),
        migrations.RemoveConstraint(
            model_name='availabilityoverride',
            name='avail_override_in_grid',
        ),
        migrations.RemoveConstraint(
            model_name='availabilityoverride',
            name='avail_override_half_hour',
        ),
        migrations.RemoveConstraint(
            model_name='availabilityoverride',
            name='avail_override_one_closed_per_date',
        ),
        migrations.RemoveConstraint(
            model_name='availabilityrule',
            name='avail_rule_ordered',
        ),
        migrations.RemoveConstraint(
            model_name='availabilityrule',
            name='avail_rule_in_grid',
        ),
        migrations.RemoveConstraint(
            model_name='availabilityrule',
            name='avail_rule_half_hour',
        ),
        migrations.AddField(
            model_name='availabilityoverride',
            name='start_minute',
            field=models.SmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='availabilityoverride',
            name='end_minute',
            field=models.SmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='availabilityoverrideevent',
            name='start_minute',
            field=models.SmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='availabilityoverrideevent',
            name='end_minute',
            field=models.SmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='availabilityrule',
            name='start_minute',
            field=models.SmallIntegerField(null=True),
        ),
        migrations.AddField(
            model_name='availabilityrule',
            name='end_minute',
            field=models.SmallIntegerField(null=True),
        ),
        migrations.AddField(
            model_name='availabilityruleevent',
            name='start_minute',
            field=models.SmallIntegerField(null=True),
        ),
        migrations.AddField(
            model_name='availabilityruleevent',
            name='end_minute',
            field=models.SmallIntegerField(null=True),
        ),
        migrations.AlterField(
            model_name='availabilityrule',
            name='start_time',
            field=models.TimeField(null=True),
        ),
        migrations.AlterField(
            model_name='availabilityrule',
            name='end_time',
            field=models.TimeField(null=True),
        ),
        migrations.AlterField(
            model_name='availabilityruleevent',
            name='start_time',
            field=models.TimeField(null=True),
        ),
        migrations.AlterField(
            model_name='availabilityruleevent',
            name='end_time',
            field=models.TimeField(null=True),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(
            model_name='availabilityoverride',
            name='start_time',
        ),
        migrations.RemoveField(
            model_name='availabilityoverride',
            name='end_time',
        ),
        migrations.RemoveField(
            model_name='availabilityoverrideevent',
            name='start_time',
        ),
        migrations.RemoveField(
            model_name='availabilityoverrideevent',
            name='end_time',
        ),
        migrations.RemoveField(
            model_name='availabilityrule',
            name='start_time',
        ),
        migrations.RemoveField(
            model_name='availabilityrule',
            name='end_time',
        ),
        migrations.RemoveField(
            model_name='availabilityruleevent',
            name='start_time',
        ),
        migrations.RemoveField(
            model_name='availabilityruleevent',
            name='end_time',
        ),
        migrations.AlterField(
            model_name='availabilityrule',
            name='start_minute',
            field=models.SmallIntegerField(),
        ),
        migrations.AlterField(
            model_name='availabilityrule',
            name='end_minute',
            field=models.SmallIntegerField(),
        ),
        migrations.AlterField(
            model_name='availabilityruleevent',
            name='start_minute',
            field=models.SmallIntegerField(),
        ),
        migrations.AlterField(
            model_name='availabilityruleevent',
            name='end_minute',
            field=models.SmallIntegerField(),
        ),
        pgtrigger.migrations.AddTrigger(
            model_name='availabilityoverride',
            trigger=pgtrigger.compiler.Trigger(name='insert_insert', sql=pgtrigger.compiler.UpsertTriggerSql(func='INSERT INTO "availability_availabilityoverrideevent" ("date", "end_minute", "id", "pgh_context_id", "pgh_created_at", "pgh_label", "pgh_obj_id", "start_minute", "teacher_id") VALUES (NEW."date", NEW."end_minute", NEW."id", _pgh_attach_context(), NOW(), \'insert\', NEW."id", NEW."start_minute", NEW."teacher_id"); RETURN NULL;', hash='19992af6b51ebfce493ea01c9a769db2f4a26f01', operation='INSERT', pgid='pgtrigger_insert_insert_6fdbe', table='teacher_availability_overrides', when='AFTER')),
        ),
        pgtrigger.migrations.AddTrigger(
            model_name='availabilityoverride',
            trigger=pgtrigger.compiler.Trigger(name='update_update', sql=pgtrigger.compiler.UpsertTriggerSql(condition='WHEN (OLD.* IS DISTINCT FROM NEW.*)', func='INSERT INTO "availability_availabilityoverrideevent" ("date", "end_minute", "id", "pgh_context_id", "pgh_created_at", "pgh_label", "pgh_obj_id", "start_minute", "teacher_id") VALUES (NEW."date", NEW."end_minute", NEW."id", _pgh_attach_context(), NOW(), \'update\', NEW."id", NEW."start_minute", NEW."teacher_id"); RETURN NULL;', hash='5aca773c0fbdc99b4b3bc8cabf6be5a13647f71c', operation='UPDATE', pgid='pgtrigger_update_update_3f6e2', table='teacher_availability_overrides', when='AFTER')),
        ),
        pgtrigger.migrations.AddTrigger(
            model_name='availabilityoverride',
            trigger=pgtrigger.compiler.Trigger(name='delete_delete', sql=pgtrigger.compiler.UpsertTriggerSql(func='INSERT INTO "availability_availabilityoverrideevent" ("date", "end_minute", "id", "pgh_context_id", "pgh_created_at", "pgh_label", "pgh_obj_id", "start_minute", "teacher_id") VALUES (OLD."date", OLD."end_minute", OLD."id", _pgh_attach_context(), NOW(), \'delete\', OLD."id", OLD."start_minute", OLD."teacher_id"); RETURN NULL;', hash='0f74a19b2d17a555735c7c58f377c086985a7225', operation='DELETE', pgid='pgtrigger_delete_delete_57590', table='teacher_availability_overrides', when='AFTER')),
        ),
        pgtrigger.migrations.AddTrigger(
            model_name='availabilityrule',
            trigger=pgtrigger.compiler.Trigger(name='insert_insert', sql=pgtrigger.compiler.UpsertTriggerSql(func='INSERT INTO "availability_availabilityruleevent" ("day_of_week", "end_minute", "id", "pgh_context_id", "pgh_created_at", "pgh_label", "pgh_obj_id", "start_minute", "teacher_id") VALUES (NEW."day_of_week", NEW."end_minute", NEW."id", _pgh_attach_context(), NOW(), \'insert\', NEW."id", NEW."start_minute", NEW."teacher_id"); RETURN NULL;', hash='e0f8ba04ff3317cff8101993fb93c9be0fc2f74f', operation='INSERT', pgid='pgtrigger_insert_insert_8e437', table='teacher_availability_rules', when='AFTER')),
        ),
        pgtrigger.migrations.AddTrigger(
            model_name='availabilityrule',
            trigger=pgtrigger.compiler.Trigger(name='update_update', sql=pgtrigger.compiler.UpsertTriggerSql(condition='WHEN (OLD.* IS DISTINCT FROM NEW.*)', func='INSERT INTO "availability_availabilityruleevent" ("day_of_week", "end_minute", "id", "pgh_context_id", "pgh_created_at", "pgh_label", "pgh_obj_id", "start_minute", "teacher_id") VALUES (NEW."day_of_week", NEW."end_minute", NEW."id", _pgh_attach_context(), NOW(), \'update\', NEW."id", NEW."start_minute", NEW."teacher_id"); RETURN NULL;', hash='0ea145a91770a418c502d5c3b61dc5789e00538c', operation='UPDATE', pgid='pgtrigger_update_update_d98c7', table='teacher_availability_rules', when='AFTER')),
        ),
        pgtrigger.migrations.AddTrigger(
            model_name='availabilityrule',
            trigger=pgtrigger.compiler.Trigger(name='delete_delete', sql=pgtrigger.compiler.UpsertTriggerSql(func='INSERT INTO "availability_availabilityruleevent" ("day_of_week", "end_minute", "id", "pgh_context_id", "pgh_created_at", "pgh_label", "pgh_obj_id", "start_minute", "teacher_id") VALUES (OLD."day_of_week", OLD."end_minute", OLD."id", _pgh_attach_context(), NOW(), \'delete\', OLD."id", OLD."start_minute", OLD."teacher_id"); RETURN NULL;', hash='43b2bbeaa6689d2a2191eda50f172a550207dc8c', operation='DELETE', pgid='pgtrigger_delete_delete_bc93e', table='teacher_availability_rules', when='AFTER')),
        ),
        migrations.AddConstraint(
            model_name='availabilityoverride',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('start_minute__isnull', True), ('end_minute__isnull', True)), models.Q(('start_minute__isnull', False), ('end_minute__isnull', False)), _connector='OR'), name='avail_override_both_or_none'),
        ),
        migrations.AddConstraint(
            model_name='availabilityoverride',
            constraint=models.CheckConstraint(condition=models.Q(('start_minute__isnull', True), ('start_minute__lt', models.F('end_minute')), _connector='OR'), name='avail_override_ordered'),
        ),
        migrations.AddConstraint(
            model_name='availabilityoverride',
            constraint=models.CheckConstraint(condition=models.Q(('start_minute__isnull', True), models.Q(('start_minute__gte', 0), ('end_minute__lte', 1440)), _connector='OR'), name='avail_override_in_grid'),
        ),
        migrations.AddConstraint(
            model_name='availabilityoverride',
            constraint=models.CheckConstraint(condition=models.Q(('start_minute__isnull', True), models.Q(django.db.models.lookups.Exact(django.db.models.functions.math.Mod('start_minute', 30), 0), django.db.models.lookups.Exact(django.db.models.functions.math.Mod('end_minute', 30), 0)), _connector='OR'), name='avail_override_half_hour'),
        ),
        migrations.AddConstraint(
            model_name='availabilityoverride',
            constraint=models.UniqueConstraint(condition=models.Q(('start_minute__isnull', True)), fields=('teacher', 'date'), name='avail_override_one_closed_per_date'),
        ),
        migrations.AddConstraint(
            model_name='availabilityrule',
            constraint=models.CheckConstraint(condition=models.Q(('start_minute__lt', models.F('end_minute'))), name='avail_rule_ordered'),
        ),
        migrations.AddConstraint(
            model_name='availabilityrule',
            constraint=models.CheckConstraint(condition=models.Q(('start_minute__gte', 0), ('end_minute__lte', 1440)), name='avail_rule_in_grid'),
        ),
        migrations.AddConstraint(
            model_name='availabilityrule',
            constraint=models.CheckConstraint(condition=models.Q(django.db.models.lookups.Exact(django.db.models.functions.math.Mod('start_minute', 30), 0), django.db.models.lookups.Exact(django.db.models.functions.math.Mod('end_minute', 30), 0)), name='avail_rule_half_hour'),
        ),
    ]
