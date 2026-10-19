import { useDeferredValue, useState } from 'react';
import { useListSearchParams } from '../../hooks/useListSearchParams';
import { useExtraLessons, useExtraLessonMutations } from '../../hooks/useExtraLessons';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { DataTable, type Column } from '../../components/table/DataTable';
import { TableSkeleton } from '../../components/ui/Skeleton';
import { ActionMenu, type ActionMenuItem } from '../../components/ui/ActionMenu';
import { ConfirmModal } from '../../components/ui/ConfirmModal';
import { AssignExtraLessonModal } from '../../components/lessons/AssignExtraLessonModal';
import { ManualExtraLessonModal } from '../../components/lessons/ManualExtraLessonModal';
import { fmtDate } from '../../lib/format';
import type { AbsenceResolution } from '../../lib/types';
import { PageHeader } from '../../components/shell/PageHeader';
import { useAuth } from '../../hooks/useAuth';
import { canDeleteExtraLessonRequest, canRollbackExtraLesson, type Role } from '../../lib/permissions';

/** Подсказка на пункте меню, закрытом ролью (менеджеру эти операции недоступны). */
const NO_RIGHTS_HINT = 'Обратитесь к администратору';

const STATUS_LABELS: Record<string, string> = {
  pending: 'Ждёт решения',
  makeup_scheduled: 'Назначен',
  makeup_done: 'Проведён',
  burned: 'Сгорел',
  // Действие «закрыть без денег» снято, но статус остаётся: в БД есть старые
  // waived-записи, которые надо отображать и фильтровать. Новых не создать.
  waived: 'Закрыт без денег',
};

const STATUS_OPTIONS = Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label }));

/**
 * Действие, требующее подтверждения. Раньше подтверждали вторым нажатием на ту
 * же кнопку («Точно сжечь?»), но теперь действия живут в меню «…», а меню
 * закрывается по выбору пункта — спрашивать приходится отдельным окном.
 */
type PendingAction =
  | { type: 'burn'; row: AbsenceResolution }
  | { type: 'rollback'; row: AbsenceResolution }
  | { type: 'deleteRequest'; row: AbsenceResolution };

function confirmConfig(action: PendingAction): {
  title: string; message: string; confirmLabel: string; danger?: boolean;
} {
  const who = action.row.student_name;
  switch (action.type) {
    case 'burn':
      return {
        title: 'Сжечь пропуск?',
        message: `Урок спишется с баланса ${who}, преподавателю начислится оплата за сгорание. Отработки не будет.`,
        confirmLabel: 'Сжечь',
        danger: true,
      };
    case 'rollback':
      return {
        title: action.row.status === 'burned' ? 'Откатить сгорание?' : 'Откатить доп.урок?',
        message: `Урок вернётся на баланс ${who}, зарплата преподавателя за него снимется, пропуск снова уйдёт в «Ждёт решения».`,
        confirmLabel: 'Откатить',
        danger: true,
      };
    case 'deleteRequest':
      return {
        title: 'Удалить заявку?',
        message: `Заявка на доп.урок для ${who} будет удалена из базы безвозвратно. Денег и занятий за ней нет, но и в очереди на разбор этот пропуск больше не появится — восстановить можно только через «Журнал изменений».`,
        confirmLabel: 'Удалить',
        danger: true,
      };
  }
}

