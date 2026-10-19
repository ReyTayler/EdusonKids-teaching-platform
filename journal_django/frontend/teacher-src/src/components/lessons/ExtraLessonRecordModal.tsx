import { useState } from 'react';
import { Modal } from '../ui/Modal';
import { ApiError, EXTRA_LESSON_ALREADY_RECORDED, REQUEST_TIMEOUT } from '@shared/lib/api';
import { useToast } from '@shared/components/ui/Toast';
import { Checkbox } from '@shared/components/form/Checkbox';
import { TextInput } from '@shared/components/form/TextInput';
import { useExtraLesson, useRecordExtraLesson } from '../../hooks/useExtraLesson';

/** Похожа ли строка на http(s)-ссылку — только мягкая подсказка, не блокирует сохранение. */
function looksLikeUrl(value: string): boolean {
  try {
    const u = new URL(value);
    return u.protocol === 'http:' || u.protocol === 'https:';
  } catch {
    return false;
  }
}

/**
 * Фиксация проведения доп.урока (AbsenceResolution) — одна резолюция = один
 * ученик, поэтому единый тумблер «Пришёл/Не пришёл», а не список участников.
 * См. apps/extra_lessons/views.py::TeacherExtraLessonRecordView,
 * apps/extra_lessons/services.py::record. Переиспользует lf-* стили из
 * groups.css (тот же визуальный язык, что LessonForm), чтобы не заводить
 * новые ad-hoc классы.
 */
export function ExtraLessonRecordModal({ assignmentId, onClose }: { assignmentId: number; onClose: () => void }) {
  const { toast } = useToast();
  const { data, isLoading, isError } = useExtraLesson(assignmentId);
  const record = useRecordExtraLesson();
  const [recordUrl, setRecordUrl] = useState('');
  const [present, setPresent] = useState(true);
  const [submitError, setSubmitError] = useState<string | null>(null);

  if (isLoading || !data) {
    return (
      <Modal title="Доп.урок" onClose={onClose}>
        {isError
          ? <div className="cal-error">Не удалось загрузить доп.урок. Попробуйте ещё раз.</div>
          : <div className="cal-empty">Загрузка…</div>}
      </Modal>
    );
  }

  const handleSubmit = () => {
    if (record.isPending || !present) return;
    setSubmitError(null);
    record.mutate(
      { id: assignmentId, body: { record_url: recordUrl.trim() || undefined, present } },
      {
        onSuccess: (result) => {
          toast(`Доп.урок записан · ${result.payment} ₽`, 'ok');
          onClose();
        },
        onError: (err) => {
          // Доп.урок уже отмечен — типично после потерянного ответа: работа
          // сделана, деньги начислены. Закрываем спокойно, а не красной ошибкой,
          // иначе преподаватель решит, что не сохранилось, и нажмёт ещё раз.
          if (err instanceof ApiError && err.code === EXTRA_LESSON_ALREADY_RECORDED) {
            toast('Это занятие уже отмечено', 'ok');
            onClose();
            return;
          }
          // Ответа не было вовсе — доп.урок мог записаться. «Попробуйте ещё раз»
          // здесь было бы приглашением к повтору вслепую.
          if (err instanceof ApiError && err.code === REQUEST_TIMEOUT) {
            setSubmitError(
              'Сервер не ответил вовремя. Доп.урок мог записаться — обновите '
              + 'страницу и проверьте, прежде чем отправлять ещё раз.',
            );
            return;
          }
          if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
            setSubmitError('Сессия истекла или доп.урок принадлежит другому преподавателю.');
          } else if (err instanceof ApiError) {
            setSubmitError(err.message);
          } else {
            setSubmitError(
              'Не удалось связаться с сервером. Доп.урок мог записаться — обновите '
              + 'страницу и проверьте, прежде чем отправлять ещё раз.',
            );
          }
        },
      },
    );
  };

  return (
    <Modal
      title={`Доп.урок за ${data.missed_lesson_date}`}
      subtitle={`${data.missed_lesson_group_name} · ${data.scheduled_date} ${data.scheduled_time.slice(0, 5)}`}
      onClose={onClose}
      busy={record.isPending}
    >
      <div>
        <div className="lf-students-hdr">
          <span className="t-sec-label">Посещаемость</span>
        </div>
        <div className="lf-students">
          <div className={`lf-student${present ? ' is-present' : ''}`}>
            <Checkbox
              checked={present}
              onChange={() => setPresent((prev) => !prev)}
              label={<span className="lf-student-name">{data.student_name}</span>}
            />
            <span className="lf-student-state">{present ? 'Пришёл' : 'Не пришёл'}</span>
          </div>
        </div>
      </div>

      <div className="lf-record">
        <div className="lf-record-head">
          <span className="t-sec-label">Запись урока</span>
          <span className="lf-record-optional">необязательно</span>
        </div>
        <div className={`lf-record-box${recordUrl.trim() ? (looksLikeUrl(recordUrl.trim()) ? ' is-valid' : ' is-suspect') : ''}`}>
          <svg className="lf-record-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
            <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
          </svg>
          <TextInput
            className="lf-record-input"
            type="url"
            inputMode="url"
            value={recordUrl}
            onChange={(e) => setRecordUrl(e.target.value)}
            placeholder="Вставьте ссылку на запись занятия…"
            aria-label="Ссылка на запись урока"
          />
          {recordUrl && (
            <button
              type="button"
              className="lf-record-clear"
              onClick={() => setRecordUrl('')}
              aria-label="Очистить ссылку"
              title="Очистить"
            >
              ×
            </button>
          )}
        </div>
      </div>

      <div className="lf-preview">
        <div className="lf-preview-row">
          <span>{present ? 'Ученик пришёл' : 'Ученик не пришёл'}</span>
        </div>
      </div>

      {/* Неявку на доп.урок фиксируют «Отменой» назначения, не записью (бэк — 400). */}
      {!present && (
        <div className="lf-warn">
          Записать доп.урок можно только с присутствием ученика. Если ученик не пришёл —
          отмените назначенный доп.урок.
        </div>
      )}

      {submitError && <div className="lf-error">{submitError}</div>}

      <div className="lf-actions">
        {/* Пока запрос в полёте, уйти из формы нельзя — иначе ответ придёт в
            размонтированный компонент и человек не узнает результата. */}
        <button
          type="button"
          className="btn-cancel"
          onClick={onClose}
          disabled={record.isPending}
        >
          Отмена
        </button>
        <button
          type="button"
          className="btn-save"
          disabled={record.isPending || !present}
          onClick={handleSubmit}
        >
          {record.isPending ? 'Сохранение…' : 'Сохранить доп.урок'}
        </button>
      </div>
    </Modal>
  );
}
