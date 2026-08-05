import { QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { AppErrorBoundary } from './app/AppErrorBoundary';
import { AppGate } from './app/AppGate';
import { ThemeProvider } from './app/ThemeProvider';
import { installAuthProviders } from './auth/install';
import { bootstrapSession } from './auth/sessionStore';
import { ToastViewport } from './components/toast/ToastViewport';
import { queryClient } from './lib/queryClient';
import { setAttentionNavigator } from './notifications/notificationService';
import { RealtimeProvider } from './realtime';
import { router } from './app/router';
import './styles/theme.css';
import './styles/global.css';

const container = document.getElementById('root');
if (!container) {
  throw new Error('Root container #root is missing from index.html');
}

// Bind the API layer's token seams before anything can issue a request.
installAuthProviders();
// Same seam shape, for the same reason: the notification module is reached from
// the realtime layer, which the route tree imports, so importing `router` there
// would close a cycle. Binding it here keeps the arrow pointing one way.
setAttentionNavigator((path) => {
  void router.navigate(path);
});
// Fire-and-forget: `AppGate` holds a splash on the store's `hydrated` flag
// rather than blocking the first render, so a slow peer handover still paints.
void bootstrapSession();

createRoot(container).render(
  <StrictMode>
    <AppErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <RealtimeProvider>
          <ThemeProvider>
            <AppGate />
            <ToastViewport />
          </ThemeProvider>
        </RealtimeProvider>
      </QueryClientProvider>
    </AppErrorBoundary>
  </StrictMode>,
);
