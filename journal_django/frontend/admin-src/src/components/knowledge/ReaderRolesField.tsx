import { Checkbox } from '../form/Checkbox';
import { ROLE_LABELS } from '../../lib/labels';
import type { KnowledgeRole } from '../../lib/knowledge';

// admin и superadmin видят всё всегда — выдавать им доступ галочкой нечего.
const SELECTABLE: KnowledgeRole[] = ['teacher', 'manager'];

/**
 * Кто читает документ. Галочки — Checkbox проекта, подписи — из lib/labels.ts
 * (enum-подписи только оттуда, правило дизайн-системы).
 */
export function ReaderRolesField({
  value,
  onChange,
  disabled,
}: {
  value: KnowledgeRole[];
  onChange: (roles: KnowledgeRole[]) => void;
  disabled?: boolean;
}) {
  const toggle = (role: KnowledgeRole, checked: boolean) => {
    const next = checked ? [...value, role] : value.filter((r) => r !== role);
    onChange([...new Set(next)].sort());
  };

  return (
    <fieldset className="kb-roles">
      <legend className="kb-roles__legend">Кто может читать</legend>
      {SELECTABLE.map((role) => (
        <Checkbox
          key={role}
          checked={value.includes(role)}
          onChange={(e) => toggle(role, e.target.checked)}
          disabled={disabled}
          label={ROLE_LABELS[role] ?? role}
        />
      ))}
      <p className="kb-roles__hint">
        Администраторы видят документ всегда, включая черновики.
      </p>
    </fieldset>
  );
}
