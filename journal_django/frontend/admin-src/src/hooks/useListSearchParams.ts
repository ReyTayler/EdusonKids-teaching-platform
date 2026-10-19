// web/admin/src/hooks/useListSearchParams.ts
import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

export interface ListSearchState {
  page: number;
  pageSize: number;
  sortBy: string;
  sortDir: 'asc' | 'desc';
  filters: Record<string, string>;
}

export interface ListSearchControls {
  setPage: (page: number) => void;
  setPageSize: (size: number) => void;
  setSort: (col: string, dir: 'asc' | 'desc') => void;
  setFilters: (filters: Record<string, string>) => void;
  /** Установить/удалить один extra-параметр (сбрасывает page). */
  setExtra: (key: string, value: string | null) => void;
  /** Атомарно установить/удалить несколько extra-параметров за один setSp (сбрасывает page). */
  setExtras: (entries: Record<string, string | null>) => void;
  getExtra: (key: string) => string | undefined;
}

interface Defaults {
  sortBy: string;
  sortDir: 'asc' | 'desc';
  pageSize?: number;
}

/**
 * Читает state пагинации из URL search params и даёт сеттеры,
 * которые обновляют URL (replace, чтобы history не засорять).
 *
 * URL формат:
 *   ?page=2&page_size=50&sort_by=lesson_date&sort_dir=desc
 *   &f.full_name=Иван&f.stage_id=4
 *
 * Префикс `f.` для пользовательских фильтров — чтобы не конфликтовать
 * с зарезервированными page/page_size/sort_by/sort_dir/etc.
 *
 * `getExtra`/`setExtra` — для нестандартных доп. параметров (например, date_from/date_to
 * в payroll, или mode в payroll). Хранятся как top-level query-params без префикса.
 */
export function useListSearchParams(defaults: Defaults): ListSearchState & ListSearchControls {
  const [sp, setSp] = useSearchParams();

  const state = useMemo<ListSearchState>(() => {
    const filters: Record<string, string> = {};
    for (const [k, v] of sp.entries()) {
      if (k.startsWith('f.')) filters[k.slice(2)] = v;
    }
    const rawDir = sp.get('sort_dir');
    return {
      page: Math.max(1, Number(sp.get('page')) || 1),
      pageSize: Math.max(1, Number(sp.get('page_size')) || defaults.pageSize || 50),
      sortBy: sp.get('sort_by') || defaults.sortBy,
      sortDir: rawDir === 'asc' || rawDir === 'desc' ? rawDir : defaults.sortDir,
      filters,
    };
  }, [sp, defaults.sortBy, defaults.sortDir, defaults.pageSize]);

  const update = useCallback(
    (changes: Partial<{
      page: number;
      pageSize: number;
      sortBy: string;
      sortDir: 'asc' | 'desc';
      filters: Record<string, string>;
    }>) => {
      setSp((prev) => {
        const next = new URLSearchParams(prev);

        if (changes.page !== undefined) {
          if (changes.page === 1) next.delete('page'); else next.set('page', String(changes.page));
        }
        if (changes.pageSize !== undefined) {
          if (changes.pageSize === (defaults.pageSize || 50)) next.delete('page_size');
          else next.set('page_size', String(changes.pageSize));
        }
        if (changes.sortBy !== undefined) {
          if (changes.sortBy === defaults.sortBy) next.delete('sort_by');
          else next.set('sort_by', changes.sortBy);
        }
        if (changes.sortDir !== undefined) {
          if (changes.sortDir === defaults.sortDir) next.delete('sort_dir');
          else next.set('sort_dir', changes.sortDir);
        }
        if (changes.filters !== undefined) {
          // Стереть все f.* и записать заново.
          for (const key of Array.from(next.keys())) {
            if (key.startsWith('f.')) next.delete(key);
          }
          for (const [k, v] of Object.entries(changes.filters)) {
            if (v) next.set(`f.${k}`, v);
          }
        }
        return next;
      }, { replace: true });
    },
    [setSp, defaults.sortBy, defaults.sortDir, defaults.pageSize],
  );

  const setPage = useCallback((page: number) => update({ page }), [update]);
  const setPageSize = useCallback((size: number) => update({ pageSize: size, page: 1 }), [update]);
  const setSort = useCallback((col: string, dir: 'asc' | 'desc') => update({ sortBy: col, sortDir: dir, page: 1 }), [update]);
  const setFilters = useCallback((filters: Record<string, string>) => update({ filters, page: 1 }), [update]);

  const getExtra = useCallback((key: string) => sp.get(key) || undefined, [sp]);

  const setExtra = useCallback((key: string, value: string | null) => {
    setSp((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value); else next.delete(key);
      // При смене extra параметра сбрасываем page на 1.
      next.delete('page');
      return next;
    }, { replace: true });
  }, [setSp]);

  const setExtras = useCallback((entries: Record<string, string | null>) => {
    setSp((prev) => {
      const next = new URLSearchParams(prev);
      for (const [key, value] of Object.entries(entries)) {
        if (value) next.set(key, value); else next.delete(key);
      }
      next.delete('page');
      return next;
    }, { replace: true });
  }, [setSp]);

  return { ...state, setPage, setPageSize, setSort, setFilters, setExtra, setExtras, getExtra };
}
