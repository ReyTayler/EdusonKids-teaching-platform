import { useDraggable } from '@dnd-kit/core';
import { Avatar } from '../../components/Avatar';
import { SLA_OVERDUE_DAYS, type RenewalCard } from '../../lib/renewals';
import { fmtLessons, fmtMonth } from '../../lib/format';

/**
 * Разметка карточки без drag-обвязки — переиспользуется и в самой колонке,
 * и в DragOverlay (там своя, немонтируемая копия, которую dnd-kit носит за курсором).
 */
export function RenewalCardContent({ card }: { card: RenewalCard }) {
  // Порог SLA — не дедлайн: у стадии нет срока, есть граница, после которой
  // сделка считается застрявшей. Поэтому «зависла», а не «просрочено».
  const stuck = card.days_in_stage > SLA_OVERDUE_DAYS;
  // Направления одним цветом: в этом разделе цвет несёт СОСТОЯНИЕ, а не
  // название продукта — иначе крашеный «Python» спорит с именем ученика.
  const dirs = (card.directions || []).map((d) => d.name).join(', ') || '—';
  return (
    <>
      <div className="renewal-card__top">
        <span title={card.assignee_name || 'Не назначен'}>
          <Avatar name={card.assignee_name || '—'} size={22} />
        </span>
        <div className="renewal-card__student">{card.student_name || '—'}</div>
      </div>
      <div className="renewal-card__direction">{dirs} · Цикл {card.cycle_no}</div>
      <div className="renewal-card__meta">
        <span
          className={`renewal-card__age${stuck ? ' is-stuck' : ''}`}
          title="Сколько дней сделка стоит на текущей стадии"
        >
          {stuck ? `Зависла ${card.days_in_stage} дн.` : `В стадии ${card.days_in_stage} дн.`}
        </span>
        {/* Баланс — оплаченные минус посещённые уроки. Два разных сигнала, и
            путать их нельзя: нулевой баланс — это «пора продлевать», а долг —
            «уже занимается в минус». Положительный баланс не показываем вовсе:
            запас уроков есть, действие не требуется. */}
        {card.balance < 0 ? (
          <span className="renewal-card__debt" title="Посещено больше уроков, чем оплачено">
            Долг {fmtLessons(-card.balance)} ур.
          </span>
        ) : card.balance === 0 && (
          <span className="renewal-card__spent" title="Оплаченные уроки закончились — баланс ровно 0">
            Уроки кончились
          </span>
        )}
        {card.frozen_until_month && (
          <span className="renewal-card__frozen" title="Заморозка до месяца">
            до {fmtMonth(card.frozen_until_month)}
          </span>
        )}
      </div>
    </>
  );
}

interface Props {
  card: RenewalCard;
  stageId: number;
  onOpen: (id: number) => void;
}

export function RenewalCardView({ card, stageId, onOpen }: Props) {
  // Данные карточки едут вместе с drag'ом — так доска берёт их прямо из события
  // (event.active.data), а не ищет в кэше. Иначе карточки из «Показать ещё»
  // (локальный стейт) и из поиска (отдельный кэш) не перетаскивались бы.
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: card.id,
    data: { card, fromStageId: stageId },
  });

  return (
    <div
      ref={setNodeRef}
      className={`renewal-card${isDragging ? ' renewal-card--dragging' : ''}`}
      onClick={() => onOpen(card.id)}
      {...listeners}
      {...attributes}
    >
      <RenewalCardContent card={card} />
    </div>
  );
}
