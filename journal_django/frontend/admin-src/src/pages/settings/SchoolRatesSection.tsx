import { HelpPopover } from '../../components/ui/HelpPopover';
import { useState } from 'react';
import { useRateVersions, useRateVersionMutations } from '../../hooks/useMotivation';
import { ConfirmModal } from '../../components/ui/ConfirmModal';
import { EmptyState } from '../../components/ui/EmptyState';
import { DirTag } from '../../components/ui/DirTag';
import { BlockLoading } from '../../components/ui/Skeleton';
import { useToast } from '../../components/ui/Toast';
import { useApiError } from '../../hooks/useApiError';
import { fmtDate, fmtDateTime, fmtRub, todayMSK } from '../../lib/format';
import type { RateVersion, RateVersionsResponse } from '../../lib/types';
import { RateVersionDialog } from './RateVersionDialog';
import { cancelText, penaltyText, savedMessage } from './motivationFormat';

type Format = 'group' | 'individual';
type DialogState = { base: RateVersion | null; editing: RateVersion | null } | null;

function Rates({ version, directions, format }: {
  version: RateVersion; directions: RateVersionsResponse['directions']; format: Format;
}) {
  const key = format === 'group' ? 'group_rate' : 'individual_rate';
  const ownRates = new Map(version.directions.map((item) => [item.direction_id, item[key]]));
  return <div className="motivation-rules" role="list" aria-label="Ставки по направлениям">
    <div className="motivation-rule" role="listitem">
      <div><strong>По умолчанию</strong><span>Для направлений без собственной ставки</span></div>
      <div className="motivation-rule__amount"><strong>{fmtRub(version[key])}</strong><span>за занятие</span></div>
    </div>
    {directions.map((direction) => {
      const own = ownRates.get(direction.id);
      return <div className="motivation-rule" role="listitem" key={direction.id}>
        <div><DirTag name={direction.name} color={direction.color} /><span>{own == null ? 'Ставка по умолчанию' : 'Собственная ставка направления'}</span></div>
        <div className="motivation-rule__amount"><strong>{fmtRub(own ?? version[key])}</strong><span>за занятие</span></div>
      </div>;
    })}
  </div>;
}

