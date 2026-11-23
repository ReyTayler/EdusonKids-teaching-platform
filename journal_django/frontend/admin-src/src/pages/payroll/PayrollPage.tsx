import { TableScrollArea } from '../../components/table/TableScrollArea';
import { useMemo, useDeferredValue, useState, type FormEvent } from 'react';
import { usePayroll, usePayrollSummary, usePayrollMutations } from '../../hooks/usePayroll';
import { useApiError } from '../../hooks/useApiError';
import { useListSearchParams } from '../../hooks/useListSearchParams';
import type { ListSearchState, ListSearchControls } from '../../hooks/useListSearchParams';
import { DataTable, type Column } from '../../components/table/DataTable';
import { DateInput } from '../../components/form/DateInput';
import { Field } from '../../components/form/Field';
import { NumberInput } from '../../components/form/NumberInput';
import { EntityLink } from '../../components/EntityLink';
import { TableSkeleton } from '../../components/ui/Skeleton';
import { EmptyState } from '../../components/ui/EmptyState';
import { StatusBadge } from '../../components/ui/StatusBadge';
import { ActionMenu, type ActionMenuItem } from '../../components/ui/ActionMenu';
import { ConfirmModal } from '../../components/ui/ConfirmModal';
import { Dialog } from '../../components/ui/Dialog';
import { useToast } from '../../components/ui/Toast';
import { fmtDate, fmtRub } from '../../lib/format';
import { PAYROLL_KIND_LABELS, PAYROLL_KIND_FILTER_OPTIONS } from '../../lib/labels';
import type { PayrollEntry, PayrollSummaryRow } from '../../lib/types';
import { PageHeader } from '../../components/shell/PageHeader';
import { FilterPopover } from '../../components/filters';
import { TextInput } from '../../components/form/TextInput';
import { SelectInput } from '../../components/form/SelectInput';

/** Колонки вынесены из вида: лупа стоит в шапке страницы, а фильтрует вид —
 * и шапке, и таблице нужен один и тот же список полей. */
const PAYROLL_COLUMNS: Column<PayrollEntry>[] = [
  { key: 'kind', label: 'Вид', sortable: true, searchable: true,
    searchOptions: PAYROLL_KIND_FILTER_OPTIONS,
    cell: (r) => r.kind === 'cancellation'
      ? <StatusBadge tone="info">{PAYROLL_KIND_LABELS.cancellation}</StatusBadge>
      : PAYROLL_KIND_LABELS.lesson },
  { key: 'lesson_date', label: 'Дата', sortable: true, searchable: false,
    cell: (r) => {
      const date = fmtDate(r.lesson_date);
      // У отмены урока нет — ссылаться некуда, показываем дату и время отмены.
      if (r.kind === 'cancellation') {
        return r.scheduled_time ? `${date} · ${r.scheduled_time.slice(0, 5)}` : date;
      }
      return <EntityLink section="lessons" id={r.lesson_id} text={date} />;
    } },
  { key: 'teacher_name', label: 'Преподаватель', sortable: true, searchable: true,
    cell: (r) => <EntityLink section="teachers" id={r.teacher_id} text={r.teacher_name} /> },
  { key: 'group_name', label: 'Группа', sortable: true, searchable: true,
    cell: (r) => <EntityLink section="groups" id={r.group_id} text={r.group_name} /> },
  { key: 'lesson_number', label: 'Урок #', sortable: true,
    cell: (r) => r.lesson_number ?? '—' },
  { key: 'present_count', label: 'Было/Всего', sortable: false,
    cell: (r) => (r.kind === 'cancellation' || r.present_count == null)
      ? '—' : `${r.present_count}/${r.total_students}` },
  { key: 'payment', label: 'Оплата', sortable: true,
    cell: (r) => (
      <div className="payroll-amount">
        <span>{fmtRub(r.payment)}</span>
        {r.is_manual && (
          <>
            <StatusBadge tone="muted">вручную</StatusBadge>
            <span className="payroll-amount__calc">расчёт {fmtRub(r.calc_payment)}</span>
          </>
        )}
      </div>
    ) },
  { key: 'penalty', label: 'Штраф', sortable: true,
    cell: (r) => r.kind === 'cancellation' ? '—' : fmtRub(r.penalty) },
  { key: 'rule_note', label: 'Как посчитано', sortable: false,
    cell: (r) => <span className="payroll-note" title={r.rule_note}>{r.rule_note || '—'}</span> },
];

