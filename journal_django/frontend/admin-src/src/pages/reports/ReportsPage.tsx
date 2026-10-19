import { useSearchParams } from 'react-router-dom';
import { DataTable, type Column } from '../../components/table/DataTable';
import { RowOpenButton } from '../../components/table/RowOpenButton';
import { PageHeader } from '../../components/shell/PageHeader';
import { Tabs, type TabItem } from '../../components/ui/Tabs';
import { REPORT_TYPES, type ReportTypeDef } from '../../lib/reports';

/**
 * Раздел «Отчёты»: две вкладки.
 *
 * «Отчёты» — фоновые выгрузки: строка отвечает на вопрос «какие отчёты есть»,
 * настройка и запуск живут на странице отчёта (тот же паттерн «список →
 * карточка», что у учеников, групп и преподавателей).
 *
 * «Дашборд» — живые экраны со сводками: строка на экран, сам экран — отдельной
 * страницей под /admin/reports/dashboard/*.
 */
const columns: Column<ReportTypeDef>[] = [
  { key: 'title', label: 'Отчёт', cell: (row) => <span className="report-list__name">{row.title}</span> },
];

interface DashboardDef {
  key: string;
  title: string;
  desc: string;
  path: string;
}

const DASHBOARDS: DashboardDef[] = [
  {
    key: 'finance',
    title: 'Финансы',
    desc: 'Выручка, отработанное, авансы и остаток за период, графики по месяцам',
    path: '/admin/reports/dashboard/finance',
  },
  {
    key: 'attendance',
    title: 'Посещения учеников',
    desc: 'Отработанные уроки за всё время и за период, с разбивкой по ученикам',
    path: '/admin/reports/dashboard/attendance',
  },
];

const dashboardColumns: Column<DashboardDef>[] = [
  { key: 'title', label: 'Дашборд', cell: (row) => <span className="report-list__name">{row.title}</span> },
];

const TABS = ['reports', 'dashboard'] as const;
type ReportsTab = (typeof TABS)[number];
const DEFAULT_TAB: ReportsTab = 'reports';

function isTab(value: string | null): value is ReportsTab {
  return !!value && (TABS as readonly string[]).includes(value);
}

export default function ReportsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const raw = searchParams.get('tab');
  const activeTab: ReportsTab = isTab(raw) ? raw : DEFAULT_TAB;

  const setActiveTab = (tab: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (tab === DEFAULT_TAB) next.delete('tab'); else next.set('tab', tab);
      return next;
    }, { replace: true });
  };

  const tabs: TabItem[] = [
    {
      value: 'reports',
      label: 'Отчёты',
      content: (
        <DataTable<ReportTypeDef>
          data={REPORT_TYPES}
          columns={columns}
          title="Список доступных отчётов"
          roomy
          rowAction={(row) => (
            <RowOpenButton to={`/admin/reports/${row.reportType}`} title={row.desc} />
          )}
        />
      ),
    },
    {
      value: 'dashboard',
      label: 'Дашборд',
      content: (
        <DataTable<DashboardDef>
          data={DASHBOARDS}
          columns={dashboardColumns}
          title="Список доступных дашбордов"
          roomy
          rowAction={(row) => <RowOpenButton to={row.path} title={row.desc} />}
        />
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Отчёты"
        sub="Выгрузки формируются в фоне и на платформе не хранятся; дашборды считаются на лету при открытии."
      />
      <Tabs items={tabs} value={activeTab} onChange={setActiveTab} />
    </>
  );
}
