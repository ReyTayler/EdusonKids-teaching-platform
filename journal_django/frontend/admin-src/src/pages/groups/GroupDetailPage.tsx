import { useRef, useState } from 'react';
import { useParams, Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { useGroup, useGroupMutations } from '../../hooks/useGroups';
import { useTeachers } from '../../hooks/useTeachers';
import { useDirections } from '../../hooks/useDirections';
import { DetailShell, EntityCard, type DetailField } from '../../components/detail/DetailShell';
import { EntityHero, HeroChip, monogramOf } from '../../components/detail/EntityHero';
import { ActionMenu } from '../../components/ui/ActionMenu';
import { Avatar } from '../../components/Avatar';
import { DirTag } from '../../components/ui/DirTag';
import { EntityLink } from '../../components/EntityLink';
import { PageLoading } from '../../components/ui/Skeleton';
import { Tabs, type TabItem } from '../../components/ui/Tabs';
import { Dialog } from '../../components/ui/Dialog';
import { LessonGrid } from '../../components/lessons/LessonGrid';
import { LessonEditor } from '../../components/lessons/LessonEditor';
import { directionColor } from '../../lib/direction-color';
import { fmtDate } from '../../lib/format';
import { formatSlot } from '../../lib/slots';
import { useToast } from '../../components/ui/Toast';
import { useApiError } from '../../hooks/useApiError';
import { useGroupPlanCalendar } from '../../hooks/useGroupPlanCalendar';
import { CalendarView, type CalendarViewMode } from '../../shared/calendar/CalendarView';
import { useAuth } from '../../hooks/useAuth';
import { canSeeChangelog, canArchiveEntities, canSeeGroupLessonsTab, type Role } from '../../lib/permissions';
import { EntityChangelogPanel } from '../../components/changelog/EntityChangelogPanel';
import type { Group } from '../../lib/types';
import GroupFormModal from './GroupFormModal';
import GroupMembersBlock from './GroupMembersBlock';
import GroupScheduleBlock from './GroupScheduleBlock';
import GroupPlanActions, { type GroupPlanActionsHandle } from './GroupPlanActions';
import GroupPlanHealthBlock from './GroupPlanHealthBlock';
import GroupPlanTable from './GroupPlanTable';
import GroupProgressBlock from './GroupProgressBlock';
import GroupKpiRow from './GroupKpiRow';

/**
 * План группы показываем только помесячно: недельная сетка здесь избыточна, а
 * «Список» не выбирают руками — он включается адаптивом на узком экране.
 * Константа вне компонента: стабильная ссылка, иначе новый массив на каждый
 * рендер. Разделы «Календарь» (admin/teacher) виды не ограничивают.
 */
const GROUP_PLAN_VIEWS: CalendarViewMode[] = ['month'];

const GROUP_TABS = ['overview', 'students', 'lessons', 'progress', 'schedule', 'history'] as const;
type GroupTab = (typeof GROUP_TABS)[number];
const DEFAULT_TAB: GroupTab = 'overview';

function isGroupTab(value: string | null): value is GroupTab {
  return !!value && (GROUP_TABS as readonly string[]).includes(value);
}

export default function GroupDetailPage() {
  const params = useParams();
  const id = Number(params.id);
  const navigate = useNavigate();
  const { data: group, isLoading } = useGroup(id);
  const { me } = useAuth();
  const { data: teachers = [] } = useTeachers(true);
  const { data: directions = [] } = useDirections(true);
  const muts = useGroupMutations();
  const { toast } = useToast();
  const showError = useApiError();
  const [editing, setEditing] = useState(false);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const [selected, setSelected] = useState<{ slot: number; lessonId: number | null } | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const planActionsRef = useRef<GroupPlanActionsHandle>(null);

  // Направление/преподаватель считаем ДО ранних return — нужны для
  // useGroupPlanCalendar, который обязан вызываться безусловно (Rules of
  // Hooks); хук сам справляется с group===undefined (пока группа грузится).
  const direction = group ? directions.find((d) => d.id === group.direction_id) || null : null;
  const teacher = group ? teachers.find((t) => t.id === group.teacher_id) || null : null;
  const planCalendar = useGroupPlanCalendar(group, direction, teachers);

  const showLessonsTab = canSeeGroupLessonsTab(me?.role as Role);
  const rawTab = searchParams.get('tab');
  const requestedTab: GroupTab = isGroupTab(rawTab) ? rawTab : DEFAULT_TAB;
  // ?tab=lessons из закладки/ссылки не должен открывать скрытую вкладку.
  const activeTab: GroupTab = requestedTab === 'lessons' && !showLessonsTab
    ? DEFAULT_TAB
    : requestedTab;
  const setActiveTab = (tab: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (tab === DEFAULT_TAB) next.delete('tab'); else next.set('tab', tab);
      return next;
    }, { replace: true });
  };

  if (isLoading) return <PageLoading />;
  if (!group) return <Navigate to="/admin/groups" replace />;

  const color = directionColor(direction);

  const fields: DetailField<Group>[] = [
    { key: 'id', label: 'ID' },
    { key: 'name', label: 'Название' },
    { key: 'direction_id', label: 'Направление',
      cell: () => direction ? <DirTag direction={direction} /> : <>#{group.direction_id}</> },
    { key: 'teacher_id', label: 'Преподаватель',
      cell: () => <EntityLink section="teachers" id={group.teacher_id} text={teacher?.name || `#${group.teacher_id}`} /> },
    { key: 'is_individual', label: 'Индивидуальная', cell: (r) => r.is_individual ? 'да' : 'нет' },
    { key: 'lesson_duration_minutes', label: 'Длительность урока', cell: (r) => `${r.lesson_duration_minutes} мин` },
    { key: 'lessons_per_week', label: 'Уроков в неделю' },
    { key: 'lessons_total', label: 'Уроков в группе',
      cell: (r) => r.lessons_total != null
        ? `${r.lessons_total} (задано вручную)`
        : 'как в направлении' },
    { key: 'group_start_date', label: 'Дата старта', cell: (r) => fmtDate(r.group_start_date) },
    { key: 'slots', label: 'Расписание', cell: (r) => (r.slots || []).map(formatSlot).join(', ') || '—' },
    { key: 'vk_chat', label: 'Чат ВК' },
    { key: 'active', label: 'Статус', cell: (r) => r.active ? 'Активна' : 'Архив' },
    { key: 'created_at', label: 'Создана', cell: (r) => fmtDate(r.created_at) },
  ];

  const handleDelete = async () => {
    try {
      await muts.remove.mutateAsync(group.id);
      toast('Архивировано', 'ok');
      navigate('/admin/groups');
    } catch (err) { showError(err); }
  };

  const handleRestore = async () => {
    try {
      await muts.update.mutateAsync({ id: group.id, body: { active: true } });
      toast('Разархивировано', 'ok');
    } catch (err) { showError(err); }
  };

  const slotsLabel = (group.slots || []).map(formatSlot).join(' · ');

  // Шапка группы: раньше это был дефолтный заголовок DetailShell (имя + строка
  // «#90 · Групповая · 90 мин»), тогда как у ученика — своя богатая карточка.
  // Теперь обе страницы используют один EntityHero.
  const customHero = (
    <EntityHero
      monogram={monogramOf(direction?.name || group.name)}
      color={color}
      title={group.name}
      badge={
        group.active
          ? <span className="status-badge status-badge--positive">Активна</span>
          : <span className="status-badge status-badge--muted">Архив</span>
      }
      meta={
        <>
          <HeroChip mono>#{group.id}</HeroChip>
          {direction && <HeroChip>{direction.name}</HeroChip>}
          <HeroChip>{group.is_individual ? 'Индивидуальная' : 'Групповая'}</HeroChip>
          <HeroChip mono>{group.lesson_duration_minutes} мин</HeroChip>
          {slotsLabel && <HeroChip mono>{slotsLabel}</HeroChip>}
        </>
      }
      actions={
        <>
          <button type="button" className="edit-btn" onClick={() => setEditing(true)}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
            </svg>
            Редактировать
          </button>
          {canArchiveEntities(me?.role as Role) && (
            <ActionMenu
              items={group.active
                // Архивация необратима одним кликом — подтверждаем диалогом
                // (в DetailShell эту роль играло состояние «Точно?» у кнопки).
                ? [{ label: 'Архивировать группу', onSelect: () => setConfirmArchive(true), danger: true }]
                : [{ label: 'Разархивировать группу', onSelect: () => { void handleRestore(); } }]}
            />
          )}
        </>
      }
      facts={[
        { label: 'Старт курса', value: fmtDate(group.group_start_date) },
        { label: 'В неделю', value: `${group.lessons_per_week} зан.` },
        ...(group.vk_chat
          ? [{ label: 'Чат ВК', value: <a href={group.vk_chat} target="_blank" rel="noreferrer">открыть</a> }]
          : []),
      ]}
      // Преподаватель — не строка в списке фактов, а отдельная кликабельная
      // карточка: это второй по важности объект на странице после самой группы.
      aside={
        <div
          className="teacher-info-card teacher-info-card--link"
          role="link"
          tabIndex={0}
          onClick={() => navigate(`/admin/teachers/${group.teacher_id}`)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              navigate(`/admin/teachers/${group.teacher_id}`);
            }
          }}
        >
          <Avatar name={teacher?.name || '?'} size={34} />
          <div style={{ minWidth: 0 }}>
            <div className="teacher-info-card__name">{teacher?.name || `#${group.teacher_id}`}</div>
            {teacher?.email && <div className="teacher-info-card__mail">{teacher.email}</div>}
          </div>
        </div>
      }
    />
  );

  const tabs: TabItem[] = [
    {
      value: 'overview',
      label: 'Обзор',
      content: (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
          <EntityCard title="Данные группы" row={group} fields={fields} />
          <GroupPlanTable groupId={group.id} />
        </div>
      ),
    },
    {
      value: 'students',
      label: 'Ученики',
      content: <GroupMembersBlock group={group} />,
    },
    // Уроки — только admin/superadmin: менеджер их не правит и вкладку не видит.
    ...(showLessonsTab ? [{
      value: 'lessons',
      label: 'Уроки',
      content: (
        <div className="detail__section">
          <div className="lesson-grid-hint">
            Серые — не проведены, цветные — проведены. Клик по любому квадрату — открыть/создать.
          </div>
          <LessonGrid
            group={group}
            selectedSlot={selected?.slot ?? null}
            onSelectSlot={(slot, lessonId) => setSelected({ slot, lessonId })}
          />
          {selected && (
            <LessonEditor
              group={group}
              slot={selected.slot}
              lessonId={selected.lessonId}
              color={color}
              onClose={() => setSelected(null)}
            />
          )}
        </div>
      ),
    }] : []),
    {
      value: 'progress',
      label: 'Прогресс',
      content: (
        <div className="detail__section">
          <div className="lesson-grid-hint">
            Посещаемость каждого ученика по урокам группы. Наведите на плитку — номер урока и дата.
          </div>
          <GroupProgressBlock groupId={group.id} />
        </div>
      ),
    },
    {
      value: 'schedule',
      label: 'Расписание',
      content: (
        <div className="detail__section" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
          <GroupPlanHealthBlock groupId={group.id} />
          <GroupPlanActions ref={planActionsRef} group={group} />
          <CalendarView
            occurrences={planCalendar.occurrences}
            unscheduled={[]}
            isLoading={planCalendar.isLoading}
            isError={planCalendar.isError}
            isFetching={planCalendar.isFetching}
            onVisibleRangeChange={() => {}}
            showKpi={false}
            views={GROUP_PLAN_VIEWS}
            role="admin"
            onAction={(kind, occ) => planActionsRef.current?.quickAction(kind, occ)}
          />
          <GroupScheduleBlock groupId={group.id} />
        </div>
      ),
    },
  ];

  if (canSeeChangelog(me?.role as Role)) {
    tabs.push({
      value: 'history',
      label: 'История',
      content: <EntityChangelogPanel entity="group" entityId={group.id} />,
    });
  }

  return (
    <>
      <DetailShell<Group>
        title={`Группа ${group.name}`}
        row={group}
        fields={fields}
        cardTitle="Данные группы"
        customHero={customHero}
        backTo="/admin/groups"
        parentLabel="Группы"
        hideCard
      >
        <GroupKpiRow groupId={group.id} isIndividual={group.is_individual} color={color} />
        <Tabs items={tabs} value={activeTab} onChange={setActiveTab} />
      </DetailShell>
      {editing && (
        <GroupFormModal initial={group} onClose={() => setEditing(false)} />
      )}
      <Dialog
        open={confirmArchive}
        onOpenChange={setConfirmArchive}
        title="Архивировать группу?"
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
          Группа «{group.name}» уйдёт в архив: пропадёт из списков и подборов, но данные
          и история занятий сохранятся. Разархивировать можно здесь же.
        </p>
      </Dialog>
    </>
  );
}
