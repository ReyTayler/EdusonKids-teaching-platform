import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { PageLoading } from './components/ui/Skeleton';
import { AuthGate } from './components/shell/AuthGate';
import { AppShell } from './components/shell/AppShell';
import { RequireRole } from './components/shell/RequireRole';
import StudentsListPage from './pages/students/StudentsListPage';
import GroupsListPage from './pages/groups/GroupsListPage';
import TeachersListPage from './pages/teachers/TeachersListPage';
import DirectionsListPage from './pages/directions/DirectionsListPage';
import LessonsListPage from './pages/lessons/LessonsListPage';
import ExtraLessonsListPage from './pages/extra-lessons/ExtraLessonsListPage';
import PayrollPage from './pages/payroll/PayrollPage';
import LessonFeedbackPage from './pages/feedback/LessonFeedbackPage';
import SubscriptionsPage from './pages/subscriptions/SubscriptionsPage';
import ArchivePage from './pages/archive/ArchivePage';
import StudentDetailPage from './pages/students/StudentDetailPage';
import GroupDetailPage from './pages/groups/GroupDetailPage';
import TeacherDetailPage from './pages/teachers/TeacherDetailPage';
import DirectionDetailPage from './pages/directions/DirectionDetailPage';
import LessonDetailPage from './pages/lessons/LessonDetailPage';
import AdminCalendarPage from './pages/calendar/AdminCalendarPage';
import DashboardPage from './pages/dashboard/DashboardPage';
import AuditPage from './pages/audit/AuditPage';
import AccountsPage from './pages/accounts/AccountsPage';
import ChangelogListPage from './pages/changelog/ChangelogListPage';
import NotificationsPage from './pages/notifications/NotificationsPage';
import SyncPage from './pages/sync/SyncPage';
import SettingsPage from './pages/settings/SettingsPage';
import RenewalsPage from './pages/renewals/RenewalsPage';
import TasksPage from './pages/tasks/TasksPage';
import RenewalStagesSettings from './pages/renewals/RenewalStagesSettings';
import ReportsPage from './pages/reports/ReportsPage';
import ReportPage from './pages/reports/ReportPage';
import AttendanceDashboardPage from './pages/reports/AttendanceDashboardPage';
import FinanceDashboardPage from './pages/reports/finance/FinanceDashboardPage';
import KnowledgeListPage from './pages/knowledge/KnowledgeListPage';
import KnowledgeDocumentPage from './pages/knowledge/KnowledgeDocumentPage';

// Recharts — тяжёлая зависимость, держим её вне основного бандла (как FinanceCharts в дашборде финансов).
const RenewalAnalyticsPage = lazy(() => import('./pages/renewals/RenewalAnalyticsPage'));

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AuthGate />}>
          <Route element={<AppShell />}>
            <Route path="/admin" element={<Navigate to="/admin/dashboard" replace />} />

            <Route path="/admin/dashboard" element={<DashboardPage />} />

            <Route path="/admin/students" element={<StudentsListPage />} />
            <Route path="/admin/students/:id" element={<StudentDetailPage />} />

            <Route path="/admin/groups" element={<GroupsListPage />} />
            <Route path="/admin/groups/:id" element={<GroupDetailPage />} />

            <Route path="/admin/teachers" element={<TeachersListPage />} />
            <Route path="/admin/teachers/:id" element={<TeacherDetailPage />} />

            <Route path="/admin/directions" element={<DirectionsListPage />} />
            <Route path="/admin/directions/:id" element={<DirectionDetailPage />} />

            <Route path="/admin/lessons" element={<LessonsListPage />} />
            <Route path="/admin/lessons/:id" element={<LessonDetailPage />} />
            <Route path="/admin/extra-lessons" element={<RequireRole roles={['manager','admin','superadmin']}><ExtraLessonsListPage /></RequireRole>} />

            <Route path="/admin/feedback" element={<RequireRole roles={['manager','admin','superadmin']}><LessonFeedbackPage /></RequireRole>} />
            <Route path="/admin/payroll"element={<RequireRole roles={['superadmin']}><PayrollPage /></RequireRole>} />
            <Route path="/admin/calendar" element={<RequireRole roles={['manager','admin','superadmin']}><AdminCalendarPage /></RequireRole>} />
            <Route path="/admin/subscriptions" element={<SubscriptionsPage />} />
            <Route path="/admin/renewals" element={<RequireRole roles={['manager','admin','superadmin']}><RenewalsPage /></RequireRole>} />
            <Route path="/admin/renewals/analytics" element={<RequireRole roles={['manager','admin','superadmin']}><Suspense fallback={<PageLoading />}><RenewalAnalyticsPage /></Suspense></RequireRole>} />
            <Route path="/admin/renewals/stages" element={<RequireRole roles={['superadmin']}><RenewalStagesSettings /></RequireRole>} />
            <Route path="/admin/tasks" element={<RequireRole roles={['manager','admin','superadmin']}><TasksPage /></RequireRole>} />
            <Route path="/admin/reports" element={<RequireRole roles={['manager','admin','superadmin']}><ReportsPage /></RequireRole>} />
            <Route path="/admin/reports/dashboard/finance" element={<RequireRole roles={['manager','admin','superadmin']}><FinanceDashboardPage /></RequireRole>} />
            <Route path="/admin/reports/dashboard/attendance" element={<RequireRole roles={['manager','admin','superadmin']}><AttendanceDashboardPage /></RequireRole>} />
            <Route path="/admin/reports/:reportType" element={<RequireRole roles={['manager','admin','superadmin']}><ReportPage /></RequireRole>} />

            <Route path="/admin/knowledge" element={<KnowledgeListPage />} />
            <Route path="/admin/knowledge/:id" element={<KnowledgeDocumentPage />} />

            <Route path="/admin/archive" element={<RequireRole roles={['superadmin']}><ArchivePage /></RequireRole>} />
            <Route path="/admin/audit" element={<RequireRole roles={['superadmin']}><AuditPage /></RequireRole>} />
            <Route path="/admin/accounts" element={<RequireRole roles={['superadmin']}><AccountsPage /></RequireRole>} />
            <Route path="/admin/changelog" element={<RequireRole roles={['manager','admin','superadmin']}><ChangelogListPage /></RequireRole>} />
            {/* Уведомления — системный раздел: бэкенд (IsAdminOrSuperAdmin) менеджеру
                отдаёт 403, поэтому и роут закрыт на admin/superadmin. */}
            <Route path="/admin/notifications" element={<RequireRole roles={['admin','superadmin']}><NotificationsPage /></RequireRole>} />
            <Route path="/admin/settings" element={<RequireRole roles={['superadmin']}><SettingsPage /></RequireRole>} />
            <Route path="/admin/sync" element={<RequireRole roles={['superadmin']}><SyncPage /></RequireRole>} />

            <Route path="*" element={<Navigate to="/admin/dashboard" replace />} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
