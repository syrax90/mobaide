import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../../test/localStorageStub';
import { APP_PROTOCOL_VERSION } from '../../api/bootstrap';

// Before importing anything that pulls in a persisted store.
stubLocalStorage();

const notifications = vi.hoisted(() => ({
  permission: 'default',
  requests: 0,
  answer: 'granted',
}));

vi.mock('../../notifications/notificationService', () => ({
  notificationPermission: () => notifications.permission,
  requestNotificationPermission: () => {
    notifications.requests += 1;
    return Promise.resolve(notifications.answer);
  },
}));

vi.mock('../../api/useServerInfo', () => ({
  useServerInfo: () => ({ data: { serverVersion: '1.2.3' } }),
}));

const { useNotificationPrefsStore } = await import('../../stores/notificationPrefsStore');
const { useSessionStore } = await import('../../auth/sessionStore');
const { SettingsScreen } = await import('./SettingsScreen');

let client: QueryClient;

const renderScreen = () =>
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/settings']}>
        <SettingsScreen />
      </MemoryRouter>
    </QueryClientProvider>,
  );

beforeEach(() => {
  notifications.permission = 'default';
  notifications.requests = 0;
  notifications.answer = 'granted';
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  useNotificationPrefsStore.setState({ enabled: true });
  useSessionStore.setState({
    session: {
      userId: 'u-1',
      email: 'dev@example.com',
      role: 'user',
      accessToken: 't',
      refreshToken: null,
    },
  } as never);
});

afterEach(() => {
  cleanup();
  client.clear();
});

describe('SettingsScreen', () => {
  it('never raises the permission prompt on load', () => {
    // The acceptance criterion, and the reason the request lives behind a
    // button: a prompt fired at load is auto-suppressed by browsers and spends
    // the user's one chance to say yes before they know what is being asked.
    renderScreen();
    expect(notifications.requests).toBe(0);
    expect(screen.getByRole('button', { name: 'Allow system notifications' })).toBeInTheDocument();
  });

  it('requests permission only from the button, and reflects the answer', async () => {
    renderScreen();
    fireEvent.click(screen.getByRole('button', { name: 'Allow system notifications' }));

    await waitFor(() => {
      expect(screen.getByText('System notifications are allowed.')).toBeInTheDocument();
    });
    expect(notifications.requests).toBe(1);
  });

  it('offers no button where the browser has no Notification API', () => {
    notifications.permission = 'unsupported';
    renderScreen();

    expect(
      screen.queryByRole('button', { name: 'Allow system notifications' }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/no notification support/i)).toBeInTheDocument();
  });

  it('explains a blocked permission rather than offering a prompt that cannot show', () => {
    notifications.permission = 'denied';
    renderScreen();

    expect(
      screen.queryByRole('button', { name: 'Allow system notifications' }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/blocked for this site/i)).toBeInTheDocument();
  });

  it('persists the in-app opt-out independently of browser permission', () => {
    notifications.permission = 'granted';
    renderScreen();

    const toggle = screen.getByRole('checkbox', {
      name: 'Notify me when a session needs attention',
    });
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);

    expect(useNotificationPrefsStore.getState().enabled).toBe(false);
  });

  it('links onward to the surfaces mobile keeps behind this screen', () => {
    renderScreen();

    for (const [name, href] of [
      ['My agents', '/agents'],
      ['Projects', '/projects'],
      ['Prompt templates', '/templates'],
      ['My devices', '/devices'],
      ['Account', '/account'],
    ]) {
      expect(screen.getByRole('link', { name })).toHaveAttribute('href', href);
    }
  });

  it('shows no terminal display-mode picker', () => {
    // Web ships Full display only — the WEB-05 platform-convention divergence.
    // The Ctrl+V escape hatch is the only terminal preference there is.
    renderScreen();

    expect(screen.queryByText(/static/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
    expect(
      screen.getByRole('checkbox', { name: 'Send Ctrl+V to the terminal instead of pasting' }),
    ).toBeInTheDocument();
  });

  it('reports both version numbers', () => {
    renderScreen();
    expect(
      screen.getByText(new RegExp(`protocol v${String(APP_PROTOCOL_VERSION)}`)),
    ).toHaveTextContent('server 1.2.3');
  });

  it('signs out with the silent reason', () => {
    // `manual` is what keeps the login screen from greeting a deliberate sign-out
    // with "your session expired".
    const invalidate = vi.fn();
    useSessionStore.setState({ invalidateUserSession: invalidate } as never);
    renderScreen();

    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(invalidate).toHaveBeenCalledWith('manual');
  });
});
