import { useEffect, useState } from 'react';
import { Dialog } from '../../components/ui/Dialog';
import { Button } from '../../components/ui/Button';
import { Field } from '../../components/form/Field';
import { TextInput } from '../../components/form/TextInput';
import { SelectInput } from '../../components/form/SelectInput';
import type { KnowledgeSection } from '../../lib/knowledge';

/**
 * Название документа и его раздел.
 *
 * До этого документ нельзя было ни переименовать, ни перенести из интерфейса
 * вовсе — API умел и то, и другое, а формы под это не было. Название задавалось
 * один раз при создании, и опечатка в нём оставалась навсегда.
 *
 * Оба поля в одном окне намеренно: переносят обычно как раз тогда, когда
 * наводят порядок, и название правят тем же движением. Два отдельных пункта
 * меню заставляли бы открывать окно дважды ради одной задачи.
 */
export function DocumentPropertiesDialog({
  open,
  onClose,
  title,
  sectionId,
  sections,
  saving,
  error,
  onSave,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  sectionId: number;
  sections: KnowledgeSection[];
  saving: boolean;
  error: string | null;
  onSave: (next: { title: string; sectionId: number }) => void;
}) {
  const [draft, setDraft] = useState(title);
  const [section, setSection] = useState(sectionId);

  // Окно живёт вместе со страницей, а не создаётся заново на каждое открытие,
  // поэтому поля надо возвращать к текущим значениям документа. Иначе после
  // отказа от правки в них остаётся прошлый черновик.
  useEffect(() => {
    if (!open) return;
    setDraft(title);
    setSection(sectionId);
  }, [open, title, sectionId]);

  const trimmed = draft.trim();
  const changed = trimmed !== title || section !== sectionId;
  const canSave = trimmed.length > 0 && changed && !saving;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => { if (!next) onClose(); }}
      title="Название и раздел"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>Отмена</Button>
          <Button
            variant="primary"
            disabled={!canSave}
            onClick={() => onSave({ title: trimmed, sectionId: section })}
          >
            {saving ? 'Сохраняем…' : 'Сохранить'}
          </Button>
        </>
      }
    >
      <div className="kb-props-form">
        <Field label="Название" full>
          <TextInput
            value={draft}
            autoFocus
            maxLength={300}
            onChange={(event) => setDraft(event.target.value)}
            // Enter — привычный способ подтвердить короткую форму; тянуться
            // мышью к кнопке ради переименования одного слова незачем.
            onKeyDown={(event) => {
              if (event.key === 'Enter' && canSave) {
                onSave({ title: trimmed, sectionId: section });
              }
            }}
          />
        </Field>

        <Field label="Раздел" full>
          <SelectInput
            value={section}
            options={sections.map((s) => ({ value: s.id, label: s.title }))}
            onChange={(event) => setSection(Number(event.target.value))}
          />
        </Field>

        {error && <p className="kb-dialog__error">{error}</p>}
        {trimmed.length === 0 && (
          <p className="kb-dialog__hint">Название не может быть пустым.</p>
        )}
      </div>
    </Dialog>
  );
}
