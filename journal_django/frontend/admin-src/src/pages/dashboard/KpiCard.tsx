import { HelpPopover } from '../../components/ui/HelpPopover';

interface Props {
  label: string;
  value: string;
  hint?: string;
  tone?: 'default' | 'info' | 'warning';
  onClick?: () => void;
  active?: boolean;
  className?: string;
}

export function KpiCard({ label, value, hint, tone = 'default', onClick, active, className }: Props) {
  const cls = `kpi-card kpi-card--${tone}` +
    (onClick ? ' kpi-card--clickable' : '') +
    (active ? ' kpi-card--active' : '') + (className ? ` ${className}` : '');
  return (
    <div className={cls}>
      {onClick && <button type="button" className="kpi-card__action" aria-label={`${label}: ${value}`}
        onClick={onClick} aria-pressed={active} />}
      <div className="kpi-card__label">
        {label}
        {hint && <HelpPopover label={`Подсказка: ${label}`}>{hint}</HelpPopover>}
      </div>
      <div className="kpi-card__value">{value}</div>
    </div>
  );
}
