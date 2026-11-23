import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { PageHeader } from '../../components/shell/PageHeader';
import { Button } from '../../components/ui/Button';
import { Field } from '../../components/form/Field';
import { SelectInput } from '../../components/form/SelectInput';
import { TableFilterPanel, type FilterField } from '../../components/filters';
import { EmptyState } from '../../components/ui/EmptyState';
import { PageLoading } from '../../components/ui/Skeleton';
import { Paginator } from '../../components/table/Paginator';
import { LibraryNav, type LibrarySelection } from '../../components/knowledge/LibraryNav';
import { DocumentTable } from '../../components/knowledge/DocumentList';
import {
  DocIcon,
  EmptyLibraryIcon,
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
} from '../../lib/knowledge';
import type { Role } from '../../lib/permissions';

const WRITE_ROLES: Role[] = ['admin', 'superadmin'];

/** Поля панели поиска: пункт «все» добавляет сама панель. */
const SEARCH_FIELDS: FilterField[] = [
  { kind: 'text', key: 'q', label: 'Название или текст', placeholder: 'Поиск по Wiki' },
  {
    kind: 'select', key: 'status', label: 'Статус', allLabel: 'Все статусы',
    options: [
      { value: 'published', label: 'Опубликованные' },
      { value: 'draft', label: 'Черновики' },
    ],
  },
];

/**
 * Папки над списком документов: одна навигация и один вид списка.
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
    duplicateDocument,
  } = useKnowledgeMutations();

  const [searchParams, setSearchParams] = useSearchParams();
  const rawSection = searchParams.get('section');
  const sectionId = rawSection && /^\d+$/.test(rawSection) ? Number(rawSection) : null;
  const scope = 'all' as const;
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

  const visible = library.data?.rows ?? [];
  const total = library.data?.total ?? 0;

  const sectionList = sections.data?.sections ?? [];
  const totalDocuments = sections.data?.total ?? 0;
  const openSection = sectionList.find((item) => item.id === sectionId) ?? null;

  const actions = {
    canWrite,
    onEdit: (id: number) => navigate(`/admin/knowledge/${id}`, { state: { edit: true } }),
    onTogglePublish: (doc: KnowledgeDocumentRow) =>
      setDocumentPublished.mutate({ id: doc.id, published: doc.status === 'draft' }, { onError: (err) => showError(err) }),
    onDuplicate: (doc: KnowledgeDocumentRow) =>
      duplicateDocument.mutate(doc.id, {
        onSuccess: (copy) => navigate(`/admin/knowledge/${copy.id}`, { state: { edit: true } }),
        onError: (err) => showError(err),
      }),
    onDelete: (doc: KnowledgeDocumentRow) => setDialog({ kind: 'delete-document', doc }),
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

  const title = 'Wiki';

  return (
    <div className="page">
      <PageHeader
        title={title}
        count={totalDocuments}
        actions={(
          <>
            {/* Искать негде, пока нет ни одного раздела. */}
            {sectionList.length > 0 && (
              <TableFilterPanel
                fields={SEARCH_FIELDS}
                filters={{ ...(query ? { q: query } : {}), ...(statusFilter ? { status: statusFilter } : {}) }}
                onApply={(next) => patchParams({ q: next.q ?? null, status: next.status ?? null, page: null })}
                label="Поиск по Wiki"
              />
            )}
            {canWrite && <>
              <Button variant="secondary" onClick={askCreateSection}>Новая папка</Button>
              <Button variant="primary" iconLeft={<PlusIcon size={16} />} onClick={askCreateDocument}>Новый документ</Button>
            </>}
          </>
        )}
      />

      {(sections.isError || library.isError) ? (
        <EmptyState action={<Button onClick={() => { sections.refetch(); library.refetch(); }}>Повторить</Button>}>
          Не удалось загрузить Wiki
        </EmptyState>
      ) : sectionList.length === 0 ? (
        <EmptyState
          icon={<EmptyLibraryIcon />}
          hint={
            canWrite
              ? 'Создайте папки по темам: «Методика», «Продажи», «Регламенты».'
              : 'Папки появятся, когда их создаст администратор.'
          }
          action={canWrite ? <Button onClick={askCreateSection}>Создать папку</Button> : undefined}
        >
          Здесь пока пусто
        </EmptyState>
      ) : (
        <div className="kb-library">
          <LibraryNav
            selection={selection}
            onSelect={selectLibrary}
            sections={sectionList}
            totalCount={totalDocuments}
            canWrite={canWrite}
            onRenameSection={(section) => setDialog({ kind: 'rename-section', section })}
            onDeleteSection={(section) => setDialog({ kind: 'delete-section', section })}
          />

          <section className="kb-drive__main" aria-busy={library.isFetching}>
            <div className={`kb-library__heading${query ? ' is-search' : ''}`}>
              <h2>{openSection?.title ?? 'Все документы'}</h2>
              <span aria-live="polite">{query ? `Найдено: ${total}` : `Документов: ${total}`}</span>
            </div>
            {visible.length === 0 ? (
              <EmptyState
                icon={<DocIcon size={32} />}
                hint={emptyHint(query, canWrite)}
                action={
                  canWrite && !query
                    ? <Button onClick={askCreateDocument}>Создать документ</Button>
                    : undefined
                }
              >
                {emptyTitle(query, openSection?.title)}
              </EmptyState>
            ) : (
              <>
                <DocumentTable rows={visible} actions={actions} showSection={sectionId === null} sections={sectionList} />
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
        title="Новая папка"
        label="Название папки"
        placeholder="Например: Методика"
        submitLabel="Создать"
        hint="Папка объединяет документы по теме."
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
      >
        <Field label="Папка" full required>
          <SelectInput aria-label="Папка документа" value={dialog?.kind === 'create-document' ? String(dialog.sectionId) : ''}
            options={sectionList.map((section) => ({ value: String(section.id), label: section.title }))}
            onChange={(e) => { if (dialog?.kind === 'create-document') setDialog({ ...dialog, sectionId: Number(e.target.value) }); }} />
        </Field>
      </NameDialog>

      <NameDialog
        open={dialog?.kind === 'rename-section'}
        title="Переименовать папку"
        label="Название папки"
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
        title="Удалить папку"
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
        title="Удалить документ навсегда"
        message={
          dialog?.kind === 'delete-document'
            ? `Документ «${dialog.doc.title}» будет удалён навсегда. Восстановить его не получится.`
            : ''
        }
        confirmLabel="Удалить навсегда"
        danger
        busy={deleteDocument.isPending}
        onConfirm={() => {
          if (dialog?.kind !== 'delete-document') return;
          deleteDocument.mutate(dialog.doc.id, { onSuccess: closeDialog, onError: (err) => showError(err) });
        }}
        onClose={closeDialog}
      />
    </div>
  );
}

function readStatus(raw: string | null): DocumentStatus | '' {
  return raw === 'draft' || raw === 'published' ? raw : '';
}

/** Число из адресной строки; мусор и отрицательные значения — на запасное. */
function readPositive(raw: string | null, fallback: number): number {
  const value = Number(raw);
  return Number.isInteger(value) && value > 0 ? value : fallback;
}

function emptyTitle(query: string, sectionTitle?: string): string {
  if (query) return 'Ничего не найдено';
  return sectionTitle ? 'В этой папке пока нет документов' : 'Документов пока нет';
}

function emptyHint(query: string, canWrite: boolean): string {
  if (query) return 'Попробуйте другое слово: поиск ищет по названию и тексту документов.';
  return canWrite ? 'Создайте документ и опубликуйте его, когда он будет готов.' : 'Документы появятся, когда их опубликуют.';
}
