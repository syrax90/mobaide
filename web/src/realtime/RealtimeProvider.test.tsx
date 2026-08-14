import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useSessionStore } from '../auth/sessionStore';
import type { UserSession } from '../auth/sessionTransport';

// The channel itself is WebSocketChannel's business; what this component owns is
// when the singleton is started and torn down.
const realtime = vi.hoisted(() => ({ starts: 0, stops: 0 }));

vi.mock('./channelRegistry', () => ({
  startUserEvents: () => {
    realtime.starts += 1;
  },
  stopAllRealtime: () => {
    realtime.stops += 1;
  },
}));

const { RealtimeProvider } = await import('./RealtimeProvider');

function session(userId: string): UserSession {
  return {
    accessToken: 'jwt',
    userId,
    email: `${userId}@example.com`,
    role: 'user',
    scheduledDeletionAt: null,
  };
}

function signIn(userId: string): void {
  act(() => {
    useSessionStore.setState({ authState: { kind: 'userOnly' }, session: session(userId) });
  });
}

function signOut(): void {
  act(() => {
    useSessionStore.setState({ authState: { kind: 'unauthenticated' }, session: null });
  });
}

function renderProvider() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <RealtimeProvider>
        <span>app</span>
      </RealtimeProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  realtime.starts = 0;
  realtime.stops = 0;
  useSessionStore.setState({ authState: { kind: 'unauthenticated' }, session: null });
});

afterEach(cleanup);

describe('RealtimeProvider', () => {
  it('renders the app without a channel while signed out', () => {
    renderProvider();

    expect(realtime.starts).toBe(0);
    expect(screen.getByText('app')).toBeInTheDocument();
  });

  it('starts the channel on sign-in and tears it down on sign-out', () => {
    renderProvider();

    signIn('u1');
    expect([realtime.starts, realtime.stops]).toEqual([1, 0]);

    signOut();
    expect([realtime.starts, realtime.stops]).toEqual([1, 1]);
  });

  it('rebuilds the channel when the account changes', () => {
    // The token is baked into the WS URL at connect time, so a channel that
    // outlived its session would push the previous account's events into the
    // new one's cache.
    renderProvider();

    signIn('u1');
    signIn('u2');

    expect([realtime.starts, realtime.stops]).toEqual([2, 1]);
  });
});
