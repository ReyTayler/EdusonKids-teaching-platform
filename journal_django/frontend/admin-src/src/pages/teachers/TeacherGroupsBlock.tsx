import { useMemo, useState, type CSSProperties } from 'react';
import { useNavigate } from 'react-router-dom';
import { EmptyState } from '../../components/ui/EmptyState';
import { TextInput } from '../../components/form/TextInput';
import { directionColor } from '../../lib/direction-color';
import { plural } from '../../lib/labels';
import { formatSlot } from '../../lib/slots';
import type { Group } from '../../lib/types';
import type { TeacherGroupProgress } from '../../hooks/useTeacherStats';

interface Props {
  groups: Group[];
  progress: TeacherGroupProgress[];
}

interface Row {
  group: Group;
  done: number;
  total: number | null;
  pct: number | null;
}

function buildRows(groups: Group[], progress: TeacherGroupProgress[]): Row[] {
  const byId = new Map(progress.map((p) => [p.group_id, p]));
  return groups.map((group) => {
    const p = byId.get(group.id);
    const done = Number(p?.lessons_done ?? 0);
    const total = p?.lessons_total ?? null;
    return {
      group,
      done,
      total,
      // `total` бывает null И 0 (CHECK у направления допускает 0) — оба
      // означают «длины курса нет», поэтому проверка на truthy, а не на null:
      // иначе деление на ноль даст Infinity. Потолок 100%: доп.уроки сверх
      // плана давали 37/36 = 102,8%.
      pct: total ? Math.min(100, Math.round((done / total) * 100)) : null,
    };
  });
}

/** 1.5 → '1,5', 18 → '18'. Разделитель как в плитках выше — на одной странице
 *  он обязан быть один. */
function fmtDone(value: number): string {
  return Number.isInteger(value) ? String(value) : String(value).replace('.', ',');
}

/** Одна строка группы: имя, направление, формат, расписание, состав, прогресс. */
function GroupRow({ row }: { row: Row }) {
  const navigate = useNavigate();
  const { group, done, total, pct } = row;
  const members = group.members_count ?? 0;
  const open = () => navigate(`/admin/groups/${group.id}`);
  const slots = (group.slots || []).map(formatSlot).join(' · ');

  return (
    <div
      className={`tgroup${group.active ? '' : ' is-archived'}`}
      role="button"
      tabIndex={0}
      onClick={open}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
      }}
      style={{ '--entity-c': directionColor(group.direction_color || group.direction_name || '') } as CSSProperties}
    >
      <div className="tgroup__head">
        <span className="tgroup__name">{group.name}</span>
        <span className="tgroup__dir">{group.direction_name || '—'}</span>
        <span className="tgroup__id">#{group.id}</span>
      </div>
      <div className="tgroup__meta">
        <span>{group.is_individual ? 'индивидуальная' : 'групповая'}</span>
        <span className="tgroup__mono">{group.lesson_duration_minutes} мин</span>
        {slots && <span className="tgroup__mono">{slots}</span>}
      </div>
      <div className="tgroup__stats">
        <span className="tgroup__students">
          <b>{members}</b> {plural(members, 'ученик', 'ученика', 'учеников')}
        </span>
        {pct == null ? (
          <span className="tgroup__nocourse">длина курса не задана</span>
        ) : (
          <>
            <span className="tgroup__mono">курс {fmtDone(done)} / {total}</span>
            <span className="tgroup__bar">
              <span className="tgroup__fill" style={{ width: `${pct}%` }} />
            </span>
            <span className="tgroup__pct">{pct}%</span>
          </>
        )}
      </div>
    </div>
  );
}

/**
 * Группы преподавателя: активные отдельно, архив отдельно и свёрнут.
 *
 * До этого 18 групп шли одним плоским списком карточек по 90 px, каждая из
 * которых несла два факта. Строка вместо карточки даёт расписание, состав и
 * прогресс курса, не увеличивая высоту.
 */
export default function TeacherGroupsBlock({ groups, progress }: Props) {
  const [query, setQuery] = useState('');
  const [archiveOpen, setArchiveOpen] = useState(false);

  const { active, archived } = useMemo(() => {
    const q = query.trim().toLowerCase();
    const rows = buildRows(groups, progress).filter((r) =>
      !q
      || r.group.name.toLowerCase().includes(q)
      || (r.group.direction_name || '').toLowerCase().includes(q),
    );
    return {
      active: rows.filter((r) => r.group.active),
      archived: rows.filter((r) => !r.group.active),
    };
  }, [groups, progress, query]);

  if (groups.length === 0) {
    return (
      <EmptyState hint="Группа привязывается к преподавателю в её карточке.">
        У преподавателя нет групп
      </EmptyState>
    );
  }

  return (
    <div className="tgroups">
      <div className="tgroups__head">
        <h3 className="sub-header">
          Активные <span className="count-badge">{active.length}</span>
        </h3>
        <div className="tgroups__search">
          {/* className обязателен: TextInput — голый <input>, а токены приходят
              из .field input (только внутри <Field>) либо из .search-input.
              Без класса поле рендерится нативным и в тёмной теме белеет. */}
          <TextInput
            className="search-input"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Группа или направление"
            aria-label="Поиск по группам преподавателя"
          />
        </div>
      </div>

      {active.length === 0 ? (
        <EmptyState hint="Проверьте поиск или разверните архив ниже.">
          Активных групп нет
        </EmptyState>
      ) : (
        <div className="tgroups__list">
          {active.map((row) => <GroupRow key={row.group.id} row={row} />)}
        </div>
      )}

      {archived.length > 0 && (
        <>
          <button
            type="button"
            className="tgroups__toggle"
            onClick={() => setArchiveOpen((v) => !v)}
            aria-expanded={archiveOpen}
          >
            <svg
              width="12" height="12" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"
              strokeLinejoin="round" aria-hidden="true"
              className={archiveOpen ? 'is-open' : ''}
            >
              <polyline points="9 18 15 12 9 6" />
            </svg>
            Архив <span className="count-badge">{archived.length}</span>
          </button>
          {archiveOpen && (
            <div className="tgroups__list">
              {archived.map((row) => <GroupRow key={row.group.id} row={row} />)}
            </div>
          )}
        </>
      )}
    </div>
  );
}
