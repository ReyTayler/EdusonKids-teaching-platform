import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Avatar } from '../../components/Avatar';
import { EntityLink } from '../../components/EntityLink';
import { Dialog } from '../../components/ui/Dialog';
import { DrawerResizeHandle } from '../../components/ui/DrawerResizeHandle';
import { Button } from '../../components/ui/Button';
import {
  ActivityEmpty, ActivityLog, ActivityRow, CollapsibleSection, CommentThread,
} from '../../components/ui/DrawerFeed';
import { InlineField } from '../../components/form/InlineField';
import { CalendarGlyph, PersonGlyph, TypeGlyph } from '../../components/form/FieldIcons';
import { SelectInput } from '../../components/form/SelectInput';
import { DateInput } from '../../components/form/DateInput';
import { Textarea } from '../../components/form/Textarea';
import { usePaymentModal } from '../../providers/PaymentModalProvider';
import {
  useRenewalActivity, useRenewalDeal, useRenewalMutations,
} from '../../hooks/useRenewals';
import { useRenewalStages } from '../../hooks/useRenewalStages';
import { useApiError } from '../../hooks/useApiError';
import { useDrawerResize } from '../../hooks/useDrawerResize';
import {
  fmtDate, fmtDateTime, fmtLessons, fmtRelativeDateTime, isoDateMSK, todayMSK,
} from '../../lib/format';
import { useAuth } from '../../hooks/useAuth';
import {
  canEditRenewalOutcomeDate, canWritePayments, type Role,
} from '../../lib/permissions';
import { RENEWAL_STAGE_LABELS } from '../../lib/labels';
import { StageBadge } from './StageBadge';
import { RenewalCloseDialog, type CloseDialogTarget } from './RenewalCloseDialog';
import { FreezeDealDialog } from './FreezeDealDialog';
import { FROZEN_STAGE_KEY, isPauseStage, type RenewalActivityItem } from '../../lib/renewals';

interface Props {
  id: number;
  onClose: () => void;
}

const RENEWAL_DRAWER_WIDTH_KEY = 'renewals.drawerWidth';

/**
 * Строка ленты истории — фраза на естественном языке. Комментарии сюда не
 * попадают: они живут отдельным блоком панели, поэтому ветки на `comment`
 * здесь нет (так же в TaskDrawer).
 *
 * Глаголы в скобочной форме («перевёл(-а)»): пола сотрудника в учётке нет, а
 * угадывать его по имени нельзя — ошибка задевает живого человека.
 * Имя автора и время рисует ActivityRow, здесь только глагольная часть.
 */
function ActivityLine({ item }: { item: RenewalActivityItem }) {
  let phrase: ReactNode;
  switch (item.kind) {
    case 'stage_change':
      // Откуда перешли не пишем: предыдущая стадия — соседняя строка ленты.
      phrase = <>перевёл(-а) сделку в «{item.to_label ?? '—'}»</>;
      break;
    case 'payment_linked':
      phrase = <>привязал(-а) оплату #{item.payment_id ?? '—'}</>;
      break;
    default:
      phrase = <>{item.body}</>;
  }
  return (
    <ActivityRow
      authorName={item.author_name}
      time={fmtRelativeDateTime(item.created_at)}
    >
      {phrase}
    </ActivityRow>
  );
}

