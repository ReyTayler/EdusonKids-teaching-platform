import { useSearchParams } from 'react-router-dom';
import { PageHeader } from '../../components/shell/PageHeader';
import { Tabs, type TabItem } from '../../components/ui/Tabs';
import { SchoolRatesSection } from './SchoolRatesSection';
import { PersonalRatesSection } from './PersonalRatesSection';
import { CancellationReasonsTab } from './CancellationReasonsTab';
import { LessonsSettingsTab } from './LessonsSettingsTab';
import { CANCELLATION_LABELS, SETTINGS_TAB_LABELS } from '../../lib/labels';
import './motivation.css';

function MotivationTab() {
  const [params, setParams] = useSearchParams();
  const selected = params.get('motivation') || 'group';
  const items: TabItem[] = [
    { value: 'group', label: 'Групповые занятия', content: <SchoolRatesSection format="group" /> },
    { value: 'individual', label: 'Индивидуальные занятия', content: <SchoolRatesSection format="individual" /> },
    { value: 'personal', label: 'Персональные ставки', content: <PersonalRatesSection /> },
  ];
  return (
    <div className="motivation-workspace">
      <div className="motivation-intro"><h2>Правила оплаты преподавателей</h2>
        <p>Ставки школы и персональные договорённости с преподавателями</p></div>
      <Tabs className="motivation-nav" items={items}
        value={items.some((item) => item.value === selected) ? selected : 'group'}
        onChange={(value) => {
          const next = new URLSearchParams(params);
          if (value === 'group') next.delete('motivation'); else next.set('motivation', value);
          setParams(next, { replace: true });
        }} />
    </div>
  );
}

const TABS: TabItem[] = [
  { value: 'motivation', label: SETTINGS_TAB_LABELS.motivation, content: <MotivationTab /> },
  { value: 'cancellations', label: CANCELLATION_LABELS.tab, content: <CancellationReasonsTab /> },
  { value: 'lessons', label: SETTINGS_TAB_LABELS.lessons, content: <LessonsSettingsTab /> },
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
