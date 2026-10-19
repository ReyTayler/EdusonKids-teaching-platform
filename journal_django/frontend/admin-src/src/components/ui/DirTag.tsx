import type { CSSProperties } from 'react';
import { directionColor } from '../../lib/direction-color';
import type { Direction } from '../../lib/types';

interface Props { name?: string | null; direction?: Direction | null; }

export function DirTag({ name, direction }: Props) {
  const label = direction?.name || name || '';
  if (!label) return null;
  const color = directionColor(direction || label);
  // Цвет направления — из данных (его задаёт администратор), поэтому приходит
  // переменной --chip-c; заливку, рамку и «чернильный» текст бейджа строит CSS
  // (.dir-tag) через color-mix. Прежнее `${color}55` дописывало альфу к строке и
  // для hsl()-цвета (направление без своего цвета) давало невалидное значение.
  return (
    <span className="dir-tag" style={{ '--chip-c': color } as CSSProperties}>
      {label}
    </span>
  );
}
