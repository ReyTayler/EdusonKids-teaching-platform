import { Suspense, lazy } from 'react';
import type { ReactNode } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthGate } from './components/shell/AuthGate';
import { TeacherShell } from './components/shell/TeacherShell';
import CalendarPage from './pages/calendar/CalendarPage';
import GroupsPage from './pages/groups/GroupsPage';
import GroupDetailPage from './pages/groups/GroupDetailPage';
import MyLessonsPage from './pages/lessons/MyLessonsPage';
import PayrollPage from './pages/payroll/PayrollPage';
import ReportPage from './pages/report/ReportPage';

// Wiki грузится отдельным чанком: её читалка тянет рендерер документов
// (@tiptap/static-renderer) и подсветку кода — 120+ КБ, которые не нужны ни в
// календаре, ни в отчёте, то есть на пяти экранах из шести.
const KnowledgeLibraryPage = lazy(() => import('./pages/knowledge/KnowledgeLibraryPage'));
const KnowledgeDocumentPage = lazy(() => import('./pages/knowledge/KnowledgeDocumentPage'));

// basename роутера = Vite base ('/teacher/' в финале, '/teacher-next/' в preview).
// Маршруты/ссылки — ОТНОСИТЕЛЬНЫЕ (без префикса), чтобы один код работал под обоими
// путями. import.meta.env.BASE_URL приходит с завершающим '/' — срезаем для basename.
const basename = import.meta.env.BASE_URL.replace(/\/$/, '');

export function App() {
  return (
    <BrowserRouter basename={basename}>
      <Routes>
        <Route element={<AuthGate />}>
          <Route element={<TeacherShell />}>
            <Route path="/" element={<Navigate to="/calendar" replace />} />
            <Route path="/calendar" element={<CalendarPage />} />
            <Route path="/groups" element={<GroupsPage />} />
            <Route path="/groups/:group" element={<GroupDetailPage />} />
            <Route path="/lessons" element={<MyLessonsPage />} />
            <Route path="/report" element={<ReportPage />} />
            <Route path="/payroll" element={<PayrollPage />} />
            <Route
              path="/knowledge"
              element={<Lazy><KnowledgeLibraryPage /></Lazy>}
            />
            <Route
              path="/knowledge/:id"
              element={<Lazy><KnowledgeDocumentPage /></Lazy>}
            />
            <Route path="*" element={<Navigate to="/calendar" replace />} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

/** Заглушка на время загрузки чанка — тот же скелет, что и у прочих экранов. */
function Lazy({ children }: { children: ReactNode }) {
  return <Suspense fallback={<div className="cal-skel" />}>{children}</Suspense>;
}
