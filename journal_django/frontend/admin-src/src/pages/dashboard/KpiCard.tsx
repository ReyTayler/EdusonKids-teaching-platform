interface Props {
  label: string;
  value: string;
  hint?: string;
  tone?: 'default' | 'info' | 'warning';
  /** Если задан — карточка становится кнопкой-фильтром (реестр). */
  onClick?: () => void;
  active?: boolean;
  className?: string;
}

export function KpiCard({ label, value, hint, tone = 'default', onClick, active, className }: Props) {
  const cls =
    `kpi-card kpi-card--${tone}` +
    (onClick ? ' kpi-card--clickable' : '') +
    (active ? ' kpi-card--active' : '') +
    (className ? ` ${className}` : '');

  const inner = (
    <>
      <div className="kpi-card__label">{label}</div>
      <div className="kpi-card__value">{value}</div>
      {hint && <div className="kpi-card__hint">{hint}</div>}
    </>
  );

  if (onClick) {
    return (
      <button type="button" className={cls} onClick={onClick} aria-pressed={active}>
        {inner}
      </button>
    );
  }
  return <div className={cls}>{inner}</div>;
}
