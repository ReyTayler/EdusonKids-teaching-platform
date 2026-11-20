import { lazy, Suspense } from 'react';
import { useSearchParams } from 'react-router-dom';
import { PageLoading } from '../../components/ui/Skeleton';
import { PageHeader } from '../../components/shell/PageHeader';
import { TableFilterPanel, columnFilterFields } from '../../components/filters';
import { useListSearchParams } from '../../hooks/useListSearchParams';
import { REGISTRY_COLUMNS } from './registry/RegistryTable';
import { FillSearch } from './fill/FillSearch';

// Реестр — отдельный чанк: таблица/сигналы грузятся только при открытии вкладки.
const RegistryTab = lazy(() => import('./registry/RegistryTab'));
const FillTab = lazy(() => import('./fill/FillTab'));

type Tab = 'registry' | 'fill';

export default function DashboardPage() {
  const [sp, setSp] = useSearchParams();
  const tab: Tab = sp.get('tab') === 'fill' ? 'fill' : 'registry';
  // Фильтры реестра живут в адресе (f.*) — вторая копия хука читает те же
  // параметры, что и вкладка. Умолчания совпадают с RegistryTab.
  const { filters, setFilters } = useListSearchParams({ sortBy: 'urgency', sortDir: 'asc', pageSize: 30 });

  const setTab = (t: Tab) => {
    const next = new URLSearchParams(sp);
    if (t === 'registry') next.delete('tab');
    else next.set('tab', t);
    setSp(next, { replace: true });
  };

  return (
    <div className="dashboard">
      <PageHeader
        title="Дашборд"
        // Лупа — в шапке для обеих вкладок (по активной).
        actions={tab === 'registry' ? (
          <TableFilterPanel fields={columnFilterFields(REGISTRY_COLUMNS)} filters={filters} onApply={setFilters} label="Поиск: Реестр" />
        ) : <FillSearch />}
      />

      <nav className="dash-tabs" role="tablist" aria-label="Разделы дашборда">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'registry'}
          className={`dash-tab${tab === 'registry' ? ' dash-tab--active' : ''}`}
          onClick={() => setTab('registry')}
        >
          Реестр
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'fill'}
          className={`dash-tab${tab === 'fill' ? ' dash-tab--active' : ''}`}
          onClick={() => setTab('fill')}
        >
          Заполнить
        </button>
      </nav>

      {tab === 'registry' && (
        <Suspense fallback={<PageLoading />}>
          <RegistryTab />
        </Suspense>
      )}
      {tab === 'fill' && (
        <Suspense fallback={<PageLoading />}>
          <FillTab />
        </Suspense>
      )}
    </div>
  );
}
