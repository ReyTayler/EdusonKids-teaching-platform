import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { PageHeader } from '../../components/shell/PageHeader';
import { Button } from '../../components/ui/Button';
import { IconButton } from '../../components/ui/IconButton';
import { SearchInput } from '../../components/ui/SearchInput';
import { EmptyState } from '../../components/ui/EmptyState';
import { PageLoading } from '../../components/ui/Skeleton';
import { Paginator } from '../../components/table/Paginator';
import { SelectInput } from '../../components/form/SelectInput';
import { LibraryNav, type LibrarySelection } from '../../components/knowledge/LibraryNav';
import { DocumentCards, DocumentTable } from '../../components/knowledge/DocumentList';
import {
  DocIcon,
  EmptyLibraryIcon,
  GridViewIcon,
  ListViewIcon,
  PlusIcon,
} from '../../components/knowledge/knowledgeIcons';
import { ConfirmDialog, NameDialog } from './KnowledgeDialogs';
import { useAuth } from '../../hooks/useAuth';
import { useApiError } from '../../hooks/useApiError';
import {
  DEFAULT_PAGE_SIZE,
  useKnowledgeLibrary,
  useKnowledgeMutations,
  useKnowledgeSections,
} from '../../hooks/useKnowledge';
import type {
  DocumentStatus,
  KnowledgeDocumentRow,
  KnowledgeSection,
  LibraryScope,
} from '../../lib/knowledge';
import type { Role } from '../../lib/permissions';

const WRITE_ROLES: Role[] = ['admin', 'superadmin'];

type View = 'grid' | 'list';
const VIEW_STORAGE_KEY = 'kb-view';

const SCOPE_TITLES: Record<LibraryScope, string> = {
  all: 'Wiki',
  favorites: 'Избранное',
  archive: 'Архив',
};

const STATUS_OPTIONS = [
  { value: '', label: 'Все статусы' },
  { value: 'published', label: 'Опубликованные' },
  { value: 'draft', label: 'Черновики' },
];

/**
 * Библиотека: слева подборки и папки, справа документы.
 *
 * Состояние экрана целиком живёт в адресе (?scope=&section=&q=&status=). Так на
 * любую выборку можно дать ссылку, «Назад» возвращает к предыдущей, а поиск
 * переживает перезагрузку страницы. Держать это в useState значило бы, что
 * найденное нечем показать коллеге.
 */
