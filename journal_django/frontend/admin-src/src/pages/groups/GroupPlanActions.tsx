import { forwardRef, useImperativeHandle, useMemo, useState } from 'react';
import { useGroupPlan, type PlanRow } from '../../hooks/useGroupPlanCalendar';
import {
  useGeneratePlan, useReschedule, usePermanentChange, usePermanentChangePreview, useCancelLesson,
  useChangeTeacher, useChangeTeacherPermanent,
  type PermanentChangeSlot, type AffectedOp,
} from '../../hooks/useGroupPlan';
import { useScheduleChange, type ScheduleChangePayload } from '../../hooks/useGroupSchedule';
import { useTeachers } from '../../hooks/useTeachers';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { Dialog } from '../../components/ui/Dialog';
import { Button } from '../../components/ui/Button';
import { Field } from '../../components/form/Field';
import { SelectInput } from '../../components/form/SelectInput';
import { DateInput } from '../../components/form/DateInput';
import { TimeInput } from '../../components/form/TimeInput';
import { Combobox } from '../../components/form/Combobox';
import { fmtDate, todayMSK } from '../../lib/format';
import type { LessonActionKind } from '../../shared/calendar/LessonPopup';
import type { Occurrence } from '../../shared/calendar/types';
import type { Group } from '../../lib/types';

/** День недели Monday-first в UI, значения — уже в конвенции проекта (Вс=0..Сб=6),
 * поэтому дополнительная конвертация индекса при отправке не нужна. */
const DOW_OPTIONS = [
  { value: 1, label: 'Понедельник' },
  { value: 2, label: 'Вторник' },
  { value: 3, label: 'Среда' },
  { value: 4, label: 'Четверг' },
  { value: 5, label: 'Пятница' },
  { value: 6, label: 'Суббота' },
  { value: 0, label: 'Воскресенье' },
];

function lessonLabel(r: PlanRow): string {
  const time = r.scheduled_time ? r.scheduled_time.slice(0, 5) : '';
  const pos = r.seq != null ? `Урок ${r.seq}` : 'Доп. занятие';
  return `${pos} — ${fmtDate(r.scheduled_date)}${time ? ` ${time}` : ''}`;
}

type TeacherScope = 'once' | 'permanent';

export interface GroupPlanActionsHandle {
  /** Вызывается из CalendarView.onAction (клик по быстрому действию в LessonPopup). */
  quickAction: (kind: LessonActionKind, occ: Occurrence) => void;
}

interface Props {
  group: Group;
}

type DialogKind = 'reschedule' | 'permanent' | 'permanent-confirm' | 'change-teacher' | 'cancel' | 'set-schedule' | null;

const AFFECTED_KIND_LABELS: Record<AffectedOp['kind'], string> = {
  reschedule: 'перенос',
  substitution: 'замена преподавателя',
  cancellation: 'отмена',
};

/**
 * Toolbar кнопок + модалки операций плана (planned_lessons) для вкладки
 * «Расписание» группы. Заменяет прежние всегда-развёрнутые инлайн-панели
 * GroupScheduleBlock (постоянная смена/разовое исключение) — те мутировали
 * group_schedule_slots/lesson_schedule_exceptions напрямую и не пересчитывали
 * planned_lessons. Операции здесь бьют в /api/admin/groups/<id>/plan/*
 * (docs/lesson-scheduling.md) и сами инвалидируют план+календарь.
 */
