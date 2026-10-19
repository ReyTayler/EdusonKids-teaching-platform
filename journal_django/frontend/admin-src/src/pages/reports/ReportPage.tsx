import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Checkbox } from '../../components/form/Checkbox';
import { Field } from '../../components/form/Field';
import { SelectInput } from '../../components/form/SelectInput';
import { PageHeader } from '../../components/shell/PageHeader';
import { MONTHS_RU, findReportType, type ReportTypeDef } from '../../lib/reports';
import { downloadReport, useReportRun } from '../../hooks/useReports';

const now = new Date();
const CURRENT_YEAR = now.getFullYear();
const YEARS = [CURRENT_YEAR, CURRENT_YEAR - 1, CURRENT_YEAR - 2];

/** Настройка и запуск одного отчёта: поля → «Сформировать» → авто-скачивание. */
function ReportForm({ def }: { def: ReportTypeDef }) {
  const [year, setYear] = useState(CURRENT_YEAR);
  const [month, setMonth] = useState(now.getMonth() + 1); // 1..12
  const [toggles, setToggles] = useState<Record<string, boolean>>({});
  const [downloaded, setDownloaded] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const { run, taskId, status, isBusy, triggerError, statusError } = useReportRun(def.reportType);

  // Скачиваем ровно один раз на задачу, как только она готова.
  const downloadedFor = useRef<string | null>(null);
  useEffect(() => {
    if (status?.state === 'SUCCESS' && taskId && downloadedFor.current !== taskId) {
      downloadedFor.current = taskId;
      setDownloadError(null);
      downloadReport(taskId, status.filename)
        .then(() => setDownloaded(true))
        .catch((e) => setDownloadError(e instanceof Error ? e.message : 'Ошибка скачивания'));
    }
  }, [status?.state, status?.filename, taskId]);

  // Не даём выбрать будущий месяц (бэк тоже валидирует).
  const isFuture = !def.noPeriod
    && (year > CURRENT_YEAR || (year === CURRENT_YEAR && month > now.getMonth() + 1));
  const errorMessage =
    triggerError?.message ?? statusError?.message ?? (status?.state === 'FAILURE' ? status.error : null);

  const start = () => {
    setDownloaded(false);
    setDownloadError(null);
    run(def.buildParams(year, month, toggles));
  };

  return (
    <>
      <PageHeader
        crumbs={[{ label: 'Отчёты', to: '/admin/reports' }, { label: def.title }]}
        title={def.title}
        sub={def.desc}
      />
      <section className="report-page">
        <form
          className="report-form"
          onSubmit={(e) => { e.preventDefault(); start(); }}
        >
          <div className="report-form__fields">
            {!def.noPeriod && (
              <>
                <Field label={def.monthLabel ?? 'Месяц'}>
                  <SelectInput
                    value={month}
                    onChange={(e) => { setMonth(Number(e.target.value)); setDownloaded(false); }}
                    options={MONTHS_RU.map((label, i) => ({ value: i + 1, label }))}
                    disabled={isBusy}
                  />
                </Field>
                <Field label="Год">
                  <SelectInput
                    value={year}
                    onChange={(e) => { setYear(Number(e.target.value)); setDownloaded(false); }}
                    options={YEARS.map((y) => ({ value: y, label: String(y) }))}
                    disabled={isBusy}
                  />
                </Field>
              </>
            )}
            {def.toggles?.map((t) => (
              <div key={t.key} className="report-form__toggle">
                <Checkbox
                  label={t.label}
                  checked={Boolean(toggles[t.key])}
                  onChange={(e) => {
                    const { checked } = e.target;
                    setToggles((prev) => ({ ...prev, [t.key]: checked }));
                    setDownloaded(false);
                  }}
                  disabled={isBusy}
                />
                {t.hint && <span className="report-form__hint">{t.hint}</span>}
              </div>
            ))}
          </div>

          <div className="report-form__submit">
            <button type="submit" className="btn-add" disabled={isBusy || isFuture}>
              {isBusy ? 'Формируется…' : 'Сформировать отчёт'}
            </button>
            <span className="report-form__hint">
              Excel-файл формируется в фоне и скачается автоматически — на платформе он не хранится.
            </span>
          </div>

          <div className="report-form__status" role="status">
            {isFuture && (
              <span className="report-form__hint report-form__hint--warn">
                Выбран будущий месяц — по нему ещё нет данных.
              </span>
            )}
            {isBusy && (
              <span className="report-form__hint">
                Формируем отчёт, это может занять несколько секунд…
              </span>
            )}
            {downloaded && !isBusy && (
              <span className="report-form__hint report-form__hint--ok">
                Готово — файл{status?.row_count != null ? ` (${status.row_count} строк)` : ''} скачан.
              </span>
            )}
            {(errorMessage || downloadError) && (
              <span className="report-form__hint report-form__hint--error">
                {errorMessage ?? downloadError}
              </span>
            )}
          </div>
        </form>
      </section>
    </>
  );
}

/**
 * Страница одного отчёта: настраиваемые поля и кнопка «Сформировать».
 *
 * Тип отчёта приходит из адреса, поэтому страницу можно дать ссылкой. Форма
 * вынесена в отдельный компонент: хуки генерации нельзя вызывать до проверки
 * того, что такой отчёт вообще существует.
 */
export default function ReportPage() {
  const { reportType = '' } = useParams();
  const def = findReportType(reportType);

  if (!def) {
    return (
      <>
        <PageHeader
          crumbs={[{ label: 'Отчёты', to: '/admin/reports' }, { label: 'Отчёт не найден' }]}
          title="Отчёт не найден"
        />
        <section className="report-page">
          <p className="report-form__hint">
            Такого отчёта нет — возможно, ссылка устарела.{' '}
            <Link to="/admin/reports">Вернуться к списку отчётов</Link>
          </p>
        </section>
      </>
    );
  }

  return <ReportForm def={def} />;
}