export default function KnowledgeListPage() {
  const { me } = useAuth();
  const canWrite = !!me && WRITE_ROLES.includes(me.role as Role);

  const navigate = useNavigate();
  const showError = useApiError();
  const sections = useKnowledgeSections();
  const {
    createSection, renameSection, deleteSection,
    createDocument, deleteDocument, setDocumentPublished,
    restoreDocument, duplicateDocument, setFavorite,
  } = useKnowledgeMutations();

  const [searchParams, setSearchParams] = useSearchParams();
  const rawSection = searchParams.get('section');
  const sectionId = rawSection && /^\d+$/.test(rawSection) ? Number(rawSection) : null;
  const scope = readScope(searchParams.get('scope'));
  const query = searchParams.get('q') ?? '';
  const statusFilter = readStatus(searchParams.get('status'));
  const page = readPositive(searchParams.get('page'), 1);
  const pageSize = readPositive(searchParams.get('size'), DEFAULT_PAGE_SIZE);

  const patchParams = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(changes)) {
      if (value === null || value === '') next.delete(key);
      else next.set(key, value);
    }
    // replace — переключение подборок не должно засорять историю: «Назад» из
    // библиотеки возвращает туда, откуда пришли, а не к предыдущему фильтру.
    setSearchParams(next, { replace: true });
  };

  const selection: LibrarySelection = { scope, sectionId };
  const selectLibrary = (next: LibrarySelection) => patchParams({
    scope: next.scope === 'all' ? null : next.scope,
    section: next.sectionId === null ? null : String(next.sectionId),
    // Смена папки или подборки возвращает к первой странице: остаться на
    // пятой при переходе в раздел из трёх документов — значит увидеть пустоту.
    page: null,
  });

  const library = useKnowledgeLibrary({
    sectionId, q: query, scope, status: statusFilter, page, pageSize,
  });

  const [dialog, setDialog] = useState<
    | { kind: 'create-section' }
    | { kind: 'create-document'; sectionId: number }
    | { kind: 'rename-section'; section: KnowledgeSection }
    | { kind: 'delete-section'; section: KnowledgeSection }
    | { kind: 'delete-document'; doc: KnowledgeDocumentRow }
    | null
  >(null);
  const closeDialog = () => setDialog(null);

  const [view, setView] = useState<View>(() =>
    (localStorage.getItem(VIEW_STORAGE_KEY) as View) === 'list' ? 'list' : 'grid',
  );
  useEffect(() => {
    localStorage.setItem(VIEW_STORAGE_KEY, view);
  }, [view]);

  const visible = library.data?.rows ?? [];
  const total = library.data?.total ?? 0;

  const sectionList = sections.data?.sections ?? [];
  const totalDocuments = sections.data?.total ?? 0;
  const openSection = sectionList.find((item) => item.id === sectionId) ?? null;

  const actions = {
    canWrite,
    archived: scope === 'archive',
    onEdit: (id: number) => navigate(`/admin/knowledge/${id}`, { state: { edit: true } }),
    onTogglePublish: (doc: KnowledgeDocumentRow) =>
      setDocumentPublished.mutate({ id: doc.id, published: doc.status === 'draft' }),
    onDuplicate: (doc: KnowledgeDocumentRow) =>
      duplicateDocument.mutate(doc.id, {
        onSuccess: (copy) => navigate(`/admin/knowledge/${copy.id}`, { state: { edit: true } }),
        onError: (err) => showError(err),
      }),
    onDelete: (doc: KnowledgeDocumentRow) => setDialog({ kind: 'delete-document', doc }),
    onRestore: (doc: KnowledgeDocumentRow) =>
      restoreDocument.mutate(doc.id, { onError: (err) => showError(err) }),
    onToggleFavorite: (doc: KnowledgeDocumentRow) =>
      setFavorite.mutate({ id: doc.id, value: !doc.is_favorite }, { onError: (err) => showError(err) }),
  };

  // isLoading, а не isFetching: с keepPreviousData повторные запросы идут
  // поверх уже показанных данных, и заслонять их скелетом не нужно.
  if (sections.isLoading || library.isLoading) return <PageLoading />;

  const askCreateSection = () => setDialog({ kind: 'create-section' });
  const askCreateDocument = () => {
    const target = sectionId ?? sectionList[0]?.id;
    if (target === undefined) setDialog({ kind: 'create-section' });
    else setDialog({ kind: 'create-document', sectionId: target });
  };

  const title = openSection && scope === 'all' ? openSection.title : SCOPE_TITLES[scope];

  return (
    <div className="page">
      <PageHeader
        title={title}
        sub={scope === 'all' && !openSection
          ? 'Регламенты, методика и инструкции школы — в одном месте.'
          : undefined}
        crumbs={
          openSection && scope === 'all'
            ? [{ label: 'Wiki', to: '/admin/knowledge' }, { label: openSection.title }]
            : undefined
        }
        actions={
          canWrite ? (
            <CreateMenu onSection={askCreateSection} onDocument={askCreateDocument} />
          ) : null
        }
      />

      {sectionList.length === 0 ? (
        <EmptyState
          icon={<EmptyLibraryIcon />}
          hint={
            canWrite
              ? 'Разделы — это папки: «Методика», «Продажи», «Регламенты». Документы лежат внутри них.'
              : 'Разделы появятся, когда их заведёт администратор.'
          }
          action={canWrite ? <Button onClick={askCreateSection}>Создать раздел</Button> : undefined}
        >
          Здесь пока пусто
        </EmptyState>
      ) : (
        <div className="kb-drive">
          <LibraryNav
            selection={selection}
            onSelect={selectLibrary}
            sections={sectionList}
            totalCount={totalDocuments}
            canWrite={canWrite}
            onRenameSection={(section) => setDialog({ kind: 'rename-section', section })}
            onDeleteSection={(section) => setDialog({ kind: 'delete-section', section })}
          />

          <section className="kb-drive__main">
            <div className="kb-drive__bar">
              <SearchInput
                value={query}
                onChange={(next) => patchParams({ q: next })}
                placeholder="Поиск по Wiki"
              />
              <div className="kb-drive__bar-right">
                <SelectInput
                  value={statusFilter}
                  onChange={(e) => patchParams({ status: e.target.value })}
                  options={STATUS_OPTIONS}
                  aria-label="Фильтр по статусу"
                />
                <ViewToggle view={view} onChange={setView} />
              </div>
            </div>

            <p className="kb-drive__count" aria-live="polite">
              {query
                ? `Найдено: ${total}`
                : total > 0 ? `Документов: ${total}` : 'Пусто'}
            </p>

            {visible.length === 0 ? (
              <EmptyState
                icon={<DocIcon size={32} />}
                hint={emptyHint(scope, query, canWrite)}
                action={
                  canWrite && scope === 'all' && !query
                    ? <Button onClick={askCreateDocument}>Создать документ</Button>
                    : undefined
                }
              >
                {emptyTitle(scope, query, openSection?.title)}
              </EmptyState>
            ) : (
              <>
                {view === 'grid' ? (
                  <DocumentCards rows={visible} actions={actions} />
                ) : (
                  <DocumentTable
                    rows={visible}
                    actions={actions}
                    showSection={sectionId === null}
                    sections={sectionList}
                  />
                )}
                {total > pageSize && (
                  <Paginator
                    page={page}
                    pageSize={pageSize}
                    total={total}
                    onPageChange={(next) => patchParams({ page: String(next) })}
                    onPageSizeChange={(next) => patchParams({
                      size: String(next), page: null,
                    })}
                  />
                )}
              </>
            )}
          </section>
        </div>
      )}

      <NameDialog
        open={dialog?.kind === 'create-section'}
        title="Новый раздел"
        label="Название раздела"
        placeholder="Например: Методика"
        submitLabel="Создать"
        hint="Раздел — это папка для документов."
        busy={createSection.isPending}
        onSubmit={(name) => {
          createSection.mutate(name, {
            onSuccess: (section) => {
              selectLibrary({ scope: 'all', sectionId: section.id });
              closeDialog();
            },
          });
        }}
        onClose={closeDialog}
      />

      <NameDialog
        open={dialog?.kind === 'create-document'}
        title="Новый документ"
        label="Название документа"
        placeholder="Например: Регламент проверки домашних заданий"
        submitLabel="Создать"
        hint="Документ появится черновиком: пока вы его не опубликуете, он виден только администраторам."
        busy={createDocument.isPending}
        onSubmit={(name) => {
          if (dialog?.kind !== 'create-document') return;
          createDocument.mutate(
            { sectionId: dialog.sectionId, title: name },
            {
              onSuccess: (doc) => {
                closeDialog();
                navigate(`/admin/knowledge/${doc.id}`, { state: { edit: true } });
              },
            },
          );
        }}
        onClose={closeDialog}
      />

      <NameDialog
        open={dialog?.kind === 'rename-section'}
        title="Переименовать раздел"
        label="Название раздела"
        submitLabel="Переименовать"
        initialValue={dialog?.kind === 'rename-section' ? dialog.section.title : ''}
        busy={renameSection.isPending}
        onSubmit={(name) => {
          if (dialog?.kind !== 'rename-section') return;
          if (name === dialog.section.title) { closeDialog(); return; }
          renameSection.mutate({ id: dialog.section.id, title: name }, { onSuccess: closeDialog });
        }}
        onClose={closeDialog}
      />

      <ConfirmDialog
        open={dialog?.kind === 'delete-section'}
        title="Удалить раздел"
        message={
          dialog?.kind === 'delete-section'
            ? `Раздел «${dialog.section.title}» будет удалён. Если в нём есть документы, сначала перенесите или удалите их.`
            : ''
        }
        confirmLabel="Удалить"
        danger
        busy={deleteSection.isPending}
        onConfirm={() => {
          if (dialog?.kind !== 'delete-section') return;
          deleteSection.mutate(dialog.section.id, {
            onSuccess: () => {
              if (sectionId === dialog.section.id) selectLibrary({ scope: 'all', sectionId: null });
              closeDialog();
            },
          });
        }}
        onClose={closeDialog}
      />

      <ConfirmDialog
        open={dialog?.kind === 'delete-document'}
        title="Удалить документ"
        message={
          dialog?.kind === 'delete-document'
            // Про архив говорим прямо: иначе «удалить» читается как «навсегда»,
            // и документ не удаляют даже тогда, когда стоило бы.
            ? `Документ «${dialog.doc.title}» уедет в архив. Оттуда его можно вернуть.`
            : ''
        }
        confirmLabel="Удалить"
        danger
        busy={deleteDocument.isPending}
        onConfirm={() => {
          if (dialog?.kind !== 'delete-document') return;
          deleteDocument.mutate(dialog.doc.id, { onSuccess: closeDialog });
        }}
        onClose={closeDialog}
      />
    </div>
  );
}

