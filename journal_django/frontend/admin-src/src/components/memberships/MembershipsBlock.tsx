import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMemberships, useMembershipMutations } from '../../hooks/useMemberships';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../ui/Toast';
import { SelectInput } from '../form/SelectInput';
import { scheduledMakeupsBlockMessage } from '../../lib/api';
import { ScheduledMakeupsBlockModal } from './ScheduledMakeupsBlockModal';
import type { GroupMembership } from '../../lib/types';
import { BlockLoading } from '../ui/Skeleton';

type PickerOption = { value: number; label: string; disabled?: boolean };
type Mode =
  // capacity — максимум активных membership; при достижении лимита блок добавления скрывается.
  | { mode: 'byStudent'; studentId: number; pickerOptions: PickerOption[]; pickerLabel: string; capacity?: number; capacityNote?: string }
  | { mode: 'byGroup';   groupId: number;   pickerOptions: PickerOption[]; pickerLabel: string; capacity?: number; capacityNote?: string };

interface Props {
  config: Mode;
  renderCard: (m: GroupMembership) => { title: string; meta: React.ReactNode; navigateTo?: string };
  emptyText: string;
  /** Показывать пикер «+Добавить». Профиль ученика этот блок больше не использует
   *  (см. StudentLearningBlock — там свой пикер), остался вход byGroup. */
  showAddPicker?: boolean;
}

export function MembershipsBlock({ config, renderCard, emptyText, showAddPicker = true }: Props) {
  const navigate = useNavigate();
  const filter = config.mode === 'byStudent'
    ? { student_id: config.studentId }
    : { group_id: config.groupId };
  const { data: memberships = [], isLoading } = useMemberships(filter);
  const muts = useMembershipMutations();
  const { toast } = useToast();
  const showError = useApiError();
  const [selectedId, setSelectedId] = useState<number | ''>('');
  // Текст блокировки «есть назначенные доп.уроки» — показываем модалкой (не тостом).
  const [blockMsg, setBlockMsg] = useState<string | null>(null);

  const usedIds = useMemo(() => {
    if (config.mode === 'byStudent') return new Set(memberships.map((m) => m.group_id));
    return new Set(memberships.map((m) => m.student_id));
  }, [memberships, config.mode]);

  const availableOptions = useMemo(() =>
    config.pickerOptions.filter((o) => !usedIds.has(o.value) && !o.disabled),
    [config.pickerOptions, usedIds],
  );

  // Лимит вместимости (например, индивидуальная группа = 1 ученик):
  // при достижении прячем и выпадающий список, и кнопку добавления.
  const atCapacity = config.capacity != null && memberships.length >= config.capacity;

  const handleAdd = async () => {
    if (!selectedId) return;
    try {
      if (config.mode === 'byStudent') {
        await muts.create.mutateAsync({ student_id: config.studentId, group_id: Number(selectedId) });
      } else {
        await muts.create.mutateAsync({ student_id: Number(selectedId), group_id: config.groupId });
      }
      setSelectedId('');
      toast('Добавлен', 'ok');
    } catch (err) { showError(err); }
  };

  const handleRemove = async (id: number) => {
    try {
      await muts.remove.mutateAsync(id);
      toast('Убран', 'ok');
    } catch (err) {
      const msg = scheduledMakeupsBlockMessage(err);
      if (msg) setBlockMsg(msg); else showError(err);
    }
  };

  if (isLoading) {
    return <BlockLoading rows={3} />;
  }

  return (
    <div className="memberships">
      {memberships.length === 0 ? (
        <div className="memberships__empty">{emptyText}</div>
      ) : (
        memberships.map((m) => {
          const card = renderCard(m);
          return (
            <div
              key={m.id}
              className="link-card membership-card"
              tabIndex={0}
              role="button"
              onClick={(e) => {
                if ((e.target as HTMLElement).closest('[data-mremove]')) return;
                if (card.navigateTo) navigate(card.navigateTo);
              }}
              onKeyDown={(e) => {
                if ((e.target as HTMLElement).closest('[data-mremove]')) return;
                if ((e.key === 'Enter' || e.key === ' ') && card.navigateTo) {
                  e.preventDefault();
                  navigate(card.navigateTo);
                }
              }}
            >
              <div className="link-card-head">
                <div>
                  <div className="link-card-title">{card.title}</div>
                  <div className="link-card-meta">{card.meta}</div>
                </div>
                <div className="membership-card__actions">
                  <button
                    type="button"
                    className="membership-card__remove"
                    data-mremove
                    aria-label="Убрать"
                    onClick={() => { void handleRemove(m.id); }}
                  >×</button>
                </div>
              </div>
              <div className="membership-card__stats">
                <div className="membership-card__stat">
                  <span className="membership-card__stat-label">Пройдено</span>
                  <span className="membership-card__stat-value">{String(m.lessons_done)}</span>
                </div>
              </div>
              {m.transferred_from_group_name && (
                <div className="membership-card__transferred-note">
                  Переведён из «{m.transferred_from_group_name}» — там отработано {String(m.transferred_from_lessons_done)} ур.
                </div>
              )}
            </div>
          );
        })
      )}

      {showAddPicker && (atCapacity ? (
        config.capacityNote && <div className="memberships__note">{config.capacityNote}</div>
      ) : (
        <div className="memberships__add">
          <SelectInput
            value={selectedId === '' ? '' : String(selectedId)}
            onChange={(e) => setSelectedId(e.target.value === '' ? '' : Number(e.target.value))}
            placeholder={config.pickerLabel}
            options={availableOptions.map((o) => ({ value: o.value, label: o.label }))}
          />
          <button
            type="button"
            className="btn-secondary"
            onClick={() => { void handleAdd(); }}
            disabled={!selectedId || muts.create.isPending}
          >+ Добавить</button>
        </div>
      ))}

      {blockMsg && (
        <ScheduledMakeupsBlockModal message={blockMsg} onClose={() => setBlockMsg(null)} />
      )}
    </div>
  );
}
