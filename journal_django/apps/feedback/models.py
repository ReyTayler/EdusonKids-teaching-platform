"""
Отзыв преподавателя по уроку (Eduson): состоялся ли урок, понимание и интерес
по шкале 1–10, технические проблемы, свободный комментарий.

Отзыв привязан к группе и дате урока, а не к строке плана: позиции плана
пересоздаются при пересборе (reset_plan/resize_plan), и ссылка на них унесла бы
отзывы «урок не состоялся» вместе с ними. Если урок состоялся, дополнительно
задан lesson. Согласованность group/lesson_date с уроком БД не проверяет (CHECK
не умеет смотреть в другую таблицу) — её обеспечивают импорт и будущий сервис
записи. Диапазон оценок держит CHECK: данные приходят импортом в обход API.
"""
from __future__ import annotations

import pghistory
from django.db import models
from django.db.models import Q


@pghistory.track(pghistory.InsertEvent(), pghistory.UpdateEvent(), pghistory.DeleteEvent())
class LessonFeedback(models.Model):
    id = models.BigAutoField(primary_key=True)
    # CASCADE — как у PlannedLesson и LessonSkip, и только на уровне ORM: в БД
    # FK без ON DELETE, DB-каскад не нужен. Жёстко группы в проекте не удаляют
    # (только архивация soft_delete_group), а группу с уроками БД и так удалить
    # не даст (lessons.group_id — NO ACTION). Остаётся редкий случай группы без
    # уроков, но с отзывами «не состоялся» — без группы они бессмысленны.
    group = models.ForeignKey(
        'groups.Group', on_delete=models.CASCADE,
        db_column='group_id', related_name='lesson_feedback',
        # Покрыт префиксом составного lesson_feedback_group_date_idx.
        db_index=False,
    )
    # Дата урока: фактическая для состоявшегося, плановая — для несостоявшегося.
    lesson_date = models.DateField()
    # SET_NULL, не CASCADE: отзыв держится на group + lesson_date, а ссылка на
    # урок — лишь уточнение. Урок удаляют штатным delete_lesson_full или откатом
    # журнала «урок записан» (undo-insert = ORM .delete()); CASCADE во втором
    # случае молча стёр бы отзыв, созданный другой операцией (импорт,
    # преподаватель). Обнуление ORM делает UPDATE'ом — pghistory пишет
    # UpdateEvent в тот же контекст, и откат удаления урока возвращает ссылку.
    # PROTECT не годится: заблокировал бы удаление ошибочно записанного урока.
    lesson = models.OneToOneField(
        'lessons.Lesson', on_delete=models.SET_NULL, null=True, blank=True,
        db_column='lesson_id', related_name='feedback',
    )
    # PROTECT: преподавателей удаляют мягко (active=false), жёсткое удаление
    # автора отзывов — ошибка, которую надо увидеть, а не молча потерять отзывы.
    teacher = models.ForeignKey(
        'teachers.Teacher', on_delete=models.PROTECT,
        db_column='teacher_id', related_name='lesson_feedback',
        # Покрыт префиксом составного lesson_feedback_teacher_idx.
        db_index=False,
    )
    took_place = models.BooleanField()
    # NULL — оценки нет (урок не состоялся или преподаватель не поставил).
    understanding = models.SmallIntegerField(null=True, blank=True)
    interest = models.SmallIntegerField(null=True, blank=True)
    tech_problems = models.BooleanField(default=False)
    comment = models.TextField(blank=True, default='')
    submitted_at = models.DateTimeField()

    class Meta:
        managed = True
        db_table = 'lesson_feedback'
        indexes = [
            # Лента без фильтров: ORDER BY submitted_at DESC, id DESC + LIMIT.
            models.Index(fields=['-submitted_at', '-id'], name='lesson_feedback_submitted_idx'),
            # Фильтр по преподавателю с тем же порядком — без сортировки в памяти.
            models.Index(fields=['teacher', '-submitted_at', '-id'],
                         name='lesson_feedback_teacher_idx'),
            # Фильтр по группе и сопоставление с позицией плана по дате.
            # У lesson_id индекс уже есть — его создаёт UNIQUE от OneToOneField.
            models.Index(fields=['group', 'lesson_date'], name='lesson_feedback_group_date_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(understanding__isnull=True)
                | Q(understanding__gte=1, understanding__lte=10),
                name='lesson_feedback_understanding_range',
            ),
            models.CheckConstraint(
                condition=Q(interest__isnull=True) | Q(interest__gte=1, interest__lte=10),
                name='lesson_feedback_interest_range',
            ),
        ]

    def __str__(self) -> str:
        return f'LessonFeedback#{self.pk} (group={self.group_id}, {self.lesson_date})'
