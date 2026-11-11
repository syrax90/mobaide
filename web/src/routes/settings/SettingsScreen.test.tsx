import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../../test/localStorageStub';
import { stubPrefersDark } from '../../test/matchMediaStub';

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

const { useNotificationPrefsStore } = await import('../../stores/notificationPrefsStore');
const { useSessionStore } = await import('../../auth/sessionStore');
const { useThemeStore } = await import('../../stores/themeStore');
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
  useThemeStore.setState({ preference: 'system' });
  stubPrefersDark(false);
  useSessionStore.setState({
    session: {
      userId: 'u-1',
      email: 'dev@example.com',
      role: 'user',
      accessToken: 't',
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

  /**
   * The card's whole reason for existing is that a touchscreen cannot reach the
   * header's `title`, so the value it prints has to be the one Auto would give —
   * the OS appearance, not what is painted. An explicit Light under a dark OS is
   * the only configuration where those differ, so it is the only one that can
   * catch this being wired to `useResolvedTheme`.
   */
  it('states the OS appearance rather than the theme currently painted', () => {
    stubPrefersDark(true);
    useThemeStore.setState({ preference: 'light' });

    renderScreen();

    expect(
      screen.getByText(/Auto follows your system appearance — currently dark\./),
    ).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Light' })).toBeChecked();
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
