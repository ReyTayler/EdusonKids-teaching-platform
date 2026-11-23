import { Link, useSearchParams } from 'react-router-dom';
import { Archive, BookOpen, ChevronRight, Users, UserRound } from 'lucide-react';
import { useArchive } from '../../hooks/useArchive';
import type { ArchiveEntityType, ArchiveFilter } from '../../lib/types';
import { ApiError } from '../../lib/api';
import { ARCHIVE_ENTITY_LABELS, ARCHIVE_FILTER_LABELS } from '../../lib/labels';
import { PageHeader } from '../../components/shell/PageHeader';
import { EmptyState } from '../../components/ui/EmptyState';
import { BlockLoading } from '../../components/ui/Skeleton';
import { Paginator } from '../../components/table/Paginator';
import { ArchiveSearch } from './ArchiveSearch';
import './archive.css';

const TYPES = ['all', 'teacher', 'group', 'direction'] as const;
const ENTITY_ICONS = { all: Archive, teacher: UserRound, group: Users, direction: BookOpen };
const ENTITY_PATHS: Record<ArchiveEntityType, string> = { teacher: 'teachers', group: 'groups', direction: 'directions' };
function numberParam(value: string | null, fallback: number) {
  const n = Number(value);
  return Number.isSafeInteger(n) && n > 0 ? n : fallback;
}

export default function ArchivePage() {
  const [sp, setSp] = useSearchParams();
  const query = (sp.get('q') ?? '').slice(0, 200);
  const normalized = query.trim().replace(/\s+/g, ' ');
  const rawType = sp.get('entity_type');
  const entityType: ArchiveFilter = TYPES.includes(rawType as ArchiveFilter) ? rawType as ArchiveFilter : 'all';
  const page = numberParam(sp.get('page'), 1);
  const rawSize = numberParam(sp.get('page_size'), 50);
  const pageSize = [25, 50, 100, 200].includes(rawSize) ? rawSize : 50;
  const archive = useArchive({ q: normalized, entity_type: entityType, page, page_size: pageSize });
  const { data } = archive;
  const waiting = archive.isLoading || archive.isPlaceholderData;
  const update = (values: Record<string, string | null>, resetPage = true) => setSp((prev) => {
    const next = new URLSearchParams(prev);
    if (resetPage) next.delete('page');
    Object.entries(values).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key));
    return next;
  }, { replace: true });
  const changeQuery = (value: string) => {
    // Новый поиск всегда глобальный, даже если до ввода был выбран один тип.
    update({ q: value || null, entity_type: null });
  };
  const totalArchive = data ? Object.values(data.archive_counts).reduce((a, b) => a + b, 0) : undefined;
  const totalMatches = data ? Object.values(data.counts).reduce((a, b) => a + b, 0) : 0;
  const invalidPage = archive.error instanceof ApiError && archive.error.status === 404 && page > 1;

  return (
    <div className="archive-page">
      <PageHeader title="Архив" count={totalArchive} actions={<ArchiveSearch value={query} onApply={changeQuery} />} />
      <section className="archive-workspace" aria-label="Поиск и записи архива">
        <div className="archive-types" role="group" aria-label="Тип архивных записей">
          {TYPES.map((type) => {
            const Icon = ENTITY_ICONS[type];
            const count = data ? type === 'all' ? totalMatches : data.counts[type] : '—';
            return <button key={type} type="button" className="archive-type" aria-pressed={entityType === type}
              onClick={() => update({ entity_type: type === 'all' ? null : type })}>
              <Icon size={18} aria-hidden="true" />
              <span className="archive-type__name">{ARCHIVE_FILTER_LABELS[type]}</span>
              <span className="archive-type__count">{count}</span>
            </button>;
          })}
        </div>

        <div className="archive-results-head">
          <h2>{normalized ? <>Результаты поиска <span className="archive-query">{normalized}</span></> : ARCHIVE_FILTER_LABELS[entityType]}</h2>
          <span role="status" aria-live="polite">{waiting ? 'Ищем…' : data ? `Найдено: ${data.total}` : ''}</span>
        </div>
        {archive.isError && <div className="archive-error" role="alert">
          <span>{invalidPage ? 'Эта страница архива больше недоступна.' : 'Не удалось загрузить архив.'}</span>
          <button type="button" className="btn-secondary" disabled={archive.isFetching}
            onClick={() => invalidPage ? update({ page: null }) : void archive.refetch()}>
            {invalidPage ? 'На первую страницу' : 'Повторить загрузку'}
          </button>
        </div>}
        <div className="archive-results" aria-busy={waiting || archive.isFetching}>
          {waiting ? <BlockLoading rows={5} label="Загружаем архив…" /> : data && !archive.isError && (
            data.rows.length === 0 ? <EmptyState icon={<Archive size={28} aria-hidden="true" />}
              hint={normalized ? 'Проверьте имя, название или ID. Можно сократить запрос или выбрать другой тип.' : 'Архивированные записи появятся здесь. Откройте карточку записи, чтобы вернуть её в работу.'}
              action={normalized || entityType !== 'all' ? <button type="button" className="btn-secondary"
                onClick={() => update({ q: null, entity_type: null })}>Сбросить поиск и фильтр</button> : undefined}>
              {normalized ? 'По вашему запросу ничего не найдено' : totalArchive === 0 ? 'Архив пока пуст' : 'В этом разделе нет архивных записей'}
            </EmptyState> : <ul className="archive-list" aria-label="Архивные записи">
              {data.rows.map((row) => {
                const Icon = ENTITY_ICONS[row.entity_type];
                return <li key={`${row.entity_type}-${row.id}`}>
                  <Link to={`/admin/${ENTITY_PATHS[row.entity_type]}/${row.id}`} className="archive-row">
                    <span className="archive-row__icon"><Icon size={20} aria-hidden="true" /></span>
                    <span className="archive-row__body"><span className="archive-row__name">{row.name}</span>
                      {row.description && <span className="archive-row__description">{row.description}</span>}</span>
                    <span className="archive-row__type">{ARCHIVE_ENTITY_LABELS[row.entity_type]}</span>
                    <span className="archive-row__id">#{row.id}</span>
                    <ChevronRight size={16} className="archive-row__arrow" aria-hidden="true" />
                  </Link>
                </li>;
              })}
            </ul>
          )}
        </div>
        {data && !waiting && !archive.isError && data.total > 0 && <Paginator page={data.page} pageSize={data.page_size} total={data.total}
          onPageChange={(value) => update({ page: value === 1 ? null : String(value) }, false)}
          onPageSizeChange={(value) => update({ page_size: value === 50 ? null : String(value) })} />}
      </section>
    </div>
  );
}
