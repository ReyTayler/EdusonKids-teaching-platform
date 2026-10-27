"""
Serializers for teacher_spa.

Порт submitLessonSchema из shared/schemas.js (lines 263-274).
date-поле — DateStringField (YYYY-MM-DD, без timezone drift).
"""
from __future__ import annotations

from urllib.parse import urlsplit

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from rest_framework import serializers

from apps.core.fields import DateStringField
from apps.directions.models import Direction
from apps.feedback.serializers import LessonFeedbackInputSerializer
from apps.payroll.models import Payroll

# Поля, которые клиент больше не выбирает — их выводит сервер из planned_lessons
# (см. services.submit_lesson): замена — из назначения «Сменить преподавателя»,
# перенос — из moved_from_date плановой строки.
_SERVER_DERIVED_FIELDS = {
    'isSubstitution': 'Поле не принимается: замена назначается администратором.',
    'originalTeacher': 'Поле не принимается: замена назначается администратором.',
    'lessonType': 'Поле не принимается: тип урока выводится из плана занятий.',
}

RECORD_URL_REQUIRED = 'Вставьте ссылку на запись урока.'
RECORD_URL_INVALID = 'Нужна ссылка на запись: адрес вида https://…'
# Стандартный валидатор Django, но только веб-адреса: ftp/javascript/data и
# прочие схемы ссылкой на видеозапись быть не могут.
_record_url_validator = URLValidator(schemes=['http', 'https'])


class StudentAttendanceSerializer(serializers.Serializer):
    """Элемент списка студентов в submitLesson: {id?, name, present, is_free?}.

    id — students.id; по нему сервер и сопоставляет ученика. Имя у Eduson не
    уникально (тёзки в одной группе), поэтому сопоставление по name оставлено
    только для клиентов без id и только при однозначном имени (см.
    services.submit_lesson). is_free — исход «бесплатное занятие» (опц., по
    умолчанию False). См. lesson-outcomes-spec."""

    id = serializers.IntegerField(min_value=1, required=False)
    name = serializers.CharField()
    present = serializers.BooleanField()
    is_free = serializers.BooleanField(required=False, default=False)


class SubmitLessonSerializer(serializers.Serializer):
    """
    Порт submitLessonSchema из shared/schemas.js.

    Обязательные: group, date, students, feedback, recordUrl. Необязательный:
    plannedLessonId.

    recordUrl — ссылка на запись урока (обязательна с 27.10.2026, решение
    владельца): только адрес http(s) с доменом, пробелы по краям срезаются.

    feedback — отзыв по уроку (этап 3 Eduson): урок без отзыва преподаватель
    сохранить не может, поэтому блок обязателен и проверяется ДО записи —
    невалидный отзыв даёт 400 с ошибками по полям, урок не пишется.

    isSubstitution/originalTeacher/lessonType клиентом больше НЕ принимаются
    (400): тип урока выводит сервер из planned_lessons — замена из назначения
    «Сменить преподавателя», перенос из moved_from_date (см. services.submit_lesson).
    """

    # Zod: z.string().optional() — null НЕ принимается (Express 400).
    # Пустую строку z.string() принимает → allow_blank=True. z.string() не триммит →
    # trim_whitespace=False.
    group = serializers.CharField()
    date = DateStringField()
    recordUrl = serializers.CharField(
        max_length=2000,
        error_messages={'required': RECORD_URL_REQUIRED, 'blank': RECORD_URL_REQUIRED,
                        'null': RECORD_URL_REQUIRED},
    )
    students = StudentAttendanceSerializer(many=True)
    # Позиция курса (planned_lessons.id), по которой кликнули в календаре. НЕ
    # входит в _SERVER_DERIVED_FIELDS: это идентификатор занятия, а не выводимое
    # сервером решение — клиент лишь называет, ЧТО отмечает, а номер урока, тип и
    # преподавателя сервер по-прежнему определяет сам. Принадлежность группе
    # проверяется на сервере (scheduling.repository.get_course_position) — чужой
    # id доступа не даёт. Необязателен: вход «Мои уроки» занятия не знает, там
    # позиция резолвится по дате.
    plannedLessonId = serializers.IntegerField(min_value=1, required=False)
    feedback = LessonFeedbackInputSerializer(
        error_messages={'required': 'Заполните обратную связь по уроку.',
                        'null': 'Заполните обратную связь по уроку.'},
    )

    def validate_recordUrl(self, value: str) -> str:  # noqa: N802 — имя поля из API
        try:
            _record_url_validator(value)
        except DjangoValidationError:
            raise serializers.ValidationError(RECORD_URL_INVALID)
        # URLValidator пропускает «localhost» — у ссылки на видеохостинг всегда
        # есть домен с точкой.
        if '.' not in (urlsplit(value).hostname or ''):
            raise serializers.ValidationError(RECORD_URL_INVALID)
        return value

    def validate(self, attrs):
        forbidden = set(_SERVER_DERIVED_FIELDS) & set(self.initial_data or {})
        if forbidden:
            raise serializers.ValidationError({
                key: _SERVER_DERIVED_FIELDS[key] for key in sorted(forbidden)
            })
        # Один ученик дважды в списке → две строки посещаемости на (урок, ученик):
        # UNIQUE в БД отвечал бы 500. Ловим здесь внятным 400.
        ids = [s['id'] for s in attrs.get('students', []) if s.get('id') is not None]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError({
                'students': 'Ученик указан в списке несколько раз.',
            })
        return attrs


