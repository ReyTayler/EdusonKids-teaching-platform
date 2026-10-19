interface Props { value: string; active?: boolean; }

export function MonoBadge({ value, active = true }: Props) {
  return (
    <span className={`mono-badge ${active ? 'mono-badge--active' : 'mono-badge--inactive'}`}>
      {value}
    </span>
  );
}
