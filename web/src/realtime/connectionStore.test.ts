import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { channelState, useConnectionStore } from './connectionStore';
import type { ChannelState } from './webSocketChannel';

const retrying: ChannelState = { status: 'connecting', attempt: 1, lastCloseCode: 1006 };

let notifications = 0;
let unsubscribe: () => void = () => undefined;

beforeEach(() => {
  useConnectionStore.setState({ channels: {} });
  notifications = 0;
  unsubscribe = useConnectionStore.subscribe(() => {
    notifications += 1;
  });
});

afterEach(() => {
  unsubscribe();
});

describe('connectionStore', () => {
  it('does not notify when a channel re-reports the state it already has', () => {
    // A reconnect storm re-publishes the same {connecting, attempt} repeatedly;
    // without conflation every failed attempt would re-render the tree.
    const store = useConnectionStore.getState();

    store.setChannelState('user-events', retrying);
    store.setChannelState('user-events', { ...retrying });

    expect(notifications).toBe(1);
    expect(channelState('user-events')).toEqual(retrying);
  });

  it('notifies when any field of the state actually changed', () => {
    const store = useConnectionStore.getState();

    store.setChannelState('user-events', retrying);
    store.setChannelState('user-events', { ...retrying, attempt: 2 });

    expect(notifications).toBe(2);
    expect(channelState('user-events')?.attempt).toBe(2);
  });

  it('forgets one channel and leaves the others mounted', () => {
    const store = useConnectionStore.getState();
    store.setChannelState('user-events', retrying);
    store.setChannelState('agent-events:a1', retrying);

    store.forgetChannel('agent-events:a1');

    expect(channelState('agent-events:a1')).toBeUndefined();
    expect(channelState('user-events')).toEqual(retrying);
  });

  it('does not notify when forgetting a channel it never tracked', () => {
    useConnectionStore.getState().forgetChannel('session:a1:s1');

    expect(notifications).toBe(0);
  });
});
