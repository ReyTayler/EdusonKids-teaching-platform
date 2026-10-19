import { useSearchParams } from 'react-router-dom';
import { LibraryNav, type LibrarySelection } from '@shared/components/knowledge/LibraryNav';
import { DocumentCards } from '@shared/components/knowledge/DocumentList';
import { DocIcon, EmptyLibraryIcon } from '@shared/components/knowledge/knowledgeIcons';
import { SearchInput } from '@shared/components/ui/SearchInput';
import { EmptyState } from '@shared/components/ui/EmptyState';
import { Paginator } from '@shared/components/table/Paginator';
import {
  DEFAULT_PAGE_SIZE,
  useKnowledgeLibrary,
  useKnowledgeMutations,
  useKnowledgeSections,
} from '@shared/hooks/useKnowledge';
import type { LibraryScope } from '@shared/lib/knowledge';
import { PageHead } from '../../components/ui/PageHead';

/** Корень адресов раздела в teacher SPA — им же размечены ссылки в дереве. */
export const KB_BASE = '/knowledge';

/**
 * Wiki у преподавателя — только чтение.
 *
 * Экран собран из тех же деталей, что и админский (дерево разделов, карточки
 * документов, поиск, постраничная выборка): раздел один, и расходиться им
 * незачем. Отличий ровно два, и оба — про права:
 *
 *   1. Ничего не создаётся и не правится. Кнопок «Создать» и меню документа
 *      здесь нет — не потому, что их спрятали, а потому что сервер их всё
 *      равно не пустит (KnowledgeReadStaffWriteAdmin: запись — admin).
 *   2. Видны только опубликованные документы, адресованные преподавателям.
 *      Это тоже решает сервер (repository.visible_documents_qs), клиент про
 *      чужие документы просто не знает.
 *
 * Избранное при этом доступно: закладка личная и правкой документа не
 * является (permissions.KnowledgePersonalMark).
 */
export default function KnowledgeLibraryPage() {
  const [searchParams, setSearchParams] = useSearchParams();

  const rawSection = searchParams.get('section');
  const sectionId = rawSection && /^\d+$/.test(rawSection) ? Number(rawSection) : null;
  const scope: LibraryScope = searchParams.get('scope') === 'favorites' ? 'favorites' : 'all';
  const query = searchParams.get('q') ?? '';
  const page = readPositive(searchParams.get('page'), 1);
  const pageSize = readPositive(searchParams.get('size'), DEFAULT_PAGE_SIZE);

  const sections = useKnowledgeSections();
  const library = useKnowledgeLibrary({ sectionId, q: query, scope, status: '', page, pageSize });
  const { setFavorite } = useKnowledgeMutations();

  // Состояние экрана живёт в адресе: на найденное можно дать ссылку коллеге,
  // «Назад» возвращает к прежней выборке.
  const patchParams = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(changes)) {
      if (value === null || value === '') next.delete(key);
      else next.set(key, value);
    }
    setSearchParams(next, { replace: true });
  };

  const selection: LibrarySelection = { scope, sectionId };
  const selectLibrary = (next: LibrarySelection) => patchParams({
    scope: next.scope === 'all' ? null : next.scope,
    section: next.sectionId === null ? null : String(next.sectionId),
    page: null,
  });

  const sectionList = sections.data?.sections ?? [];
  const rows = library.data?.rows ?? [];
  const total = library.data?.total ?? 0;
  const openSection = sectionList.find((item) => item.id === sectionId) ?? null;

  const title = scope === 'favorites'
    ? 'Избранное'
    : openSection?.title ?? 'Wiki';

  return (
    <div className="kb-teacher">
      <PageHead title={title} />

      {sections.isLoading || library.isLoading ? (
        <div className="cal-skel" />
      ) : sections.isError || library.isError ? (
        <div className="cal-error">Не удалось загрузить Wiki. Попробуйте обновить страницу.</div>
      ) : sectionList.length === 0 ? (
        <EmptyState
          icon={<EmptyLibraryIcon />}
          hint="Документы появятся здесь, когда их опубликует администратор."
        >
          Здесь пока пусто
        </EmptyState>
      ) : (
        <div className="kb-drive">
          <LibraryNav
            selection={selection}
            onSelect={selectLibrary}
            sections={sectionList}
            totalCount={sections.data?.total ?? 0}
            canWrite={false}
            basePath={KB_BASE}
          />

          <section className="kb-drive__main">
            <div className="kb-drive__bar">
              <SearchInput
                value={query}
                onChange={(next) => patchParams({ q: next, page: null })}
                placeholder="Поиск по Wiki"
              />
            </div>

            <p className="kb-drive__count" aria-live="polite">
              {query ? `Найдено: ${total}` : total > 0 ? `Документов: ${total}` : 'Пусто'}
            </p>

            {rows.length === 0 ? (
              <EmptyState
                icon={<DocIcon size={32} />}
                hint={
                  query
                    ? 'Попробуйте другое слово: поиск ищет и по названию, и по тексту документов.'
                    : scope === 'favorites'
                      ? 'Звёздочка в карточке документа кладёт его сюда.'
                      : 'Документы появятся, когда их опубликуют.'
                }
              >
                {query ? 'Ничего не нашлось' : scope === 'favorites' ? 'В избранном пусто' : 'Документов пока нет'}
              </EmptyState>
            ) : (
              <>
                <DocumentCards
                  rows={rows}
                  basePath={KB_BASE}
                  actions={{
                    canWrite: false,
                    onToggleFavorite: (doc) =>
                      setFavorite.mutate({ id: doc.id, value: !doc.is_favorite }),
                  }}
                />
                {total > pageSize && (
                  <Paginator
                    page={page}
                    pageSize={pageSize}
                    total={total}
                    onPageChange={(next) => patchParams({ page: String(next) })}
                    onPageSizeChange={(next) => patchParams({ size: String(next), page: null })}
                  />
                )}
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

/** Число из адресной строки; мусор и отрицательные значения — на запасное. */
function readPositive(raw: string | null, fallback: number): number {
  const value = Number(raw);
  return Number.isInteger(value) && value > 0 ? value : fallback;
}
