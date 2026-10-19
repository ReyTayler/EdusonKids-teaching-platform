import { useCallback, useEffect, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ActionMenu } from '../ui/ActionMenu';
import { ArchiveIcon, DocIcon, FolderAllIcon, FolderIcon, StarIcon } from './knowledgeIcons';
import { useSectionDocuments } from '../../hooks/useKnowledge';
import type { KnowledgeSection, LibraryScope } from '../../lib/knowledge';

/**
 * Левая панель библиотеки: дерево разделов и документов.
 *
 * Дерево, а не плоский список: раньше «Все документы», разделы и «Архив» шли
 * одной колонкой на одном уровне, и по виду нельзя было понять, что раздел
 * лежит ВНУТРИ библиотеки, а архив — рядом с ней. Вложенность видна отступом и
 * стрелкой раскрытия, как в любом файловом дереве.
 *
 * Документы раздела подгружаются только при раскрытии — и только они, без
 * содержимого. Иначе панель тянула бы всю Wiki на каждое открытие
 * экрана, а весь смысл серверной пагинации списка (useKnowledgeLibrary) как раз
 * в том, чтобы этого не делать.
 *
 * Порядок верхнего уровня не случаен. «Избранное» — вход по памяти («я это уже
 * отметил»), дерево — вход по структуре («это должно лежать в методике»).
 * Первый путь короче, поэтому он сверху и отделён чертой. «Архив» стоит
 * последним и виден только тем, кто вправе восстанавливать.
 */
export interface LibrarySelection {
  scope: LibraryScope;
  /** null — «все документы» внутри выбранной подборки. */
  sectionId: number | null;
}

/** Что раскрыто в дереве. Переживает перезагрузку: дерево — это навигация. */
const OPEN_STORAGE_KEY = 'kb-tree-open';
const ROOT_KEY = 'all';

