"""
Модели раздела «Продления» — управляемые Django (managed=True), новые таблицы.

renewal_pipeline — воронка (обычно одна, is_default).
renewal_stage    — КОНФИГУРИРУЕМЫЕ стадии воронки (kind: progress/decision/won/lost).
renewal_deal     — сделка продления: ученик × направление × номер цикла.
renewal_activity — таймлайн: смена стадии, комментарий, привязка оплаты, системное.

Прогресс/баланс НЕ хранятся — вычисляются на чтении (см. repository/serializers).
"""
from __future__ import annotations

import pghistory
from django.db import models

from apps.core.db_fields import TolerantJSONField


@pghistory.track(pghistory.InsertEvent(), pghistory.UpdateEvent(), pghistory.DeleteEvent())
class RenewalPipeline(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.TextField()
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'renewal_pipeline'
        constraints = [
            models.UniqueConstraint(
                fields=['is_default'],
                condition=models.Q(is_default=True),
                name='renewal_pipeline_one_default',
            ),
        ]


@pghistory.track(pghistory.InsertEvent(), pghistory.UpdateEvent(), pghistory.DeleteEvent())
class RenewalStage(models.Model):
    class Kind(models.TextChoices):
        PROGRESS = 'progress', 'Прогресс'   # авто-стадии «урок 1–4»
        DECISION = 'decision', 'Решение'    # ручные промежуточные
        WON = 'won', 'Продлён'              # терминальная-успех
        LOST = 'lost', 'Ушёл'               # терминальная-провал

    id = models.BigAutoField(primary_key=True)
    pipeline = models.ForeignKey(
        RenewalPipeline, on_delete=models.CASCADE,
        db_column='pipeline_id', related_name='stages',
    )
    key = models.TextField()                # стабильный машинный ключ для авто-правил
    label = models.TextField()
    color = models.TextField(null=True, blank=True)
    sort_order = models.IntegerField()
    kind = models.CharField(max_length=10, choices=Kind.choices)
    is_auto = models.BooleanField(default=False)  # двигается движком vs руками
    # «Пауза, а не решение»: на такую стадию можно увести сделку в ЛЮБОЙ момент —
    # с авто-стадии («Урок 2», «Ждём оплату») и при незавершённом цикле, а выход
    # с неё — только «Вернуть в работу» (engine.return_to_work). Так живёт
    # «Заморожен»; свойством (а не ключом) это сделано, чтобы superadmin мог
    # завести свои стадии того же характера («Закончил курс» и т.п.).
    # Действует ТОЛЬКО при kind='decision' — см. transitions._is_pause_target.
    allow_mid_cycle = models.BooleanField(default=False)

    class Meta:
        managed = True
        db_table = 'renewal_stage'
        constraints = [
            models.UniqueConstraint(fields=['pipeline', 'key'], name='renewal_stage_pipeline_key_uq'),
            models.CheckConstraint(
                name='renewal_stage_kind_check',
                condition=models.Q(kind__in=['progress', 'decision', 'won', 'lost']),
            ),
            models.CheckConstraint(
                name='renewal_stage_color_check',
                condition=models.Q(color__isnull=True) | models.Q(color__regex=r'^#[0-9a-fA-F]{6}$'),
            ),
        ]
        indexes = [models.Index(fields=['pipeline', 'sort_order'], name='renewal_stage_order_idx')]


@pghistory.track(pghistory.InsertEvent(), pghistory.UpdateEvent(), pghistory.DeleteEvent())
class RenewalDeal(models.Model):
    """
    Сделка продления — сущность УЧЕНИКА (подписочная модель, LT/LTV):
    cycle_no считается от общей истории посещений по всем направлениям
    (цикл = абонемент = 4 суммарных урока). Направления ученика — справочная
    информация на чтении (активные membership), в идентичность не входят.
    """
    id = models.BigAutoField(primary_key=True)
    # RESTRICT — защищаем историю продлений от хард-удаления ученика.
    student = models.ForeignKey(
        'students.Student', on_delete=models.RESTRICT,
        db_column='student_id', related_name='renewal_deals',
    )
    cycle_no = models.IntegerField()
    pipeline = models.ForeignKey(
        RenewalPipeline, on_delete=models.RESTRICT,
        db_column='pipeline_id', related_name='deals',
    )
    stage = models.ForeignKey(
        RenewalStage, on_delete=models.RESTRICT,
        db_column='stage_id', related_name='deals',
    )
    # Ответственный менеджер. SET NULL — учётку могут удалить, сделку теряем нельзя.
    assignee = models.ForeignKey(
        'accounts.Account', on_delete=models.SET_NULL,
        db_column='assignee_id', related_name='renewal_deals',
        null=True, blank=True,
    )
    # Дата «созревания» продления: 4-й урок цикла отработан (ставит движок).
    # Основа когортной аналитики по месяцам. NULL — цикл ещё не отработан.
    due_at = models.DateField(null=True, blank=True)
    # Месяц окончания заморозки («до какого месяца»), всегда 1-е число.
    # Заполнено ТОЛЬКО пока сделка на стадии key='frozen'; уход со стадии
    # обнуляет (repository.move_deal / engine.return_from_freeze). DB-CHECK нет:
    # ключ стадии живёт в другой таблице, условие по FK-джойну не выражается.
    frozen_until_month = models.DateField(null=True, blank=True)
    # Снимок направлений цикла — список id из `directions`, например [3, 7].
    # Направления сделки иначе читаются вживую из АКТИВНЫХ членств в группах, и
    # у закрытой сделки они менялись задним числом: ученик перешёл на другой курс
    # или ушёл — членство погасло, и архив показывал не тот курс, что был.
    # Пишется ОДИН раз, когда сделка перестаёт быть текущей (цикл созрел или
    # менеджер увёл её руками), и больше не перезаписывается: снимок отвечает на
    # вопрос «о каком курсе был этот цикл», а не «где ученик сейчас».
    # Имена и цвета НЕ денормализуем — их берём джойном, чтобы переименование
    # направления подхватывалось и в истории.
    # TolerantJSONField, а не JSONField: apps.core.apps регистрирует
    # register_default_jsonb, и psycopg2 отдаёт уже готовый list — обычное поле
    # падало бы на json.loads(list).
    directions_snapshot = TolerantJSONField(null=True, blank=True)
    reason_code = models.TextField(null=True, blank=True)
    stage_entered_at = models.DateTimeField(auto_now_add=True)
    outcome_at = models.DateTimeField(null=True, blank=True)  # NOT NULL ⇒ сделка закрыта
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = 'renewal_deal'
        constraints = [
            models.UniqueConstraint(
                fields=['student', 'cycle_no'],
                name='renewal_deal_student_cycle_uq',
            ),
        ]
        indexes = [
            models.Index(fields=['stage'], condition=models.Q(outcome_at__isnull=True),
                         name='renewal_deal_open_stage_idx'),
            models.Index(fields=['assignee'], name='renewal_deal_assignee_idx'),
            models.Index(fields=['student'], name='renewal_deal_student_idx'),
            # Подзапрос «последняя сделка ученика» (аннотация стадии в
            # apps/students/repository.py) отдельного индекса НЕ требует:
            # UniqueConstraint(student, cycle_no) выше — тот же btree, и
            # ORDER BY cycle_no DESC LIMIT 1 идёт по нему обратным сканом.
        ]


@pghistory.track(pghistory.InsertEvent(), pghistory.DeleteEvent())  # activity — лог, update не трекаем
class RenewalActivity(models.Model):
    class Kind(models.TextChoices):
        STAGE_CHANGE = 'stage_change', 'Смена стадии'
        COMMENT = 'comment', 'Комментарий'
        PAYMENT_LINKED = 'payment_linked', 'Оплата'
        SYSTEM = 'system', 'Система'

    id = models.BigAutoField(primary_key=True)
    deal = models.ForeignKey(
        RenewalDeal, on_delete=models.CASCADE,
        db_column='deal_id', related_name='activities',
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    from_stage = models.ForeignKey(
        RenewalStage, on_delete=models.SET_NULL, null=True, blank=True,
        db_column='from_stage_id', related_name='+',
    )
    to_stage = models.ForeignKey(
        RenewalStage, on_delete=models.SET_NULL, null=True, blank=True,
        db_column='to_stage_id', related_name='+',
    )
    payment = models.ForeignKey(
        'payments.Payment', on_delete=models.SET_NULL, null=True, blank=True,
        db_column='payment_id', related_name='+',
    )
    body = models.TextField(null=True, blank=True)
    author = models.ForeignKey(
        'accounts.Account', on_delete=models.SET_NULL, null=True, blank=True,
        db_column='author_id', related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'renewal_activity'
        constraints = [
            models.CheckConstraint(
                name='renewal_activity_kind_check',
                condition=models.Q(kind__in=['stage_change', 'comment', 'payment_linked', 'system']),
            ),
        ]
        indexes = [
            models.Index(fields=['deal', '-created_at'], name='renewal_activity_deal_idx'),
        ]
