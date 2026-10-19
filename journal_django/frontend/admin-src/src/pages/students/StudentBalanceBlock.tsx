import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { useStudentBalance } from '../../hooks/useStudentBalance';
import { usePaymentMutations } from '../../hooks/usePayments';
import { usePaymentModal } from '../../providers/PaymentModalProvider';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { useAuth } from '../../hooks/useAuth';
import { fmtRub, fmtLessons, fmtDate } from '../../lib/format';
import { RefundModal } from './RefundModal';
import { SurchargeModal } from './SurchargeModal';
import { canWritePayments, type Role } from '../../lib/permissions';
import type { Payment } from '../../lib/types';

interface Props {
  studentId: number;
}

/** Плюс/минус-иконка секции: вертикальная штриха гаснет при раскрытии → «−». */
function AccordionIcon() {
  return (
    <span className="fin-acc__icon" aria-hidden="true">
      <svg viewBox="0 0 16 16" width="16" height="16" fill="none"
           stroke="currentColor" strokeWidth="1.75" strokeLinecap="round">
        <line x1="3.5" y1="8" x2="12.5" y2="8" />
        <line x1="8" y1="3.5" x2="8" y2="12.5" className="fin-acc__icon-bar" />
      </svg>
    </span>
  );
}

interface SectionProps {
  title: string;
  meta?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
}

function FinSection({ title, meta, defaultOpen = false, children }: SectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`fin-acc${open ? ' is-open' : ''}`}>
      <button
        type="button"
        className="fin-acc__trigger"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <AccordionIcon />
        <span className="fin-acc__title">{title}</span>
        {meta != null && <span className="fin-acc__meta">{meta}</span>}
      </button>
      {open && <div className="fin-acc__body">{children}</div>}
    </div>
  );
}

/** Русское склонение «доплата/доплаты/доплат» — предупреждение при удалении оплаты. */
function pluralizeSurcharges(n: number): string {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return 'доплата';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return 'доплаты';
  return 'доплат';
}

/** Абонементы оплаты: по 4 урока, последний может быть неполным. Цена блока —
 *  базовая доля суммы плюс доплаты именно к нему (правило бэка,
 *  apps/finances/lots.py::_split_into_blocks). */
function subscriptionBlocks(p: Payment, surcharges: Payment[]) {
  const lessons = Number(p.lessons_count || 0);
  if (lessons <= 0) return [];
  const basePerLesson = Number(p.total_amount) / lessons;
  const blocks: { index: number; lessons: number; pricePerLesson: number; extra: number }[] = [];
  let remaining = lessons;
  let index = 1;
  while (remaining > 0) {
    const blockLessons = Math.min(4, remaining);
    const extra = surcharges
      .filter((s) => s.subscription_index === index)
      .reduce((acc, s) => acc + Number(s.total_amount), 0);
    blocks.push({
      index,
      lessons: blockLessons,
      pricePerLesson: (basePerLesson * blockLessons + extra) / blockLessons,
      extra,
    });
    remaining -= blockLessons;
    index += 1;
  }
  return blocks;
}

/** Одна строка таблицы истории оплат. Доплаты (kind='surcharge') сюда не приходят —
 *  они показываются под своим абонементом при раскрытии строки родителя. */
