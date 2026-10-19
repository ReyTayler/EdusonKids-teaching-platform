import { useState } from 'react';
import { useParams, Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { useTeacher, useTeacherMutations } from '../../hooks/useTeachers';
import { useTeacherGroups, useTeacherStats } from '../../hooks/useTeacherStats';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { DetailShell, EntityCard, type DetailField } from '../../components/detail/DetailShell';
import { EntityHero, HeroChip, monogramOf } from '../../components/detail/EntityHero';
import { ActionMenu } from '../../components/ui/ActionMenu';
import { PageLoading } from '../../components/ui/Skeleton';
import { Tabs, type TabItem } from '../../components/ui/Tabs';
import { Dialog } from '../../components/ui/Dialog';
import { EntityChangelogPanel } from '../../components/changelog/EntityChangelogPanel';
import { nameColor } from '../../lib/direction-color';
import { fmtDate, todayMSK } from '../../lib/format';
import type { Teacher } from '../../lib/types';
import TeacherFormModal from './TeacherFormModal';
import { TeacherTelegramBlock } from './TeacherTelegramBlock';
import TeacherStatsRow from './TeacherStatsRow';
import TeacherDirectionsBreakdown from './TeacherDirectionsBreakdown';
import TeacherMonthDetails from './TeacherMonthDetails';
import TeacherAttentionBar from './TeacherAttentionBar';
import TeacherGroupsBlock from './TeacherGroupsBlock';
import { useAuth } from '../../hooks/useAuth';
import { canSeeChangelog, canWriteTeachers, type Role } from '../../lib/permissions';
import { plural } from '../../lib/labels';

const TEACHER_TABS = ['overview', 'groups', 'data', 'history'] as const;
type TeacherTab = (typeof TEACHER_TABS)[number];
const DEFAULT_TAB: TeacherTab = 'overview';

function isTeacherTab(value: string | null): value is TeacherTab {
  return !!value && (TEACHER_TABS as readonly string[]).includes(value);
}

export default function TeacherDetailPage() {
  const params = useParams();
  const id = Number(params.id);
  const navigate = useNavigate();
  const { data: teacher, isLoading } = useTeacher(id);
  const [searchParams, setSearchParams] = useSearchParams();
  const [month, setMonth] = useState(() => todayMSK().slice(0, 7));
  const { data: groups = [] } = useTeacherGroups(id);
  const { data: stats } = useTeacherStats(id, month);
  const muts = useTeacherMutations();
  const { toast } = useToast();
  const showError = useApiError();
  const [editing, setEditing] = useState(false);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const { me } = useAuth();
  const canWrite = canWriteTeachers(me?.role as Role);

  const rawTab = searchParams.get('tab');
  const activeTab: TeacherTab = isTeacherTab(rawTab) ? rawTab : DEFAULT_TAB;
  const setActiveTab = (tab: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (tab === DEFAULT_TAB) next.delete('tab'); else next.set('tab', tab);
      return next;
    }, { replace: true });
  };

  if (isLoading) return <PageLoading />;
  if (!teacher) return <Navigate to="/admin/teachers" replace />;

  const fields: DetailField<Teacher>[] = [
    { key: 'id', label: 'ID' },
    { key: 'email', label: 'Email' },
    { key: 'phone', label: 'Телефон' },
    { key: 'active', label: 'Статус', cell: (r) => r.active ? 'Активен' : 'Архив' },
    { key: 'created_at', label: 'Добавлен', cell: (r) => fmtDate(r.created_at) },
  ];

  const handleDelete = async () => {
    try {
      await muts.remove.mutateAsync(teacher.id);
      toast('Архивировано', 'ok');
      navigate('/admin/teachers');
    } catch (err) { showError(err); }
  };

  const handleRestore = async () => {
    try {
      await muts.update.mutateAsync({ id: teacher.id, body: { active: true } });
      toast('Разархивировано', 'ok');
    } catch (err) { showError(err); }
  };

  const activeGroups = groups.filter((g) => g.active);
  const directionsCount = new Set(activeGroups.map((g) => g.direction_id)).size;
  const contacts = [teacher.email, teacher.phone].filter(Boolean).join(' · ');

  // У преподавателя нет направления, откуда EntityHero берёт цвет у группы, —
  // берём детерминированный тон из имени (та же формула, что у Avatar).
  const customHero = (
    <EntityHero
      monogram={monogramOf(teacher.name)}
      color={nameColor(teacher.name)}
      title={teacher.name}
      badge={
        teacher.active
          ? <span className="status-badge status-badge--positive">Активен</span>
          : <span className="status-badge status-badge--muted">Архив</span>
      }
      meta={
        <>
          <HeroChip mono>#{teacher.id}</HeroChip>
          {directionsCount > 0 && <HeroChip>{directionsCount} напр.</HeroChip>}
          <HeroChip mono>
            {activeGroups.length} {plural(activeGroups.length, 'группа', 'группы', 'групп')}
          </HeroChip>
          {contacts && <HeroChip>{contacts}</HeroChip>}
        </>
      }
      actions={
        canWrite ? (
          <>
            <button type="button" className="edit-btn" onClick={() => setEditing(true)}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
              </svg>
              Редактировать
            </button>
            <ActionMenu
              items={teacher.active
                // Архивация необратима одним кликом — подтверждаем диалогом
                // (в DetailShell эту роль играло состояние «Точно?» у кнопки).
                ? [{ label: 'Архивировать преподавателя', onSelect: () => setConfirmArchive(true), danger: true }]
                : [{ label: 'Разархивировать преподавателя', onSelect: () => { void handleRestore(); } }]}
            />
          </>
        ) : undefined
      }
      facts={[
        { label: 'В школе с', value: fmtDate(teacher.created_at) },
        {
          label: 'Последнее занятие',
          value: stats?.last_lesson_date ? fmtDate(stats.last_lesson_date) : '—',
        },
      ]}
      aside={<TeacherTelegramBlock teacher={teacher} />}
    />
  );

  const tabs: TabItem[] = [
    {
      value: 'overview',
      label: 'Обзор',
      content: (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
          <TeacherDirectionsBreakdown stats={stats} />
          <TeacherMonthDetails stats={stats} />
        </div>
      ),
    },
    {
      value: 'groups',
      label: 'Группы',
      content: (
        <TeacherGroupsBlock groups={groups} progress={stats?.group_progress ?? []} />
      ),
    },
    {
      value: 'data',
      label: 'Данные',
      content: <EntityCard title="Данные преподавателя" row={teacher} fields={fields} />,
    },
  ];

  if (canSeeChangelog(me?.role as Role)) {
    tabs.push({
      value: 'history',
      label: 'История',
      content: <EntityChangelogPanel entity="teacher" entityId={teacher.id} />,
    });
  }

  return (
    <>
      <DetailShell<Teacher>
        title={teacher.name}
        row={teacher}
        fields={fields}
        cardTitle="Данные преподавателя"
        customHero={customHero}
        backTo="/admin/teachers"
        parentLabel="Преподаватели"
        hideCard
      >
        <TeacherStatsRow
          month={month}
          onMonthChange={setMonth}
          stats={stats}
          groups={groups}
        />
        <TeacherAttentionBar stats={stats} />
        <Tabs items={tabs} value={activeTab} onChange={setActiveTab} />
      </DetailShell>
      {editing && (
        <TeacherFormModal initial={teacher} onClose={() => setEditing(false)} />
      )}
      <Dialog
        open={confirmArchive}
        onOpenChange={setConfirmArchive}
        title="Архивировать преподавателя?"
        footer={
          <>
            <button type="button" className="btn-cancel" onClick={() => setConfirmArchive(false)}>
              Отмена
            </button>
            <button
              type="button"
              className="btn-delete is-confirming"
              onClick={() => { setConfirmArchive(false); void handleDelete(); }}
            >
              Архивировать
            </button>
          </>
        }
      >
        <p className="status-form__hint">
          Преподаватель «{teacher.name}» уйдёт в архив: пропадёт из списков и подборов, но
          данные и история занятий сохранятся. Разархивировать можно здесь же.
        </p>
      </Dialog>
    </>
  );
}
