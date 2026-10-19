import type { RegistrySegment, RegistrySummary } from '../../../lib/types';

type SignalKey = 'ending' | 'closed' | 'idle' | 'no_plan';

const ROWS: { key: SignalKey; label: string }[] = [
  { key: 'ending', label: 'Заканчивается (≤2 урока) — продлевать' },
  { key: 'closed', label: 'Пакет закрыт — апсейл нового' },
  { key: 'idle', label: 'Простой (>14 дней без урока)' },
  { key: 'no_plan', label: 'Нет плана / расписания' },
];

interface Props {
  signals: RegistrySummary['signals'];
  active: RegistrySegment;
  onSelect: (segment: RegistrySegment) => void;
}

// «Сигналы менеджеру» — 4 группы. Клик по строке фильтрует таблицу (toggle).
export function SignalsCard({ signals, active, onSelect }: Props) {
  return (
    <section className="dash-card">
      <div className="dash-card__head">
        <span className="dash-card__title">Сигналы менеджеру</span>
      </div>
      <ul className="reg-signals">
        {ROWS.map(({ key, label }) => {
          const count = signals[key]?.count ?? 0;
          const isActive = active === key;
          return (
            <li key={key}>
              <button
                type="button"
                className={`reg-signal reg-signal--${key}${isActive ? ' reg-signal--active' : ''}`}
                onClick={() => onSelect(isActive ? 'all' : key)}
                aria-pressed={isActive}
              >
                <span className={`reg-signal__count reg-signal__count--${key}`}>{count}</span>
                <span className="reg-signal__label">{label}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