export function LibraryNav({
  selection,
  onSelect,
  sections,
  totalCount,
  canWrite,
  onRenameSection,
  onDeleteSection,
  basePath = '/admin/knowledge',
  activeDocumentId,
}: {
  selection: LibrarySelection;
  onSelect: (next: LibrarySelection) => void;
  sections: KnowledgeSection[];
  totalCount: number;
  canWrite: boolean;
  onRenameSection?: (section: KnowledgeSection) => void;
  onDeleteSection?: (section: KnowledgeSection) => void;
  /** Корень адресов документов: у преподавателя он свой (/knowledge). */
  basePath?: string;
  /** Открытый сейчас документ — подсвечивается в дереве. */
  activeDocumentId?: number;
}) {
  const [open, setOpen] = useState<Set<string>>(readOpen);

  useEffect(() => {
    localStorage.setItem(OPEN_STORAGE_KEY, JSON.stringify([...open]));
  }, [open]);

  const toggle = useCallback((key: string) => {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  // Выбранный раздел раскрываем сам: переход по ссылке вида ?section=12
  // иначе показывал бы свёрнутое дерево, в котором ничего не подсвечено.
  useEffect(() => {
    if (selection.scope !== 'all' || selection.sectionId === null) return;
    setOpen((prev) => (prev.has(sectionKey(selection.sectionId!)) && prev.has(ROOT_KEY)
      ? prev
      : new Set(prev).add(ROOT_KEY).add(sectionKey(selection.sectionId!))));
  }, [selection.scope, selection.sectionId]);

  const pick = (scope: LibraryScope, sectionId: number | null = null) =>
    () => onSelect({ scope, sectionId });

  const rootOpen = open.has(ROOT_KEY);

  return (
    <nav className="kb-drive__nav" aria-label="Разделы Wiki">
      <ul className="kb-tree">
        <TreeRow
          level={0}
          icon={<StarIcon size={16} />}
          title="Избранное"
          active={selection.scope === 'favorites'}
          onOpen={pick('favorites')}
        />
      </ul>

      <div className="kb-drive__nav-divider" role="separator" />

      <ul className="kb-tree">
        <TreeRow
          level={0}
          icon={<FolderAllIcon size={16} />}
          title="Все документы"
          count={totalCount}
          active={selection.scope === 'all' && selection.sectionId === null}
          onOpen={pick('all')}
          expanded={rootOpen}
          onToggle={() => toggle(ROOT_KEY)}
        >
          {sections.map((section) => (
            <SectionBranch
              key={section.id}
              section={section}
              selection={selection}
              expanded={open.has(sectionKey(section.id))}
              onToggle={() => toggle(sectionKey(section.id))}
              onOpen={pick('all', section.id)}
              canWrite={canWrite}
              onRename={onRenameSection}
              onDelete={onDeleteSection}
              basePath={basePath}
              activeDocumentId={activeDocumentId}
            />
          ))}
        </TreeRow>
      </ul>

      {canWrite && (
        <>
          <div className="kb-drive__nav-divider" role="separator" />
          <ul className="kb-tree">
            <TreeRow
              level={0}
              icon={<ArchiveIcon size={16} />}
              title="Архив"
              active={selection.scope === 'archive'}
              onOpen={pick('archive')}
            />
          </ul>
        </>
      )}
    </nav>
  );
}

/** Ветка одного раздела: сам раздел плюс его документы при раскрытии. */
function SectionBranch({
  section,
  selection,
  expanded,
  onToggle,
  onOpen,
  canWrite,
  onRename,
  onDelete,
  basePath,
  activeDocumentId,
}: {
  section: KnowledgeSection;
  selection: LibrarySelection;
  expanded: boolean;
  onToggle: () => void;
  onOpen: () => void;
  canWrite: boolean;
  onRename?: (section: KnowledgeSection) => void;
  onDelete?: (section: KnowledgeSection) => void;
  basePath: string;
  activeDocumentId?: number;
}) {
  // enabled: запрос уходит только за раскрытую ветку. Свёрнутый раздел не
  // стоит ни одного обращения к серверу.
  const documents = useSectionDocuments(section.id, expanded);
  const rows = documents.data?.rows ?? [];
  const hidden = Math.max(0, (documents.data?.total ?? 0) - rows.length);

  const menu = canWrite && (onRename || onDelete) ? (
    <ActionMenu
      label={`Действия с разделом «${section.title}»`}
      items={[
        ...(onRename ? [{ label: 'Переименовать', onSelect: () => onRename(section) }] : []),
        ...(onDelete
          ? [{ label: 'Удалить раздел', danger: true, onSelect: () => onDelete(section) }]
          : []),
      ]}
    />
  ) : null;

  return (
    <TreeRow
      level={1}
      icon={<FolderIcon size={16} />}
      title={section.title}
      count={section.document_count}
      active={selection.scope === 'all' && selection.sectionId === section.id}
      onOpen={onOpen}
      expanded={expanded}
      onToggle={onToggle}
      menu={menu}
    >
      {documents.isLoading ? (
        <li className="kb-tree__note" style={indent(2)}>Загружаем…</li>
      ) : rows.length === 0 ? (
        <li className="kb-tree__note" style={indent(2)}>Пусто</li>
      ) : (
        <>
          {rows.map((doc) => (
            <TreeRow
              key={doc.id}
              level={2}
              icon={<DocIcon size={16} />}
              title={doc.title}
              active={doc.id === activeDocumentId}
              to={`${basePath}/${doc.id}`}
              muted={doc.status === 'draft'}
            />
          ))}
          {hidden > 0 && (
            // Дерево — навигация, а не список: показывать в нём сотни строк
            // незачем, для этого есть основная часть экрана с пагинацией.
            <li className="kb-tree__note" style={indent(2)}>
              <button type="button" className="kb-tree__more" onClick={onOpen}>
                Ещё {hidden} — открыть раздел
              </button>
            </li>
          )}
        </>
      )}
    </TreeRow>
  );
}

/**
 * Строка дерева.
 *
 * Раскрытие и выбор — две РАЗНЫЕ кнопки: щелчок по стрелке разворачивает
 * ветку, щелчок по названию открывает раздел справа. Совмести их — нельзя
 * было бы посмотреть содержимое папки, не уйдя с текущей выборки.
 */
function TreeRow({
  level,
  icon,
  title,
  count,
  active,
  onOpen,
  to,
  expanded,
  onToggle,
  menu,
  muted,
  children,
}: {
  level: number;
  icon: ReactNode;
  title: string;
  count?: number;
  active?: boolean;
  onOpen?: () => void;
  /** Лист-документ — ссылка, а не кнопка: его открывают в новой вкладке. */
  to?: string;
  expanded?: boolean;
  onToggle?: () => void;
  menu?: ReactNode;
  muted?: boolean;
  children?: ReactNode;
}) {
  const expandable = onToggle !== undefined;
  const classes = [
    'kb-tree__row',
    active ? 'is-active' : '',
    muted ? 'is-muted' : '',
  ].filter(Boolean).join(' ');

  const body = (
    <>
      <span className="kb-tree__icon">{icon}</span>
      <span className="kb-tree__title" title={title}>{title}</span>
      {count !== undefined && <span className="kb-tree__count">{count}</span>}
    </>
  );

  return (
    <li className="kb-tree__item">
      <div className={classes} style={indent(level)}>
        {expandable ? (
          <button
            type="button"
            className="kb-tree__twist"
            onClick={onToggle}
            aria-expanded={expanded}
            aria-label={expanded ? `Свернуть «${title}»` : `Развернуть «${title}»`}
          >
            <Chevron open={!!expanded} />
          </button>
        ) : (
          <span className="kb-tree__twist" aria-hidden="true" />
        )}

        {to ? (
          <Link to={to} className="kb-tree__open" aria-current={active ? 'page' : undefined}>
            {body}
          </Link>
        ) : (
          <button type="button" className="kb-tree__open" onClick={onOpen} aria-current={active}>
            {body}
          </button>
        )}

        {menu && <div className="kb-tree__menu">{menu}</div>}
      </div>

      {expandable && expanded && <ul className="kb-tree__group">{children}</ul>}
    </li>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
      className={`kb-tree__chev${open ? ' is-open' : ''}`}
    >
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}

/** Отступ уровня. Через переменную — величину шага держит CSS, а не разметка. */
function indent(level: number) {
  return { '--kb-tree-level': level } as CSSProperties;
}

const sectionKey = (id: number) => `sec:${id}`;

function readOpen(): Set<string> {
  try {
    const raw = localStorage.getItem(OPEN_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (Array.isArray(parsed)) return new Set(parsed.filter((k) => typeof k === 'string'));
  } catch {
    // Испорченное значение в localStorage не повод ронять экран.
  }
  // По умолчанию раскрыт корень: свёрнутое дерево при первом заходе выглядит
  // как пустая панель.
  return new Set([ROOT_KEY]);
}
