import type { CSSProperties } from 'react';
import { Avatar } from '../Avatar';

interface Props {
  /** Имена людей по порядку. Пустые (нет full_name) подставляет вызывающий. */
  names: string[];
  /** Сколько аватаров показать до сворачивания остатка в «+N». */
  max?: number;
  /** Диаметр аватара — тот же проп, что у Avatar. */
  size?: number;
  className?: string;
}

/**
 * Несколько людей одной стопкой аватаров: круги стоят в ряд с перекрытием,
 * а всё, что не поместилось, сворачивается в кружок «+N».
 *
 * Имена рядом не пишем — это решает вызывающий: на карточке задачи места нет
 * (заказчик просил только аватары), а в панели подписи идут отдельным рядом.
 *
 * Перекрытие и обводка неразделимы: без кольца цветом подложки соседние круги
 * сливаются в сплошное пятно, и сосчитать людей нельзя. Цвет кольца берётся из
 * --avatar-stack-ring — по умолчанию поверхность карточки, но фон под стопкой
 * бывает другим (строка свойства панели), и его переопределяют на месте.
 */
export function AvatarStack({ names, max = 3, size = 18, className }: Props) {
  if (names.length === 0) return null;

  const shown = names.slice(0, max);
  const rest = names.slice(max);
  // Наезд пропорционален размеру: фиксированные пиксели на крупных аватарах
  // читаются как «просто рядом», а на мелких съедают инициалы целиком.
  const style = { '--avatar-stack-overlap': `${Math.round(size * 0.3)}px` } as CSSProperties;

  return (
    <span className={`avatar-stack${className ? ` ${className}` : ''}`} style={style}>
      {shown.map((name, i) => (
        // Ключ с индексом: тёзки в списке возможны, и одного имени не хватает.
        <span className="avatar-stack__item" key={`${name}-${i}`} title={name}>
          <Avatar name={name} size={size} />
        </span>
      ))}
      {rest.length > 0 && (
        <span
          className="avatar-stack__item avatar-stack__more"
          // Кто именно спрятан — иначе «+2» ничего не сообщает, а раскрыть
          // стопку негде: карточка на доске и так тесная.
          title={rest.join(', ')}
          style={{ width: size, height: size, fontSize: Math.round(size * 0.34) }}
        >
          +{rest.length}
        </span>
      )}
    </span>
  );
}
