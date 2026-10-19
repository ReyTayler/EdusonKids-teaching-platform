/**
 * Фирменный знак Eduson Kids: чёрный квадрат с моно-«E» и название рядом
 * (по образцу .brand/.logo старого Eduson). Стили — styles/brand.css.
 *
 * Вынесен отдельным компонентом, потому что мест, где он нужен, несколько:
 * боковая/верхняя панель admin, верхняя панель teacher, узкий режим.
 * Квадрат красится токенами --accent/--on-accent, поэтому в тёмной теме
 * сам становится белым с чёрной буквой.
 *
 * `compact` — только квадрат (рельса, узкая верхняя панель).
 * `subtitle` — подпись под названием («Администрирование», «Кабинет преподавателя»).
 */

interface BrandMarkProps {
  compact?: boolean;
  subtitle?: string;
  className?: string;
}

export function BrandMark({ compact, subtitle, className }: BrandMarkProps) {
  const cls = ['brand', compact ? 'brand--compact' : '', className ?? ''].filter(Boolean).join(' ');
  return (
    <span className={cls} role="img" aria-label={subtitle ? `Eduson Kids — ${subtitle}` : 'Eduson Kids'}>
      <span className="brand__logo" aria-hidden="true">E</span>
      <span className="brand__text" aria-hidden="true">
        <span className="brand__name">Eduson Kids</span>
        {subtitle && <span className="brand__sub">{subtitle}</span>}
      </span>
    </span>
  );
}
