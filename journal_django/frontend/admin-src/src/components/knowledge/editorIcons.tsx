import type { ReactNode } from 'react';

/**
 * Иконки панели форматирования редактора базы знаний.
 *
 * Тот же визуальный язык, что и навигационные иконки в navConfig.tsx: inline-SVG,
 * viewBox 24×24, stroke=currentColor, единая толщина штриха 1.8, скруглённые
 * концы и стыки. Ничего не залито.
 *
 * Геометрия повторяет формы Lucide (лицензия MIT) — они выверены под мелкий
 * кегль и остаются читаемыми на 18px. Рисуем разметкой, а не подключаем
 * библиотеку: одна иконка из пакета тянет за собой зависимость там, где хватает
 * десятка path'ей, а CSP script-src 'self' не допускает внешние спрайты.
 *
 * Правило набора: буквенные и цифровые глифы (B, I, H2, H3, 1-2-3) рисуются
 * штрихом, а не текстом — иначе они поедут вместе с системным шрифтом
 * пользователя и разойдутся с остальными иконками.
 */
export interface IconProps {
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

/** «H» с цифрой 1: та же буква, цифра — стойка с засечкой. */
export function Heading1Icon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 12h8" />
      <path d="M4 18V6" />
      <path d="M12 18V6" />
      <path d="M17 11l3-2v9" />
    </Icon>
  );
}

/** «H» с цифрой 2: буква — две стойки с перекладиной, цифра — дуга и диагональ. */
export function Heading2Icon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 12h8" />
      <path d="M4 18V6" />
      <path d="M12 18V6" />
      <path d="M21 18h-4c0-4 4-3 4-6 0-1.5-2-2.5-4-1" />
    </Icon>
  );
}

/** «H» с цифрой 3: цифра собрана из двух дуг, как в наборе Lucide. */
export function Heading3Icon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 12h8" />
      <path d="M4 18V6" />
      <path d="M12 18V6" />
      <path d="M17.5 10.5c1.7-1 3.5 0 3.5 1.5a2 2 0 0 1-2 2" />
      <path d="M17 17.5c2 1.5 4 .3 4-1.5a2 2 0 0 0-2-2" />
    </Icon>
  );
}

/** Глиф «B»: стойка и две чаши. */
export function BoldIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 12h9a4 4 0 0 1 0 8H7a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h7a4 4 0 0 1 0 8" />
    </Icon>
  );
}

/** Глиф «I» с верхней и нижней засечкой. */
export function ItalicIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <line x1="19" y1="4" x2="10" y2="4" />
      <line x1="14" y1="20" x2="5" y2="20" />
      <line x1="15" y1="4" x2="9" y2="20" />
    </Icon>
  );
}

/** Глиф «U» — буква с подчёркивающей линией. */
export function UnderlineIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 4v6a6 6 0 0 0 12 0V4" />
      <line x1="4" y1="20" x2="20" y2="20" />
    </Icon>
  );
}

export function AlignLeftIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <line x1="4" y1="6" x2="20" y2="6" />
      <line x1="4" y1="12" x2="14" y2="12" />
      <line x1="4" y1="18" x2="18" y2="18" />
    </Icon>
  );
}

export function AlignCenterIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <line x1="4" y1="6" x2="20" y2="6" />
      <line x1="7" y1="12" x2="17" y2="12" />
      <line x1="5" y1="18" x2="19" y2="18" />
    </Icon>
  );
}

export function AlignRightIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <line x1="4" y1="6" x2="20" y2="6" />
      <line x1="10" y1="12" x2="20" y2="12" />
      <line x1="6" y1="18" x2="20" y2="18" />
    </Icon>
  );
}

export function AlignJustifyIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <line x1="4" y1="6" x2="20" y2="6" />
      <line x1="4" y1="12" x2="20" y2="12" />
      <line x1="4" y1="18" x2="20" y2="18" />
    </Icon>
  );
}

export function StrikeIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M16 4H9a3 3 0 0 0-2.83 4" />
      <path d="M14 12a4 4 0 0 1 0 8H6" />
      <line x1="4" y1="12" x2="20" y2="12" />
    </Icon>
  );
}

/** Инлайн-код: угловые скобки. Отличается от CodeBlockIcon отсутствием рамки. */
export function CodeIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <polyline points="16 18 22 12 16 6" />
      <polyline points="8 6 2 12 8 18" />
    </Icon>
  );
}

/** Маркеры — короткие линии с круглым концом, а не заливка: один язык с набором. */
export function BulletListIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <line x1="8" y1="6" x2="21" y2="6" />
      <line x1="8" y1="12" x2="21" y2="12" />
      <line x1="8" y1="18" x2="21" y2="18" />
      <line x1="3" y1="6" x2="3.01" y2="6" strokeWidth="2.6" />
      <line x1="3" y1="12" x2="3.01" y2="12" strokeWidth="2.6" />
      <line x1="3" y1="18" x2="3.01" y2="18" strokeWidth="2.6" />
    </Icon>
  );
}

