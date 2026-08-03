/**
 * Live connection state per channel, for reconnect UI.
 *
 * The mobile equivalents expose a conflated three-state flow (Android
 * `AgentEventBus.ConnState`, iOS `BusConnectionState`) with one bus per
 * channel; the web client multiplexes several channels at once, so the state is
 * a map keyed by channel name. WEB-05's terminal overlay is the first consumer.
 *
 * Writes are conflated: a channel re-reporting an identical state does not
 * notify subscribers, so a reconnect storm cannot re-render the tree on every
 * failed attempt.
 */
import { create } from 'zustand';
import type { ChannelState } from './webSocketChannel';

interface ConnectionStoreState {
  channels: Record<string, ChannelState>;
  setChannelState: (name: string, state: ChannelState) => void;
  forgetChannel: (name: string) => void;
}

function sameState(a: ChannelState | undefined, b: ChannelState): boolean {
  return (
    a !== undefined &&
    a.status === b.status &&
    a.attempt === b.attempt &&
    a.lastCloseCode === b.lastCloseCode
  );
}

export const useConnectionStore = create<ConnectionStoreState>()((set, get) => ({
  channels: {},

  setChannelState: (name, state) => {
    if (sameState(get().channels[name], state)) return;
    set((current) => ({ channels: { ...current.channels, [name]: state } }));
  },

  forgetChannel: (name) => {
    const { [name]: removed, ...rest } = get().channels;
    if (removed === undefined) return;
    set({ channels: rest });
  },
}));

/** Non-reactive read, for code outside React. */
export function channelState(name: string): ChannelState | undefined {
  return useConnectionStore.getState().channels[name];
}
