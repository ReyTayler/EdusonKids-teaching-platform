import type { CSSProperties, ReactNode } from 'react';

export interface HeroFact {
  label: string;
  value: ReactNode;
}

interface Props {
  /** 1–2 буквы монограммы. Цвет берётся из `color` (направление / хеш имени). */
  monogram: string;
  color: string;
  title: string;
  /** Бейдж статуса справа от имени (StageBadge и т.п.). */
  badge?: ReactNode;
  /** Ряд чипов под именем: id, тип, расписание, длительность. */
  meta?: ReactNode;
  /** Кнопки действий. Первая — основная, остальные вторичные/в меню «…». */
  actions?: ReactNode;
  /** Правая колонка: короткие факты (2–4 строки), без декоративного цвета. */
  facts?: HeroFact[];
  /** Произвольный блок под фактами (например, последний комментарий). */
  aside?: ReactNode;
}

/**
 * Единая шапка карточки сущности (ученик, группа). Один макет на обе страницы —
 * до этого у ученика была своя вёрстка, а у группы — дефолтный заголовок
 * DetailShell, из-за чего разделы выглядели как из разных продуктов.
 *
 * Цвет сущности прокидывается CSS-переменной `--entity-c`: сама подложка/рамка
 * монограммы считаются от неё через color-mix, поэтому в JS остаётся один цвет,
 * а не тройка hsl-строк.
 */
export function EntityHero({ monogram, color, title, badge, meta, actions, facts, aside }: Props) {
  const hasSide = Boolean(facts?.length) || Boolean(aside);

  return (
    <header className="ehero" style={{ '--entity-c': color } as CSSProperties}>
      <div className="ehero__main">
        <div className="ehero__mono" aria-hidden="true">{monogram}</div>
        <div className="ehero__body">
          <div className="ehero__name-row">
            <h2 className="ehero__name">{title}</h2>
            {badge}
          </div>
          {meta && <div className="ehero__meta">{meta}</div>}
          {actions && <div className="ehero__actions">{actions}</div>}
        </div>
      </div>

      {hasSide && (
        <div className="ehero__side">
          {!!facts?.length && (
            <dl className="ehero__facts">
              {facts.map((f) => (
                <div key={f.label} className="ehero__fact">
                  <dt className="ehero__fact-label">{f.label}</dt>
                  <dd className="ehero__fact-value">{f.value}</dd>
                </div>
              ))}
            </dl>
          )}
          {aside}
        </div>
      )}
    </header>
  );
}

/** Нейтральный чип метаданных в шапке. `mono` — для чисел (id, длительность). */
export function HeroChip({ children, mono }: { children: ReactNode; mono?: boolean }) {
  return <span className={`ehero-chip${mono ? ' ehero-chip--mono' : ''}`}>{children}</span>;
}

/** Монограмма из 1–2 букв: первые буквы слов, иначе первые символы строки. */
export function monogramOf(name: string): string {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return String(name || '??').slice(0, 2).toUpperCase();
}
