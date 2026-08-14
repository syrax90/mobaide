import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { stubLocalStorage } from '../../test/localStorageStub';

// Before importing anything that pulls in a persisted store.
stubLocalStorage();

const { useSessionStore } = await import('../../auth/sessionStore');
const { Account } = await import('./Account');

let client: QueryClient;

const renderScreen = () =>
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/account']}>
        <Account />
      </MemoryRouter>
    </QueryClientProvider>,
  );

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  useSessionStore.setState({
    session: {
      userId: 'u-1',
      email: 'dev@example.com',
      // Deliberately the loudest value the field can hold, so the absence
      // assertion below fails on any re-render of the role.
      role: 'admin',
      accessToken: 't',
      scheduledDeletionAt: null,
    },
  } as never);
});

afterEach(() => {
  cleanup();
  client.clear();
});

describe('Account', () => {
  it('identifies the session by the address the user signed in with', () => {
    renderScreen();

    expect(screen.getByText('Signed in as')).toBeInTheDocument();
    expect(screen.getByText('dev@example.com')).toBeInTheDocument();
  });

  it('says nothing about the account role', () => {
    // `session.role` stays on the model — it still gates admin sign-in — but it
    // is server-side bookkeeping, and the summary is the user's own account,
    // not a dump of the record behind it.
    renderScreen();

    expect(screen.queryByText('Role')).not.toBeInTheDocument();
    expect(screen.queryByText('admin')).not.toBeInTheDocument();
  });
});