function HistoryRow({
  p,
  surcharges,
  studentId,
  confirming,
  onDelete,
  canWrite,
}: {
  p: Payment;
  /** Доплаты этой оплаты (kind='surcharge', parent_payment_id === p.id). */
  surcharges: Payment[];
  studentId: number;
  confirming: boolean;
  onDelete: () => void;
  /** Менеджер историю оплат видит, но не правит — кнопка удаления ему не нужна. */
  canWrite: boolean;
}) {
  const isRefund = p.kind === 'refund';
  const subsCount = p.subscriptions_count ?? 0;
  const perUnit = isRefund || subsCount <= 0 ? null : Number(p.total_amount) / subsCount;

  const [expanded, setExpanded] = useState(false);
  const [surchargeTarget, setSurchargeTarget] = useState<number | null>(null);

  const blocks = isRefund ? [] : subscriptionBlocks(p, surcharges);
  const surchargeTotal = surcharges.reduce((acc, s) => acc + Number(s.total_amount), 0);

  return (
    <>
      <tr className={isRefund ? 'is-refund' : ''}>
        <td className="fin-history__date">{fmtDate(p.paid_at)}</td>
        <td className="fin-history__num">
          <span className="fin-history__amount">
            {isRefund ? '−' : ''}{fmtRub(p.total_amount)}
          </span>
          {perUnit != null && (
            <span className="fin-history__sub">{subsCount} × {fmtRub(perUnit)}</span>
          )}
          {surcharges.length > 0 && (
            <span
              className="fin-tag fin-tag--surcharge"
              title="Есть доплаты к абонементам — итог по курсу отличается от суммы этой оплаты"
            >
              +{fmtRub(surchargeTotal)}
            </span>
          )}
        </td>
        <td className="fin-history__subs">
          {isRefund ? (
            <span className="fin-tag fin-tag--refund">Возврат</span>
          ) : blocks.length > 0 ? (
            <button
              type="button"
              className="fin-history__subs-toggle"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
              title="Показать абонементы"
            >
              {p.subscriptions_count != null ? (
                <span className="fin-history__count">{p.subscriptions_count}</span>
              ) : (
                // Уроков не кратно 4 — абонементов нет, показываем число уроков.
                <span className="fin-tag fin-tag--prepay" title={`Поштучно, ${fmtLessons(p.lessons_count ?? 0)} уроков`}>
                  {fmtLessons(p.lessons_count ?? 0)} ур.
                </span>
              )}
              <svg
                className={`fin-history__subs-caret${expanded ? ' is-open' : ''}`}
                width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true"
              >
                <path d="M2 3.5L5 6.5L8 3.5" stroke="currentColor" strokeWidth="1.4"
                      strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          ) : (
            <span className="muted">—</span>
          )}
        </td>
        <td>
          {p.direction_name
            ? <span className="fin-dir-name">{p.direction_name}</span>
            : <span className="muted">—</span>}
        </td>
        <td className="fin-history__author">{p.created_by || <span className="muted">—</span>}</td>
        <td className="fin-history__note">
          {p.note ? <NoteCell text={p.note} /> : <span className="muted">—</span>}
        </td>
        <td className="fin-history__action">
          {canWrite && (
          <button
            type="button"
            className={`btn-delete${confirming ? ' is-confirming' : ''}`}
            onClick={onDelete}
            title="Удалить оплату"
            aria-label="Удалить"
          >
            {confirming ? (
              surcharges.length > 0
                ? `Точно? (+${surcharges.length} ${pluralizeSurcharges(surcharges.length)})`
                : 'Точно?'
            ) : (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                <line x1="10" y1="11" x2="10" y2="17" /><line x1="14" y1="11" x2="14" y2="17" />
              </svg>
            )}
          </button>
          )}
        </td>
      </tr>
      {expanded && blocks.length > 0 && (
        <tr className="fin-subs-row">
          <td colSpan={7}>
            <div className="fin-subs-list">
              {blocks.map((b) => {
                const blockSurcharges = surcharges.filter((s) => s.subscription_index === b.index);
                return (
                  <div key={b.index} className="fin-subs-item">
                    <div className="fin-subs-item__row">
                      <span className="fin-subs-item__num">Абонемент №{b.index}</span>
                      <span className="fin-subs-item__lessons">{b.lessons} ур.</span>
                      <span className="fin-subs-item__price">{fmtRub(b.pricePerLesson)}/урок</span>
                      {canWrite && (
                        <button
                          type="button"
                          className="btn-link"
                          onClick={() => setSurchargeTarget(b.index)}
                        >
                          + Доплата
                        </button>
                      )}
                    </div>
                    {blockSurcharges.length > 0 && (
                      <div className="fin-subs-item__extras">
                        {blockSurcharges.map((s) => (
                          <div key={s.id} className="fin-subs-extra">
                            <span className="fin-subs-extra__date">{fmtDate(s.paid_at)}</span>
                            <span className="fin-subs-extra__amount">+{fmtRub(s.total_amount)}</span>
                            {s.note && <span className="fin-subs-extra__note">{s.note}</span>}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </td>
        </tr>
      )}
      {surchargeTarget != null && (
        <SurchargeModal
          studentId={studentId}
          paymentId={p.id}
          subscriptionIndex={surchargeTarget}
          onClose={() => setSurchargeTarget(null)}
        />
      )}
    </>
  );
}

export function StudentBalanceBlock({ studentId }: Props) {
  const balance = useStudentBalance(studentId);
  const muts = usePaymentMutations();
  const { open } = usePaymentModal();
  const showError = useApiError();
  const { toast } = useToast();
  const { me } = useAuth();
  const canRefund = me?.role === 'admin' || me?.role === 'superadmin';
  // Внесение и удаление оплат — админ/суперадмин (бэк: ReadStaffWriteAdmin).
  const canWrite = canWritePayments(me?.role as Role);

  const [confirmingId, setConfirmingId] = useState<number | null>(null);
  const [refundOpen, setRefundOpen] = useState(false);

  if (balance.isLoading) return null;
  const data = balance.data;
  if (!data) return null;
  if (
    data.paid_by_direction.length === 0 &&
    data.attended_by_direction.length === 0 &&
    data.payments.length === 0
  ) return null;

  // Доплаты (kind='surcharge') не показываются строками верхнего уровня — они
  // выводятся под своим абонементом при раскрытии родительской оплаты.
  const topLevelPayments = data.payments.filter((p) => p.kind !== 'surcharge');
  const surchargesByParent = new Map<number, Payment[]>();
  for (const p of data.payments) {
    if (p.kind === 'surcharge' && p.parent_payment_id != null) {
      const list = surchargesByParent.get(p.parent_payment_id);
      if (list) list.push(p);
      else surchargesByParent.set(p.parent_payment_id, [p]);
    }
  }

  const handleDelete = async (id: number) => {
    if (confirmingId !== id) { setConfirmingId(id); return; }
    try {
      const res = await muts.remove.mutateAsync(id);
      toast('Оплата удалена', 'ok');
      if (res.warning === 'balance_negative') {
        toast(`Внимание: баланс стал ${fmtLessons(res.new_balance)}`, 'error');
      }
      setConfirmingId(null);
    } catch (err) {
      showError(err);
      setConfirmingId(null);
    }
  };

  return (
    <section className="balance-block">
      <div className="balance-block__head">
        <h3>Финансы</h3>
        <div className="balance-block__head-actions">
          {canRefund && data.total_balance > 0 && (
            <button type="button" className="btn-danger" onClick={() => setRefundOpen(true)}>
              Возврат средств
            </button>
          )}
          {canWrite && (
            <button type="button" className="btn-save" onClick={() => open({ studentId })}>
              + Внести оплату
            </button>
          )}
        </div>
      </div>

      <div className="balance-summary">
        <div className="balance-tile">
          <span className="balance-tile__label">Оплачено всего</span>
          <span className="balance-tile__value">{fmtRub(data.total_paid_amount)}</span>
        </div>
        <div className="balance-tile">
          <span className="balance-tile__label">Остаток оплаченных уроков</span>
          <span className={`balance-tile__value${data.total_balance < 0 ? ' is-neg' : ''}`}>
            {fmtLessons(data.total_balance)}
          </span>
        </div>
      </div>

      <div className="fin-sections">
        {data.paid_by_direction.length > 0 && (
          <FinSection title="Оплачено по направлениям" meta={data.paid_by_direction.length}>
            <div className="fin-dir-list">
              {data.paid_by_direction.map((d) => (
                <div key={d.direction_id} className="fin-dir-row">
                  <span className="fin-dir-dot" style={{ background: d.direction_color || 'var(--text4)' }} />
                  <span className="fin-dir-name">{d.direction_name}</span>
                  <span className="fin-dir-value">{fmtRub(d.total_paid_amount)}</span>
                </div>
              ))}
            </div>
          </FinSection>
        )}

        {data.attended_by_direction.length > 0 && (
          <FinSection title="Отработано по направлениям" meta={data.attended_by_direction.length}>
            <div className="fin-dir-list">
              {data.attended_by_direction.map((d) => (
                <div key={d.direction_id} className="fin-dir-row">
                  <span className="fin-dir-dot" style={{ background: d.direction_color || 'var(--text4)' }} />
                  <span className="fin-dir-name">{d.direction_name}</span>
                  <span className="fin-dir-value">
                    {fmtLessons(d.attended_lessons)} <span className="fin-dir-unit">уроков</span>
                  </span>
                </div>
              ))}
            </div>
          </FinSection>
        )}

        {topLevelPayments.length > 0 && (
          <FinSection title="История оплат" meta={topLevelPayments.length} defaultOpen>
            <div className="table-wrap fin-history">
              <table>
                <thead>
                  <tr>
                    <th>Дата</th>
                    <th className="col-num">Сумма</th>
                    <th className="col-center">Абонементы</th>
                    <th>Направление</th>
                    <th>Внёс</th>
                    <th className="col-flex">Комментарий</th>
                    <th aria-label="Действия" />
                  </tr>
                </thead>
                <tbody>
                  {topLevelPayments.map((p) => (
                    <HistoryRow
                      key={p.id}
                      canWrite={canWrite}
                      studentId={studentId}
                      p={p}
                      surcharges={surchargesByParent.get(p.id) || []}
                      confirming={confirmingId === p.id}
                      onDelete={() => { void handleDelete(p.id); }}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </FinSection>
        )}
      </div>

      <RefundModal
        open={refundOpen}
        onClose={() => setRefundOpen(false)}
        studentId={studentId}
        remainingValue={data.remaining_value}
        remainingLessons={data.total_balance}
      />
    </section>
  );
}

/**
 * Комментарий к оплате.
 *
 * Раньше текст обрезался одной строкой с многоточием, а целиком его показывал
 * только нативный тултип — то есть комментарий фактически нельзя было прочитать
 * (и нельзя было выделить/скопировать). Расширять строку нельзя: в таблице уже
 * семь колонок, длинный комментарий сломал бы остальные.
 *
 * Решение: две строки по умолчанию и раскрытие по месту. Кнопка появляется
 * только когда текст реально не поместился — это измеряется, а не угадывается
 * по длине строки, потому что ширина колонки зависит от содержимого таблицы.
 */
function NoteCell({ text }: { text: string }) {
  const ref = useRef<HTMLParagraphElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [clamped, setClamped] = useState(false);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const check = () => setClamped(el.scrollHeight > el.clientHeight + 1);
    check();
    // Ширина колонки меняется от содержимого таблицы и размера окна.
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [text]);

  return (
    <div className="note-cell">
      <p ref={ref} className={`note-cell__text${expanded ? ' is-expanded' : ''}`}>{text}</p>
      {(clamped || expanded) && (
        <button
          type="button"
          className="note-cell__toggle"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
        >
          {expanded ? 'Свернуть' : 'Показать полностью'}
        </button>
      )}
    </div>
  );
}
