import { QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router';
import { AppErrorBoundary } from './app/AppErrorBoundary';
import { router } from './app/router';
import { ThemeProvider } from './app/ThemeProvider';
import { ToastViewport } from './components/toast/ToastViewport';
import { queryClient } from './lib/queryClient';
import './styles/theme.css';
import './styles/global.css';

const container = document.getElementById('root');
if (!container) {
  throw new Error('Root container #root is missing from index.html');
}

createRoot(container).render(
  <StrictMode>
    <AppErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider>
          <RouterProvider router={router} />
          <ToastViewport />
        </ThemeProvider>
      </QueryClientProvider>
    </AppErrorBoundary>
  </StrictMode>,
);
