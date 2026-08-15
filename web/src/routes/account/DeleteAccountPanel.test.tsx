import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubWebStorage } from '../../test/localStorageStub';

// Before importing anything that captures either global.
stubWebStorage('sessionStorage');
stubWebStorage('localStorage');

const deleted = vi.hoisted(() => ({ modes: [] as string[] }));

vi.mock('../../api/authRepository', () => ({
  deleteAccount: (request: { mode: string }) => {
    deleted.modes.push(request.mode);
    return Promise.resolve();
  },
  deleteAccountMessage: () => 'failed',
}));

// Keeps the panel off the network and off react-query: with no server info
// there is no Google or Apple re-auth button, and the password path is the one
// every account has.
vi.mock('../../api/useServerInfo', () => ({
  useServerInfo: () => ({ data: undefined }),
}));

const { useSessionStore } = await import('../../auth/sessionStore');
const { DeleteAccountPanel } = await import('./DeleteAccountPanel');

const EMAIL = 'dev@example.com';

const session = {
  accessToken: 'access-1',
  userId: 'user-1',
  email: EMAIL,
  role: 'viewer',
  scheduledDeletionAt: null,
};

/** Walk the `explain -> reauthPassword -> confirmEmail` machine. */
function reachConfirmStep(): void {
  fireEvent.click(screen.getByRole('button', { name: 'Continue with password' }));
  fireEvent.change(screen.getByLabelText('Current password'), {
    target: { value: 'hunter2' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  fireEvent.change(screen.getByLabelText('Confirm your email address'), {
    target: { value: EMAIL },
  });
}

beforeEach(() => {
  deleted.modes = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(
      (url: string): Promise<Response> =>
        Promise.resolve({ status: 204, ok: true, url } as unknown as Response),
    ),
  );
  useSessionStore.setState({
    authState: { kind: 'userOnly' },
    session,
    lastInvalidation: null,
  } as never);
  render(<DeleteAccountPanel email={EMAIL} onClose={() => undefined} />);
});

afterEach(cleanup);

describe('DeleteAccountPanel sign-out reason', () => {
  it('tells the login screen about the grace window after a scheduled deletion', async () => {
    // Scheduling signs the user out everywhere; without a reason carrying a
    // notice, the last thing they see is a bare login form and no hint that the
    // grace window is the way back.
    reachConfirmStep();

    fireEvent.click(screen.getByRole('button', { name: 'Schedule deletion' }));

    await waitFor(() => {
      expect(useSessionStore.getState().lastInvalidation).toBe('deletionScheduled');
    });
    expect(deleted.modes).toEqual(['scheduled']);
  });

  it('signs out silently after an immediate deletion', async () => {
    // There is no grace window to tell the user about: "sign in again to cancel"
    // would send them at an account that no longer exists.
    fireEvent.click(screen.getByRole('radio', { name: /Delete immediately/ }));
    reachConfirmStep();

    fireEvent.click(screen.getByRole('button', { name: 'Delete immediately' }));

    await waitFor(() => {
      expect(useSessionStore.getState().lastInvalidation).toBe('manual');
    });
    expect(deleted.modes).toEqual(['immediate']);
  });
});