export function RenewalDrawer({ id, onClose }: Props) {
  const { data: deal, isLoading: dealLoading } = useRenewalDeal(id);
  const { data: activity, isLoading: activityLoading } = useRenewalActivity(id);
  const { data: stages } = useRenewalStages();
  const { comment, patch, move, reopen, unfreeze,
    setOutcomeDate: setOutcomeDateM } = useRenewalMutations();
  const { open: openPayment } = usePaymentModal();
  const { me } = useAuth();
  // Оплату вносит админ/суперадмин — менеджер ведёт сделку, но не деньги.
  const canPay = canWritePayments(me?.role as Role);
  const showError = useApiError();
  const [text, setText] = useState('');
  const [closeTarget, setCloseTarget] = useState<CloseDialogTarget | null>(null);
  const [confirmReopen, setConfirmReopen] = useState(false);
  // Стадия «Заморожен» выбрана в дропдауне: move требует месяц окончания,
  // спрашиваем его тем же диалогом, что и доска.
  const [freezeStageId, setFreezeStageId] = useState<number | null>(null);
  // Дата закрытия в форме правки. Держим в состоянии, а не читаем из deal
  // напрямую, чтобы календарь не сбрасывал ввод на каждый рефетч сделки.
  const [outcomeDate, setOutcomeDate] = useState('');

  // Пользовательская ширина панели — тот же общий хук, что и у TaskDrawer
  // (задача 2026-08-26: ручка ресайза видна всегда + переиспользуемая механика).
  const {
    width: drawerWidth, resizing, handleProps: resizeHandleProps, wrapOverlayClose,
  } = useDrawerResize({ storageKey: RENEWAL_DRAWER_WIDTH_KEY });
  const handleOverlayClick = wrapOverlayClose(onClose);

  // onClose обычно приходит как новая инлайн-функция от родителя на каждый рендер —
  // без useCallback здесь listener пересоздавался бы при каждом ре-рендере RenewalDrawer.
  const handleEscape = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') onClose();
  }, [onClose]);

  useEffect(() => {
    window.addEventListener('keydown', handleEscape);
    return () => window.removeEventListener('keydown', handleEscape);
  }, [handleEscape]);

  // Подставляем сохранённую дату и переподставляем при открытии другой сделки
  // (drawer переиспользуется) или после успешной записи.
  useEffect(() => {
    setOutcomeDate(isoDateMSK(deal?.outcome_at));
  }, [deal?.outcome_at]);

  const handleOutcomeDateSave = () => {
    if (!deal || !outcomeDate) return;
    setOutcomeDateM.mutate({ id: deal.id, outcome_date: outcomeDate }, {
      onError: (err) => showError(err, 'Не удалось изменить дату закрытия'),
    });
  };

  const handleAddComment = () => {
    const body = text.trim();
    if (!body) return;
    comment.mutate({ id, body }, { onSuccess: () => setText('') });
  };

  const save = (body: Record<string, unknown>) =>
    patch.mutate({ id, body }, {
      onError: (err) => showError(err, 'Не удалось сохранить изменение'),
    });

  // Смена стадии из карточки: открытые стадии — сразу move; won/lost — через
  // тот же диалог закрытия, что и на доске (причина/оплата, ничего молча).
  const handleStageChange = (stageIdStr: string) => {
    if (!deal || !stageIdStr) return;
    const stageId = Number(stageIdStr);
    if (stageId === deal.stage_id) return;
    const target = (stages || []).find((s) => s.id === stageId);
    if (!target) return;
    if (target.kind === 'won' || target.kind === 'lost') {
      setCloseTarget({
        dealId: deal.id,
        studentId: deal.student_id,
        studentName: deal.student_name,
        mode: target.kind,
      });
      return;
    }
    if (target.key === FROZEN_STAGE_KEY) {
      setFreezeStageId(stageId);
      return;
    }
    move.mutate({ id: deal.id, to_stage_id: stageId }, {
      onError: (err) => showError(err, 'Не удалось сменить стадию'),
    });
  };

  const handleCloseConfirm = ({ reason_code, comment: dialogText }:
    { reason_code?: string; comment?: string }) => {
    if (!closeTarget) return;
    const stage = (stages || []).find((s) => s.kind === closeTarget.mode);
    if (!stage) {
      setCloseTarget(null);
      return;
    }
    move.mutate(
      { id: closeTarget.dealId, to_stage_id: stage.id, reason_code },
      {
        onSuccess: () => {
          if (dialogText) comment.mutate({ id: closeTarget.dealId, body: dialogText });
          setCloseTarget(null);
        },
        onError: (err) => {
          setCloseTarget(null);
          showError(err, 'Не удалось закрыть сделку');
        },
      },
    );
  };

  // Комментарии и системные события больше не идут одной лентой: читают их
  // по-разному — комментарии как переписку, историю как журнал событий.
  // Лента приезжает от новых к старым (ORDER BY created_at DESC на бэке).
  // Историю так и оставляем — свежее событие сверху; переписку разворачиваем:
  // комментарии читают сверху вниз по порядку, как в панели задачи.
  const comments = (activity || []).filter((a) => a.kind === 'comment').slice().reverse();
  const history = (activity || []).filter((a) => a.kind !== 'comment');

  const stageLabel = deal?.stage_label || (deal ? RENEWAL_STAGE_LABELS[deal.stage_key] : undefined);
  const isClosed = deal?.outcome_at != null;
  const canEditOutcomeDate = canEditRenewalOutcomeDate(me?.role as Role);
  // Дата закрытия по МСК — и для подстановки в календарь, и чтобы гасить
  // «Сохранить», пока значение не изменили.
  const closedOn = isoDateMSK(deal?.outcome_at);
  const cycleDone = !!deal?.cycle_completed;
  const currentStage = (stages || []).find((s) => s.id === deal?.stage_id);
  const isFrozen = deal?.stage_key === FROZEN_STAGE_KEY;
  // Сделка стоит на стадии-паузе: единственный выход с неё — «Вернуть в работу».
  const isOnPauseStage = !!currentStage && isPauseStage(currentStage);
  // Обычные ручные переходы (в другую decision-стадию, закрытие) бэк разрешает
  // только С ручной decision-стадии или со «Ждём продление»: с авто-стадий
  // (прогресс, «Ждём оплату») любой move даёт 409 (transitions.py: from_is_auto).
  const fromAllowsManualMoves = !!currentStage
    && ((currentStage.kind === 'decision' && !currentStage.is_auto)
      || currentStage.key === 'awaiting_renewal');
  // Стадии-паузы — исключение: бэк пускает в них с ЛЮБОЙ стадии и при незавершённом
  // цикле (transitions.py: _is_pause_target), потому что это пауза, а не решение.
  // Поэтому «Урок 2» → «Заморожен»/«Закончил курс» должно быть доступно и из панели,
  // не только драгом.
  const pauseTargets = (stages || []).filter(
    (s) => isPauseStage(s) && s.id !== deal?.stage_id);
  const stageMovable = !!currentStage && (fromAllowsManualMoves || pauseTargets.length > 0);
  // Прочие ручные цели — только при завершённом цикле и только если уйти с текущей
  // стадии вообще можно. Паузы идут отдельными пунктами (pauseTargets) и не
  // дублируются здесь.
  const manualTargets = fromAllowsManualMoves
    ? (stages || []).filter(
      (s) => s.kind === 'decision' && !s.is_auto && cycleDone
        && s.id !== deal?.stage_id && !isPauseStage(s))
    : [];
  // «Ушёл» — всегда; «Продлён» — только при завершённом цикле (через диалог).
  // С авто-стадии закрыть сделку руками нельзя, поэтому и не предлагаем.
  const closeStages = fromAllowsManualMoves
    ? (stages || []).filter((s) => s.kind === 'lost' || (s.kind === 'won' && cycleDone))
    : [];

  return (
    <div className="renewal-drawer-overlay" onClick={handleOverlayClick}>
      <aside
        className="renewal-drawer"
        style={{ width: drawerWidth }}
        role="dialog"
        aria-modal="true"
        aria-label="Карточка сделки"
        onClick={(e) => e.stopPropagation()}
      >
        <DrawerResizeHandle resizing={resizing} {...resizeHandleProps} />
        {dealLoading || !deal ? (
          <div className="renewal-drawer__loading">Загружаем сделку…</div>
        ) : (
          <>
            <header className="renewal-drawer__head">
              <div className="renewal-drawer__title">
                <Avatar name={deal.student_name || '—'} size={32} />
                <EntityLink section="students" id={deal.student_id} text={deal.student_name} />
              </div>
              <button
                type="button"
                className="renewal-drawer__close"
                onClick={onClose}
                aria-label="Закрыть"
              >
                ✕
              </button>
            </header>

            <div className="renewal-drawer__subhead">
              <span>
                {(deal.directions || []).map((d, i) => (
                  <span key={d.name} style={d.color ? { color: d.color } : undefined}>
                    {i > 0 && ', '}{d.name}
                  </span>
                ))}
                {(deal.directions || []).length === 0 && '—'}
              </span>
              <span> · Цикл {deal.cycle_no}</span>
              {stageLabel && <StageBadge label={stageLabel} kind={deal.stage_kind} />}
            </div>

            <div className="renewal-drawer__section renewal-drawer__progress">
              {!isClosed && (
                deal.cycle_completed
                  ? (
                    <span className="status-badge status-badge--info">
                      Цикл отработан{deal.due_at ? ` ${fmtDate(deal.due_at)}` : ''} — пора продлевать
                    </span>
                  )
                  : deal.lesson_in_cycle === 1
                    ? <span>Не было уроков цикла</span>
                    : <span>Отработано {deal.lesson_in_cycle - 1} из 4</span>
              )}
              {deal.debt && (
                <span className="status-badge status-badge--negative" title="Баланс ученика отрицательный">
                  Долг
                </span>
              )}
            </div>

            <div className="renewal-drawer__section renewal-drawer__balance">
              <span className="renewal-drawer__balance-label">Баланс</span>
              <span className="renewal-drawer__balance-value">{fmtLessons(deal.balance)} ур.</span>
            </div>

            {isClosed ? (
              <>
                <div className="renewal-drawer__section renewal-drawer__closed">
                  <span className={`status-badge${deal.stage_kind === 'won' ? ' status-badge--positive' : ' status-badge--negative'}`}>
                    {deal.stage_kind === 'won' ? 'Продлена' : 'Закрыта'} {fmtDateTime(deal.outcome_at!)}
                  </span>
                  {/* Переоткрывать можно только последнюю сделку ученика: оживление
                      старого цикла поверх более позднего закрытого рвало нумерацию
                      (прод, 25.08.2026). Флаг считает бэк тем же правилом, что и гард. */}
                  {deal.can_reopen ? (
                    <button
                      type="button"
                      className="btn-secondary"
                      disabled={reopen.isPending}
                      onClick={() => setConfirmReopen(true)}
                    >
                      Переоткрыть
                    </button>
                  ) : (
                    <span className="renewal-drawer__hint">
                      Переоткрыть можно только последнюю сделку ученика — у него есть
                      более поздние циклы
                    </span>
                  )}
                </div>
                {/* Сделку часто закрывают позже, чем ученик реально ушёл, а месяц
                    закрытия — это месяц продления/ухода в аналитике. Поэтому дату
                    можно поправить задним числом; правка видна в таймлайне. */}
                {canEditOutcomeDate && (
                  <div className="renewal-drawer__section renewal-drawer__outcome-date">
                    <InlineField icon={<CalendarGlyph />} label="Дата закрытия">
                      <div className="renewal-drawer__outcome-date-row">
                        <DateInput
                          value={outcomeDate}
                          placeholder="Выбрать дату…"
                          onChange={(e) => setOutcomeDate(e.target.value)}
                        />
                        {/* DateInput — свой календарь, min/max он не понимает,
                            поэтому будущее отсекаем здесь (бэк проверяет тоже). */}
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={!outcomeDate
                            || outcomeDate === closedOn
                            || outcomeDate > todayMSK()
                            || setOutcomeDateM.isPending}
                          onClick={handleOutcomeDateSave}
                        >
                          Сохранить
                        </button>
                      </div>
                    </InlineField>
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="renewal-drawer__section renewal-drawer__fields">
                  {stageMovable && currentStage && (
                    <InlineField icon={<TypeGlyph />} label="Стадия">
                      <SelectInput
                        value={String(deal.stage_id)}
                        onChange={(e) => handleStageChange(e.target.value)}
                        options={[
                          { value: String(currentStage.id), label: currentStage.label },
                          // «…» — у заморозки следом спросим срок; прочие паузы
                          // переводят сразу, поэтому и многоточия не обещают.
                          ...pauseTargets.map((s) => ({
                            value: String(s.id),
                            label: s.key === FROZEN_STAGE_KEY ? `${s.label}…` : s.label,
                          })),
                          ...manualTargets.map((s) => ({ value: String(s.id), label: s.label })),
                          ...closeStages.map((s) => ({
                            value: String(s.id),
                            label: s.kind === 'won' ? `✓ ${s.label}…` : `✕ ${s.label}…`,
                          })),
                        ]}
                      />
                    </InlineField>
                  )}
                  {/* Ответственного здесь только показывают — меняется он на
                      странице ученика. Оформление то же, что у соседних строк,
                      иначе поле читалось бы как сломанный контрол. */}
                  <InlineField icon={<PersonGlyph />} label="Ответственный">
                    <div className="inline-field__readonly" title="Меняется на странице ученика">
                      {deal.assignee_name || '— не назначен —'}
                    </div>
                  </InlineField>
                </div>

                <div className="renewal-drawer__actions">
                  {canPay && (
                    <button
                      type="button"
                      className="btn-primary renewal-drawer__pay-btn"
                      onClick={() => openPayment({ studentId: deal.student_id })}
                    >
                      Внести оплату
                    </button>
                  )}
                  {/* Продление заморозки: переход «Заморожен → Заморожен» бэк
                      разрешает и перезаписывает месяц. Без этой кнопки менеджеру
                      пришлось бы разморозить и заморозить заново, оставив в
                      таймлайне «возврат в работу», которого не было. */}
                  {isFrozen && (
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => setFreezeStageId(deal.stage_id)}
                    >
                      Изменить месяц
                    </button>
                  )}
                  {/* Единственный выход со стадии-паузы: ставит расчётную авто-стадию
                      по посещаемости и балансу и гасит месяц. Автовыхода по факту
                      записанного урока нет (решение пользователя 2026-07-25). */}
                  {isOnPauseStage && (
                    <button
                      type="button"
                      className="btn-secondary"
                      disabled={unfreeze.isPending}
                      onClick={() => unfreeze.mutate({ id: deal.id }, {
                        onError: (err) => showError(err, 'Не удалось вернуть сделку в работу'),
                      })}
                    >
                      Вернуть в работу
                    </button>
                  )}
                </div>
              </>
            )}

            {/* Комментарии + история: на узкой панели друг под другом,
                на широкой — двумя колонками (см. @container в renewals.css),
                тот же приём, что .task-drawer__lower в TaskDrawer. */}
            <div className="renewal-drawer__lower">
              <div className="renewal-drawer__section">
                <CollapsibleSection title="Комментарии">
                  {activityLoading ? (
                    <div className="renewal-drawer__loading">Загружаем комментарии…</div>
                  ) : (
                    <CommentThread
                      items={comments.map((c) => ({
                        id: c.id,
                        author: c.author_name,
                        iso: c.created_at,
                        // Текст комментария сделки лежит в body (у задач — в text).
                        text: c.body,
                      }))}
                      emptyText="Пока нет комментариев"
                    />
                  )}
                  <Textarea
                    className="renewal-drawer__comment-input"
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    placeholder="Комментарий…"
                    rows={2}
                  />
                  {/* Кнопка появляется только с набранным текстом: пустое поле в
                      референсе — просто поле. Совсем прятать действие нельзя —
                      человек должен видеть, чем отправить. */}
                  {text.trim() && (
                    <Button
                      className="renewal-drawer__comment-send"
                      variant="secondary"
                      disabled={comment.isPending}
                      onClick={handleAddComment}
                    >
                      Добавить
                    </Button>
                  )}
                </CollapsibleSection>
              </div>

              <div className="renewal-drawer__section renewal-drawer__timeline-section">
                <CollapsibleSection title="История">
                  {activityLoading ? (
                    <div className="renewal-drawer__loading">Загружаем историю…</div>
                  ) : (
                    <ActivityLog>
                      {history.map((item) => (
                        <ActivityLine key={item.id} item={item} />
                      ))}
                      {history.length === 0 && (
                        <ActivityEmpty>Пока нет истории</ActivityEmpty>
                      )}
                    </ActivityLog>
                  )}
                </CollapsibleSection>
              </div>
            </div>

            {closeTarget && (
              <RenewalCloseDialog
                target={closeTarget}
                pending={move.isPending}
                onClose={() => setCloseTarget(null)}
                onConfirm={handleCloseConfirm}
              />
            )}

            {freezeStageId !== null && (
              <FreezeDealDialog
                studentName={deal.student_name}
                pending={move.isPending}
                // Цель = текущая стадия ⇒ это правка месяца у уже замороженной
                // сделки: подставляем сохранённый месяц вместо ближайшего.
                initialMonth={
                  freezeStageId === deal.stage_id ? deal.frozen_until_month : undefined
                }
                onClose={() => setFreezeStageId(null)}
                onConfirm={(frozen_until_month) => {
                  move.mutate(
                    { id: deal.id, to_stage_id: freezeStageId, frozen_until_month },
                    {
                      onSuccess: () => setFreezeStageId(null),
                      onError: (err) => {
                        setFreezeStageId(null);
                        showError(err, 'Не удалось изменить заморозку');
                      },
                    },
                  );
                }}
              />
            )}

            {confirmReopen && (
              <Dialog
                open
                onOpenChange={(o) => { if (!o) setConfirmReopen(false); }}
                title="Переоткрыть сделку?"
                footer={
                  <>
                    <button type="button" className="btn-secondary" onClick={() => setConfirmReopen(false)}>
                      Отмена
                    </button>
                    <button
                      type="button"
                      className="btn-primary"
                      disabled={reopen.isPending}
                      onClick={() => reopen.mutate({ id }, {
                        onSuccess: () => setConfirmReopen(false),
                        onError: (err) => {
                          setConfirmReopen(false);
                          showError(err, 'Не удалось переоткрыть сделку');
                        },
                      })}
                    >
                      Переоткрыть
                    </button>
                  </>
                }
              >
                <p className="renewal-close-dialog__text">
                  Сделка вернётся на доску в актуальную стадию по посещаемости и
                  балансу. Если при закрытии была создана нетронутая сделка
                  следующего месяца — она будет удалена.
                </p>
              </Dialog>
            )}
          </>
        )}
      </aside>
    </div>
  );
}
