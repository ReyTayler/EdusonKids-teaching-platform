import { useCallback } from 'react';
import { ApiError } from '../lib/api';
import { useToast } from '../components/ui/Toast';

export function useApiError() {
  const { toast } = useToast();
  return useCallback((err: unknown, fallback = 'Ошибка') => {
    if (err instanceof ApiError) {
      toast(err.message || fallback, 'error');
      return;
    }
    if (err instanceof Error) {
      toast(err.message || fallback, 'error');
      return;
    }
    toast(fallback, 'error');
  }, [toast]);
}
