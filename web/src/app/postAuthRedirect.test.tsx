import { cleanup, render, screen } from '@testing-library/react';
import { useEffect, useRef } from 'react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { stubWebStorage } from '../test/localStorageStub';
import type { UserSession } from '../auth/sessionTransport';

// Before importing anything that captures either global.
stubWebStorage('sessionStorage');
stubWebStorage('localStorage');

const { useSessionStore } = await import('../auth/sessionStore');
const { resetSessionTransport } = await import('../auth/sessionTransport');
const { RequireAnonymous } = await import('./guards');
const { useAfterAuth } = await import('../routes/auth/useAfterAuth');

const SCHEDULED = '2026-09-01T00:00:00+00:00';

const session = (scheduledDeletionAt: string | null): UserSession => ({
  accessToken: 'access-1',
  userId: 'user-1',
  email: 'user@example.com',
  role: 'viewer',
  scheduledDeletionAt,
});

function Probe() {
  return <span data-testid="location">{useLocation().pathname}</span>;
}

/** Exact, not `toHaveTextContent`: every target contains "/" as a substring. */
const currentPath = (): string | null => screen.getByTestId('location').textContent;

/** Where the guard sends a user who reaches /login already signed in. */
function renderGuard(search: string, scheduledDeletionAt: string | null): void {
  useSessionStore.setState({
    authState: { kind: 'userOnly' },
    session: session(scheduledDeletionAt),
  } as never);
  render(
    <MemoryRouter initialEntries={[`/login${search}`]}>
      <Probe />
      <Routes>
        <Route element={<RequireAnonymous />}>
          <Route path="/login" element={<p>sign in</p>} />
        </Route>
        {/* A destination for the redirect, so no location goes unmatched. */}
        <Route path="*" element={<p>landed</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

function SignInForm({ adopted }: { adopted: UserSession }) {
  const afterAuth = useAfterAuth();
  // Stands in for the submit handler: one call, on a session the server just
  // returned. The latch keeps a re-render from re-running the handler.
  const submitted = useRef(false);
  useEffect(() => {
    if (submitted.current) return;
    submitted.current = true;
    afterAuth(adopted);
  }, [afterAuth, adopted]);
  return <p>sign in</p>;
}

/** Where the sign-in handler sends a user who just adopted a session. */
function renderSignIn(search: string, scheduledDeletionAt: string | null): void {
  render(
    <MemoryRouter initialEntries={[`/login${search}`]}>
      <Probe />
      <Routes>
        <Route
          path="/login"
          element={<SignInForm adopted={session(scheduledDeletionAt)} />}
        />
        <Route path="*" element={<p>landed</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

interface RedirectCase {
  name: string;
  search: string;
  scheduledDeletionAt: string | null;
  target: string;
}

const cases: RedirectCase[] = [
  { name: 'a plain sign-in', search: '', scheduledDeletionAt: null, target: '/' },
  {
    name: 'an account inside its deletion grace window',
    search: '',
    scheduledDeletionAt: SCHEDULED,
    target: '/account',
  },
  {
    name: 'a captured deep link',
    search: '?next=%2Fsettings',
    scheduledDeletionAt: null,
    target: '/settings',
  },
  {
    name: 'a captured deep link from inside the grace window',
    search: '?next=%2Fsettings',
    scheduledDeletionAt: SCHEDULED,
    target: '/settings',
  },
  {
    name: 'a hostile target from inside the grace window',
    search: '?next=%2F%2Fevil.example',
    scheduledDeletionAt: SCHEDULED,
    target: '/account',
  },
];

beforeEach(() => {
  resetSessionTransport();
  sessionStorage.clear();
  localStorage.clear();
  useSessionStore.setState({
    authState: { kind: 'unauthenticated' },
    session: null,
    lastInvalidation: null,
  } as never);
});

afterEach(cleanup);

// Adopting a session re-renders `RequireAnonymous`, so the guard's redirect
// races the sign-in handler's own. One table for both paths: if they ever
// disagree, whichever wins decides where the user lands.
describe('the post-sign-in destination', () => {
  it.each(cases)(
    '$name lands on $target via the guard',
    ({ search, scheduledDeletionAt, target }) => {
      renderGuard(search, scheduledDeletionAt);

      expect(currentPath()).toBe(target);
    },
  );

  it.each(cases)(
    '$name lands on $target via the sign-in handler',
    ({ search, scheduledDeletionAt, target }) => {
      renderSignIn(search, scheduledDeletionAt);

      expect(currentPath()).toBe(target);
    },
  );
});
