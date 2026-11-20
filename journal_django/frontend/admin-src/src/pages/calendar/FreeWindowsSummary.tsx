import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useFreeWindows } from '../../hooks/useFreeWindows';
import { useToast } from '../../components/ui/Toast';
import { Button } from '../../components/ui/Button';
import { TableFilterPanel, type FilterField } from '../../components/filters';
import { EmptyState } from '../../components/ui/EmptyState';
import { BlockLoading } from '../../components/ui/Skeleton';
import { DOW_SHORT, WEEK_DOW, formatIntervals, toMinutes } from '../../shared/availability/grid';
import type { FreeWindowsTeacher } from '../../lib/shared-types';

/** Строки сводки Пн…Вс + «Итого» — тот же формат, что у сводки «Графика работы». */
function summaryLines(t: FreeWindowsTeacher): string[] {
  const days = t.days ?? {};
  let minutes = 0;
  const lines = WEEK_DOW.map((dow) => {
    const ivs = days[String(dow)] ?? [];
    for (const [s, e] of ivs) minutes += toMinutes(e) - toMinutes(s);
    return `${DOW_SHORT[dow]} ${formatIntervals(ivs)}`;
  });
  // Окна не обязаны быть кратны получасу (уроки вычтены по минутам) — округляем до 0,1 ч.
  const hours = Math.round(minutes / 6) / 10;
  lines.push(`Итого ${hours.toLocaleString('ru-RU')} ч/нед`);
  return lines;
}

function blockText(t: FreeWindowsTeacher): string {
  return [t.name, ...summaryLines(t)].join('\n');
}

const SEARCH_FIELDS: FilterField[] = [{ kind: 'text', key: 'q', label: 'Имя преподавателя' }];

export default function FreeWindowsSummary() {
  const { data, isLoading, isError } = useFreeWindows();
  const { toast } = useToast();
  const [query, setQuery] = useState('');

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const all = data?.teachers ?? [];
    return q ? all.filter((t) => t.name.toLowerCase().includes(q)) : all;
  }, [data, query]);

  const copiable = visible.filter((t) => t.has_schedule && t.days);

  // clipboard недоступен вне https или без разрешения — говорим об этом, а не молчим.
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast('Скопировано', 'ok');
    } catch {
      toast('Не удалось скопировать — выделите текст и скопируйте вручную', 'error');
    }
  };

  if (isLoading) return <BlockLoading rows={4} label="Загружаем свободные окна…" />;
  if (isError) {
    return (
      <EmptyState hint="Обновите страницу или попробуйте чуть позже.">
        Не удалось загрузить свободные окна
      </EmptyState>
    );
  }

  return (
    <div className="free-windows">
      <div className="free-windows__toolbar">
        <TableFilterPanel
          fields={SEARCH_FIELDS}
          filters={query ? { q: query } : {}}
          onApply={(next) => setQuery(next.q ?? '')}
          label="Поиск по преподавателям"
        />
        <Button
          variant="secondary"
          disabled={copiable.length === 0}
          onClick={() => { void copy(copiable.map(blockText).join('\n\n')); }}
        >
          Копировать всё
        </Button>
      </div>

      {visible.length === 0 ? (
        <EmptyState hint={query ? 'Попробуйте изменить запрос.' : undefined}>
          {query ? 'Никого не найдено' : 'Нет преподавателей'}
        </EmptyState>
      ) : (
        <div className="free-windows__grid">
          {visible.map((t) => (
            <section key={t.id} className="free-windows__card">
              <header className="free-windows__head">
                <Link to={`/admin/teachers/${t.id}`} className="free-windows__name">{t.name}</Link>
                {t.has_schedule && t.days && (
                  <Button size="sm" onClick={() => { void copy(blockText(t)); }}>Копировать</Button>
                )}
              </header>
              {t.has_schedule && t.days ? (
                <div className="free-windows__text">{summaryLines(t).join('\n')}</div>
              ) : (
                <div className="free-windows__text free-windows__text--empty">График не задан</div>
              )}
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
