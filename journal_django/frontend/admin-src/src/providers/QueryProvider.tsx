import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReactQueryDevtools } from '@tanstack/react-query-devtools';
import { type ReactNode, useState } from 'react';

export function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        retry: (count, err: any) => {
          if (err?.status === 401 || err?.status === 404) return false;
          return count < 1;
        },
        staleTime: 30_000,
        // Возврат к вкладке обновляет то, что на экране: правки коллеги (другой
        // менеджер, преподаватель) иначе не видны вообще — инвалидация react-query
        // локальна для вкладки, push-канала (SSE/WS) в проекте нет. Перезапрос
        // идёт только по АКТИВНЫМ и УСТАРЕВШИМ (staleTime 30с) запросам, поэтому
        // частое переключение окон лишнего трафика не создаёт.
        refetchOnWindowFocus: true,
      },
    },
  }));
  return (
    <QueryClientProvider client={client}>
      {children}
      {import.meta.env.DEV && <ReactQueryDevtools initialIsOpen={false} />}
    </QueryClientProvider>
  );
}
