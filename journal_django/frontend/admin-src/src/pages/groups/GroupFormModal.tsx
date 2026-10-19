import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useGroupMutations } from '../../hooks/useGroups';
import { useTeachers } from '../../hooks/useTeachers';
import { useDirections } from '../../hooks/useDirections';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { Dialog } from '../../components/ui/Dialog';
import { Field } from '../../components/form/Field';
import { TextInput } from '../../components/form/TextInput';
import { SelectInput } from '../../components/form/SelectInput';
import { NumberInput } from '../../components/form/NumberInput';
import type { Group, LessonDuration } from '../../lib/types';

interface Props { initial: Group | null; onClose: () => void; }

/**
 * Форма создания/редактирования «паспорта» группы: название, направление,
 * преподаватель, чат ВК, формат, длительность. Расписание (дата начала занятий +
 * слоты) здесь НЕ задаётся — это единая точка «Задать расписание» на карточке
 * группы (вкладка «Расписание»), которая создаёт слоты, ставит дату начала и
 * генерирует план. После создания направление/преподаватель/длительность/формат
 * закреплены за группой (сервер игнорирует их смену в PATCH); преподаватель
 * меняется только операцией «смена преподавателя на все уроки».
 */
export default function GroupFormModal({ initial, onClose }: Props) {
  const isNew = !initial;
  const navigate = useNavigate();
  const muts = useGroupMutations();
  const { data: teachers = [] } = useTeachers(true);
  const { data: directions = [] } = useDirections(true);
  const { toast } = useToast();
  const showError = useApiError();

  const [name, setName] = useState(initial?.name || '');
  const [directionId, setDirectionId] = useState<string>(initial?.direction_id ? String(initial.direction_id) : '');
  const [teacherId, setTeacherId] = useState<string>(initial?.teacher_id ? String(initial.teacher_id) : '');
  const [vkChat, setVkChat] = useState(initial?.vk_chat || '');
  const [duration, setDuration] = useState<LessonDuration>(initial?.lesson_duration_minutes || 90);
  // Новая группа по умолчанию — индивидуальный формат; при редактировании берём значение группы.
  const [isIndividual, setIsIndividual] = useState(initial ? initial.is_individual : true);
  // Пустая строка = «как в направлении» (на бэк уйдёт null).
  const [lessonsTotal, setLessonsTotal] = useState<string>(
    initial?.lessons_total != null ? String(initial.lessons_total) : '',
  );

  const teacherOptions = [{ value: '', label: '— выберите —' }, ...teachers
    .filter((t) => t.active || (initial && initial.teacher_id === t.id))
    .map((t) => ({ value: t.id, label: t.name }))];
  const directionOptions = [{ value: '', label: '— выберите —' }, ...directions
    .filter((d) => d.active || (initial && initial.direction_id === d.id))
    .map((d) => ({ value: d.id, label: d.name }))];
  const selectedDirection = directions.find((d) => d.id === Number(directionId));
  const directionLessons = selectedDirection?.total_lessons ?? null;
  // Половинный формат: один урок курса = два занятия по 45 минут.
  const sessionsHint = lessonsTotal && duration === 45
    ? `${lessonsTotal} ур. = ${Number(lessonsTotal) * 2} занятий по 45 мин`
    : null;

  // Формат закреплён за группой после создания — при редактировании смена запрещена.
  const chooseFormat = (individual: boolean) => {
    if (!isNew) return;
    setIsIndividual(individual);
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!directionId || !teacherId) {
      toast('Направление и преподаватель обязательны', 'error');
      return;
    }
    try {
      if (isNew) {
        // Группа создаётся без расписания. Дата начала + слоты + план — на карточке
        // группы («Задать расписание»). lessons_per_week — плейсхолдер (1), реальное
        // число проставит schedule-change по количеству слотов.
        const created = await muts.create.mutateAsync({
          name,
          direction_id: Number(directionId),
          teacher_id: Number(teacherId),
          is_individual: isIndividual,
          lesson_duration_minutes: duration,
          lessons_per_week: 1,
          lessons_total: lessonsTotal ? Number(lessonsTotal) : null,
          vk_chat: vkChat || null,
          slots: [],
        });
        toast('Создано', 'ok');
        onClose();
        navigate(`/admin/groups/${created.id}`);
      } else {
        // Неизменны направление, преподаватель, длительность (сервер их игнорирует
        // в PATCH). Расписание/дата начала задаются на карточке группы. Здесь —
        // только название и чат ВК.
        await muts.update.mutateAsync({ id: initial!.id, body: {
          name,
          vk_chat: vkChat || null,
          lessons_total: lessonsTotal ? Number(lessonsTotal) : null,
        } });
        toast('Сохранено', 'ok');
        onClose();
      }
    } catch (err) { showError(err); }
  };

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      wide
      title={isNew ? 'Новая группа' : `Редактировать: ${initial!.name}`}
      footer={
        <button type="submit" form="group-form" className="btn-save" disabled={muts.create.isPending || muts.update.isPending}>
          Сохранить
        </button>
      }
    >
      <form id="group-form" className="modal-form" onSubmit={onSubmit}>
        <div className="modal-section-label">Основное</div>
        <Field label="Название группы" required full>
          <TextInput required value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Направление" required>
          <SelectInput value={directionId} onChange={(e) => setDirectionId(e.target.value)} options={directionOptions} required disabled={!isNew} />
          {!isNew && (
            <span className="field-hint">Направление закреплено за группой после создания</span>
          )}
        </Field>
        <Field label="Преподаватель" required>
          <SelectInput value={teacherId} onChange={(e) => setTeacherId(e.target.value)} options={teacherOptions} required disabled={!isNew} />
          {!isNew && (
            <span className="field-hint">Сменить преподавателя можно только операцией «смена преподавателя на все уроки» на странице группы</span>
          )}
        </Field>
        <Field label="Ссылка на чат ВК" full>
          <TextInput value={vkChat} onChange={(e) => setVkChat(e.target.value)} placeholder="https://vk.me/..." />
        </Field>

        <div className="modal-section-label">Параметры</div>
        <Field label="Формат занятия" full>
          {/* Формат жёстко закреплён за группой после создания — при редактировании только чтение. */}
          <div className="format-toggle" role="radiogroup" aria-label="Формат занятия">
            <button
              type="button"
              role="radio"
              aria-checked={isIndividual}
              disabled={!isNew}
              data-format="individual"
              className={`format-toggle__btn${isIndividual ? ' is-active' : ''}`}
              onClick={() => chooseFormat(true)}
            >
              <span className="format-toggle__dot" />
              <span className="format-toggle__title">Индивидуальный формат</span>
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={!isIndividual}
              disabled={!isNew}
              data-format="group"
              className={`format-toggle__btn${!isIndividual ? ' is-active' : ''}`}
              onClick={() => chooseFormat(false)}
            >
              <span className="format-toggle__dot" />
              <span className="format-toggle__title">Групповой формат</span>
            </button>
          </div>
          {!isNew && (
            <span className="field-hint">Формат нельзя изменить после создания группы</span>
          )}
        </Field>
        <Field label="Длительность">
          <SelectInput
            value={String(duration)}
            onChange={(e) => setDuration(Number(e.target.value) as LessonDuration)}
            options={[
              { value: 45, label: '45 мин' },
              { value: 60, label: '60 мин' },
              { value: 90, label: '90 мин' },
            ]}
            disabled={!isNew}
          />
          {!isNew && (
            <span className="field-hint">Длительность закреплена за группой после создания</span>
          )}
        </Field>
        <Field label="Уроков в группе">
          <NumberInput
            min={1}
            step={1}
            value={lessonsTotal}
            onChange={(e) => setLessonsTotal(e.target.value)}
            placeholder={directionLessons ? `как в направлении (${directionLessons})` : 'как в направлении'}
          />
          <span className="field-hint">
            {sessionsHint
              || 'Сколько уроков курса пройдёт эта группа. Пусто — весь курс направления.'}
          </span>
        </Field>

        {isNew && (
          <div className="field-hint" style={{ marginTop: 'var(--space-2)' }}>
            Дни занятий и дату начала зададите после создания — кнопкой «Задать
            расписание» на карточке группы (вкладка «Расписание»). Тогда же
            сгенерируется план.
          </div>
        )}
      </form>
    </Dialog>
  );
}
