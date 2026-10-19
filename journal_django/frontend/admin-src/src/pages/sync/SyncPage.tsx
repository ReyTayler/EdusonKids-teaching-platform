// journal_django/frontend/admin-src/src/pages/sync/SyncPage.tsx
import { SYNC_ACTIONS } from '../../lib/sync';
import { SyncActionCard } from './SyncActionCard';
import { PageHeader } from '../../components/shell/PageHeader';

export default function SyncPage() {
  const rebuild = SYNC_ACTIONS.filter((a) => a.group === 'rebuild');
  const checks = SYNC_ACTIONS.filter((a) => a.group === 'checks');

  return (
    <section className="page sync-page">
      <PageHeader title="Синхро" />

      <div className="sync-page__group-title">Проверки (только чтение)</div>
      {checks.map((def) => <SyncActionCard key={def.action} def={def} />)}

      <div className="sync-page__group-title">Пересчёт из БД</div>
      {rebuild.map((def) => <SyncActionCard key={def.action} def={def} />)}
    </section>
  );
}
