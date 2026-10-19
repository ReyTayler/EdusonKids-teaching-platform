import { useCallback, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { useDirections } from '../../hooks/useDirections';
import { useRenewalAssignees, useRenewalUnassignedCount } from '../../hooks/useRenewals';
import { useRenewalStages } from '../../hooks/useRenewalStages';
import { RenewalUnassignedDialog } from './RenewalUnassignedDialog';
import { SelectInput } from '../../components/form/SelectInput';
import { TextInput } from '../../components/form/TextInput';
import { Checkbox } from '../../components/form/Checkbox';
import { SearchInput } from '../../components/ui/SearchInput';
import { canWriteRenewalStages, type Role } from '../../lib/permissions';
import { RenewalBoard } from './RenewalBoard';
import { RenewalList } from './RenewalList';
import { RenewalDrawer } from './RenewalDrawer';
import type { RenewalFilters } from '../../lib/renewals';
import { PageHeader } from '../../components/shell/PageHeader';

type ViewMode = 'board' | 'list';

/**
 * Ключи фильтров, живущие в URL — состояние раздела шарится ссылкой.
 * `student` теперь общий для обоих видов: в канбане уходит в filter[student]
 * доски (бэк применяет его в _board_where ко ВСЕМ колонкам), в списке — в
 * тот же фильтр списка. Отдельного поля «Ученик» в тулбаре больше нет.
 */
const FILTER_KEYS = ['student', 'assignee_id', 'direction_id', 'cycle_no', 'stage_id', 'include_closed'];

export default function RenewalsPage() {
  const { me } = useAuth();
  const [sp, setSp] = useSearchParams();
  const view: ViewMode = sp.get('view') === 'list' ? 'list' : 'board';
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const { data: assignees } = useRenewalAssignees();
  const { data: directions } = useDirections();
  const { data: stages } = useRenewalStages();
  // Только число: сам список грузит диалог, и только когда его открыли.
  const { data: unassigned } = useRenewalUnassignedCount();
  const [showUnassigned, setShowUnassigned] = useState(false);
  const unassignedCount = unassigned?.count ?? 0;

  // Цикл/стадия/закрытые применяются только в списочном виде — канбан их
  // игнорирует (доска показывает открытые сделки, разложенные по стадиям).
  const filters: RenewalFilters = {
    student: sp.get('student') ?? undefined,
    assignee_id: sp.get('assignee_id') ?? undefined,
    direction_id: sp.get('direction_id') ?? undefined,
    ...(view === 'list' ? {
      cycle_no: sp.get('cycle_no') ?? undefined,
      stage_id: sp.get('stage_id') ?? undefined,
      include_closed: sp.get('include_closed') ?? undefined,
    } : {}),
  };

  const setView = (v: ViewMode) => {
    const next = new URLSearchParams(sp);
    next.set('view', v);
    setSp(next, { replace: true });
  };

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(sp);
    if (value) next.set(key, value); else next.delete(key);
    setSp(next, { replace: true });
  };

  const resetFilters = () => {
    const next = new URLSearchParams(sp);
    FILTER_KEYS.forEach((k) => next.delete(k));
    setSp(next, { replace: true });
  };

  const closeDrawer = useCallback(() => setSelectedId(null), []);

  // Чипы активных фильтров: в подписи стоит ИМЯ из справочника, а не id —
  // «Ответственный: 17» не сообщает ничего. Справочник мог ещё не догрузиться,
  // тогда показываем сам ключ значением, а не пустоту.
  const assigneeName = (assignees || []).find((a) => String(a.id) === sp.get('assignee_id'))?.full_name;
  const directionName = (directions || []).find((d) => String(d.id) === sp.get('direction_id'))?.name;
  const stageName = (stages || []).find((s) => String(s.id) === sp.get('stage_id'))?.label;

  const chips: { key: string; label: string }[] = [];
  if (sp.get('student')) chips.push({ key: 'student', label: `Поиск: ${sp.get('student')}` });
  if (sp.get('assignee_id')) chips.push({ key: 'assignee_id', label: `Ответственный: ${assigneeName ?? sp.get('assignee_id')}` });
  if (sp.get('direction_id')) chips.push({ key: 'direction_id', label: `Направление: ${directionName ?? sp.get('direction_id')}` });
  if (view === 'list') {
    if (sp.get('cycle_no')) chips.push({ key: 'cycle_no', label: `Цикл ${sp.get('cycle_no')}` });
    if (sp.get('stage_id')) chips.push({ key: 'stage_id', label: `Стадия: ${stageName ?? sp.get('stage_id')}` });
    if (sp.get('include_closed') === 'true') chips.push({ key: 'include_closed', label: 'С закрытыми' });
  }

  return (
    /* Модификатор --board несёт плотность и полную ширину: базовый класс
       общий с аналитикой и настройкой стадий, которым это не нужно. */
    <div className="renewals-page renewals-page--board">
      <PageHeader
        dense
        title="Продления"
        actions={
          <>
            <div className="segmented" role="group" aria-label="Вид раздела">
              <button
                type="button"
                className={`segmented__btn${view === 'board' ? ' is-active' : ''}`}
                aria-pressed={view === 'board'}
                onClick={() => setView('board')}
              >Канбан</button>
              <button
                type="button"
                className={`segmented__btn${view === 'list' ? ' is-active' : ''}`}
                aria-pressed={view === 'list'}
                onClick={() => setView('list')}
              >Список</button>
            </div>
            <button
              type="button"
              className={`btn-secondary${unassignedCount > 0 ? ' renewals-page__unassigned-btn--attention' : ''}`}
              onClick={() => setShowUnassigned(true)}
            >
              Без сделок{unassignedCount > 0 ? ` (${unassignedCount})` : ''}
            </button>
            <Link to="/admin/renewals/analytics" className="btn-secondary">Аналитика</Link>
            {canWriteRenewalStages(me?.role as Role) && (
              /* Иконкой, а не подписью: действие открывают раз в квартал, а
                 место в шапке оно занимало постоянно. */
              <Link
                to="/admin/renewals/stages"
                className="ui-iconbtn ui-iconbtn--md"
                aria-label="Настройка стадий"
                title="Настройка стадий"
              >
                <GearGlyph />
              </Link>
            )}
          </>
        }
      />

      <div className="rnl-toolbar">
        <div className="rnl-toolbar__row">
          {/* Поиск — часть панели фильтрации, а не отдельный контрол внутри
              каждой колонки: искать ученика приходится, НЕ зная его стадии. */}
          <SearchInput
            value={sp.get('student') ?? ''}
            onChange={(v) => setFilter('student', v)}
            placeholder="Поиск по имени ученика…"
            width={240}
          />

          {/* Пустое значение названо самоописательно — так триггер объясняет
              себя без uppercase-подписи сверху, которая съедала 20px высоты. */}
          <SelectInput
            className="rnl-toolbar__select"
            value={sp.get('assignee_id') ?? ''}
            onChange={(e) => setFilter('assignee_id', e.target.value)}
            options={[
              { value: '', label: 'Все ответственные' },
              ...(assignees || []).map((a) => ({ value: String(a.id), label: a.full_name })),
            ]}
          />
          <SelectInput
            className="rnl-toolbar__select"
            value={sp.get('direction_id') ?? ''}
            onChange={(e) => setFilter('direction_id', e.target.value)}
            options={[
              { value: '', label: 'Все направления' },
              ...(directions || []).map((d) => ({ value: String(d.id), label: d.name })),
            ]}
          />

          {view === 'list' && (
            <>
              <SelectInput
                className="rnl-toolbar__select"
                value={sp.get('stage_id') ?? ''}
                onChange={(e) => setFilter('stage_id', e.target.value)}
                options={[
                  { value: '', label: 'Все стадии' },
                  ...(stages || []).map((s) => ({ value: String(s.id), label: s.label })),
                ]}
              />
              <TextInput
                className="rnl-toolbar__cycle"
                inputMode="numeric"
                placeholder="Цикл"
                aria-label="Номер цикла"
                value={sp.get('cycle_no') ?? ''}
                onChange={(e) => setFilter('cycle_no', e.target.value.replace(/\D/g, ''))}
              />
              <Checkbox
                label="Закрытые"
                checked={sp.get('include_closed') === 'true'}
                onChange={(e) => setFilter('include_closed', e.target.checked ? 'true' : '')}
              />
            </>
          )}
        </div>

        {chips.length > 0 && (
          <div className="rnl-chips">
            {chips.map((c) => (
              <button
                key={c.key}
                type="button"
                className="rnl-chip"
                onClick={() => setFilter(c.key, '')}
                title="Снять фильтр"
              >
                <span className="rnl-chip__text">{c.label}</span>
                <span className="rnl-chip__x" aria-hidden="true">×</span>
                <span className="sr-only">— снять фильтр</span>
              </button>
            ))}
            <button type="button" className="btn-reset-filters" onClick={resetFilters}>
              Сбросить
            </button>
          </div>
        )}
      </div>

      {view === 'board'
        ? <RenewalBoard filters={filters} onOpen={setSelectedId} />
        : <RenewalList filters={filters} onOpen={setSelectedId} />}

      {selectedId != null && (
        <RenewalDrawer id={selectedId} onClose={closeDrawer} />
      )}

      {showUnassigned && (
        <RenewalUnassignedDialog onClose={() => setShowUnassigned(false)} />
      )}
    </div>
  );
}

function GearGlyph() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c.2.6.77 1.02 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}
