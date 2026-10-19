import { useEffect, useRef, useState } from 'react';
import { useLessonFull, useLessonMutations, useLessonSkips } from '../../hooks/useLessons';
import { useMemberships } from '../../hooks/useMemberships';
import { useApiError } from '../../hooks/useApiError';
import { useAuth } from '../../hooks/useAuth';
import { useToast } from '../ui/Toast';
import { Dialog } from '../ui/Dialog';
import { DateInput } from '../form/DateInput';
import { unpaidAttendanceBlockMessage } from '../../lib/api';
import { canRecordLessonInDebt, type Role } from '../../lib/permissions';
import type { Group } from '../../lib/types';

interface Props {
  group: Group;
  slot: number;
  lessonId: number | null;
  color: string;
  onClose: () => void;
}

// Переведённый ученик уже отработал `transferred_from_lessons_done` уроков в
// прежней группе — эти уроки в новой группе ему не считаются (см. transfer
// progress alignment). Слот с lesson_number <= этого числа для него заблокирован.
function isLockedByTransfer(
  m: { transferred_from_id?: number | null; transferred_from_lessons_done?: string | number | null },
  slotLessonNumber: number,
): boolean {
  if (!m.transferred_from_id || m.transferred_from_lessons_done == null) return false;
  return slotLessonNumber <= Number(m.transferred_from_lessons_done);
}

