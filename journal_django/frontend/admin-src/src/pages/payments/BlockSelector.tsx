import { useState } from 'react';

interface Props {
  totalSubscriptions: number;     // N коробок
  alreadyPurchased: number;       // сколько закрашено (locked)
  selected: number;               // сколько выбрано прямо сейчас
  color: string | null | undefined;
  onChange: (next: number) => void;
}

export function BlockSelector({ totalSubscriptions, alreadyPurchased, selected, color, onChange }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const accent = color || 'var(--accent)';

  // Коробки 0..N-1.
  // [0 .. alreadyPurchased-1] — locked (уже купленные).
  // [alreadyPurchased .. alreadyPurchased+selected-1] — выбранные.
  // [alreadyPurchased+selected .. N-1] — свободные.

  const handleClick = (idx: number) => {
    if (idx < alreadyPurchased) return; // locked
    // Клик по уже выбранному → откатить до idx-1 включительно (т.е. снять текущий и далее)
    if (idx < alreadyPurchased + selected) {
      onChange(idx - alreadyPurchased);
      return;
    }
    // Иначе выбрать диапазон до idx (включительно)
    onChange(idx - alreadyPurchased + 1);
  };

  const cells = [];
  for (let i = 0; i < totalSubscriptions; i++) {
    const isLocked = i < alreadyPurchased;
    const isSelected = i >= alreadyPurchased && i < alreadyPurchased + selected;
    const isHoverPreview = hover !== null && !isLocked && !isSelected
      && i >= alreadyPurchased && i <= alreadyPurchased + hover - 1;
    cells.push(
      <button
        key={i}
        type="button"
        className={`block-cell${isLocked ? ' block-cell--locked' : ''}${isSelected ? ' block-cell--selected' : ''}${isHoverPreview ? ' block-cell--hover' : ''}`}
        style={{ ['--dir-color' as string]: accent }}
        onClick={() => handleClick(i)}
        onMouseEnter={() => !isLocked && setHover(i - alreadyPurchased + 1)}
        onMouseLeave={() => setHover(null)}
        disabled={isLocked}
        aria-label={isLocked ? `Уже куплен абонемент №${i + 1}` : `Абонемент №${i + 1}`}
      >
      </button>
    );
  }

  const free = totalSubscriptions - alreadyPurchased - selected;
  return (
    <div className="block-selector">
      <div className="block-selector__row">{cells}</div>
      <div className="block-selector__legend">
        {alreadyPurchased > 0 && <span>{alreadyPurchased} ранее</span>}
        {alreadyPurchased > 0 && <span> · </span>}
        <span>{selected} выбрано</span>
        <span> · </span>
        <span>{free} свободно</span>
      </div>
    </div>
  );
}
