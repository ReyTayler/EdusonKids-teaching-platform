import { stageTone, type StageKind } from '../../lib/renewals';

// Бейдж стадии СДЕЛКИ — список сделок, канбан, панель сделки. Стадия ученика
// (последняя сделка) рисуется StudentStageBadge, тон у обоих общий: stageTone().
export function StageBadge({ label, kind }: { label: string; kind: StageKind }) {
  return <span className={`status-badge status-badge--${stageTone(kind)}`}>{label}</span>;
}