export default function PayrollPage() {
  const search = useListSearchParams({ sortBy: 'lesson_date', sortDir: 'desc' });
  const mode = (search.getExtra('mode') === 'summary') ? 'summary' : 'list';
  const setMode = (m: 'list' | 'summary') => search.setExtra('mode', m === 'list' ? null : 'summary');

  return (
    <>
      {/* Шапка живёт во внешнем компоненте, а загрузку показывают вложенные
          представления — поэтому заголовок не пропадает при переключении вида. */}
      <PageHeader
        title="Зарплата"
        actions={(
          <>
          <PayrollSearch key={mode} search={search} mode={mode} />
          <div className="segmented" role="group" aria-label="Режим отображения">
            <button
              type="button"
              className={`segmented__btn${mode === 'list' ? ' is-active' : ''}`}
              aria-pressed={mode === 'list'}
              onClick={() => setMode('list')}
            >Список</button>
            <button
              type="button"
              className={`segmented__btn${mode === 'summary' ? ' is-active' : ''}`}
              aria-pressed={mode === 'summary'}
              onClick={() => setMode('summary')}
            >Сводка</button>
          </div>
          </>
        )}
      />

      {mode === 'list' ? (
        <PayrollListView search={search} />
      ) : (
        <PayrollSummaryView search={search} />
      )}
    </>
  );
}

type SearchProps = ListSearchState & ListSearchControls;

function PayrollSearch({ search, mode }: { search: SearchProps; mode: 'list' | 'summary' }) {
  const current = () => ({
    dateFrom: search.getExtra('date_from') || '',
    dateTo: search.getExtra('date_to') || '',
    kind: search.filters.kind || '',
    teacher: search.filters.teacher_name || '',
    group: search.filters.group_name || '',
  });
  const [draft, setDraft] = useState(current);
  const applied = current();
  const isList = mode === 'list';
  const active = !!(applied.dateFrom || applied.dateTo || (isList && (applied.kind || applied.teacher || applied.group)));
  const apply = (values: typeof draft) => search.setExtras({
    date_from: values.dateFrom || null,
    date_to: values.dateTo || null,
    ...(isList ? {
      'f.kind': values.kind || null,
      'f.teacher_name': values.teacher.trim() || null,
      'f.group_name': values.group.trim() || null,
    } : {}),
  });

  return <FilterPopover label="Поиск" active={active} onOpen={() => setDraft(current())}
    onSubmit={() => apply(draft)}
    onReset={() => apply({ dateFrom: '', dateTo: '', kind: '', teacher: '', group: '' })}>
    {isList && <>
      <Field label="Преподаватель">
        <TextInput value={draft.teacher} onChange={(e) => setDraft((d) => ({ ...d, teacher: e.target.value }))} />
      </Field>
      <Field label="Группа">
        <TextInput value={draft.group} onChange={(e) => setDraft((d) => ({ ...d, group: e.target.value }))} />
      </Field>
    </>}
    <Field label="Период с">
      <DateInput value={draft.dateFrom} onChange={(e) => setDraft((d) => ({ ...d, dateFrom: e.target.value }))} placeholder="от" />
    </Field>
    <Field label="Период по">
      <DateInput value={draft.dateTo} onChange={(e) => setDraft((d) => ({ ...d, dateTo: e.target.value }))} placeholder="до" />
    </Field>
    {isList && <Field label="Вид начисления">
      <SelectInput value={draft.kind} onChange={(e) => setDraft((d) => ({ ...d, kind: e.target.value }))}
        options={[{ value: '', label: 'Уроки и отмены' }, ...PAYROLL_KIND_FILTER_OPTIONS]} aria-label="Вид начисления" />
    </Field>}
  </FilterPopover>;
}

