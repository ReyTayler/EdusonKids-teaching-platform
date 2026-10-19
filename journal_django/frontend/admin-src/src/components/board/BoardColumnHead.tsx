import type { ReactNode } from 'react';
import { stageTone } from '../../lib/stage-tone';

interface Props {
  label: string;
  count: number;
  /** Цвет стадии из справочника. Показывается маленькой точкой слева. */
  color?: string | null;
  /** Служебная пометка рядом с названием (например «авто» в «Продлениях»). */
  badge?: ReactNode;
  /** Кнопки справа от счётчика: поиск, меню. */
  actions?: ReactNode;
}

const HEX_RE = /^#[0-9a-fA-F]{6}$/;

/**
 * Шапка колонки доски — нейтральная плашка в языке Eduson.
 *
 * Общая для «Задач» и «Продлений»: шапка стадии выглядит одинаково во всей
 * системе. Заливка цветом стадии убрана (цвет = смысл, а цвет стадии выбирает
 * человек): теперь он живёт только в маленькой точке. Белый «цвет» палитры и
 * стадия без цвета точки не получают — иначе пустой кружок читался бы как
 * отдельный статус. Специфика разделов приходит слотами `badge` и `actions`.
 */
export function BoardColumnHead({ label, count, color, badge, actions }: Props) {
  const hasDot = !!color && HEX_RE.test(color) && color.toLowerCase() !== '#ffffff';
  return (
    <div className="board-col-head">
      {hasDot && (
        <span
          className="board-col-head__dot"
          style={{ background: stageTone(color, label).bg }}
          aria-hidden="true"
        />
      )}
      <span className="board-col-head__label" title={label}>{label}</span>
      {badge}
      <span className="board-col-head__count">{count}</span>
      {actions && <span className="board-col-head__actions">{actions}</span>}
    </div>
  );
}
