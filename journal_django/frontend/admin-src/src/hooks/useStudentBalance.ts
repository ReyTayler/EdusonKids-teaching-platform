import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { StudentBalance } from '../lib/types';

export function useStudentBalance(studentId: number | undefined) {
  return useQuery({
    queryKey: ['student-balance', studentId],
    queryFn: () => api<StudentBalance>('GET', `/api/admin/students/${studentId}/balance`),
    enabled: Number.isFinite(studentId) && (studentId as number) > 0,
  });
}
