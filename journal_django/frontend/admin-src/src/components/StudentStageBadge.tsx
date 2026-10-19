import type { Student } from '../lib/types';
import { stageTone } from '../lib/renewals';
import { fmtMonth } from '../lib/format';

type StageLike = Pick<Student, 'stage' | 'stage_is_open' | 'stage_frozen_until_month'>;

/**
 * «Статус» ученика = стадия его последней сделки продления (спека 2026-07-25).
 * Закрытая сделка (won/lost) — тот же бейдж, но приглушённый: ушедший ученик
 * остаётся визуально ушедшим, при этом не спорит по весу с активными.
 *
 * Тон берётся из общего stageTone(), чтобы одна и та же стадия выглядела
 * одинаково здесь и в разделе «Продления» (StageBadge).
 */
export function StudentStageBadge({ row }: { row: StageLike }) {
  const { stage } = row;
  if (!stage) return <>—</>;

  // «Заморожен · до сентября 2026»: день в frozen_until_month всегда 1-е и
  // смысла не несёт, поэтому месяц без дня (fmtMonth).
  const label = row.stage_frozen_until_month
    ? `${stage.label} · до ${fmtMonth(row.stage_frozen_until_month)}`
    : stage.label;

  return (
    <span
      className={`status-badge status-badge--${stageTone(stage.kind)}${row.stage_is_open ? '' : ' status-badge--dim'}`}
      title={row.stage_is_open ? undefined : 'Сделка закрыта'}
    >
      {label}
    </span>
  );
}
