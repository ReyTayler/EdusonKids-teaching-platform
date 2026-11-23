import { useState } from 'react';
import { useParams, Navigate, useSearchParams } from 'react-router-dom';
import { useStudent, useStudentMutations } from '../../hooks/useStudents';
import { useGroupsAll } from '../../hooks/useGroups';
import { useDirections } from '../../hooks/useDirections';
import { useMemberships } from '../../hooks/useMemberships';
import { useApiError } from '../../hooks/useApiError';
import { DetailShell, EntityCard, type DetailField } from '../../components/detail/DetailShell';
import { EntityHero, HeroChip, monogramOf, type HeroFact } from '../../components/detail/EntityHero';
import { ActionMenu, type ActionMenuItem } from '../../components/ui/ActionMenu';
import { StudentStageBadge } from '../../components/StudentStageBadge';
import { PageLoading } from '../../components/ui/Skeleton';
import { Tabs, type TabItem } from '../../components/ui/Tabs';
import { Dialog } from '../../components/ui/Dialog';
import { Field } from '../../components/form/Field';
import { useToast } from '../../components/ui/Toast';
import { usePaymentModal } from '../../providers/PaymentModalProvider';
import { fmtDate, fmtAge } from '../../lib/format';
import type { Student } from '../../lib/types';
import StudentFormModal from './StudentFormModal';
import StudentLearningBlock from './StudentLearningBlock';
import StudentLessonsTable from '../../components/lessons/StudentLessonsTable';
import StudentKpiRow from './StudentKpiRow';
import { StudentBalanceBlock } from './StudentBalanceBlock';
import { EntityCommentsBlock } from '../../components/comments/EntityCommentsBlock';
import { LastCommentCard } from '../../components/comments/LastCommentCard';
import StudentTasksBlock from './StudentTasksBlock';
import { useLatestEntityComment } from '../../hooks/useEntityComments';
import { useAuth } from '../../hooks/useAuth';
import { canDeleteStudentComments, canSeeChangelog, canWriteStudentManager, canWritePayments, type Role } from '../../lib/permissions';
import { EntityChangelogPanel } from '../../components/changelog/EntityChangelogPanel';
import { useRenewalAssignees } from '../../hooks/useRenewals';
import { SelectInput } from '../../components/form/SelectInput';

// ── Диалог смены ответственного менеджера — только admin/superadmin.
// Меняет Student.manager И синхронно активную (открытую) сделку продления
// ученика (assignee) — закрытые сделки сохраняют исторического ответственного. ──
function StudentManagerDialog({ student, onClose }: { student: Student; onClose: () => void }) {
  const { data: assignees } = useRenewalAssignees();
  const muts = useStudentMutations();
  const showError = useApiError();
  const { toast } = useToast();
  const [managerId, setManagerId] = useState<string>(student.manager_id != null ? String(student.manager_id) : '');

  const handleSave = async () => {
    try {
      await muts.setManager.mutateAsync({
        id: student.id,
        managerId: managerId ? Number(managerId) : null,
      });
      toast('Менеджер обновлён', 'ok');
      onClose();
    } catch (err) {
      showError(err, 'Не удалось сменить менеджера');
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Сменить менеджера ученика" help={<>Смена менеджера сразу переставит ответственного в активной сделке продления
          этого ученика в разделе «Продления». Закрытые сделки не меняются.</>}>
      <div className="status-form">
        <Field label="Менеджер">
          <SelectInput
            value={managerId}
            onChange={(e) => setManagerId(e.target.value)}
            options={[
              { value: '', label: '— не назначен —' },
              ...(assignees || []).map((a) => ({ value: String(a.id), label: a.full_name })),
            ]}
          />
        </Field>

        <div className="status-form__footer">
          <button type="button" className="btn-cancel" onClick={onClose}>Отмена</button>
          <button
            type="button"
            className="btn-save"
            onClick={handleSave}
            disabled={muts.setManager.isPending}
          >
            Сохранить
          </button>
        </div>
      </div>
    </Dialog>
  );
}

