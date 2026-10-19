import type { ReactNode } from 'react';

/**
 * Иконки экрана «Wiki» — папки-разделы, переключатель вида, документ.
 *
 * Отдельно от editorIcons.tsx: те принадлежат панели форматирования и живут в
 * ленивом чанке редактора, эти нужны списку, который грузится сразу. Общий
 * визуальный язык тот же, что у навигации в navConfig.tsx: viewBox 24×24, штрих
 * 1.8, скруглённые концы, ничего не залито.
 */
interface IconProps {
  size?: number;
  className?: string;
}

function Icon({ size = 18, className, children }: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export function FolderIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </Icon>
  );
}

/** Папка «все документы» — со звёздочкой-точкой, чтобы отличалась от обычной. */
export function FolderAllIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
      <line x1="8" y1="13" x2="16" y2="13" />
    </Icon>
  );
}

/** Лист с загнутым углом — документ. */
export function DocIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <polyline points="14 3 14 8 19 8" />
      <line x1="9" y1="13" x2="15" y2="13" />
      <line x1="9" y1="17" x2="13" y2="17" />
    </Icon>
  );
}

export function GridViewIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="4" y="4" width="7" height="7" rx="1.5" />
      <rect x="13" y="4" width="7" height="7" rx="1.5" />
      <rect x="4" y="13" width="7" height="7" rx="1.5" />
      <rect x="13" y="13" width="7" height="7" rx="1.5" />
    </Icon>
  );
}

export function ListViewIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <line x1="9" y1="6" x2="20" y2="6" />
      <line x1="9" y1="12" x2="20" y2="12" />
      <line x1="9" y1="18" x2="20" y2="18" />
      <line x1="4" y1="6" x2="4.01" y2="6" strokeWidth="2.6" />
      <line x1="4" y1="12" x2="4.01" y2="12" strokeWidth="2.6" />
      <line x1="4" y1="18" x2="4.01" y2="18" strokeWidth="2.6" />
    </Icon>
  );
}

export function PlusIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </Icon>
  );
}

/** Пустое состояние — крупная папка с наклонным листом внутри. */
export function EmptyLibraryIcon(props: IconProps) {
  return (
    <Icon size={40} {...props}>
      <path d="M3 8a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
      <line x1="9" y1="13" x2="15" y2="13" />
    </Icon>
  );
}

/** Звезда «в избранном». Заливка появляется, когда закладка стоит. */
export function StarIcon({ filled, ...props }: IconProps & { filled?: boolean }) {
  return (
    <svg
      width={props.size ?? 18}
      height={props.size ?? 18}
      viewBox="0 0 24 24"
      fill={filled ? 'currentColor' : 'none'}
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={props.className}
      aria-hidden="true"
      focusable="false"
    >
      <path d="m12 4 2.4 4.9 5.4.8-3.9 3.8.9 5.4-4.8-2.5-4.8 2.5.9-5.4L4.2 9.7l5.4-.8z" />
    </svg>
  );
}

/** Карандаш — правка документа. */
export function PencilIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z" />
      <line x1="13.5" y1="6.5" x2="17.5" y2="10.5" />
    </Icon>
  );
}

/** Коробка архива. */
export function ArchiveIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3" y="4" width="18" height="4" rx="1.5" />
      <path d="M5 8v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8" />
      <line x1="10" y1="12" x2="14" y2="12" />
    </Icon>
  );
}

/** История версий — часы со стрелкой назад. */
export function HistoryIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1" />
      <polyline points="3 4 3 9 8 9" />
      <polyline points="12 8 12 12 15 14" />
    </Icon>
  );
}
