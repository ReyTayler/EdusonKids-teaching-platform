import type { ReactNode } from 'react';

interface Props {
  /** Глиф свойства — из FieldIcons.tsx. */
  icon: ReactNode;
  /** Подпись поля: «Срок», «Приоритет», «Стадия». */
  label: string;
  /** Контрол правки: Combobox / SelectInput / DateInput — или готовое значение. */
  children: ReactNode;
  /** Довесок под контролом — например ссылка на карточку ученика. */
  below?: ReactNode;
}

/**
 * Строка свойства в панели: слева иконка с подписью, справа — сам контрол.
 *
 * Контрол стоит здесь ВСЕГДА и притворяется обычной строкой: рамку и подложку
 * ему гасит CSS (.inline-field__control в forms.css), а возвращает их только
 * наведение и фокус. Раньше строка показывала значение текстом и подменялась
 * контролом по клику — облик поля скакал от того, читают его или правят, и
 * каждая правка стоила лишнего клика «войти в режим».
 *
 * Пустое значение контрол показывает не прочерком, а приглушённой подсказкой
 * «Назначить…»: прочерк сообщает, что данных нет, но молчит о том, что поле
 * вообще заполняемое. За это отвечает проп `placeholderValue` у Combobox и
 * SelectInput — см. их код.
 */
export function InlineField({ icon, label, children, below }: Props) {
  return (
    <div className="inline-field">
      <div className="inline-field__label">
        <span className="inline-field__icon">{icon}</span>
        <span className="inline-field__label-text">{label}</span>
      </div>

      <div className="inline-field__control">{children}</div>

      {below && <div className="inline-field__below">{below}</div>}
    </div>
  );
}