export default function ExtraLessonsListPage() {
  // Порядок по умолчанию — очередь разбора: «Ждёт решения» сверху, внутри блока
  // свежие заявки первыми. Это не колонка таблицы, поэтому стрелки сортировки ни
  // у одного заголовка не горят; щелчок по любому из них переключает список на
  // обычную сортировку и группировку по статусу снимает (так и задумано).
  // Значение обязано совпадать с repository.QUEUE_ORDER на бэкенде.
  const search = useListSearchParams({ sortBy: 'pending_first', sortDir: 'desc' });
  const { page, pageSize, sortBy, sortDir, filters, setPage, setPageSize, setSort, setFilters } = search;
  const debouncedFilters = useDeferredValue(filters);

  const { data, isLoading, isFetching } = useExtraLessons({
    page, page_size: pageSize, sort_by: sortBy, sort_dir: sortDir, filters: debouncedFilters,
  });
  const muts = useExtraLessonMutations();
  const showError = useApiError();
  const { toast } = useToast();
  const { me } = useAuth();
  const role = me?.role as Role;
  // Откат факта двигает баланс ученика и зарплату, удаление заявки необратимо —
  // и то, и другое только админ/суперадмин. Менеджеру пункты меню оставляем на
  // месте, но неактивными: иначе состав меню молча меняется от роли к роли.
  const canRollback = canRollbackExtraLesson(role);
  const canDeleteRequest = canDeleteExtraLessonRequest(role);
  // pending → назначить доп.урок (модалка).
  const [assigning, setAssigning] = useState<AbsenceResolution | null>(null);
  // Ручной доп.урок сверх курса (kind='extra') — открывается кнопкой в шапке.
  const [manualOpen, setManualOpen] = useState(false);
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);

  const rows: AbsenceResolution[] = data?.rows || [];
  const total = data?.total || 0;

  const handleCancel = async (id: number) => {
    try {
      await muts.cancel.mutateAsync(id);
      toast('Назначение отменено, пропуск снова ждёт решения', 'ok');
    } catch (err) { showError(err); }
  };

  const isConfirmPending = muts.burn.isPending || muts.remove.isPending;

  const handleConfirm = async () => {
    if (!pendingAction) return;
    const { type, row } = pendingAction;
    try {
      if (type === 'burn') {
        await muts.burn.mutateAsync(row.id);
        toast('Пропуск сожжён, урок списан с баланса', 'ok');
      } else if (type === 'rollback') {
        // remove = DELETE /extra-lessons/:id — на бэке откатывает и проведённый
        // доп.урок (makeup_done), и сгорание (burned).
        await muts.remove.mutateAsync(row.id);
        toast('Факт удалён, пропуск снова ждёт решения', 'ok');
      } else {
        // Тот же DELETE, но по заявке в статусе pending — там бэк удаляет строку целиком.
        await muts.remove.mutateAsync(row.id);
        toast('Заявка удалена', 'ok');
      }
    } catch (err) {
      showError(err);
    }
    setPendingAction(null);
  };

  /** Пункты меню «…» для строки — набор зависит от статуса резолюции. */
  const menuItems = (r: AbsenceResolution): ActionMenuItem[] => {
    if (r.status === 'pending') {
      return [
        { label: 'Назначить доп.урок', onSelect: () => setAssigning(r) },
        { label: 'Сжечь', onSelect: () => setPendingAction({ type: 'burn', row: r }) },
        {
          label: 'Удалить заявку',
          danger: true,
          disabled: !canDeleteRequest,
          hint: canDeleteRequest ? undefined : NO_RIGHTS_HINT,
          onSelect: () => setPendingAction({ type: 'deleteRequest', row: r }),
        },
      ];
    }
    if (r.status === 'makeup_scheduled') {
      return [{ label: 'Отменить назначение', onSelect: () => { void handleCancel(r.id); } }];
    }
    if (r.status === 'makeup_done' || r.status === 'burned') {
      return [{
        label: r.status === 'burned' ? 'Откатить сгорание' : 'Откатить доп.урок',
        danger: true,
        disabled: !canRollback,
        hint: canRollback ? undefined : NO_RIGHTS_HINT,
        onSelect: () => setPendingAction({ type: 'rollback', row: r }),
      }];
    }
    return [];
  };

  const columns: Column<AbsenceResolution>[] = [
    { key: 'scheduled_date', label: 'Дата доп.урока', sortable: true, searchable: false, cell: (r) => (r.scheduled_date ? fmtDate(r.scheduled_date) : '—') },
    {
      key: 'missed_lesson_group_name', label: 'Группа', sortable: false, searchable: true,
      cell: (r) => r.resolution_group_name || r.missed_lesson_group_name || '—',
    },
    {
      key: 'missed_lesson', label: 'За какой урок', sortable: false, searchable: false,
      cell: (r) => {
        // extra (сверх курса): пропуска нет — показываем выбранный номер (или «—»)
        // с пометкой «доп.»; makeup — номер+дата пропущенного урока.
        if (r.kind === 'extra') {
          return r.target_lesson_number != null
            ? `Урок №${Number(r.target_lesson_number)} · доп.`
            : 'Доп.урок сверх курса';
        }
        return `Урок №${Number(r.missed_lesson_number)} · ${fmtDate(r.missed_lesson_date ?? '')}`;
      },
    },
    { key: 'teacher_name', label: 'Преподаватель', sortable: true, searchable: false, cell: (r) => r.teacher_name || '—' },
    { key: 'student_name', label: 'Ученик', sortable: true, searchable: true },
    {
      key: 'status', label: 'Статус', sortable: true, searchable: true,
      searchOptions: STATUS_OPTIONS,
      cell: (r) => STATUS_LABELS[r.status] || r.status,
    },
    {
      // Все действия строки — в одном меню «…»: ряд разноцветных кнопок, меняв-
      // шийся от статуса к статусу, забивал таблицу и мешал читать сами данные.
      key: 'actions', label: '', sortable: false, searchable: false,
      cell: (r) => (
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <ActionMenu items={menuItems(r)} label={`Действия: ${r.student_name}`} />
        </div>
      ),
    },
  ];

  // Шапка рисуется и во время загрузки: раньше страница возвращала
  // скелетон ДО неё, и заголовок пропадал при каждом переходе.
  const header = (
    <PageHeader
      title="Доп.уроки"
      count={isLoading ? undefined : total}
      sub="Незакрытые пропуски: назначить отработку или сжечь урок."
      actions={
        <button type="button" className="btn-add" onClick={() => setManualOpen(true)}>
          + Назначить вручную
        </button>
      }
    />
  );

  if (isLoading) return <>{header}<TableSkeleton rows={8} cols={columns.length} /></>;

  return (
    <>
      {header}
      <DataTable<AbsenceResolution>
        data={rows}
        columns={columns}
        title="Доп.уроки"
        isLoading={isFetching}
        // Нерешённые пропуски — красным на всю строку: это очередь на разбор,
        // и её видно до того, как глаз дошёл до колонки «Статус».
        rowClassName={(r) => (r.status === 'pending' ? 'row--danger' : undefined)}
        serverPagination={{
          page, pageSize, total, sortBy, sortDir, filters,
          onPageChange: setPage, onPageSizeChange: setPageSize,
          onSortChange: setSort, onFiltersChange: setFilters,
        }}
      />
      {assigning && assigning.missed_lesson_id != null && (
        <AssignExtraLessonModal
          missedLessonId={assigning.missed_lesson_id}
          candidates={[{ student_id: assigning.student_id, student_name: assigning.student_name }]}
          defaultTeacherId={assigning.assigned_teacher_id ?? 0}
          onClose={() => setAssigning(null)}
        />
      )}
      {manualOpen && <ManualExtraLessonModal onClose={() => setManualOpen(false)} />}
      {pendingAction && (
        <ConfirmModal
          {...confirmConfig(pendingAction)}
          isPending={isConfirmPending}
          onConfirm={() => { void handleConfirm(); }}
          onClose={() => setPendingAction(null)}
        />
      )}
    </>
  );
}
