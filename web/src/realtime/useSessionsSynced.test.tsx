import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { agentEventsChannelName } from './channels';
import { useConnectionStore } from './connectionStore';
import { clearSessionsSnapshot, markSessionsSnapshotApplied } from './sessionsSyncStore';
import type { ChannelStatus } from './webSocketChannel';
import { useSessionsSynced } from './useSessionsSynced';
import { resetSessionsSynced } from './__testing__/sessionsSynced';

function setChannel(status: ChannelStatus) {
  useConnectionStore.setState({
    channels: {
      [agentEventsChannelName('a1')]: { status, attempt: 0, lastCloseCode: null },
    },
  });
}

afterEach(() => {
  resetSessionsSynced();
});

describe('useSessionsSynced', () => {
  it('is false on an open channel that has not delivered a snapshot yet', () => {
    // The agent replays its snapshot right after the socket opens; in that
    // window the cache is still whatever survived the drop.
    setChannel('connected');

    const { result } = renderHook(() => useSessionsSynced('a1'));

    expect(result.current).toBe(false);
  });

  it('is false on a snapshot whose channel has since dropped', () => {
    markSessionsSnapshotApplied('a1');
    setChannel('connecting');

    const { result } = renderHook(() => useSessionsSynced('a1'));

    expect(result.current).toBe(false);
  });

  it('is true only with both an open channel and its snapshot', () => {
    setChannel('connected');
    markSessionsSnapshotApplied('a1');

    const { result } = renderHook(() => useSessionsSynced('a1'));

    expect(result.current).toBe(true);
  });

  it('is scoped per agent', () => {
    setChannel('connected');
    markSessionsSnapshotApplied('a2');

    const { result } = renderHook(() => useSessionsSynced('a1'));

    expect(result.current).toBe(false);
  });

  it('goes false again once the snapshot is cleared', () => {
    setChannel('connected');
    markSessionsSnapshotApplied('a1');
    const { result, rerender } = renderHook(() => useSessionsSynced('a1'));
    expect(result.current).toBe(true);

    clearSessionsSnapshot('a1');
    rerender();

    expect(result.current).toBe(false);
  });
});