export function LessonEditor({ group, slot, lessonId, color, onClose }: Props) {
  const { data: lesson, isLoading: lessonLoading } = useLessonFull(lessonId);
  const { data: members = [], isLoading: membersLoading } = useMemberships({ group_id: group.id });
  const muts = useLessonMutations();
  const { toast } = useToast();
  const showError = useApiError();
  const { me } = useAuth();
  const canDebt = canRecordLessonInDebt(me?.role as Role);
  // Модалка «записать в долг». Появляется ТОЛЬКО после реального отказа сервера
  // по балансу и только суперадмину: пред-проверять баланс на клиенте нельзя —
  // источник правды один, серверный (assert_students_paid). `retry` повторяет
  // ровно ту операцию, что отклонили, уже с allow_debt.
  const [debtPrompt, setDebtPrompt] =
    useState<{ message: string; retry: () => Promise<void> } | null>(null);

  /** Отказ по долгу и суперадмин → показать модалку; иначе обычная ошибка. */
  const handleBlocked = (err: unknown, retry: () => Promise<void>): void => {
    const message = unpaidAttendanceBlockMessage(err);
    if (message && canDebt) { setDebtPrompt({ message, retry }); return; }
    showError(err);
  };
  const editorRef = useRef<HTMLDivElement | null>(null);
  const isFirstOpenRef = useRef(true);

  const [date, setDate] = useState('');
  const [url, setUrl] = useState('');
  const [present, setPresent] = useState<Record<number, boolean>>({});
  // Исход «бесплатное занятие» (present=true, баланс ученика не списывается,
  // зарплата преподавателю начисляется как обычно). Задаётся только при создании нового урока. См.
  // docs/superpowers/specs/2026-07-23-lesson-outcomes-spec.md.
  const [free, setFree] = useState<Record<number, boolean>>({});
  // Исход «неоплачиваемый пропуск» (present=false, ученик этот урок не посещает).
  // На новом уроке — локально (в payload); на проведённом — сразу через эндпоинт.
  const [skip, setSkip] = useState<Record<number, boolean>>({});
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  // slot — номер ЯЧЕЙКИ (1,2,3…); lesson_number = slot*step (45-мин → шаг 0.5).
  const step = group.lesson_duration_minutes === 45 ? 0.5 : 1;
  const slotLessonNumber = slot * step;
  // Маркеры «неоплачиваемый пропуск» на ЭТОТ слот — источник правды (работает и на
  // ещё не проведённом уроке). Вариант A.
  const { data: skipsData } = useLessonSkips(group.id, slotLessonNumber);

  useEffect(() => {
    if (lesson) {
      setDate(String(lesson.lesson_date).slice(0, 10));
      setUrl(lesson.record_url || '');
      const initP: Record<number, boolean> = {};
      const initF: Record<number, boolean> = {};
      for (const a of lesson.attendance || []) {
        initP[a.student_id] = !!a.present;
        initF[a.student_id] = !!a.is_free;
      }
      setPresent(initP);
      setFree(initF);
    } else if (lessonId === null) {
      setDate('');
      setUrl('');
      // Новый урок — по умолчанию все НЕ присутствовали; админ выставляет вручную.
      const init: Record<number, boolean> = {};
      for (const m of members) init[m.student_id] = false;
      setPresent(init);
      setFree({});
    }
  }, [lesson, lessonId, members]);

  // skip — из маркеров слота (единый источник; на проведённом уроке материализован
  // в attendance, но маркер авторитетнее и покрывает будущие слоты).
  useEffect(() => {
    if (!skipsData) return;
    const s: Record<number, boolean> = {};
    for (const id of skipsData.student_ids) s[id] = true;
    setSkip(s);
  }, [skipsData]);

  useEffect(() => {
    if (isFirstOpenRef.current && editorRef.current) {
      isFirstOpenRef.current = false;
      editorRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, []);

  if (lessonLoading || membersLoading) {
    return <div ref={editorRef} className="lesson-editor-host--loading" />;
  }

  // Проведённый урок редактируется поячеечно (точечные эндпоинты пересчитывают
  // зарплату) — типовой кейс «проставить бесплатное занятие постфактум» (спор
  // разрешают после урока). Заблокированы только компенсированные доп.уроком/
  // сожжённые ячейки (двойной учёт) и локнутые переводом — они отдельно ниже.

  const handleSave = async () => {
    if (!date) { toast('Укажите дату', 'error'); return; }
    const attendance = members
      .filter((m) => !isLockedByTransfer(m, slotLessonNumber))
      .map((m) => (skip[m.student_id]
        ? { student_id: m.student_id, present: false, is_free: false, unpaid_skip: true }
        : {
            student_id: m.student_id,
            present: !!present[m.student_id],
            is_free: !!free[m.student_id],
            unpaid_skip: false,
          }));
    // free-ученик present=true (урок для него состоялся), поэтому засчитывается в
    // «хотя бы один присутствующий»; из зарплаты его исключит сервер.
    const presentCount = attendance.filter((a) => a.present).length;
    const totalStudents = attendance.length;

    if (totalStudents === 0) {
      toast('В группе нет учеников — урок зафиксировать нельзя', 'error');
      return;
    }
    if (presentCount === 0) {
      toast('Отметьте хотя бы одного присутствующего ученика', 'error');
      return;
    }

    // Вынесено в функцию, чтобы модалка «в долг» могла повторить РОВНО ту же
    // отправку с allow_debt, не собирая payload заново.
    const createLesson = async (allowDebt: boolean) => {
      await muts.create.mutateAsync({
        lesson_date: date,
        group_id: group.id,
        teacher_id: group.teacher_id,
        lesson_number: slotLessonNumber,
        lesson_duration_minutes: group.lesson_duration_minutes,
        lesson_type: 'regular',
        record_url: url,
        submitted_by_token: 'admin-imported',
        attendance,
        ...(allowDebt ? { allow_debt: true } : {}),
      });
      toast('Урок создан', 'ok');
      onClose();
    };

    try {
      if (lesson) {
        // Посещаемость завершённого урока не редактируется здесь — только дата/ссылка.
        await muts.update.mutateAsync({
          id: lesson.id,
          body: { lesson_date: date, record_url: url },
        });
        toast('Сохранено', 'ok');
        onClose();
      } else {
        await createLesson(false);
      }
    } catch (err) {
      handleBlocked(err, () => createLesson(true));
    }
  };

  const handleDelete = async () => {
    if (!lesson) return;
    if (!confirmingDelete) { setConfirmingDelete(true); return; }
    try {
      await muts.remove.mutateAsync(lesson.id);
      toast('Урок удалён', 'ok');
      onClose();
    } catch (err) { showError(err); }
  };

  return (
    <div ref={editorRef} className="lesson-editor" style={{ ['--dir-color' as string]: color }}>
      <div className="lesson-editor__header">
        <h4>Урок №{slot}{lesson ? '' : ' · новый'}</h4>
        <button type="button" className="btn-secondary" onClick={onClose}>Закрыть</button>
      </div>
      <div className="lesson-editor__row">
        <label>Дата проведения</label>
        <DateInput value={date} onChange={(e) => setDate(e.target.value)} />
      </div>
      <div className="lesson-editor__row">
        <label>Ссылка на запись урока</label>
        <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://..." />
      </div>
      <div className="lesson-editor__row">
        <label>
          Посещаемость <span className="lesson-editor__hint">(клик по карточке: не был → пришёл → бесплатно)</span>
        </label>
        <div className="attendance-grid">
          {members.length ? members.map((m) => {
            const isPresent = !!present[m.student_id];
            const isFree = !!free[m.student_id];
            const isSkip = !!skip[m.student_id];
            // Пропуск уже закрыт доп.уроком/сожжён — флип в present задвоил бы учёт.
            // Берём из списка урока, а НЕ из строки посещаемости: ученика могли
            // добавить в группу после проведения урока и дать доп.урок вдогонку —
            // строки посещаемости у него нет, а карточку мы всё равно рисуем (они
            // идут по составу группы), и без списка она была бы красной и активной.
            const compensated = !!lesson?.compensated_student_ids?.includes(m.student_id);
            // Переведённый: слоты <= отработанного в старой группе ему не считаются.
            const lockedByTransfer = isLockedByTransfer(m, slotLessonNumber);
            // Исход недоступен при неопл.пропуске, компенсации и локе перевода;
            // в остальном ячейка редактируема и на проведённом уроке.
            const outcomeDisabled = isSkip || compensated || lockedByTransfer;
            // Цикл: не был → пришёл → бесплатно → не был. На новом уроке — только
            // локальное состояние (уйдёт в payload при создании); на проведённом —
            // сразу точечный эндпоинт (пересчитает зарплату), с откатом при ошибке.
            const cycle = () => {
              let nextPresent = isPresent;
              let nextFree = isFree;
              if (isFree) { nextPresent = false; nextFree = false; }        // бесплатно → не был
              else if (isPresent) { nextFree = true; }                       // пришёл → бесплатно
              else { nextPresent = true; nextFree = false; }                 // не был → пришёл
              setPresent((p) => ({ ...p, [m.student_id]: nextPresent }));
              setFree((f) => ({ ...f, [m.student_id]: nextFree }));
              if (lesson) {
                const send = (allowDebt: boolean) => muts.toggleAttendance.mutateAsync({
                  lessonId: lesson.id, studentId: m.student_id,
                  present: nextPresent, is_free: nextFree,
                  ...(allowDebt ? { allow_debt: true } : {}),
                });
                const rollback = () => {
                  setPresent((p) => ({ ...p, [m.student_id]: isPresent }));
                  setFree((f) => ({ ...f, [m.student_id]: isFree }));
                };
                const apply = () => {
                  setPresent((p) => ({ ...p, [m.student_id]: nextPresent }));
                  setFree((f) => ({ ...f, [m.student_id]: nextFree }));
                };
                send(false).catch((err) => {
                  // Оптимистичную отметку снимаем сразу: пока висит модалка,
                  // ячейка должна показывать реальное состояние сервера.
                  rollback();
                  handleBlocked(err, async () => {
                    apply();
                    await send(true).catch((e) => { rollback(); showError(e); });
                  });
                });
              }
            };
            // Неопл.пропуск: помечаем СЛОТ через group-эндпоинт — работает и на ещё
            // не проведённом уроке (без даты). На проведённом сервер материализует
            // сразу. Оптимистично + откат при ошибке.
            const toggleSkip = () => {
              const next = !isSkip;
              setSkip((s) => ({ ...s, [m.student_id]: next }));
              if (next) {
                setPresent((p) => ({ ...p, [m.student_id]: false }));
                setFree((f) => ({ ...f, [m.student_id]: false }));
              }
              muts.setGroupLessonSkip.mutateAsync({
                groupId: group.id, studentId: m.student_id, lessonNumber: slotLessonNumber, value: next,
              })
                .then(() => toast(next ? 'Неоплачиваемый пропуск' : 'Пропуск снят', 'ok'))
                .catch((err) => { setSkip((s) => ({ ...s, [m.student_id]: !next })); showError(err); });
            };
            return (
              <div key={m.student_id} className={`attendance-cell${isSkip ? ' is-skip' : ''}`}>
                <button
                  type="button"
                  className={`attendance-card ${isSkip ? 'is-skip' : compensated ? 'is-compensated' : isPresent ? 'is-present' : 'is-absent'}${isFree ? ' is-free' : ''}${outcomeDisabled ? ' is-locked' : ''}`}
                  onClick={outcomeDisabled ? undefined : cycle}
                  disabled={outcomeDisabled}
                  aria-disabled={outcomeDisabled}
                  title={
                    compensated ? 'Пропуск уже закрыт доп.уроком/сгоранием — исход не меняем (двойной учёт)'
                    : lockedByTransfer ? 'Переведён: этот урок ему не засчитывается'
                    : isSkip ? 'Неоплачиваемый пропуск — снимите его, чтобы менять исход'
                    : isFree ? 'Бесплатное занятие — ученик не платит (баланс не списывается), преподавателю зарплата начисляется как обычно; прогресс курса идёт'
                    : undefined
                  }
                >
                  <span className="attendance-card__icon" aria-hidden>{isSkip ? '—' : compensated ? '↻' : isFree ? '🎁' : isPresent ? '✓' : '✕'}</span>
                  <span className="attendance-card__name">{m.student_name || `#${m.student_id}`}</span>
                  {isFree && <span className="attendance-card__free">бесплатно</span>}
                  {isSkip && <span className="attendance-card__free">неопл. пропуск</span>}
                  {compensated && <span className="attendance-card__free">доп.урок / сгорание</span>}
                </button>
                <button
                  type="button"
                  className={`attendance-skip-btn${isSkip ? ' is-on' : ''}`}
                  onClick={compensated || lockedByTransfer ? undefined : toggleSkip}
                  disabled={compensated || lockedByTransfer || muts.setGroupLessonSkip.isPending}
                  title="Неоплачиваемый пропуск: ученик этот урок не посещает (перевод / начал не с 1-го). Денег ноль, в зарплату не входит."
                >
                  {isSkip ? 'Снять' : 'Неопл. пропуск'}
                </button>
              </div>
            );
          }) : (
            <div className="memberships__empty">В группе нет учеников</div>
          )}
        </div>
      </div>
      <div className="lesson-editor__footer">
        {lesson && (
          <button
            type="button"
            className={`btn-delete${confirmingDelete ? ' is-confirming' : ''}`}
            onClick={() => { void handleDelete(); }}
          >{confirmingDelete ? 'Точно удалить?' : 'Удалить урок'}</button>
        )}
        <button
          type="button"
          className="btn-save"
          style={{ marginLeft: 'auto' }}
          onClick={() => { void handleSave(); }}
          disabled={muts.create.isPending || muts.update.isPending || muts.remove.isPending}
        >{lesson ? 'Сохранить' : 'Создать урок'}</button>
      </div>

      {debtPrompt && (
        <Dialog
          open
          onOpenChange={(o) => { if (!o) setDebtPrompt(null); }}
          title="Записать в долг?"
          footer={
            <>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setDebtPrompt(null)}
              >Отмена</button>
              <button
                type="button"
                className="btn-primary"
                onClick={() => {
                  const { retry } = debtPrompt;
                  setDebtPrompt(null);
                  void retry();
                }}
              >Записать в долг</button>
            </>
          }
        >
          <p className="lesson-editor__debt-text">{debtPrompt.message}</p>
          <p className="lesson-editor__debt-text">
            Занятие запишется как обычное платное: баланс ученика уйдёт глубже в
            минус, преподавателю зарплата за него начислится. Это не «бесплатное
            занятие» — там с ученика не списывается ничего.
          </p>
        </Dialog>
      )}
    </div>
  );
}
