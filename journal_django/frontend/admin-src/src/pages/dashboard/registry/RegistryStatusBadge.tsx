import { REGISTRY_STATUS_LABELS } from '../../../lib/labels';
import type { RegistryStatus } from '../../../lib/types';

// Бейдж статуса ученика реестра. Цвет — по семантической оси через reg-badge--*.
export function RegistryStatusBadge({ status }: { status: RegistryStatus }) {
  return <span className={`reg-badge reg-badge--${status}`}>{REGISTRY_STATUS_LABELS[status]}</span>;
}
