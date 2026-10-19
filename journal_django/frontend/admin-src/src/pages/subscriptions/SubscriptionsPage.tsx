import { useSearchParams } from 'react-router-dom';
import { SubscriptionsView } from './SubscriptionsView';
import { DiscountsView } from './DiscountsView';
import { PageHeader } from '../../components/shell/PageHeader';

type Tab = 'subscriptions' | 'discounts';

export default function SubscriptionsPage() {
  const [sp, setSp] = useSearchParams();
  const tab: Tab = sp.get('tab') === 'discounts' ? 'discounts' : 'subscriptions';
  const setTab = (t: Tab) => {
    const next = new URLSearchParams(sp);
    if (t === 'subscriptions') next.delete('tab');
    else next.set('tab', t);
    setSp(next, { replace: true });
  };

  return (
    <>
      <PageHeader
        title="Абонементы и скидки"
        actions={
          <div className="segmented" role="group" aria-label="Раздел">
            <button
              type="button"
              className={`segmented__btn${tab === 'subscriptions' ? ' is-active' : ''}`}
              aria-pressed={tab === 'subscriptions'}
              onClick={() => setTab('subscriptions')}
            >Абонементы</button>
            <button
              type="button"
              className={`segmented__btn${tab === 'discounts' ? ' is-active' : ''}`}
              aria-pressed={tab === 'discounts'}
              onClick={() => setTab('discounts')}
            >Скидки</button>
          </div>
        }
      />
      {tab === 'subscriptions' ? <SubscriptionsView /> : <DiscountsView />}
    </>
  );
}
