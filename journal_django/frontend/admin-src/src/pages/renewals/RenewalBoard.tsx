import { useEffect, useState } from 'react';
import {
  DndContext, DragOverlay, PointerSensor, useDroppable, useSensor, useSensors,
  type DragStartEvent, type DragEndEvent,
} from '@dnd-kit/core';
import { useQueryClient } from '@tanstack/react-query';
import { useRenewalBoard, useRenewalMutations } from '../../hooks/useRenewals';
import { useRenewalStages } from '../../hooks/useRenewalStages';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { RenewalColumn } from './RenewalColumn';
import { RenewalCardContent } from './RenewalCardView';
import { RenewalCloseDialog, type CloseDialogTarget } from './RenewalCloseDialog';
import { FreezeDealDialog } from './FreezeDealDialog';
import { EmptyState } from '../../components/ui/EmptyState';
import { Button } from '../../components/ui/Button';
import { FROZEN_STAGE_KEY, type RenewalBoard as RenewalBoardData, type RenewalCard, type RenewalFilters } from '../../lib/renewals';

interface Props {
  filters: RenewalFilters;
  onOpen: (id: number) => void;
}

/**
 * Зона-мишень закрытия сделки («Продлён»/«Ушёл»). Показывается только пока
 * тащат карточку — вместо вечно пустых терминальных колонок на доске.
 */
function CloseZone({ id, label, tone }: { id: string; label: string; tone: 'won' | 'lost' }) {
  const { setNodeRef, isOver } = useDroppable({ id });
  return (
    <div
      ref={setNodeRef}
      className={`renewal-close-zone renewal-close-zone--${tone}${isOver ? ' renewal-close-zone--over' : ''}`}
    >
      {label}
    </div>
  );
}

