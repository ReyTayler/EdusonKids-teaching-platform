import { useMemo, useState } from 'react';
import { useRecordUrlRule, useSaveRecordUrlRule } from '../../hooks/useRecordUrlRule';
import { useTeachers } from '../../hooks/useTeachers';
import { useGroupsAll } from '../../hooks/useGroups';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { EmptyState } from '../../components/ui/EmptyState';
import { BlockLoading } from '../../components/ui/Skeleton';
import { Field } from '../../components/form/Field';
import { Switch } from '../../components/form/Switch';
import { TokenSelect, type TokenOption } from '../../components/form/TokenSelect';
import { fmtDateTime } from '../../lib/format';
import { RECORD_URL_RULE_LABELS as L } from '../../lib/labels';
import type { RecordUrlRule } from '../../lib/types';

interface Draft { required: boolean; teacherIds: number[]; groupIds: number[] }

const toDraft = (r: RecordUrlRule): Draft => ({
  required: r.required,
  teacherIds: r.teachers.map((t) => t.id),
  groupIds: r.groups.map((g) => g.id),
});

/** Наборы id без учёта порядка: порядок выбора правкой не считается. */
const sameSet = (a: number[], b: number[]) =>
  a.length === b.length && a.every((v) => b.includes(v));

/**
 * «Настройки → Уроки». Страница целиком за RequireRole(superadmin), поэтому роль
 * здесь не проверяем. Правка копится в буфере и уходит одним PUT: сервер заменяет
 * набор исключений целиком.
 */
export function LessonsSettingsTab() {
  const rule = useRecordUrlRule();
  if (rule.isLoading) return <BlockLoading rows={3} />;
  // Упавший запрос не должен выглядеть как «правило по умолчанию».
  if (rule.isError || !rule.data) {
    return (
      <EmptyState
        hint={L.loadErrorHint}
        action={<button type="button" className="btn-cancel" onClick={() => rule.refetch()}>{L.retry}</button>}
      >
        {L.loadErrorTitle}
      </EmptyState>
    );
  }
  // key: после сохранения (новый updated_at) буфер пересоздаётся из ответа сервера.
  return <RuleForm key={rule.data.updated_at ?? 'initial'} rule={rule.data} />;
}

/** Опции из справочника + уже выбранные, но выпавшие из него (неактивные) — чтобы бейдж не пропал. */
function withSelected(
  base: { id: number; name: string }[] | undefined,
  selected: { id: number; name: string }[],
): TokenOption[] {
  const out = (base ?? []).map((x) => ({ value: x.id, label: x.name }));
  for (const s of selected) {
    if (!out.some((o) => o.value === s.id)) out.push({ value: s.id, label: s.name });
  }
  return out;
}

function RuleForm({ rule }: { rule: RecordUrlRule }) {
  const save = useSaveRecordUrlRule();
  const apiError = useApiError();
  const { toast } = useToast();
  const teachers = useTeachers();
  const groups = useGroupsAll();
  const initial = useMemo(() => toDraft(rule), [rule]);
  const [draft, setDraft] = useState<Draft>(initial);

  const teacherOptions = useMemo(() => withSelected(teachers.data, rule.teachers), [teachers.data, rule.teachers]);
  const groupOptions = useMemo(() => withSelected(groups.data, rule.groups), [groups.data, rule.groups]);

  const dirty = draft.required !== initial.required
    || !sameSet(draft.teacherIds, initial.teacherIds)
    || !sameSet(draft.groupIds, initial.groupIds);

  const submit = () => {
    save.mutate(
      { required: draft.required, teacher_ids: draft.teacherIds, group_ids: draft.groupIds },
      {
        onSuccess: () => toast(L.saved, 'ok'),
        // Буфер остаётся как есть — правку не нужно вводить заново.
        onError: (e) => apiError(e, L.saveFailed),
      },
    );
  };

  return (
    <div className="settings-tab">
      <section className="settings-section" aria-labelledby="record-url-rule-title">
        <h2 id="record-url-rule-title" className="settings-section__title">{L.title}</h2>
        <p className="settings-section__lead">{L.lead}</p>

        <Switch
          checked={draft.required}
          onChange={(next) => setDraft((d) => ({ ...d, required: next }))}
          label={L.required}
        />

        <p className="settings-subtitle">{draft.required ? L.requiredOn : L.requiredOff}</p>
        <div className="record-url-rule__grid">
          <Field label={L.teachers}>
            <TokenSelect
              aria-label={L.teachers}
              values={draft.teacherIds}
              options={teacherOptions}
              onChange={(ids) => setDraft((d) => ({ ...d, teacherIds: ids }))}
              placeholder={L.teachersPlaceholder}
              emptyText={L.empty}
            />
          </Field>
          <Field label={L.groups}>
            <TokenSelect
              aria-label={L.groups}
              values={draft.groupIds}
              options={groupOptions}
              onChange={(ids) => setDraft((d) => ({ ...d, groupIds: ids }))}
              placeholder={L.groupsPlaceholder}
              emptyText={L.empty}
            />
          </Field>
        </div>

        <div className="record-url-rule__actions">
          <button type="button" className="btn-save" disabled={!dirty || save.isPending} onClick={submit}>
            {L.save}
          </button>
          {rule.updated_by && rule.updated_at && (
            <span className="settings-muted">{L.changedBy(rule.updated_by, fmtDateTime(rule.updated_at))}</span>
          )}
        </div>
      </section>
    </div>
  );
}