function PayrollListView({ search }: { search: SearchProps }) {
  const { page, pageSize, sortBy, sortDir, filters, setPage, setPageSize, setSort, setFilters, getExtra } = search;

  const globalDateRange = useMemo(() => ({
    from: getExtra('date_from') || '',
    to: getExtra('date_to') || '',
  }), [getExtra]);

  const effectiveFilters = useMemo(() => {
    const f = { ...filters };
    if (globalDateRange.from) f.date_from = globalDateRange.from;
    if (globalDateRange.to)   f.date_to   = globalDateRange.to;
    return f;
  }, [filters, globalDateRange]);

  const deferredFilters = useDeferredValue(effectiveFilters);

  const { data, isLoading, isFetching, isError } = usePayroll({
    page,
    page_size: pageSize,
    sort_by: sortBy,
    sort_dir: sortDir,
    filters: deferredFilters,
  });
  const muts = usePayrollMutations();
  const { toast } = useToast();
  const showError = useApiError();
  const [editing, setEditing] = useState<PayrollEntry | null>(null);
  const [resetting, setResetting] = useState<PayrollEntry | null>(null);

  const rows: PayrollEntry[] = data?.rows || [];
  const total = data?.total || 0;

  const doReset = async () => {
    if (!resetting) return;
    try {
      await muts.reset.mutateAsync({ kind: resetting.kind, id: resetting.id });
      toast('Сумма возвращена к расчётной', 'ok');
      setResetting(null);
    } catch (err) { showError(err); }
  };

  const rowAction = (r: PayrollEntry) => {
    const items: ActionMenuItem[] = [
      { label: 'Изменить сумму', onSelect: () => setEditing(r) },
    ];
    if (r.is_manual) items.push({ label: 'Сбросить к расчёту', onSelect: () => setResetting(r) });
    return <ActionMenu items={items} label="Действия со строкой" />;
  };

  return (
    <>
      {/* Упавший запрос — не «пусто»: на денежном экране ноль читается как факт. */}
      {isError && !data ? (
        <EmptyState hint="Не удалось загрузить список. Обновите страницу или попробуйте позже — это не значит, что выплат нет.">
          Ошибка загрузки зарплаты
        </EmptyState>
      ) : (
        <DataTable<PayrollEntry>
          data={rows}
          columns={PAYROLL_COLUMNS}
          title="Список выплат"
          searchOutside
          rowKey={(r) => `${r.kind}-${r.id}`}
          rowAction={rowAction}
          isLoading={isLoading || isFetching}
          serverPagination={{
            page,
            pageSize,
            total,
            sortBy,
            sortDir,
            filters,
            onPageChange: setPage,
            onPageSizeChange: setPageSize,
            onSortChange: setSort,
            onFiltersChange: setFilters,
          }}
        />
      )}
      {editing && <PayrollEditDialog row={editing} onClose={() => setEditing(null)} />}
      {resetting && (
        <ConfirmModal
          title="Сбросить к расчёту"
          message={resetting.kind === 'cancellation'
            ? `Сумма будет пересчитана по ставкам (последний расчёт: ${fmtRub(resetting.calc_payment)}). Продолжить?`
            : `Сумма и штраф будут пересчитаны по ставкам (последний расчёт: ${fmtRub(resetting.calc_payment)}). Продолжить?`}
          confirmLabel="Сбросить"
          isPending={muts.reset.isPending}
          onConfirm={() => { void doReset(); }}
          onClose={() => setResetting(null)}
        />
      )}
    </>
  );
}

/** Правка суммы. Урок и отмена уходят на РАЗНЫЕ эндпоинты (см. usePayrollMutations);
 *  у отмены штрафа нет, поэтому поле штрафа только для урока. */
function PayrollEditDialog({ row, onClose }: { row: PayrollEntry; onClose: () => void }) {
  const muts = usePayrollMutations();
  const { toast } = useToast();
  const showError = useApiError();
  const isCancel = row.kind === 'cancellation';
  const [payment, setPayment] = useState(String(row.payment));
  const [penalty, setPenalty] = useState(String(row.penalty));

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (payment === '' || Number(payment) < 0) { toast('Введите сумму не меньше нуля', 'error'); return; }
    // Шлём только изменённые поля: любой PATCH помечает строку «вручную», так что
    // сохранение без правок молча зафиксировало бы расчётную сумму как ручную.
    const body: { payment?: string; penalty?: string } = {};
    if (Number(payment) !== Number(row.payment)) body.payment = payment;
    if (!isCancel && Number(penalty || 0) !== Number(row.penalty)) body.penalty = penalty === '' ? '0' : penalty;
    if (Object.keys(body).length === 0) { onClose(); return; }
    try {
      await muts.update.mutateAsync({ target: { kind: row.kind, id: row.id }, body });
      toast('Сохранено', 'ok');
      onClose();
    } catch (err) { showError(err); }
  };

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={isCancel ? 'Сумма за отмену' : 'Сумма за урок'}
      footer={
        <button type="submit" form="payroll-edit-form" className="btn-save" disabled={muts.update.isPending}>
          Сохранить
        </button>
      }
    >
      <form id="payroll-edit-form" className="payroll-edit-form" onSubmit={onSubmit}>
        <Field label="Оплата, ₽" required>
          <NumberInput step={0.01} min={0} required value={payment} onChange={(e) => setPayment(e.target.value)} />
        </Field>
        {!isCancel && (
          <Field label="Штраф, ₽">
            <NumberInput step={0.01} min={0} value={penalty} onChange={(e) => setPenalty(e.target.value)} />
          </Field>
        )}
        <p className="payroll-note-full">
          Расчётная сумма: {fmtRub(row.calc_payment)}. {row.rule_note}
        </p>
        <p className="payroll-note-full">
          После правки строка помечается «вручную» и не пересчитывается, пока её не сбросить к расчёту.
        </p>
      </form>
    </Dialog>
  );
}

