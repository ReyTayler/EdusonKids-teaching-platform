import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, ApiError, LESSON_ALREADY_RECORDED } from '@shared/lib/api';
import type { SubmitPayload, SubmitResult } from '../lib/types';

/**
 * POST /api/submitLesson. retry:0 — ОБЯЗАТЕЛЬНО: авто-resubmit запрещён. Сервер
 * теперь защищён от дублей захватом позиции курса (apps.lessons.services.
 * record_lesson), но эта защита работает только там, где позиция есть; на
 * фолбэк-путях (группа без плана, дата вне плана) повтор по-прежнему создал бы
 * второй урок. Повторную отправку инициирует только человек.
 */
export function useSubmitLesson() {
  const qc = useQueryClient();

  const invalidateAll = () => {
    qc.invalidateQueries({ queryKey: ['report'] });
    qc.invalidateQueries({ queryKey: ['teacherData'] });
    qc.invalidateQueries({ queryKey: ['allData'] });
    qc.invalidateQueries({ queryKey: ['schedule'] });
    qc.invalidateQueries({ queryKey: ['calendar'] });
    qc.invalidateQueries({ queryKey: ['groupProgress'] });
    // История «Мои уроки» и «Зарплата» показывают записанные уроки: без сброса
    // они до минуты держали бы старый список — а LessonForm при таймауте прямо
    // отправляет проверять запись в «Мои уроки».
    qc.invalidateQueries({ queryKey: ['myLessons'] });
    qc.invalidateQueries({ queryKey: ['myPayroll'] });
  };

  return useMutation<SubmitResult, unknown, SubmitPayload>({
    mutationFn: (payload) => api<SubmitResult>('POST', '/api/submitLesson', payload),
    retry: 0,
    onSuccess: (result) => {
      if (result.success === true) {
        invalidateAll();
        return;
      }
      // Отказ сервера (например «Список учеников устарел»): подтягиваем свежий
      // состав группы, чтобы повторная отправка шла уже с актуальным списком.
      qc.invalidateQueries({ queryKey: ['teacherData'] });
      qc.invalidateQueries({ queryKey: ['allData'] });
    },
    onError: (err) => {
      // «Уже записан» → урок в БД есть (предыдущая попытка дошла, ответ потерялся).
      // Экран устарел: обновляем данные так же, как при успехе, иначе календарь
      // продолжит показывать занятие неотмеченным и учитель нажмёт ещё раз.
      if (err instanceof ApiError && err.code === LESSON_ALREADY_RECORDED) {
        invalidateAll();
      }
    },
  });
}