/** Цифры 1 и 2 слева от строк — минимальные формы, читаемые на 18px. */
export function OrderedListIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <line x1="10" y1="6" x2="21" y2="6" />
      <line x1="10" y1="12" x2="21" y2="12" />
      <line x1="10" y1="18" x2="21" y2="18" />
      <path d="M4 6h1v4" />
      <path d="M4 10h2" />
      <path d="M6 18H4c0-1 2-2 2-3s-1-1.5-2-1" />
    </Icon>
  );
}

/** Цитата: строки с отступом и вертикальной чертой слева — как выглядит blockquote. */
export function QuoteIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 6v12" />
      <line x1="10" y1="7" x2="20" y2="7" />
      <line x1="10" y1="12" x2="20" y2="12" />
      <line x1="10" y1="17" x2="16" y2="17" />
    </Icon>
  );
}

/** Блок кода: те же скобки, но в рамке — различие блочного и инлайнового. */
export function CodeBlockIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <polyline points="14.5 9 17 12 14.5 15" />
      <polyline points="9.5 9 7 12 9.5 15" />
    </Icon>
  );
}

/** Разделитель: линия между двумя фрагментами текста. */
export function HorizontalRuleIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <line x1="7" y1="6" x2="17" y2="6" />
      <line x1="3" y1="12" x2="21" y2="12" />
      <line x1="7" y1="18" x2="17" y2="18" />
    </Icon>
  );
}

export function TableIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <line x1="3" y1="10" x2="21" y2="10" />
      <line x1="9" y1="4" x2="9" y2="20" />
    </Icon>
  );
}

/** Скрепка — общепринятый знак вложения; ни с чем в наборе не путается. */
export function AttachIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M21 11.5 12.5 20a5 5 0 0 1-7-7l8-8a3.5 3.5 0 0 1 5 5l-8 8a2 2 0 0 1-3-3l7.5-7.5" />
    </Icon>
  );
}

export function ImageIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <circle cx="8.5" cy="9.5" r="1.5" />
      <polyline points="21 15 15 9 5 19" />
    </Icon>
  );
}

/**
 * Спиннер загрузки картинки — вращение задаёт CSS (.kb-spin в knowledge.css).
 *
 * Под prefers-reduced-motion вращение не выключается, а замедляется: оно
 * единственный носитель состояния «идёт загрузка», и остановить его значило бы
 * скрыть от пользователя реальный статус.
 */
export function SpinnerIcon({ size = 18, className }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      className={['kb-spin', className].filter(Boolean).join(' ')}
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="12" cy="12" r="8" opacity="0.25" />
      <path d="M20 12a8 8 0 0 0-8-8" />
    </svg>
  );
}

/** Ручка блока — шесть точек, как в Notion. */
export function BlockHandleIcon({ size = 14 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" focusable="false">
      <circle cx="5.5" cy="3.5" r="1.4" />
      <circle cx="10.5" cy="3.5" r="1.4" />
      <circle cx="5.5" cy="8" r="1.4" />
      <circle cx="10.5" cy="8" r="1.4" />
      <circle cx="5.5" cy="12.5" r="1.4" />
      <circle cx="10.5" cy="12.5" r="1.4" />
    </svg>
  );
}

/** Плюс — добавить блок. */
export function PlusIcon({ size = 14 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" aria-hidden="true" focusable="false">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

/** Ссылка — две скобы цепи. */
export function LinkIcon({ size = 18 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.5 1.5" />
      <path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7L12 19" />
    </svg>
  );
}

/** Чеклист. */
export function TaskListIcon({ size = 18 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <polyline points="3 7 5 9 8.5 5" />
      <polyline points="3 16 5 18 8.5 14" />
      <line x1="12" y1="7" x2="21" y2="7" />
      <line x1="12" y1="16" x2="21" y2="16" />
    </svg>
  );
}

/** Выноска — облачко с восклицанием. */
export function CalloutIcon({ size = 18 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <rect x="3" y="4" width="18" height="14" rx="2.5" />
      <line x1="12" y1="8" x2="12" y2="12" />
      <line x1="12" y1="14.5" x2="12" y2="14.6" strokeWidth="2.4" />
    </svg>
  );
}

/** Отменить. */
export function UndoIcon({ size = 18 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <polyline points="4 8 4 13 9 13" />
      <path d="M4.5 12.5a7.5 7.5 0 1 1 2 6" />
    </svg>
  );
}

/** Вернуть. */
export function RedoIcon({ size = 18 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <polyline points="20 8 20 13 15 13" />
      <path d="M19.5 12.5a7.5 7.5 0 1 0-2 6" />
    </svg>
  );
}
