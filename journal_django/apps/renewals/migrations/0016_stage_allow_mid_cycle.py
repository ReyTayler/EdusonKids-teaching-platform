"""Свойство стадии «можно перевести посреди цикла» (бывшая привилегия ключа 'frozen').

Правило «пауза, а не решение» (вход с авто-стадии и при незавершённом цикле,
выход — только «Вернуть в работу») перестаёт быть привилегией одного ключа и
становится настраиваемым свойством стадии: школе нужны и другие паузы того же
характера («Закончил курс» — решаем, переводить на другой курс или ученик
уходит). Заморозке флаг проставляется здесь же, в одной миграции со схемой,
чтобы не было окна, в котором она теряет своё поведение.
"""

import pgtrigger.compiler
import pgtrigger.migrations
from django.db import migrations, models


def set_frozen_allow_mid_cycle(apps, schema_editor):
    RenewalStage = apps.get_model('renewals', 'RenewalStage')
    RenewalStage.objects.filter(key='frozen').update(allow_mid_cycle=True)


def unset_frozen_allow_mid_cycle(apps, schema_editor):
    RenewalStage = apps.get_model('renewals', 'RenewalStage')
    RenewalStage.objects.filter(key='frozen').update(allow_mid_cycle=False)


class Migration(migrations.Migration):

    dependencies = [
        ('renewals', '0015_drop_redundant_student_cycle_index'),
    ]

    operations = [
        pgtrigger.migrations.RemoveTrigger(
            model_name='renewalstage',
            name='insert_insert',
        ),
        pgtrigger.migrations.RemoveTrigger(
            model_name='renewalstage',
            name='update_update',
        ),
        pgtrigger.migrations.RemoveTrigger(
            model_name='renewalstage',
            name='delete_delete',
        ),
        migrations.AddField(
            model_name='renewalstage',
            name='allow_mid_cycle',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='renewalstageevent',
            name='allow_mid_cycle',
            field=models.BooleanField(default=False),
        ),
        pgtrigger.migrations.AddTrigger(
            model_name='renewalstage',
            trigger=pgtrigger.compiler.Trigger(name='insert_insert', sql=pgtrigger.compiler.UpsertTriggerSql(func='INSERT INTO "renewals_renewalstageevent" ("allow_mid_cycle", "color", "id", "is_auto", "key", "kind", "label", "pgh_context_id", "pgh_created_at", "pgh_label", "pgh_obj_id", "pipeline_id", "sort_order") VALUES (NEW."allow_mid_cycle", NEW."color", NEW."id", NEW."is_auto", NEW."key", NEW."kind", NEW."label", _pgh_attach_context(), NOW(), \'insert\', NEW."id", NEW."pipeline_id", NEW."sort_order"); RETURN NULL;', hash='289a8a82a1cf841bde43825f9d3555dd3933c17b', operation='INSERT', pgid='pgtrigger_insert_insert_c209a', table='renewal_stage', when='AFTER')),
        ),
        pgtrigger.migrations.AddTrigger(
            model_name='renewalstage',
            trigger=pgtrigger.compiler.Trigger(name='update_update', sql=pgtrigger.compiler.UpsertTriggerSql(condition='WHEN (OLD.* IS DISTINCT FROM NEW.*)', func='INSERT INTO "renewals_renewalstageevent" ("allow_mid_cycle", "color", "id", "is_auto", "key", "kind", "label", "pgh_context_id", "pgh_created_at", "pgh_label", "pgh_obj_id", "pipeline_id", "sort_order") VALUES (NEW."allow_mid_cycle", NEW."color", NEW."id", NEW."is_auto", NEW."key", NEW."kind", NEW."label", _pgh_attach_context(), NOW(), \'update\', NEW."id", NEW."pipeline_id", NEW."sort_order"); RETURN NULL;', hash='435a5a9193956d8318ddf50757cee1998c255179', operation='UPDATE', pgid='pgtrigger_update_update_0b298', table='renewal_stage', when='AFTER')),
        ),
        pgtrigger.migrations.AddTrigger(
            model_name='renewalstage',
            trigger=pgtrigger.compiler.Trigger(name='delete_delete', sql=pgtrigger.compiler.UpsertTriggerSql(func='INSERT INTO "renewals_renewalstageevent" ("allow_mid_cycle", "color", "id", "is_auto", "key", "kind", "label", "pgh_context_id", "pgh_created_at", "pgh_label", "pgh_obj_id", "pipeline_id", "sort_order") VALUES (OLD."allow_mid_cycle", OLD."color", OLD."id", OLD."is_auto", OLD."key", OLD."kind", OLD."label", _pgh_attach_context(), NOW(), \'delete\', OLD."id", OLD."pipeline_id", OLD."sort_order"); RETURN NULL;', hash='e5a52de9dfd6c8e1d8b5994df72de205fba6ed48', operation='DELETE', pgid='pgtrigger_delete_delete_5d810', table='renewal_stage', when='AFTER')),
        ),
        # Django после AddField снимает DEFAULT с колонки, и любой INSERT, который
        # не перечисляет allow_mid_cycle явно (raw SQL в фикстурах reports, ручные
        # запросы на проде), падает NotNullViolation. Возвращаем дефолт на уровне БД —
        # тот же приём, что и для прочих NOT NULL-колонок этой схемы.
        migrations.RunSQL(
            'ALTER TABLE renewal_stage ALTER COLUMN allow_mid_cycle SET DEFAULT false',
            'ALTER TABLE renewal_stage ALTER COLUMN allow_mid_cycle DROP DEFAULT',
        ),
        migrations.RunPython(set_frozen_allow_mid_cycle, unset_frozen_allow_mid_cycle),
    ]