export function SchoolRatesSection({ format = 'group' }: { format?: Format }) {
  const query = useRateVersions();
  const { remove } = useRateVersionMutations();
  const { toast } = useToast();
  const apiError = useApiError();
  const [dialog, setDialog] = useState<DialogState>(null);
  const [deleting, setDeleting] = useState<RateVersion | null>(null);
  const [openVersions, setOpenVersions] = useState<Set<number>>(() => new Set());
  if (query.isLoading) return <BlockLoading rows={4} />;
  if (query.isError || !query.data) return <EmptyState
    hint="Не удалось получить данные. Отсутствие ставок не подтверждено."
    action={<button type="button" className="btn-cancel" onClick={() => query.refetch()}>Повторить</button>}>
    Не удалось загрузить ставки школы
  </EmptyState>;
  const { results, no_rate, directions } = query.data;
  const current = results.find((version) => version.is_current) ?? null;
  const next = [...results].filter((version) => version.effective_from > todayMSK())
    .sort((a, b) => a.effective_from.localeCompare(b.effective_from))[0];
  const title = format === 'group' ? 'Групповые занятия' : 'Индивидуальные занятия';
  return <section className="motivation-section" aria-labelledby={`school-${format}-title`}>
    <div className="motivation-section__head">
      <div><h2 id={`school-${format}-title`}>{title} <HelpPopover>Фиксированная оплата за занятие, со ставками по направлениям. Количество учеников и длительность не меняют базовую ставку. Групповые и индивидуальные ставки, компенсация отмены и штраф входят в одну версию школы.</HelpPopover></h2></div>
      <button type="button" className="btn-primary" onClick={() => setDialog({ base: current, editing: null })}>
        {current ? 'Изменить с даты…' : 'Задать ставки'}
      </button>
    </div>
    {no_rate.lessons > 0 && <div className="settings-warn" role="status">Уроков без ставки: {no_rate.lessons}
      {no_rate.earliest && ` · самый ранний ${fmtDate(no_rate.earliest)}`}. За них зарплата не начисляется.</div>}
    {next && <div className="motivation-notice" role="status"><span>Следующее изменение — с {fmtDate(next.effective_from)}</span>
      <button type="button" className="motivation-text-button" onClick={() => setDialog({ base: next, editing: next })}>Открыть версию</button></div>}
    <div className="motivation-panel">
      <div className="motivation-panel__head"><h3>Действующие условия</h3>
        <span className="motivation-status">{current ? `Действует с ${fmtDate(current.effective_from)}` : 'Ставки не заданы'}</span></div>
      {current ? <Rates version={current} directions={directions} format={format} /> :
        <p className="motivation-panel__note">Пока на дату занятия нет версии ставок, начисление равно 0 ₽. Задайте первую версию с даты начала оплаты.</p>}

    </div>
    {current && <div className="motivation-secondary">
      <div className="motivation-panel"><div className="motivation-panel__head"><h3>Компенсация отмены <HelpPopover>Для отмен с компенсацией. Решение об оплате определяется правилами причины отмены.</HelpPopover></h3></div>
        <div className="motivation-rule-summary"><strong>{cancelText(current)}</strong>
          </div></div>
      <div className="motivation-panel"><div className="motivation-panel__head"><h3>Поздний отчёт <HelpPopover>Только отчёт преподавателя позже дня занятия. Ставка и штраф хранятся раздельно.</HelpPopover></h3>
        <span className="motivation-status">{current.penalty_enabled ? 'Штраф включён' : 'Без штрафа'}</span></div>
        <div className="motivation-rule-summary"><strong>{current.penalty_enabled ? penaltyText(current) : '0 ₽'}</strong>
          </div></div>
    </div>}
    <details className="motivation-history"><summary>История и будущие изменения <span>{results.length} верс.</span></summary>
      <div className="motivation-history__body">
        {!results.length && <p className="motivation-meta">Первая сохранённая версия появится здесь.</p>}
        {results.map((version) => <details className="motivation-history__entry" key={version.id}
          onToggle={(event) => {
            const open = event.currentTarget.open;
            setOpenVersions((previous) => {
              if (previous.has(version.id) === open) return previous;
              const next = new Set(previous);
              if (open) next.add(version.id); else next.delete(version.id);
              return next;
            });
          }}>
          <summary>С {fmtDate(version.effective_from)} <span className="motivation-status">
            {version.is_current ? 'Действует' : version.effective_from > todayMSK() ? 'Запланирована' : 'Архив'}</span></summary>
          {openVersions.has(version.id) && <div className="motivation-history__content">
            <Rates version={version} directions={directions} format={format} />
            <p>Отмена: {cancelText(version)} · Штраф: {penaltyText(version)}</p>
            <p className="motivation-meta">{version.created_by_name || 'Автор не указан'} · {fmtDateTime(version.created_at)}</p>
            <div className="motivation-history__actions">
              <button type="button" className="btn-cancel" onClick={() => setDialog({ base: version, editing: null })}>Создать на основе этой версии</button>
              {version.editable ? <><button type="button" className="btn-cancel" onClick={() => setDialog({ base: version, editing: version })}>Исправить версию</button>
                <button type="button" className="motivation-text-button motivation-text-button--danger" onClick={() => setDeleting(version)}>Удалить версию</button></> :
                <span className="motivation-meta">Начавшуюся версию можно только просмотреть</span>}
            </div>
          </div>}
        </details>)}
      </div>
    </details>

    {dialog && <RateVersionDialog directions={directions} base={dialog.base} editing={dialog.editing}
      defaultDate={todayMSK()} onClose={() => setDialog(null)} />}
    {deleting && <ConfirmModal title="Удалить версию ставок?"
      message={`Версия с ${fmtDate(deleting.effective_from)} будет удалена для обоих форматов. Начисления её периода пересчитаются по предыдущей версии, а без неё — в 0 ₽.`}
      confirmLabel="Удалить" danger isPending={remove.isPending} onClose={() => !remove.isPending && setDeleting(null)}
      onConfirm={() => remove.mutate(deleting.id, {
        onSuccess: (result) => { toast(savedMessage(result.recalculated), 'ok'); setDeleting(null); }, onError: (err) => apiError(err),
      })} />}
  </section>;
}