const STUDENT_TABS = ['learning', 'lessons', 'finance', 'tasks', 'comments', 'history'] as const;
type StudentTab = (typeof STUDENT_TABS)[number];
const DEFAULT_TAB: StudentTab = 'learning';

function isStudentTab(value: string | null): value is StudentTab {
  return !!value && (STUDENT_TABS as readonly string[]).includes(value);
}

export default function StudentDetailPage() {
  const params = useParams();
  const id = Number(params.id);
  const { data: student, isLoading } = useStudent(id);
  const { me } = useAuth();
  const { data: groups = [] } = useGroupsAll(true);
  const { data: directions = [] } = useDirections(true);
  const { data: lastComment } = useLatestEntityComment('students', id);
  const { data: activeMemberships = [] } = useMemberships({ student_id: id });
  const [editing, setEditing] = useState(false);
  const [managingManager, setManagingManager] = useState(false);
  const { open: openPaymentModal } = usePaymentModal();
  const [searchParams, setSearchParams] = useSearchParams();

  const rawTab = searchParams.get('tab');
  const activeTab: StudentTab = isStudentTab(rawTab) ? rawTab : DEFAULT_TAB;
  const setActiveTab = (tab: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (tab === DEFAULT_TAB) next.delete('tab'); else next.set('tab', tab);
      return next;
    }, { replace: true });
  };

  if (isLoading) return <PageLoading />;
  if (!student) return <Navigate to="/admin/students" replace />;

  // Цвет личности ученика — устойчивый хеш имени (тот же приём, что в Avatar).
  const identityColor = `hsl(${[...String(student.full_name || '')]
    .reduce((a, c) => a + c.charCodeAt(0), 0) % 360}, 52%, 45%)`;

  // Короткие факты справа: раньше это были акцентно-синие «пилюли» — цвет акцента
  // тратился на декорацию, хотя по токенам он зарезервирован за смыслом.
  const facts: HeroFact[] = [];
  const ageLabel = fmtAge(student.birth_date);
  if (ageLabel !== '—') facts.push({ label: 'Возраст', value: ageLabel });
  if (student.parent1_name) facts.push({ label: 'Родитель', value: student.parent1_name });
  if (student.parent1_phone) {
    facts.push({
      label: 'Телефон',
      value: <a href={`tel:${student.parent1_phone.replace(/[^\d+]/g, '')}`}>{student.parent1_phone}</a>,
    });
  }
  facts.push({ label: 'Менеджер', value: student.manager_name || '—' });

  // Вторичные действия уезжают в «…», в шапке остаются оплата и «Редактировать».
  // Заморозка и уход больше не действия ученика — это переходы его сделки
  // продления (спека 2026-07-25), они живут в разделе «Продления». Пустой список
  // ActionMenu не рендерит вовсе.
  const menuItems: ActionMenuItem[] = [];
  if (canWriteStudentManager(me?.role as Role)) {
    menuItems.push({ label: 'Сменить менеджера', onSelect: () => setManagingManager(true) });
  }

  const customHero = (
    <EntityHero
      monogram={monogramOf(student.full_name)}
      color={identityColor}
      title={student.full_name}
      badge={<StudentStageBadge row={student} />}
      meta={
        <>
          <HeroChip mono>id {student.id}</HeroChip>
          {activeMemberships.slice(0, 3).map((m) => (
            <HeroChip key={m.id}>{m.group_name || `#${m.group_id}`}</HeroChip>
          ))}
          {activeMemberships.length > 3 && (
            <HeroChip mono>+{activeMemberships.length - 3}</HeroChip>
          )}
        </>
      }
      actions={
        <>
          {canWritePayments(me?.role as Role) && (
            <button type="button" className="btn-save" onClick={() => openPaymentModal({ studentId: student.id })}>
              + Внести оплату
            </button>
          )}
          <button type="button" className="edit-btn" onClick={() => setEditing(true)}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
            </svg>
            Редактировать
          </button>
          <ActionMenu items={menuItems} />
        </>
      }
      facts={facts}
      aside={lastComment && (
        <LastCommentCard comment={lastComment} onOpen={() => setActiveTab('comments')} />
      )}
    />
  );

  // «Данные ученика» показывают ВСЕ поля формы ученика (пустые — «—»), плюс
  // менеджера и дату создания. Совпадение с шапкой здесь намеренное.
  const dash = (v: string | null) => v || '—';
  const fields: DetailField<Student>[] = [
    { key: 'full_name', label: 'Имя ученика' },
    { key: 'birth_date', label: 'Дата рождения', cell: (r) => fmtDate(r.birth_date) },
    { key: 'phone', label: 'Телефон ученика', cell: (r) => dash(r.phone) },
    { key: 'telegram', label: 'Telegram ученика', cell: (r) => dash(r.telegram) },
    { key: 'email', label: 'Почта ученика', cell: (r) => dash(r.email) },
    { key: 'parent1_name', label: 'Имя родителя 1', cell: (r) => dash(r.parent1_name) },
    { key: 'parent1_phone', label: 'Телефон родителя 1', cell: (r) => dash(r.parent1_phone) },
    { key: 'parent1_email', label: 'Почта родителя 1', cell: (r) => dash(r.parent1_email) },
    { key: 'parent1_telegram', label: 'Telegram родителя 1', cell: (r) => dash(r.parent1_telegram) },
    { key: 'parent2_name', label: 'Имя родителя 2', cell: (r) => dash(r.parent2_name) },
    { key: 'parent2_phone', label: 'Телефон родителя 2', cell: (r) => dash(r.parent2_phone) },
    { key: 'parent2_email', label: 'Почта родителя 2', cell: (r) => dash(r.parent2_email) },
    { key: 'platform_id', label: 'Platform ID', cell: (r) => dash(r.platform_id) },
    { key: 'crm_link', label: 'Ссылка в CRM', cell: (r) => dash(r.crm_link) },
    { key: 'manager_name', label: 'Менеджер', cell: (r) => r.manager_name || '—' },
    { key: 'created_at', label: 'Создан', cell: (r) => fmtDate(r.created_at) },
  ];

  const tabs: TabItem[] = [
    {
      value: 'learning',
      label: 'Обучение',
      content: (
        <div className="student-learning">
          <StudentLearningBlock
            studentId={student.id}
            groups={groups}
            directions={directions}
          />
          {/* Все поля ученика — под основным содержимым, всегда раскрыты. */}
          <EntityCard title="Данные ученика" row={student} fields={fields} collapsible={false} />
        </div>
      ),
    },
    {
      value: 'lessons',
      label: 'Уроки',
      content: <StudentLessonsTable studentId={student.id} />,
    },
    {
      value: 'finance',
      label: 'Финансы',
      content: <StudentBalanceBlock studentId={student.id} />,
    },
    {
      value: 'tasks',
      label: 'Задачи',
      content: <StudentTasksBlock studentId={student.id} />,
    },
    {
      value: 'comments',
      label: 'Комментарии',
      content: (
        <EntityCommentsBlock
          key={student.id}
          entity="students"
          entityId={student.id}
          canDelete={canDeleteStudentComments(me?.role as Role)}
          placeholder="Оставьте комментарий об ученике…"
          emptyHint="Здесь появятся заметки об ученике"
        />
      ),
    },
  ];

  if (canSeeChangelog(me?.role as Role)) {
    tabs.push({
      value: 'history',
      label: 'История',
      content: <EntityChangelogPanel entity="student" entityId={student.id} />,
    });
  }

  return (
    <>
      <DetailShell<Student>
        title={student.full_name}
        row={student}
        fields={fields}
        cardTitle="Данные ученика"
        customHero={customHero}
        backTo="/admin/students"
        parentLabel="Ученики"
        hideCard
      >
        <StudentKpiRow studentId={student.id} />
        <Tabs items={tabs} value={activeTab} onChange={setActiveTab} />
      </DetailShell>
      {editing && (
        <StudentFormModal initial={student} onClose={() => setEditing(false)} />
      )}
      {managingManager && (
        <StudentManagerDialog student={student} onClose={() => setManagingManager(false)} />
      )}
    </>
  );
}
