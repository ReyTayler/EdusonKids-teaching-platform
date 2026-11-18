import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../lib/api';
import type { Interval, ScheduleRange, ScheduleTemplate } from './types';

const key = (basePath: string) => ['schedule', basePath] as const;

export function useScheduleTemplate(basePath: string) {
  return useQuery<ScheduleTemplate>({
    queryKey: [...key(basePath), 'template'],
    queryFn: () => api<ScheduleTemplate>('GET', `${basePath}/template`),
  });
}

export function useScheduleRange(basePath: string, from: string, to: string, enabled: boolean) {
  return useQuery<ScheduleRange>({
    queryKey: [...key(basePath), 'range', from, to],
    queryFn: () => api<ScheduleRange>('GET', `${basePath}?from=${from}&to=${to}`),
    // Пока листаем недели, прошлая неделя остаётся на экране — без мигания пустотой.
    // Но только в рамках одного basePath: при смене преподавателя страница не
    // ремоунтится, и окна A нельзя показывать (и сохранять) как окна B.
    placeholderData: (prev, prevQuery) => (prevQuery?.queryKey[1] === basePath ? prev : undefined),
    staleTime: 60_000,
    enabled,
  });
}

export function useScheduleMutations(basePath: string) {
  const qc = useQueryClient();
  // График влияет и на календари (фон «окна работы») — сбрасываем и их.
  const invalidate = () => Promise.all([
    qc.invalidateQueries({ queryKey: key(basePath) }),
    qc.invalidateQueries({ queryKey: ['calendar'] }),
    qc.invalidateQueries({ queryKey: ['admin-calendar'] }),
  ]);
  return {
    saveTemplate: useMutation({
      mutationFn: (days: Record<string, Interval[]>) => api('PUT', `${basePath}/template`, { days }),
      onSuccess: invalidate,
    }),
    saveWeek: useMutation({
      mutationFn: (p: { monday: string; days: Record<string, Interval[]> }) => api('PUT', `${basePath}/week`, p),
      onSuccess: invalidate,
    }),
    clearOverride: useMutation({
      mutationFn: (date: string) => api('DELETE', `${basePath}/overrides/${date}`),
      onSuccess: invalidate,
    }),
  };
}
