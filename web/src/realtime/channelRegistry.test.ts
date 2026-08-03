import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * The registry owns channel *lifetimes*, not sockets, so the channel factories
 * are the seam. Everything below them is covered by `webSocketChannel.test.ts`
 * and `channels.test.ts`.
 */
interface StubChannel {
  name: string;
  connects: number;
  disconnects: number;
  connect: () => void;
  disconnect: () => void;
  subscribe: () => () => void;
}

const built = vi.hoisted(() => ({
  user: 0,
  agents: [] as string[],
  userChannel: null as StubChannel | null,
  agentChannels: new Map<string, StubChannel>(),
  make(name: string): StubChannel {
    const channel: StubChannel = {
      name,
      connects: 0,
      disconnects: 0,
      connect: () => {
        channel.connects += 1;
      },
      disconnect: () => {
        channel.disconnects += 1;
      },
      subscribe: () => () => undefined,
    };
    return channel;
  },
}));

vi.mock('./channels', () => ({
  createUserEventsChannel: () => {
    built.user += 1;
    built.userChannel = built.make('user-events');
    return built.userChannel;
  },
  createAgentEventsChannel: (agentId: string) => {
    built.agents.push(agentId);
    const channel = built.make(`agent-events:${agentId}`);
    built.agentChannels.set(agentId, channel);
    return channel;
  },
}));

const { connectAgentEvents, startUserEvents, stopAllRealtime, stopUserEvents } =
  await import('./channelRegistry');

let client: QueryClient;

beforeEach(() => {
  built.user = 0;
  built.agents.length = 0;
  built.userChannel = null;
  built.agentChannels.clear();
  client = new QueryClient();
});

afterEach(() => {
  stopAllRealtime();
});

describe('startUserEvents', () => {
  it('builds one channel and reuses it', () => {
    startUserEvents(client);
    startUserEvents(client);

    expect(built.user).toBe(1);
  });

  it('re-arms the cached channel instead of handing back a dead one', () => {
    // A channel stops for good whenever buildUrl resolves to null or throws —
    // one failed token mint is enough — and connect() is idempotent, so this is
    // the only way back short of a page reload.
    startUserEvents(client);
    startUserEvents(client);

    expect(built.userChannel?.connects).toBe(2);
  });
});

describe('connectAgentEvents', () => {
  it('reuses the live channel per agent and re-arms it', () => {
    connectAgentEvents(client, 'A1');
    connectAgentEvents(client, 'A1');
    connectAgentEvents(client, 'A2');

    expect(built.agents).toEqual(['A1', 'A2']);
    expect(built.agentChannels.get('A1')?.connects).toBe(2);
  });
});

describe('stopAllRealtime', () => {
  it('closes agent channels too, not just the user channel', () => {
    // The relay authenticates a mobile socket only at handshake, so an agent
    // channel surviving sign-out stays authorised for the previous account.
    startUserEvents(client);
    connectAgentEvents(client, 'A1');

    stopAllRealtime();

    expect(built.userChannel?.disconnects).toBe(1);
    expect(built.agentChannels.get('A1')?.disconnects).toBe(1);
  });

  it('leaves the registry empty, so the next start builds a fresh channel', () => {
    startUserEvents(client);
    stopAllRealtime();
    startUserEvents(client);

    expect(built.user).toBe(2);
  });
});

describe('stopUserEvents', () => {
  it('is a no-op when nothing is running', () => {
    expect(() => {
      stopUserEvents();
    }).not.toThrow();
  });
});
