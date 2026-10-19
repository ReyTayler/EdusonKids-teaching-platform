import type { ReactElement } from 'react';
import {
  canSeePayroll, canSeeAccounts, canSeeAudit, canSeeChangelog,
  canSeeSync, canSeeArchive, type Role,
} from '../../lib/permissions';

/**
 * Значки боковой колонки. Язык Eduson: монохром (currentColor), линия 1.5,
 * без заливок, сетка 24 → рисуются 18px. Значок есть у «Дашборда» и у КАЖДОЙ
 * группы; у вложенных пунктов значков нет намеренно — вложенность показывает
 * отступ, а одинаковые пиктограммы на каждой строке превращали список в шум.
 */
const svgProps = {
  width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
  strokeWidth: 1.5, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true,
} as const;

export const NAV_ICONS: Record<string, ReactElement> = {
  dashboard: (
    <svg {...svgProps}>
      <rect x="3" y="3" width="7" height="9" rx="1.5"/>
      <rect x="14" y="3" width="7" height="5" rx="1.5"/>
      <rect x="14" y="12" width="7" height="9" rx="1.5"/>
      <rect x="3" y="16" width="7" height="5" rx="1.5"/>
    </svg>
  ),
  'group-study': (
    <svg {...svgProps}>
      <path d="M22 10 12 5 2 10l10 5 10-5Z"/>
      <path d="M6 12v5c3 2.5 9 2.5 12 0v-5"/>
      <path d="M22 10v5"/>
    </svg>
  ),
  'group-lessons': (
    <svg {...svgProps}>
      <path d="M2 4h5.5A2.5 2.5 0 0 1 10 6.5V20a2 2 0 0 0-2-2H2z"/>
      <path d="M22 4h-5.5A2.5 2.5 0 0 0 14 6.5V20a2 2 0 0 1 2-2h6z"/>
    </svg>
  ),
  'group-finance': (
    <svg {...svgProps}>
      <path d="M3 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2"/>
      <path d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-3"/>
      <path d="M21 10h-4a2 2 0 0 0 0 4h4z"/>
    </svg>
  ),
  'group-system': (
    <svg {...svgProps}>
      <path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3"/>
      <path d="M1 14h6M9 8h6M17 16h6"/>
    </svg>
  ),
  // Один шеврон вправо; в развёрнутой группе поворачивается средствами CSS.
  chevron: (
    <svg {...svgProps} width={14} height={14} strokeWidth={1.75}>
      <polyline points="9 18 15 12 9 6"/>
    </svg>
  ),
};

export interface NavItem {
  key: string;
  label: string;
  path: string;
  /** Ролевой гейт: пункт виден, только если функция вернёт true. Без неё — всем staff. */
  can?: (role: Role | undefined) => boolean;
}

export interface NavGroup {
  /** Устойчивый ключ группы (по нему находится активная группа и ключ значка). */
  key: string;
  title: string;
  /** Ключ в NAV_ICONS для значка группы. */
  icon: string;
  items: NavItem[];
}

/**
 * Закреплено сверху, вне групп. «Дашборд» — точка входа, пункт верхнего уровня колонки: прятать его
 * внутрь группы незачем.
 */
export const NAV_PINNED: NavItem[] = [
  { key: 'dashboard', label: 'Дашборд', path: '/admin/dashboard' },
];

/**
 * Единый источник навигации admin SPA. Разделы спрятаны в смысловые группы:
 * группа — раскрывающийся блок колонки (открыт один). Ролевые пункты несут `can`;
 * группа без единого видимого пункта не рисуется вовсе.
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    key: 'study',
    title: 'Учебная часть',
    icon: 'group-study',
    items: [
      { key: 'students', label: 'Ученики', path: '/admin/students' },
      { key: 'groups', label: 'Группы', path: '/admin/groups' },
      { key: 'teachers', label: 'Преподаватели', path: '/admin/teachers' },
      { key: 'directions', label: 'Направления', path: '/admin/directions' },
      { key: 'renewals', label: 'Продления', path: '/admin/renewals' },
      { key: 'tasks', label: 'Задачи', path: '/admin/tasks' },
    ],
  },
  {
    key: 'lessons',
    title: 'Занятия',
    icon: 'group-lessons',
    items: [
      { key: 'lessons', label: 'Уроки', path: '/admin/lessons' },
      { key: 'extra-lessons', label: 'Доп.уроки', path: '/admin/extra-lessons' },
      { key: 'calendar', label: 'Календарь', path: '/admin/calendar' },
      { key: 'feedback', label: 'Отзывы', path: '/admin/feedback' },
    ],
  },
  {
    key: 'finance',
    title: 'Финансы',
    icon: 'group-finance',
    items: [
      { key: 'subscriptions', label: 'Абонементы', path: '/admin/subscriptions' },
      { key: 'payroll', label: 'Зарплата', path: '/admin/payroll', can: canSeePayroll },
    ],
  },
  {
    key: 'system',
    title: 'Система',
    icon: 'group-system',
    items: [
      // Первыми в группе — пункты без ролевого условия, то есть те, которые
      // видят все. Между разделами, закрытыми для большинства ролей, они бы
      // терялись.
      { key: 'knowledge', label: 'Wiki', path: '/admin/knowledge' },
      { key: 'reports', label: 'Отчёты', path: '/admin/reports' },
      { key: 'archive', label: 'Архив', path: '/admin/archive', can: canSeeArchive },
      { key: 'accounts', label: 'Учётки', path: '/admin/accounts', can: canSeeAccounts },
      { key: 'audit', label: 'Журнал ИБ', path: '/admin/audit', can: canSeeAudit },
      { key: 'changelog', label: 'Журнал изменений', path: '/admin/changelog', can: canSeeChangelog },
      { key: 'notifications', label: 'Уведомления', path: '/admin/notifications', can: canSeeChangelog },
      { key: 'sync', label: 'Синхро', path: '/admin/sync', can: canSeeSync },
    ],
  },
];

/**
 * Ключ группы, которой принадлежит путь, либо null.
 *
 * Сравнение по началу пути с обязательным «/» на стыке: вложенные маршруты
 * (`/admin/students/42`) должны подсвечивать свою группу, но `/admin/lessons`
 * не должен ловить чужой `/admin/lessons-archive`, если такой однажды заведут.
 */
export function groupKeyOfPath(pathname: string): string | null {
  for (const group of NAV_GROUPS) {
    const hit = group.items.some(
      (it) => pathname === it.path || pathname.startsWith(`${it.path}/`),
    );
    if (hit) return group.key;
  }
  return null;
}

/**
 * Подпись раздела, которому принадлежит путь, либо null.
 *
 * Нужна верхней полосе узкого режима: колонки там нет, и название раздела —
 * единственный ответ на вопрос «где я».
 */
export function navLabelOfPath(pathname: string): string | null {
  const hit = (it: NavItem) => pathname === it.path || pathname.startsWith(`${it.path}/`);
  const pinned = NAV_PINNED.find(hit);
  if (pinned) return pinned.label;
  for (const group of NAV_GROUPS) {
    const it = group.items.find(hit);
    if (it) return it.label;
  }
  return null;
}