def my_lesson_student_status(lesson_type: str, attendance) -> str:
    """
    Статус ученика в истории «Мои уроки».

    Тип урока проверяется РАНЬШЕ флагов посещения: у сгорания посещение в базе
    отмечено present=true, но занятия не было — показывать «был» было бы неправдой.
    """
    if lesson_type == 'burned':
        return 'burned'
    if attendance.present:
        return 'free' if attendance.is_free else 'present'
    return 'skip' if attendance.unpaid_skip else 'absent'


class MyLessonSerializer(serializers.Serializer):
    """
    Read-only элемент истории «Мои уроки» (GET /api/lessons).

    Payroll (1:1) может отсутствовать у исторических записей → все производные
    от него поля отдаются как None. Decimal-поля рендерятся строкой с масштабом
    (DateSafeJSONRenderer) — фронт приводит Number().
    """

    id = serializers.IntegerField()
    date = serializers.DateField(source='lesson_date')
    group = serializers.CharField(source='group.name')
    lessonNumber = serializers.DecimalField(
        source='lesson_number', max_digits=5, decimal_places=1
    )
    # «Пройдено до учёта» группы (read-only; group уже в select_related → без N+1).
    # Фронт показывает пометку в раскрытии урока. Отдаётся строкой ("7.0"),
    # как lessonNumber: DateSafeJSONRenderer сериализует Decimal строкой.
    lessonNumberOffset = serializers.DecimalField(
        source='group.lesson_number_offset', max_digits=6, decimal_places=1,
    )
    lessonType = serializers.CharField(source='lesson_type')
    isSubstitution = serializers.SerializerMethodField()
    originalTeacher = serializers.SerializerMethodField()
    recordUrl = serializers.CharField(source='record_url', allow_null=True)
    submittedAt = serializers.DateTimeField(source='submitted_at')
    direction = serializers.SerializerMethodField()
    directionColor = serializers.SerializerMethodField()
    presentCount = serializers.SerializerMethodField()
    totalCount = serializers.SerializerMethodField()
    payment = serializers.SerializerMethodField()
    penalty = serializers.SerializerMethodField()
    students = serializers.SerializerMethodField()

    def _payroll(self, obj):
        try:
            return obj.payroll
        except Payroll.DoesNotExist:
            return None

    def _direction(self, obj):
        try:
            return obj.group.direction
        except Direction.DoesNotExist:
            return None

    def get_direction(self, obj):
        d = self._direction(obj)
        return d.name if d else None

    def get_directionColor(self, obj):
        d = self._direction(obj)
        return d.color if d else None

    def get_isSubstitution(self, obj) -> bool:
        return obj.lesson_type == 'substitution'

    def get_originalTeacher(self, obj):
        return obj.original_teacher.name if obj.original_teacher_id else None

    def get_presentCount(self, obj):
        pr = self._payroll(obj)
        return pr.present_count if pr else None

    def get_totalCount(self, obj):
        pr = self._payroll(obj)
        return pr.total_students if pr else None

    def get_payment(self, obj):
        pr = self._payroll(obj)
        return str(pr.payment) if pr else None

    def get_penalty(self, obj):
        pr = self._payroll(obj)
        return str(pr.penalty) if pr else None

    def get_students(self, obj):
        # Посещения приходят из prefetch (MyLessonsView) уже по алфавиту —
        # без него здесь был бы запрос на каждый урок выдачи.
        return [
            {
                'id': att.student_id,
                'name': att.student.full_name,
                'status': my_lesson_student_status(obj.lesson_type, att),
            }
            for att in obj.attendance.all()
        ]
