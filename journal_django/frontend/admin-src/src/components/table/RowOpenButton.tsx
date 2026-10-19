import { Link } from 'react-router-dom';

/**
 * Переход в карточку сущности из строки списка.
 *
 * Ссылка, а не кнопка с navigate(): так работают средний клик и Ctrl+клик
 * («открыть в новой вкладке»), а сам адрес виден в статусной строке браузера.
 * Для навигации это правильная семантика — кнопкой её подменять незачем.
 *
 * stopPropagation оставлен намеренно, хотя в списках сущностей клик по строке
 * больше ничего не делает: компонент общий, и в таблице с onRowClick нажатие
 * иначе отработало бы дважды.
 */
export function RowOpenButton({ to, label = 'Открыть', title }: {
  to: string;
  label?: string;
  title?: string;
}) {
  return (
    <Link
      className="btn-open"
      to={to}
      /* На узком экране подпись прячется и остаётся один шеврон — имя ссылки
         обязано пережить это, иначе кнопка станет безымянной для скринридера. */
      aria-label={label}
      title={title || label}
      onClick={(e) => e.stopPropagation()}
    >
      <span className="btn-open__label">{label}</span>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <polyline points="9 18 15 12 9 6" />
      </svg>
    </Link>
  );
}