const GroupPlanActions = forwardRef<GroupPlanActionsHandle, Props>(function GroupPlanActions(
  { group },
  ref,
) {
  const groupId = group.id;
  const { data: plan = [], isLoading: planLoading } = useGroupPlan(groupId);
  const { data: teachers = [] } = useTeachers(true);
  const { toast } = useToast();
  const showError = useApiError();

  const generatePlan = useGeneratePlan(groupId);
  const reschedule = useReschedule(groupId);
  const permanentChange = usePermanentChange(groupId);
  const permanentChangePreview = usePermanentChangePreview(groupId);
  const changeTeacher = useChangeTeacher(groupId);
  const changeTeacherPermanent = useChangeTeacherPermanent(groupId);
  const cancelLesson = useCancelLesson(groupId);
  const scheduleChange = useScheduleChange(groupId);

  // Есть ли у группы активное расписание (recurrence-слоты). Без слотов план не
  // строится (generate_for_group → reason=no_slots), а «Изменить расписание»
  // недоступно (нужен существующий урок-якорь). Поэтому для группы без слотов
  // даём отдельный вход «Задать расписание» (bootstrap первого расписания).
  const hasSlots = (group.slots ?? []).length > 0;

  const [dialog, setDialog] = useState<DialogKind>(null);
  const closeDialog = () => setDialog(null);

  const teacherOptions = useMemo(
    () => teachers.filter((t) => t.active).map((t) => ({ value: String(t.id), label: t.name })),
    [teachers],
  );
  const teacherOptionsOptional = useMemo(
    () => [{ value: '', label: '— не менять —' }, ...teacherOptions],
    [teacherOptions],
  );

  // Разовый перенос / смена препода разово: доступны для активных строк (курсовых
  // и доп. занятий), но НЕ для проведённых ('done') и НЕ для маркеров отмены
  // ('cancelled') — двигать/переназначать пин отмены бессмысленно.
  const reschedulableRows = useMemo(
    () => plan.filter((r) => r.status !== 'done' && r.status !== 'cancelled'),
    [plan],
  );
  // Отмена / перенос-навсегда: бэкенд требует курсовую строку (seq != null)
  // в статусе pending/overdue (services.cancel/permanent_change) — done,
  // cancelled, moved и доп. занятия (seq=NULL) отклоняются 400/409.
  const courseRows = useMemo(
    () => plan.filter((r) => r.seq != null && (r.status === 'pending' || r.status === 'overdue')),
    [plan],
  );
  const permanentRows = useMemo(
    () => [...courseRows].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0)),
    [courseRows],
  );

  // ── Перенести занятие (reschedule) ──
  const [rLessonId, setRLessonId] = useState('');
  const [rDate, setRDate] = useState('');
  const [rTime, setRTime] = useState('');
  const [rTeacherId, setRTeacherId] = useState('');

  const openReschedule = (prefill?: PlanRow) => {
    setRLessonId(prefill ? String(prefill.id) : '');
    setRDate(prefill?.scheduled_date ?? '');
    setRTime(prefill?.scheduled_time?.slice(0, 5) ?? '');
    setRTeacherId(prefill?.teacher_id ? String(prefill.teacher_id) : '');
    setDialog('reschedule');
  };

  const selectRescheduleLesson = (id: string) => {
    setRLessonId(id);
    const row = reschedulableRows.find((r) => String(r.id) === id);
    if (row) {
      setRDate(row.scheduled_date);
      setRTime(row.scheduled_time?.slice(0, 5) ?? '');
      setRTeacherId(row.teacher_id ? String(row.teacher_id) : '');
    }
  };

  const submitReschedule = async () => {
    if (!rLessonId) { toast('Выберите занятие', 'error'); return; }
    if (!rDate) { toast('Укажите новую дату', 'error'); return; }
    try {
      await reschedule.mutateAsync({
        lessonId: Number(rLessonId),
        body: {
          new_date: rDate,
          new_time: rTime || null,
          new_teacher_id: rTeacherId ? Number(rTeacherId) : null,
        },
      });
      toast('Занятие перенесено', 'ok');
      closeDialog();
    } catch (err) { showError(err); }
  };

  // ── Изменить расписание навсегда (permanent-change) ──
  // Целевое РАСПИСАНИЕ задаётся набором слотов (день+время). Для мультислотовых
  // групп это единственный корректный способ; для одно-слотовых — набор из одного
  // слота. Прочерк — текущим активным расписанием группы.
  const [pFromSeq, setPFromSeq] = useState('');
  const [pEffectiveFrom, setPEffectiveFrom] = useState('');
  const [pSlots, setPSlots] = useState<PermanentChangeSlot[]>([]);
  const [pAffected, setPAffected] = useState<AffectedOp[]>([]);

  const openPermanent = (prefill?: PlanRow) => {
    setPFromSeq(prefill?.seq != null ? String(prefill.seq) : '');
    setPEffectiveFrom(prefill?.scheduled_date ?? '');
    const current = (group.slots ?? []).map((s) => ({
      day_of_week: s.day_of_week,
      start_time: String(s.start_time).slice(0, 5),
    }));
    setPSlots(current.length > 0 ? current : [{ day_of_week: 1, start_time: '18:00' }]);
    setDialog('permanent');
  };

  const updatePSlot = (i: number, key: 'day_of_week' | 'start_time', value: string) => {
    setPSlots((arr) => {
      const next = [...arr];
      next[i] = { ...next[i], [key]: key === 'day_of_week' ? Number(value) : value };
      return next;
    });
  };
  const addPSlot = () => setPSlots((arr) => [...arr, { day_of_week: 1, start_time: '18:00' }]);
  const removePSlot = (i: number) => setPSlots((arr) => arr.filter((_, idx) => idx !== i));

  const selectPermanentLesson = (seq: string) => {
    setPFromSeq(seq);
    const row = permanentRows.find((r) => String(r.seq) === seq);
    if (row) setPEffectiveFrom(row.scheduled_date);
  };

  /** Валидирует форму и собирает тело запроса, либо тостит ошибку и возвращает null. */
  const buildPermanentPayload = () => {
    if (!pFromSeq) { toast('Выберите занятие, с которого меняем расписание', 'error'); return null; }
    if (!pEffectiveFrom) { toast('Укажите дату, с которой действует новое расписание', 'error'); return null; }
    const slots = pSlots
      .filter((s) => s.start_time)
      .map((s) => ({ day_of_week: s.day_of_week, start_time: s.start_time }));
    if (slots.length === 0) { toast('Добавьте хотя бы один слот', 'error'); return null; }
    const seen = new Set<string>();
    for (const s of slots) {
      const k = `${s.day_of_week} ${s.start_time}`;
      if (seen.has(k)) { toast('Есть повторяющиеся слоты (день + время)', 'error'); return null; }
      seen.add(k);
    }
    return { from_seq: Number(pFromSeq), effective_from: pEffectiveFrom, new_slots: slots };
  };

  const applyPermanent = async (payload: ReturnType<typeof buildPermanentPayload>) => {
    if (!payload) return;
    try {
      await permanentChange.mutateAsync(payload);
      toast('Расписание изменено', 'ok');
      closeDialog();
    } catch (err) { showError(err); }
  };

  // Сначала preview: если менять расписание — значит, сбрасывать разовые
  // операции (переносы/замены/отмены) в хвосте начиная с from_seq. Если
  // такие есть — сначала показываем, что именно сбросится, и ждём
  // подтверждения; иначе применяем сразу (см. RevertConfirmDialog — тот же
  // паттерн «сначала спросить бэкенд, потом либо закрыть, либо подтвердить»).
  const submitPermanent = async () => {
    const payload = buildPermanentPayload();
    if (!payload) return;
    try {
      const { affected } = await permanentChangePreview.mutateAsync(payload);
      if (affected.length === 0) {
        await applyPermanent(payload);
      } else {
        setPAffected(affected);
        setDialog('permanent-confirm');
      }
    } catch (err) { showError(err); }
  };

  const confirmPermanent = async () => {
    await applyPermanent(buildPermanentPayload());
  };

  // ── Сменить преподавателя (разово / навсегда) ──
  const [tScope, setTScope] = useState<TeacherScope>('once');
  const [tLessonId, setTLessonId] = useState('');
  const [tFromSeq, setTFromSeq] = useState('');
  const [tTeacherId, setTTeacherId] = useState('');

  const openChangeTeacher = (prefill?: PlanRow, scope: TeacherScope = 'once') => {
    setTScope(scope);
    setTLessonId(prefill ? String(prefill.id) : '');
    setTFromSeq(prefill?.seq != null ? String(prefill.seq) : '');
    setTTeacherId(prefill?.teacher_id ? String(prefill.teacher_id) : '');
    setDialog('change-teacher');
  };

  const submitChangeTeacher = async () => {
    if (!tTeacherId) { toast('Выберите преподавателя', 'error'); return; }
    try {
      if (tScope === 'permanent') {
        if (!tFromSeq) { toast('Выберите занятие, с которого меняем преподавателя', 'error'); return; }
        await changeTeacherPermanent.mutateAsync({
          from_seq: Number(tFromSeq), new_teacher_id: Number(tTeacherId),
        });
        toast('Преподаватель изменён для будущих занятий', 'ok');
      } else {
        if (!tLessonId) { toast('Выберите занятие', 'error'); return; }
        await changeTeacher.mutateAsync({
          lessonId: Number(tLessonId), newTeacherId: Number(tTeacherId),
        });
        toast('Преподаватель занятия изменён', 'ok');
      }
      closeDialog();
    } catch (err) { showError(err); }
  };

  // ── Отменить занятие (cancel) ──
  const [cLessonId, setCLessonId] = useState('');

  const openCancel = (prefill?: PlanRow) => {
    setCLessonId(prefill ? String(prefill.id) : '');
    setDialog('cancel');
  };

  const submitCancel = async () => {
    if (!cLessonId) { toast('Выберите занятие', 'error'); return; }
    try {
      await cancelLesson.mutateAsync(Number(cLessonId));
      toast('Занятие отменено, курс продлён на неделю', 'ok');
      closeDialog();
    } catch (err) { showError(err); }
  };

  // ── Задать расписание (bootstrap первого расписания для группы без слотов) ──
  const [ssDate, setSsDate] = useState('');
  const [ssSlots, setSsSlots] = useState<PermanentChangeSlot[]>([]);

  const openSetSchedule = () => {
    setSsDate(group.group_start_date?.slice(0, 10) || todayMSK());
    setSsSlots([{ day_of_week: 1, start_time: '18:00' }]);
    setDialog('set-schedule');
  };
  const updateSsSlot = (i: number, key: 'day_of_week' | 'start_time', value: string) => {
    setSsSlots((arr) => {
      const next = [...arr];
      next[i] = { ...next[i], [key]: key === 'day_of_week' ? Number(value) : value };
      return next;
    });
  };
  // Групповой формат — максимум один слот (индивидуальный — сколько угодно).
  // Инвариант держала форма создания; теперь расписание задаётся здесь.
  const canAddSsSlot = group.is_individual || ssSlots.length < 1;
  const addSsSlot = () => setSsSlots((arr) => (
    canAddSsSlot ? [...arr, { day_of_week: 1, start_time: '18:00' }] : arr
  ));
  const removeSsSlot = (i: number) => setSsSlots((arr) => arr.filter((_, idx) => idx !== i));

  const submitSetSchedule = async () => {
    if (!ssDate) { toast('Укажите дату начала', 'error'); return; }
    const slots = ssSlots
      .filter((s) => s.start_time)
      .map((s) => ({ day_of_week: s.day_of_week, start_time: s.start_time }));
    if (slots.length === 0) { toast('Добавьте хотя бы один слот', 'error'); return; }
    if (!group.is_individual && slots.length > 1) {
      toast('Групповой формат допускает только один слот', 'error'); return;
    }
    const seen = new Set<string>();
    for (const s of slots) {
      const k = `${s.day_of_week} ${s.start_time}`;
      if (seen.has(k)) { toast('Есть повторяющиеся слоты (день + время)', 'error'); return; }
      seen.add(k);
    }
    try {
      // Единый вызов: schedule-change создаёт слоты, проставляет дату начала
      // (если ещё не задана) и число занятий в неделю, автогенерирует план — всё
      // в apps.groups.services.apply_schedule_change.
      const body: ScheduleChangePayload = { effective_from: ssDate, slots };
      await scheduleChange.mutateAsync(body);
      toast('Расписание задано, план сгенерирован', 'ok');
      closeDialog();
    } catch (err) { showError(err); }
  };

  // ── Сгенерировать план (generate) ──
  const submitGenerate = async () => {
    try {
      await generatePlan.mutateAsync();
      toast('План сгенерирован', 'ok');
    } catch (err) { showError(err); }
  };

  // ── Быстрые действия из LessonPopup (CalendarView.onAction) ──
  useImperativeHandle(ref, () => ({
    quickAction(kind, occ) {
      const row = occ.id != null ? plan.find((r) => r.id === occ.id) : undefined;
      if (kind === 'cancel') {
        openCancel(row);
      } else if (kind === 'change-teacher') {
        openChangeTeacher(row, 'once');
      } else {
        openReschedule(row);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- plan из замыкания useImperativeHandle пересоздаётся при каждом изменении plan (deps ниже).
  }), [plan]);

  const rescheduleOptions = reschedulableRows.map((r) => ({ value: String(r.id), label: lessonLabel(r) }));
  const cancelOptions = courseRows.map((r) => ({ value: String(r.id), label: lessonLabel(r) }));
  const permanentOptions = permanentRows.map((r) => ({ value: String(r.seq), label: lessonLabel(r) }));
  const teacherScopeOptions = [
    { value: 'once', label: 'Только это занятие' },
    { value: 'permanent', label: 'Это и все последующие' },
  ];

  return (
    <div className="plan-actions">
      <div className="plan-actions__toolbar">
        {!planLoading && plan.length === 0 ? (
          hasSlots ? (
            <Button variant="primary" onClick={() => { void submitGenerate(); }} disabled={generatePlan.isPending}>
              Сгенерировать план
            </Button>
          ) : (
            // Слотов нет — план сгенерировать нечем; сначала задаём расписание.
            <Button variant="primary" onClick={openSetSchedule}>
              Задать расписание
            </Button>
          )
        ) : (
          <>
            <Button onClick={() => openReschedule()} disabled={reschedulableRows.length === 0}>
              Перенести занятие
            </Button>
            <Button onClick={() => openChangeTeacher()} disabled={reschedulableRows.length === 0}>
              Сменить преподавателя
            </Button>
            <Button onClick={() => openPermanent()} disabled={permanentRows.length === 0}>
              Изменить расписание
            </Button>
            <Button variant="danger" onClick={() => openCancel()} disabled={courseRows.length === 0}>
              Отменить занятие
            </Button>
          </>
        )}
      </div>

      {/* Перенести занятие */}
      <Dialog
        open={dialog === 'reschedule'}
        onOpenChange={(o) => !o && closeDialog()}
        title="Перенести занятие"
        footer={
          <Button variant="primary" onClick={() => { void submitReschedule(); }} disabled={reschedule.isPending}>
            Перенести
          </Button>
        }
      >
        <Field label="Занятие" required>
          <SelectInput
            value={rLessonId}
            onChange={(e) => selectRescheduleLesson(e.target.value)}
            options={rescheduleOptions}
            placeholder="Выберите занятие"
          />
        </Field>
        <Field label="Новая дата" required>
          <DateInput value={rDate} onChange={(e) => setRDate(e.target.value)} />
        </Field>
        <Field label="Новое время">
          <TimeInput value={rTime} onChange={(e) => setRTime(e.target.value)} />
        </Field>
        <Field label="Преподаватель">
          <Combobox value={rTeacherId} onChange={setRTeacherId} options={teacherOptionsOptional} placeholder="Не менять" />
        </Field>
      </Dialog>

      {/* Изменить расписание навсегда */}
      <Dialog
        open={dialog === 'permanent'}
        onOpenChange={(o) => !o && closeDialog()}
        title="Изменить расписание"
        footer={
          <Button
            variant="primary"
            onClick={() => { void submitPermanent(); }}
            disabled={permanentChangePreview.isPending || permanentChange.isPending}
          >
            Применить
          </Button>
        }
      >
        <div className="schedule-form__hint">
          Задаёт недельный НАБОР слотов (день + время) для всех будущих занятий,
          начиная с выбранного, и дату, с которой новое расписание действует.
          Можно добавлять и удалять слоты — так меняется и число занятий в
          неделю. Преподавателя меняйте через «Сменить преподавателя».
        </div>
        <Field label="С какого занятия" required>
          <SelectInput
            value={pFromSeq}
            onChange={(e) => selectPermanentLesson(e.target.value)}
            options={permanentOptions}
            placeholder="Выберите занятие"
          />
        </Field>
        <Field label="Дата, с которой действует новое расписание" required>
          <DateInput value={pEffectiveFrom} onChange={(e) => setPEffectiveFrom(e.target.value)} />
        </Field>
        <Field label="Слоты (день + время)" required>
          <div className="slots-block">
            <div id="permanent-slots-list">
              {pSlots.map((s, i) => (
                <div key={i} className="slot-row">
                  <SelectInput
                    value={String(s.day_of_week)}
                    onChange={(e) => updatePSlot(i, 'day_of_week', e.target.value)}
                    options={DOW_OPTIONS}
                  />
                  <TimeInput
                    value={s.start_time}
                    onChange={(e) => updatePSlot(i, 'start_time', e.target.value)}
                  />
                  <button
                    type="button"
                    className="slot-row__remove"
                    onClick={() => removePSlot(i)}
                    aria-label="Удалить слот"
                    disabled={pSlots.length <= 1}
                  >×</button>
                </div>
              ))}
            </div>
            <button type="button" className="slot-add" onClick={addPSlot}>+ Добавить слот</button>
          </div>
        </Field>
      </Dialog>

      {/* Подтверждение: что сбросится в хвосте при реальном применении permanent-change */}
      <Dialog
        open={dialog === 'permanent-confirm'}
        onOpenChange={(o) => !o && closeDialog()}
        title="Применить изменение расписания?"
        footer={
          <>
            <Button onClick={closeDialog} disabled={permanentChange.isPending}>
              Отмена
            </Button>
            <Button
              variant="primary"
              onClick={() => { void confirmPermanent(); }}
              disabled={permanentChange.isPending}
            >
              Применить и сбросить
            </Button>
          </>
        }
      >
        <div className="schedule-form__hint">
          В хвосте есть разовые операции (переносы, замены преподавателя,
          отмены), которые будут сброшены к новому расписанию:
        </div>
        <ul className="affected-ops-list">
          {pAffected.map((op, i) => (
            <li key={i} className="affected-ops-list__item">
              <span className="status-badge status-badge--negative">{AFFECTED_KIND_LABELS[op.kind]}</span>
              {op.seq != null && <span> урок {op.seq}</span>}
              <span> — {fmtDate(op.date)}</span>
              {op.time && <span> {op.time.slice(0, 5)}</span>}
              {op.from_date && <span> (было {fmtDate(op.from_date)})</span>}
            </li>
          ))}
        </ul>
      </Dialog>

      {/* Сменить преподавателя (разово / навсегда) */}
      <Dialog
        open={dialog === 'change-teacher'}
        onOpenChange={(o) => !o && closeDialog()}
        title="Сменить преподавателя"
        footer={
          <Button
            variant="primary"
            onClick={() => { void submitChangeTeacher(); }}
            disabled={changeTeacher.isPending || changeTeacherPermanent.isPending}
          >
            Применить
          </Button>
        }
      >
        <Field label="Область" required>
          <SelectInput
            value={tScope}
            onChange={(e) => setTScope(e.target.value as TeacherScope)}
            options={teacherScopeOptions}
          />
        </Field>
        {tScope === 'permanent' ? (
          <Field label="С какого занятия" required>
            <SelectInput
              value={tFromSeq}
              onChange={(e) => setTFromSeq(e.target.value)}
              options={permanentOptions}
              placeholder="Выберите занятие"
            />
          </Field>
        ) : (
          <Field label="Занятие" required>
            <SelectInput
              value={tLessonId}
              onChange={(e) => setTLessonId(e.target.value)}
              options={rescheduleOptions}
              placeholder="Выберите занятие"
            />
          </Field>
        )}
        <Field label="Преподаватель" required>
          <Combobox value={tTeacherId} onChange={setTTeacherId} options={teacherOptions} placeholder="Выберите преподавателя" />
        </Field>
      </Dialog>

      {/* Отменить занятие */}
      <Dialog
        open={dialog === 'cancel'}
        onOpenChange={(o) => !o && closeDialog()}
        title="Отменить занятие"
        footer={
          <Button variant="danger" onClick={() => { void submitCancel(); }} disabled={cancelLesson.isPending}>
            Отменить занятие
          </Button>
        }
      >
        <Field label="Занятие" required>
          <SelectInput
            value={cLessonId}
            onChange={(e) => setCLessonId(e.target.value)}
            options={cancelOptions}
            placeholder="Выберите занятие"
          />
        </Field>
        <div className="schedule-form__hint">
          Все последующие непроведённые занятия сдвинутся на неделю позже — курс продлевается,
          отменённое занятие не списывается с абонемента.
        </div>
      </Dialog>

      {/* Задать расписание (bootstrap для группы без слотов) */}
      <Dialog
        open={dialog === 'set-schedule'}
        onOpenChange={(o) => !o && closeDialog()}
        title="Задать расписание"
        footer={
          <Button
            variant="primary"
            onClick={() => { void submitSetSchedule(); }}
            disabled={scheduleChange.isPending}
          >
            Задать и сгенерировать план
          </Button>
        }
      >
        <div className="schedule-form__hint">
          Недельный набор слотов (день + время) и дата начала занятий. После
          сохранения план курса генерируется автоматически.
        </div>
        <Field label="Дата начала занятий" required>
          <DateInput
            value={ssDate}
            onChange={(e) => setSsDate(e.target.value)}
            disabled={!!group.group_start_date}
          />
          {!!group.group_start_date && (
            <span className="field-hint">Дата начала уже закреплена за группой</span>
          )}
        </Field>
        <Field label="Слоты (день + время)" required>
          <div className="slots-block">
            <div id="set-schedule-slots-list">
              {ssSlots.map((s, i) => (
                <div key={i} className="slot-row">
                  <SelectInput
                    value={String(s.day_of_week)}
                    onChange={(e) => updateSsSlot(i, 'day_of_week', e.target.value)}
                    options={DOW_OPTIONS}
                  />
                  <TimeInput
                    value={s.start_time}
                    onChange={(e) => updateSsSlot(i, 'start_time', e.target.value)}
                  />
                  <button
                    type="button"
                    className="slot-row__remove"
                    onClick={() => removeSsSlot(i)}
                    aria-label="Удалить слот"
                    disabled={ssSlots.length <= 1}
                  >×</button>
                </div>
              ))}
            </div>
            {canAddSsSlot && (
              <button type="button" className="slot-add" onClick={addSsSlot}>+ Добавить слот</button>
            )}
            {!group.is_individual && (
              <span className="field-hint">Групповой формат — один слот в неделю</span>
            )}
          </div>
        </Field>
      </Dialog>
    </div>
  );
});

export default GroupPlanActions;