export function RenewalBoard({ filters, onOpen }: Props) {
  const qc = useQueryClient();
  const { data, isLoading, isError, refetch } = useRenewalBoard(filters);
  const { data: stages } = useRenewalStages();
  const { move, comment } = useRenewalMutations();
  const showError = useApiError();
  const { toast } = useToast();

  // Карточка, которую сейчас тащат — рендерится отдельно в DragOverlay (портал
  // в document.body), чтобы не обрезаться overflow колонки и не уезжать под неё.
  const [activeCard, setActiveCard] = useState<RenewalCard | null>(null);
  // Сделка, ожидающая подтверждения закрытия в диалоге (drop на зону закрытия).
  const [closeTarget, setCloseTarget] = useState<CloseDialogTarget | null>(null);
  // Сделка, брошенная в «Заморожен»: move требует месяц окончания, поэтому
  // сначала спрашиваем его диалогом.
  const [freezeTarget, setFreezeTarget] = useState<
    { dealId: number; studentName: string; stageId: number } | null
  >(null);

  // Фоновый refetch упал, но данные с прошлого раза есть — доску не сносим
  // (см. ветку isError ниже), иначе она исчезала бы из-под рук. Но и молчать
  // нельзя: на экране висели бы устаревшие карточки без единого признака,
  // что связь потеряна.
  const staleAfterError = isError && !!data;
  useEffect(() => {
    if (staleAfterError) toast('Доска не обновилась — показаны прежние данные', 'error');
  }, [staleAfterError, toast]);

  // Терминальные стадии больше не колонки — их id нужны только для move при закрытии.
  const wonStage = (stages || []).find((s) => s.kind === 'won');
  const lostStage = (stages || []).find((s) => s.kind === 'lost');

  // Небольшой порог перед стартом драга — иначе клик по карточке (открытие
  // drawer'а) будет постоянно перехватываться сенсором как начало drag'а.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );

  // Ключ должен совпадать с queryKey внутри useRenewalBoard (['renewals','board',filters]).
  const queryKey = ['renewals', 'board', filters] as const;

  // Данные карточки едут в event.active.data (см. RenewalCardView) — поэтому
  // перенос работает и для карточек из «Показать ещё»/поиска, которых нет в кэше доски.
  const dragCard = (event: DragStartEvent | DragEndEvent): RenewalCard | undefined =>
    (event.active.data.current as { card?: RenewalCard } | undefined)?.card;
  const dragFromStage = (event: DragEndEvent): number | undefined =>
    (event.active.data.current as { fromStageId?: number } | undefined)?.fromStageId;

  const handleDragStart = (event: DragStartEvent) => {
    setActiveCard(dragCard(event) ?? null);
  };

  const handleDragEnd = (event: DragEndEvent) => {
    setActiveCard(null);
    const { active, over } = event;
    if (!over) return;
    const dealId = Number(active.id);

    // Зона «Ушёл» — терминальная стадия 'churned' (kind='lost') не показана
    // колонкой на доске (board() её исключает), это единственный способ на
    // неё «бросить». Каскадов по членствам и расписанию больше нет: это
    // обычное закрытие сделки с причиной (спека 2026-07-25).
    if (over.id === 'close-lost') {
      const card = dragCard(event);
      if (card) {
        setCloseTarget({
          dealId,
          studentId: card.student_id,
          studentName: card.student_name,
          mode: 'lost',
        });
      }
      return;
    }

    // Зона «Продлён» — по-прежнему ручное решение менеджера с диалогом
    // (оплата сама по себе не закрывает сделку, см. RenewalCloseDialog).
    if (over.id === 'close-won') {
      const card = dragCard(event);
      if (card) {
        setCloseTarget({
          dealId,
          studentId: card.student_id,
          studentName: card.student_name,
          mode: 'won',
        });
      }
      return;
    }

    const toStageId = Number(over.id);
    const fromStageId = dragFromStage(event);
    if (fromStageId === toStageId) return; // бросили в тот же столбец — не трогаем

    // Авто-стадии двигает только движок (transitions.is_allowed блокирует
    // ручной вход) — колонка «Урок 1–4» вообще не droppable (RenewalColumn),
    // но «Ждём оплату»/«Ждём продление» технически droppable.
    const targetStage = (stages || []).find((s) => s.id === toStageId);
    if (targetStage?.is_auto) {
      toast('Эта стадия управляется системой', 'info');
      return;
    }

    // «Заморожен» — обычная ручная стадия, но move требует месяц окончания,
    // поэтому вместо тихого переноса спрашиваем его диалогом.
    if (targetStage?.key === FROZEN_STAGE_KEY) {
      const card = dragCard(event);
      if (card) {
        setFreezeTarget({ dealId, studentName: card.student_name, stageId: toStageId });
      }
      return;
    }

    const prev = qc.getQueryData<RenewalBoardData>(queryKey);
    const movedCard = dragCard(event);

    // Оптимистично правим кэш доски: колонки-стадии в нём есть всегда, поэтому
    // счётчики и целевая колонка обновятся сразу. Сама карточка могла быть из
    // «хвоста»/поиска (нет в cards исходной колонки) — тогда она визуально уедет
    // после инвалидации; лишний «фантом» в источнике снимает сброс extraCards.
    if (prev) {
      const next: RenewalBoardData = {
        columns: prev.columns.map((col) => {
          if (col.stage_id === fromStageId) {
            return { ...col, count: Math.max(0, col.count - 1), cards: col.cards.filter((c) => c.id !== dealId) };
          }
          if (col.stage_id === toStageId && movedCard && !col.cards.some((c) => c.id === dealId)) {
            return { ...col, count: col.count + 1, cards: [{ ...movedCard, days_in_stage: 0 }, ...col.cards] };
          }
          return col;
        }),
      };
      qc.setQueryData(queryKey, next);
    }

    // move сам инвалидирует ['renewals'] onSuccess — здесь откат + сообщение об ошибке
    // (409 «переход запрещён» и т.п. — бэк уже возвращает человекочитаемый текст в error).
    move.mutate(
      { id: dealId, to_stage_id: toStageId },
      {
        onError: (err) => {
          if (prev) qc.setQueryData(queryKey, prev);
          showError(err, 'Не удалось переместить сделку');
        },
      },
    );
  };

  // Подтверждение диалога закрытия: move в терминальную стадию,
  // комментарий (если ввели) — отдельной записью таймлайна.
  const handleCloseConfirm = ({ reason_code, comment: text }: { reason_code?: string; comment?: string }) => {
    if (!closeTarget) return;
    const stage = closeTarget.mode === 'won' ? wonStage : lostStage;
    if (!stage) {
      setCloseTarget(null);
      return;
    }
    move.mutate(
      { id: closeTarget.dealId, to_stage_id: stage.id, reason_code },
      {
        onSuccess: () => {
          if (text) comment.mutate({ id: closeTarget.dealId, body: text });
          setCloseTarget(null);
        },
        onError: (err) => {
          setCloseTarget(null);
          showError(err, 'Не удалось закрыть сделку');
        },
      },
    );
  };

  if (isLoading) {
    return <BoardSkeleton stageLabels={(stages || [])
      .filter((s) => s.kind !== 'won' && s.kind !== 'lost')
      .map((s) => s.label)} />;
  }

  // Без этой ветки сбитый бэкенд давал пустую доску, неотличимую от «никого
  // нет» — менеджер видел бы нулевую воронку и поверил бы ей.
  //
  // Условие именно `&& !data`: у запроса keepPreviousData, а каждая мутация
  // инвалидирует ['renewals'], поэтому упавший ФОНОВЫЙ refetch уже показанной
  // доски тоже переводит запрос в error. Без проверки данных доска исчезала бы
  // из-под рук — в том числе посреди перетаскивания карточки. Про фоновую
  // ошибку сообщает тост (см. эффект ниже), доска при этом остаётся на экране.
  if (isError && !data) {
    return (
      <EmptyState
        hint="Проверьте соединение и попробуйте ещё раз"
        action={<Button variant="secondary" onClick={() => refetch()}>Повторить</Button>}
      >
        Не удалось загрузить продления
      </EmptyState>
    );
  }

  const columns = data?.columns || [];

  return (
    <DndContext
      sensors={sensors}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onDragCancel={() => setActiveCard(null)}
    >
      <div className="renewal-board">
        {columns.map((col) => (
          <RenewalColumn key={col.stage_id} col={col} filters={filters} onOpen={onOpen} />
        ))}
      </div>
      {activeCard && (
        <div className="renewal-close-zones">
          {/* Пока цикл (4 урока) не завершён — «Продлён» вручную недоступен
              (move всё равно ответит 409, см. transitions.py), поэтому зону
              для такой сделки не показываем вовсе — нечего бросать мимо. */}
          {activeCard.cycle_completed && (
            <CloseZone id="close-won" label="✓ Продлён" tone="won" />
          )}
          <CloseZone id="close-lost" label="✕ Ушёл" tone="lost" />
        </div>
      )}
      <DragOverlay>
        {activeCard && (
          <div className="renewal-card renewal-card--overlay">
            <RenewalCardContent card={activeCard} />
          </div>
        )}
      </DragOverlay>
      {closeTarget && (
        <RenewalCloseDialog
          target={closeTarget}
          pending={move.isPending}
          onClose={() => setCloseTarget(null)}
          onConfirm={handleCloseConfirm}
        />
      )}
      {freezeTarget && (
        <FreezeDealDialog
          studentName={freezeTarget.studentName}
          pending={move.isPending}
          onClose={() => setFreezeTarget(null)}
          onConfirm={(frozen_until_month) => {
            move.mutate(
              { id: freezeTarget.dealId, to_stage_id: freezeTarget.stageId, frozen_until_month },
              {
                onSuccess: () => setFreezeTarget(null),
                onError: (err) => {
                  setFreezeTarget(null);
                  showError(err, 'Не удалось заморозить сделку');
                },
              },
            );
          }}
        />
      )}
    </DndContext>
  );
}