function readScope(raw: string | null): LibraryScope {
  return raw === 'favorites' || raw === 'archive' ? raw : 'all';
}

function readStatus(raw: string | null): DocumentStatus | '' {
  return raw === 'draft' || raw === 'published' ? raw : '';
}

/** Число из адресной строки; мусор и отрицательные значения — на запасное. */
function readPositive(raw: string | null, fallback: number): number {
  const value = Number(raw);
  return Number.isInteger(value) && value > 0 ? value : fallback;
}

function emptyTitle(scope: LibraryScope, query: string, sectionTitle?: string): string {
  if (query) return 'Ничего не нашлось';
  if (scope === 'favorites') return 'В избранном пусто';
  if (scope === 'archive') return 'Архив пуст';
  return sectionTitle ? `В разделе «${sectionTitle}» пока нет документов` : 'Документов пока нет';
}

function emptyHint(scope: LibraryScope, query: string, canWrite: boolean): string {
  if (query) return 'Попробуйте другое слово: поиск ищет и по названию, и по тексту документов.';
  if (scope === 'favorites') return 'Звёздочка в строке документа кладёт его сюда.';
  if (scope === 'archive') return 'Удалённые документы попадают сюда, и отсюда их можно вернуть.';
  return canWrite
    ? 'Создайте первый документ — он появится здесь черновиком, видимым только администраторам.'
    : 'Документы появятся, когда их опубликуют.';
}

/** Кнопка «Создать» с выбором: раздел или документ. */
function CreateMenu({ onSection, onDocument }: { onSection: () => void; onDocument: () => void }) {
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <Button variant="primary" iconLeft={<PlusIcon size={16} />}>Создать</Button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="action-menu__list" align="end" sideOffset={6}>
          <DropdownMenu.Item className="action-menu__item" onSelect={onDocument}>
            Документ
          </DropdownMenu.Item>
          <DropdownMenu.Item className="action-menu__item" onSelect={onSection}>
            Раздел
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

function ViewToggle({ view, onChange }: { view: View; onChange: (v: View) => void }) {
  return (
    <div className="kb-view" role="group" aria-label="Вид списка">
      <IconButton
        size="sm"
        label="Карточками"
        active={view === 'grid'}
        icon={<GridViewIcon size={18} />}
        onClick={() => onChange('grid')}
      />
      <IconButton
        size="sm"
        label="Таблицей"
        active={view === 'list'}
        icon={<ListViewIcon size={18} />}
        onClick={() => onChange('list')}
      />
    </div>
  );
}
