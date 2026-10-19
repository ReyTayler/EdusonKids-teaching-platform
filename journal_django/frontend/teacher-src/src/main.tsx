import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles/index.css';
import { App } from './App';
// Провайдеры и Toast — общие с admin (переиспользуются через alias @shared).
import { QueryProvider } from '@shared/providers/QueryProvider';
import { AuthProvider } from '@shared/providers/AuthProvider';
import { ThemeProvider } from '@shared/providers/ThemeProvider';
import { ToastProvider } from '@shared/components/ui/Toast';

const root = document.getElementById('app');
if (!root) throw new Error('#app element not found');

createRoot(root).render(
  <StrictMode>
    <QueryProvider>
      <ThemeProvider>
        <AuthProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </AuthProvider>
      </ThemeProvider>
    </QueryProvider>
  </StrictMode>
);
