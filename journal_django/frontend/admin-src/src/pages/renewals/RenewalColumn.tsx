import { useDeferredValue, useEffect, useRef, useState } from 'react';
import { useDroppable } from '@dnd-kit/core';
import { RenewalCardView } from './RenewalCardView';
import { BoardColumnHead } from '../../components/board/BoardColumnHead';
import { TextInput } from '../../components/form/TextInput';
import { IconButton } from '../../components/ui/IconButton';
import { EmptyState } from '../../components/ui/EmptyState';
import { fetchRenewalColumnCards, useRenewalColumnSearch } from '../../hooks/useRenewals';
import { useApiError } from '../../hooks/useApiError';
import type { RenewalCard, RenewalColumn as RenewalColumnData, RenewalFilters } from '../../lib/renewals';

interface Props {
  col: RenewalColumnData;
  filters: RenewalFilters;
  onOpen: (id: number) => void;
}

export function RenewalColumn({ col, filters, onOpen }: Props) {
  // Прогресс-стадии («Не было урока», «Урок 1–3») двигает только движок по
  // событиям посещаемости/оплаты — вручную перетащить карточку СЮДА нельзя
  // (droppable отключён), бэк на move в такую стадию тоже ответит 409.
  // Забрать карточку ИЗ такой колонки (заморозить, отметить ушедшим) можно.
  const isAutoOnly = col.kind === 'progress';
  const { setNodeRef, isOver } = useDroppable({ id: col.stage_id, disabled: isAutoOnly });
  const showError = useApiError();

  // Поиск по имени ученика в ЭТОЙ колонке (server-side, ILIKE): ищем на сервере,
  // а не по загруженным карточкам — иначе ученик из непрогруженного «хвоста»
  // колонки не найдётся. Поле свёрнуто в лупу: восемь одинаковых инпутов
  // подряд — шум, а глобальный поиск живёт в панели фильтров.
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState('');
  const searchToggleRef = useRef<HTMLButtonElement>(null);
  const deferredSearch = useDeferredValue(search.trim());
  const searching = deferredSearch.length > 0;

  // Колонночный поиск СУЖАЕТ глобальный: два ILIKE по одному полю не сложить,
  // поэтому внутри своей колонки её строка перекрывает общий фильтр.
  const colFilters: RenewalFilters = searching
    ? { ...filters, student: deferredSearch }
    : filters;

  const { data: searchData, isFetching: searchFetching } =
    useRenewalColumnSearch(col.stage_id, colFilters, searching);

  // Источник карточек: результат поиска либо данные доски.
  const baseCards = searching ? (searchData?.cards ?? []) : col.cards;
  const count = searching ? (searchData?.count ?? 0) : col.count;

  const [extraCards, setExtraCards] = useState<RenewalCard[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);

  // Фильтры/поиск сменились, либо счётчик колонки изменился (карточку перенесли
  // в неё/из неё, добавили оплату) — старая догрузка «Показать ещё» больше не
  // актуальна (иначе перенесённая карточка осталась бы «фантомом»), начинаем с нуля.
  const colFiltersKey = JSON.stringify(colFilters);
  useEffect(() => {
    setExtraCards([]);
  }, [col.stage_id, colFiltersKey, col.count]);

  const cards = [...baseCards, ...extraCards];
  const hasMore = count > cards.length;

  const handleShowMore = async () => {
    setLoadingMore(true);
    try {
      const more = await fetchRenewalColumnCards(col.stage_id, cards.length, colFilters);
      setExtraCards((prev) => [...prev, ...more.cards]);
    } catch (err) {
      showError(err, 'Не удалось догрузить карточки');
    } finally {
      setLoadingMore(false);
    }
  };

  // Закрытие поиска размонтирует и поле, и кнопку «Закрыть» — фокус улетел бы
  // на <body>, и клавиатурный обход ленты из восьми колонок начинался бы
  // заново. Возвращаем его на лупу, из которой поиск и открыли.
  const closeSearch = () => {
    setSearch('');
    setSearchOpen(false);
    requestAnimationFrame(() => searchToggleRef.current?.focus());
  };
  const showSearchSpinner = searching && searchFetching && cards.length === 0;

  return (
    <div
      ref={setNodeRef}
      className={`renewal-col${isOver ? ' renewal-col--over' : ''}`}
    >
      {/* Развёрнутый поиск ЗАМЕЩАЕТ плашку стадии, а не живёт внутри неё:
          плашка тянется от края до края колонки, и поле ввода на цветной
          заливке читалось бы хуже, чем на фоне колонки. */}
      {searchOpen ? (
        <div className="renewal-col__search">
          <TextInput
            className="renewal-col__search-input"
            value={search}
            autoFocus
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Escape') closeSearch(); }}
            placeholder="Имя ученика…"
            aria-label={`Поиск ученика в стадии «${col.label}»`}
          />
          <IconButton
            size="sm"
            label="Закрыть поиск"
            onClick={closeSearch}
            icon={<CloseGlyph />}
          />
        </div>
      ) : (
        <BoardColumnHead
          label={col.label}
          count={count}
          color={col.color}
          badge={isAutoOnly ? (
            <span
              className="renewal-col__auto-badge"
              title="Двигает только система по событиям — вручную перенести сделку сюда нельзя"
            >
              авто
            </span>
          ) : undefined}
          actions={(
            /* Подсветка при активном поиске: иначе свёрнутый фильтр невидим
               и «пропавшие» карточки нечем объяснить. */
            <IconButton
              ref={searchToggleRef}
              size="sm"
              label={`Поиск в стадии «${col.label}»`}
              active={searching}
              onClick={() => setSearchOpen(true)}
              icon={<SearchGlyph />}
            />
          )}
        />
      )}

      {/* aria-live на области карточек: колонка перерисовывается от поиска,
          и без объявления незрячий пользователь не узнаёт, что список сменился. */}
      <div className="renewal-col__body" aria-live="polite">
        {showSearchSpinner ? (
          <div className="renewal-col__note">Ищем…</div>
        ) : cards.length === 0 ? (
          searching ? (
            <div className="renewal-col__note">Никого не найдено</div>
          ) : (
            <EmptyState hint="На этой стадии пока никого нет">Нет учеников</EmptyState>
          )
        ) : (
          cards.map((card) => (
            <RenewalCardView key={card.id} card={card} stageId={col.stage_id} onOpen={onOpen} />
          ))
        )}
      </div>

      {hasMore && (
        <button
          type="button"
          className="btn ghost sm renewal-col__more"
          disabled={loadingMore}
          onClick={handleShowMore}
        >
          {loadingMore ? 'Загружаем…' : `Показать ещё (${count - cards.length})`}
        </button>
      )}
    </div>
  );
}

function SearchGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}

function CloseGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}