/**
 * Каркас доски на время загрузки. Заголовки колонок настоящие, когда справочник
 * стадий уже в кэше (он тянется отдельным запросом с длинным staleTime) —
 * тогда переход к данным не «перерисовывает» экран целиком. Без справочника
 * рисуем пять безымянных колонок: столько стадий в воронке по умолчанию.
 */
function BoardSkeleton({ stageLabels }: { stageLabels: string[] }) {
  const columns = stageLabels.length > 0 ? stageLabels : ['', '', '', '', ''];
  return (
    <div className="renewal-board" aria-busy="true">
      {/* Каркас состоит из пустых плашек — объявлять диктору нечего, поэтому
          вместо aria-live даём одну внятную строку. */}
      <span className="sr-only">Загружаем доску продлений</span>
      {columns.map((label, i) => (
        <div key={i} className="renewal-col">
          {/* Цвет и счётчик стадии приезжают вместе с доской, поэтому в
              каркасе вместо цветной плашки — нейтральная полоса той же
              высоты: при появлении данных колонка не дёргается. */}
          <div className="rnl-skeleton__head">
            {label
              ? <span className="rnl-skeleton__head-label">{label}</span>
              : <div className="skeleton-block rnl-skeleton__title" />}
          </div>
          <div className="renewal-col__body">
            {[0, 1, 2, 3].map((j) => (
              <div key={j} className="rnl-skeleton-card">
                {/* Первый ряд повторяет структуру карточки — кружок аватара
                    плюс имя, а не одну полоску: иначе каркас ниже настоящей
                    карточки, и при появлении данных колонка дёргается. */}
                <div className="rnl-skeleton__row">
                  <div className="skeleton-block rnl-skeleton__avatar" />
                  <div className="skeleton-block rnl-skeleton__line rnl-skeleton__line--name" />
                </div>
                <div className="skeleton-block rnl-skeleton__line rnl-skeleton__line--dir" />
                <div className="skeleton-block rnl-skeleton__line rnl-skeleton__line--meta" />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