function PayrollSummaryView({ search }: { search: SearchProps }) {
  const { getExtra } = search;
  const dateFrom = getExtra('date_from') || '';
  const dateTo = getExtra('date_to') || '';

  const { data, isLoading, isError } = usePayrollSummary({
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
  });
  if (isLoading) return <TableSkeleton rows={5} cols={8} />;
  if (isError) {
    return (
      <EmptyState hint="Не удалось загрузить сводку. Обновите страницу или попробуйте позже — это не значит, что выплат нет.">
        Ошибка загрузки сводки
      </EmptyState>
    );
  }
  const rows: PayrollSummaryRow[] = data || [];

  // Суммы приходят строками (точный Decimal); итог считаем в копейках целыми,
  // чтобы не копить ошибку float.
  const cents = (v: string) => Math.round(Number(v || 0) * 100);
  // «К выплате» = начислено − штрафы, как в кабинете преподавателя; сервер поля не отдаёт.
  const net = (r: PayrollSummaryRow) => (cents(r.sum_payment) - cents(r.sum_penalty)) / 100;
  const sumMoney = (pick: (r: PayrollSummaryRow) => string | number) =>
    rows.reduce((acc, r) => acc + Math.round(Number(pick(r) || 0) * 100), 0) / 100;
  const sumCount = (pick: (r: PayrollSummaryRow) => string) =>
    rows.reduce((acc, r) => acc + Number(pick(r) || 0), 0);

  return (
    <>
      <TableScrollArea>
        <table className="data-table">
          <thead>
            <tr>
              <th>Преподаватель</th><th>Уроков</th><th>Отмен</th>
              <th>За уроки</th><th>За отмены</th><th>Начислено</th><th>Штрафы</th><th>К выплате</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={8} style={{ textAlign: 'center', padding: 24, color: 'var(--text3)' }}>
                Нет данных за выбранный период
              </td></tr>
            ) : (
              <>
                {rows.map((r) => (
                  <tr key={r.teacher_id}>
                    <td><EntityLink section="teachers" id={r.teacher_id} text={r.teacher_name} /></td>
                    <td>{r.lessons_count}</td>
                    <td>{r.cancellations_count}</td>
                    <td>{fmtRub(r.sum_lessons_payment)}</td>
                    <td>{fmtRub(r.sum_cancellations_payment)}</td>
                    <td>{fmtRub(r.sum_payment)}</td>
                    <td>{fmtRub(r.sum_penalty)}</td>
                    <td>{fmtRub(net(r))}</td>
                  </tr>
                ))}
                <tr className="payroll-sum-row">
                  <td>Итого</td>
                  <td>{sumCount((r) => r.lessons_count)}</td>
                  <td>{sumCount((r) => r.cancellations_count)}</td>
                  <td>{fmtRub(sumMoney((r) => r.sum_lessons_payment))}</td>
                  <td>{fmtRub(sumMoney((r) => r.sum_cancellations_payment))}</td>
                  <td>{fmtRub(sumMoney((r) => r.sum_payment))}</td>
                  <td>{fmtRub(sumMoney((r) => r.sum_penalty))}</td>
                  <td>{fmtRub(sumMoney(net))}</td>
                </tr>
              </>
            )}
          </tbody>
        </table>
      </TableScrollArea>
    </>
  );
}
