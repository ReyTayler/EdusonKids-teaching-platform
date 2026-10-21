import { useMemo, useState } from 'react';
import { usePersonalRates, usePersonalRateMutations } from '../../hooks/useMotivation';
import { useTeachers } from '../../hooks/useTeachers';
import { useGroupsAll } from '../../hooks/useGroups';
import { Combobox } from '../../components/form/Combobox';
import { ConfirmModal } from '../../components/ui/ConfirmModal';
import { ActionMenu } from '../../components/ui/ActionMenu';
import { EmptyState } from '../../components/ui/EmptyState';
import { DirTag } from '../../components/ui/DirTag';
import { BlockLoading } from '../../components/ui/Skeleton';
import { useToast } from '../../components/ui/Toast';
import { useApiError } from '../../hooks/useApiError';
import { Paginator } from '../../components/table/Paginator';
import { PERSONAL_RATE_KIND_LABELS } from '../../lib/labels';
import type { PersonalRate } from '../../lib/types';
import { PersonalRateDialog } from './PersonalRateDialog';
import { lessonsRangeText, personalValueText, savedMessage } from './motivationFormat';

type DialogState = { editing: PersonalRate | null } | null;

/** Персональные ставки: ставка преподавателя в конкретной группе поверх ставок школы. */
export function PersonalRatesSection() {
  const [teacher, setTeacher] = useState('');
  const [group, setGroup] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [dialog, setDialog] = useState<DialogState>(null);
  const [deleting, setDeleting] = useState<PersonalRate | null>(null);

  const q = usePersonalRates({
    teacher, group, page, page_size: pageSize, sort_by: 'teacher', sort_dir: 'asc',
  });
  const { remove } = usePersonalRateMutations();
  const { toast } = useToast();
  const apiError = useApiError();
  const teachers = useTeachers(true);
  const groups = useGroupsAll(true);

  const teacherOptions = useMemo(() => [
    { value: '', label: 'Все преподаватели' },
    ...(teachers.data ?? []).map((t) => ({ value: String(t.id), label: t.name, muted: !t.active })),
  ], [teachers.data]);
  const groupOptions = useMemo(() => [
    { value: '', label: 'Все группы' },
    ...(groups.data ?? []).map((g) => ({ value: String(g.id), label: g.name, muted: !g.active })),
  ], [groups.data]);

  const confirmDelete = () => {
    if (!deleting) return;
    remove.mutate(deleting.id, {
      onSuccess: (res) => {
        toast(savedMessage(res.recalculated), 'ok');
        setDeleting(null);
        // Удалили последнюю строку не первой страницы — иначе останемся на пустой.
        if (rows.length === 1 && page > 1) setPage(page - 1);
      },
      onError: (e) => { apiError(e, 'Не удалось удалить'); setDeleting(null); },
    });
  };

  const rows = q.data?.rows ?? [];

  return (
    <section className="settings-section" aria-labelledby="personal-rates-title">
      <div className="settings-section__head">
        <h2 id="personal-rates-title" className="settings-section__title">Персональные ставки</h2>
        <button type="button" className="btn-add" onClick={() => setDialog({ editing: null })}>
          Добавить
        </button>
      </div>
      <p className="settings-section__lead">
        Ставка преподавателя в конкретной группе: вместо ставки школы или надбавка к ней.
      </p>

      <div className="filterbar settings-filters">
        <div className="filterbar__fields">
          <div className="filterbar__field settings-filters__field">
            <Combobox value={teacher} options={teacherOptions} placeholder="Все преподаватели"
              aria-label="Фильтр по преподавателю"
              onChange={(v) => { setTeacher(v); setPage(1); }} />
          </div>
          <div className="filterbar__field settings-filters__field">
            <Combobox value={group} options={groupOptions} placeholder="Все группы"
              aria-label="Фильтр по группе"
              onChange={(v) => { setGroup(v); setPage(1); }} />
          </div>
        </div>
      </div>

      {q.isLoading ? <BlockLoading rows={4} /> : q.isError ? (
        // Сбой загрузки не должен читаться как «ставок нет».
        <EmptyState
          hint="Это сбой загрузки, а не отсутствие ставок. Попробуйте ещё раз."
          action={<button type="button" className="btn-cancel" onClick={() => q.refetch()}>Повторить</button>}
        >
          Не удалось загрузить персональные ставки
        </EmptyState>
      ) : rows.length === 0 ? (
        <EmptyState hint={teacher || group ? 'Сбросьте фильтры или добавьте ставку.' : 'Все преподаватели получают ставки школы.'}>
          Персональных ставок нет
        </EmptyState>
      ) : (
        <div className="table-panel">
          <div className={`table-wrap${q.isPlaceholderData ? ' data-table--loading' : ''}`}>
            <table className="settings-table">
              <thead>
                <tr>
                  <th>Преподаватель</th><th>Группа</th><th>Направление</th><th>Уроки</th>
                  <th>Вид</th><th>Значение</th><th>Примечание</th><th aria-label="Действия" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td>{r.teacher_name}</td>
                    <td>{r.group_name}</td>
                    <td>{r.direction_name ? <DirTag name={r.direction_name} color={r.direction_color} /> : '—'}</td>
                    <td>{lessonsRangeText(r)}</td>
                    <td>{PERSONAL_RATE_KIND_LABELS[r.kind]}</td>
                    <td>{personalValueText(r)}</td>
                    <td className="settings-note">{r.note || '—'}</td>
                    <td>
                      <ActionMenu
                        label="Действия со ставкой"
                        items={[
                          { label: 'Изменить', onSelect: () => setDialog({ editing: r }) },
                          { label: 'Удалить', danger: true, onSelect: () => setDeleting(r) },
                        ]}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Paginator
            page={page}
            pageSize={pageSize}
            total={q.data?.total ?? 0}
            onPageChange={setPage}
            onPageSizeChange={(s) => { setPageSize(s); setPage(1); }}
          />
        </div>
      )}

      {dialog && <PersonalRateDialog editing={dialog.editing} onClose={() => setDialog(null)} />}
      {deleting && (
        <ConfirmModal
          title="Удалить персональную ставку?"
          message={`Ставка «${deleting.teacher_name} — ${deleting.group_name}» будет удалена, начисления пересчитаются по ставкам школы.`}
          confirmLabel="Удалить"
          danger
          isPending={remove.isPending}
          onConfirm={confirmDelete}
          onClose={() => setDeleting(null)}
        />
      )}
    </section>
  );
}
