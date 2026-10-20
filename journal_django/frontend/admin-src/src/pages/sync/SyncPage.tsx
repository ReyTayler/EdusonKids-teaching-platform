// journal_django/frontend/admin-src/src/pages/sync/SyncPage.tsx
import { SYNC_ACTIONS } from '../../lib/sync';
import { SyncActionCard } from './SyncActionCard';
import { ProdRefreshCard } from './ProdRefreshCard';
import { PageHeader } from '../../components/shell/PageHeader';

export default function SyncPage() {
  return (
    <section className="page sync-page">
      <PageHeader title="Синхро" />

      <ProdRefreshCard />

      <div className="sync-page__group-title">Проверки (только чтение)</div>
      {SYNC_ACTIONS.map((def) => <SyncActionCard key={def.action} def={def} />)}
    </section>
  );
}
