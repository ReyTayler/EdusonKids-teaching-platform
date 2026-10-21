import { useState } from 'react';
import { useRateVersions, useRateVersionMutations } from '../../hooks/useMotivation';
import { ConfirmModal } from '../../components/ui/ConfirmModal';
import { ActionMenu } from '../../components/ui/ActionMenu';
import { EmptyState } from '../../components/ui/EmptyState';
import { DirTag } from '../../components/ui/DirTag';
import { Pill } from '../../components/ui/Pill';
import { BlockLoading } from '../../components/ui/Skeleton';
import { useToast } from '../../components/ui/Toast';
import { useApiError } from '../../hooks/useApiError';
import { fmtDate, fmtDateTime, fmtRub, todayMSK } from '../../lib/format';
import type { RateVersion } from '../../lib/types';
import { RateVersionDialog } from './RateVersionDialog';
import { addDaysISO, cancelText, penaltyText, savedMessage } from './motivationFormat';

type DialogState = { kind: 'create' } | { kind: 'edit'; version: RateVersion } | null;

/**
 * Ставки школы: текущая версия, кнопка «Изменить с даты…», предупреждение об
 * уроках без ставки и история версий.
 */
export function SchoolRatesSection() {
  const q = useRateVersions();
  const { remove } = useRateVersionMutations();
  const { toast } = useToast();
  const apiError = useApiError();
  const [dialog, setDialog] = useState<DialogState>(null);
  const [deleting, setDeleting] = useState<RateVersion | null>(null);

  if (q.isLoading) return <BlockLoading rows={4} />;
  // Упавший запрос не должен выглядеть как «ставки не заданы»: это про деньги.
  if (q.isError || !q.data) {
    return (
      <EmptyState
        hint="Это сбой загрузки, а не отсутствие ставок. Обновите страницу или попробуйте позже."
        action={<button type="button" className="btn-cancel" onClick={() => q.refetch()}>Повторить</button>}
      >
        Не удалось загрузить ставки школы
      </EmptyState>
    );
  }

  const { results, no_rate, directions } = q.data;
  const current = results.find((v) => v.is_current) ?? null;
  // Текущей нет, но версии есть — значит все они в будущем; ближайшая — с минимальной датой.
  const nextVersion = current ? null
    : results.reduce<RateVersion | null>(
      (m, v) => (!m || v.effective_from < m.effective_from ? v : m), null);
  // Новые ставки по умолчанию — со следующего дня: уже прошедшие дни школа
  // отработала по прежним ставкам, и менять их задним числом надо осознанно
  // (админ вправе выбрать любую дату). Если ставок ещё нет — с самого раннего
  // урока без ставки, чтобы сразу посчитать всю историю.
  const defaultDate = current
    ? addDaysISO(todayMSK(), 1)
    : (no_rate.earliest ?? todayMSK());

  const dirRate = (v: RateVersion, id: number) => v.directions.find((d) => d.direction_id === id);

  const confirmDelete = () => {
    if (!deleting) return;
    remove.mutate(deleting.id, {
      onSuccess: (res) => { toast(savedMessage(res.recalculated), 'ok'); setDeleting(null); },
      onError: (e) => { apiError(e, 'Не удалось удалить'); setDeleting(null); },
    });
  };

  return (
    <section className="settings-section" aria-labelledby="school-rates-title">
      <div className="settings-section__head">
        <h2 id="school-rates-title" className="settings-section__title">Ставки школы</h2>
        <button type="button" className="btn-add" onClick={() => setDialog({ kind: 'create' })}>
          {current ? 'Изменить с даты…' : 'Задать ставки'}
        </button>
      </div>

      {no_rate.lessons > 0 && (
        <div className="settings-warn" role="status">
          Уроков без ставки: {no_rate.lessons}
          {no_rate.earliest && ` (самый ранний — ${fmtDate(no_rate.earliest)})`}.
          {no_rate.earliest && ` Задайте ставки с даты не позже ${fmtDate(no_rate.earliest)}, чтобы посчитать всю историю.`}
        </div>
      )}

      {!current ? (
        <EmptyState hint="Пока ставок нет, зарплата преподавателям не начисляется.">
          {nextVersion
            ? `Текущих ставок нет, ближайшая версия действует с ${fmtDate(nextVersion.effective_from)}`
            : 'Ставки ещё не заданы'}
        </EmptyState>
      ) : (
        <div className="table-panel">
          <div className="table-wrap">
            <table className="settings-table">
              <thead>
                <tr><th>Направление</th><th>Групповой урок</th><th>Индивидуальный урок</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td>По умолчанию</td>
                  <td>{fmtRub(current.group_rate)}</td>
                  <td>{fmtRub(current.individual_rate)}</td>
                </tr>
                {directions.map((d) => {
                  const r = dirRate(current, d.id);
                  return (
                    <tr key={d.id}>
                      <td><DirTag name={d.name} color={d.color} /></td>
                      {([
                        [r?.group_rate, current.group_rate],
                        [r?.individual_rate, current.individual_rate],
                      ] as const).map(([own, def], i) => (
                        own != null
                          ? <td key={i}>{fmtRub(own)}</td>
                          : <td key={i} className="settings-muted">как по умолчанию ({fmtRub(def)})</td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="settings-summary">
            <div>Отмена урока: {cancelText(current)}</div>
            <div>Штраф за отчёт не в день урока: {penaltyText(current)}</div>
            <div className="settings-muted">Действует с {fmtDate(current.effective_from)}</div>
          </div>
        </div>
      )}

      {results.length > 0 && (
        <>
          <h3 className="settings-subtitle">История версий</h3>
          <div className="table-panel">
            <div className="table-wrap">
              <table className="settings-table">
                <thead>
                  <tr>
                    <th>С даты</th><th>Групповой</th><th>Индивидуальный</th>
                    <th>Отмена</th><th>Штраф</th><th>Кто изменил</th><th>Когда</th><th aria-label="Действия" />
                  </tr>
                </thead>
                <tbody>
                  {results.map((v) => (
                    <tr key={v.id}>
                      <td>
                        {fmtDate(v.effective_from)} {v.is_current && <Pill>Действует</Pill>}
                      </td>
                      <td>{fmtRub(v.group_rate)}</td>
                      <td>{fmtRub(v.individual_rate)}</td>
                      <td>{cancelText(v)}</td>
                      <td>{penaltyText(v)}</td>
                      <td>{v.created_by_name || '—'}</td>
                      <td>{fmtDateTime(v.created_at)}</td>
                      <td>
                        <ActionMenu
                          label="Действия с версией"
                          items={[
                            { label: 'Исправить', onSelect: () => setDialog({ kind: 'edit', version: v }) },
                            { label: 'Удалить', danger: true, onSelect: () => setDeleting(v) },
                          ]}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {dialog && (
        <RateVersionDialog
          directions={directions}
          base={dialog.kind === 'edit' ? dialog.version : current}
          editing={dialog.kind === 'edit' ? dialog.version : null}
          defaultDate={defaultDate}
          onClose={() => setDialog(null)}
        />
      )}
      {deleting && (
        <ConfirmModal
          title="Удалить версию ставок?"
          message={`Версия «с ${fmtDate(deleting.effective_from)}» будет удалена, начисления этого периода пересчитаются по предыдущей версии.`}
          confirmLabel="Удалить"
          danger
          isPending={remove.isPending}
          onConfirm={confirmDelete}
          onClose={() => setDeleting(null)}
        />
      )}
    </section>
  );
}
