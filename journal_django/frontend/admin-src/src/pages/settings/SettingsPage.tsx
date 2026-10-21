import { useSearchParams } from 'react-router-dom';
import { PageHeader } from '../../components/shell/PageHeader';
import { Tabs, type TabItem } from '../../components/ui/Tabs';
import { SchoolRatesSection } from './SchoolRatesSection';
import { PersonalRatesSection } from './PersonalRatesSection';

function MotivationTab() {
  return (
    <div className="settings-tab">
      <SchoolRatesSection />
      <PersonalRatesSection />
    </div>
  );
}

const TABS: TabItem[] = [
  { value: 'motivation', label: 'Мотивация', content: <MotivationTab /> },
];
const DEFAULT_TAB = 'motivation';

export default function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const raw = params.get('tab');
  const tab = TABS.some((t) => t.value === raw) ? (raw as string) : DEFAULT_TAB;

  const onTab = (next: string) => {
    const p = new URLSearchParams(params);
    // Вкладка по умолчанию в адресе не хранится.
    if (next === DEFAULT_TAB) p.delete('tab');
    else p.set('tab', next);
    setParams(p, { replace: true });
  };

  return (
    <>
      <PageHeader title="Настройки" />
      <Tabs items={TABS} value={tab} onChange={onTab} />
    </>
  );
}
