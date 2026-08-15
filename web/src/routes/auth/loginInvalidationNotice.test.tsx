import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubWebStorage } from '../../test/localStorageStub';

// Before importing anything that captures either global.
stubWebStorage('sessionStorage');
stubWebStorage('localStorage');

// No server info: the screen renders its password form alone, with no Google or
// Apple button and no network call behind either.
vi.mock('../../api/useServerInfo', () => ({
  useServerInfo: () => ({ data: undefined }),
}));

const { useSessionStore } = await import('../../auth/sessionStore');
const { Login } = await import('./Login');

const renderLogin = () =>
  render(
    <MemoryRouter initialEntries={['/login']}>
      <Login />
    </MemoryRouter>,
  );

beforeEach(() => {
  useSessionStore.setState({
    authState: { kind: 'unauthenticated' },
    session: null,
    lastInvalidation: null,
  } as never);
});

afterEach(cleanup);

describe('the notice the login screen shows on arrival', () => {
  it('explains the grace window when a deletion was just scheduled', () => {
    // The one piece of `/portal/login` functionality that had to move here: its
    // `?deleted=1` banner. Signing out is otherwise silent, so a user who just
    // scheduled a deletion would be dropped on a login form with no
    // explanation and no hint that signing back in is how they undo it.
    useSessionStore.setState({ lastInvalidation: 'deletionScheduled' });

    renderLogin();

    const notice = screen.getByRole('alert');
    expect(notice).toHaveTextContent(/scheduled for deletion/i);
    expect(notice).toHaveTextContent(/grace window/i);
  });

  it('says nothing when the user signed themselves out', () => {
    // `manual` is silent by design — and it is what an *immediate* deletion
    // still passes, because that account has no grace window to return to.
    useSessionStore.setState({ lastInvalidation: 'manual' });

    renderLogin();

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows the notice once, not again on the next render', () => {
    // The reason is one-shot: it is consumed on mount, so a re-render (or a
    // second visit to /login in the same session) must not resurrect it.
    useSessionStore.setState({ lastInvalidation: 'deletionScheduled' });

    const { unmount } = renderLogin();
    expect(screen.getByRole('alert')).toBeInTheDocument();
    unmount();
    renderLogin();

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
