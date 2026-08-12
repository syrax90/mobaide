/**
 * Owns one `ws/autoscan` channel for as long as a detail screen needs it, and
 * folds its frames into `autoscanModel` state.
 *
 * Two effects, split for the reason `useAgentEvents` documents: **ownership**
 * opens the channel and disposes it on unmount, while **revival** re-opens it
 * after `RealtimeProvider` calls `stopAllRealtime()` on a session change — which
 * disposes channels out from under mounted components. Revival must have no
 * cleanup of its own, or disconnect → `forgetChannel` → "not registered" →
 * reconnect becomes an oscillation.
 *
 * Frames are buffered and flushed on an animation frame rather than dispatched
 * one at a time. Steady state is quiet — a cycle emits tens of events every
 * `scan_interval_seconds`, 300 by default — but a snapshot replays the agent's
 * whole 500-event ring at once, which is the burst the reducer's batch shape and
 * this buffer exist for.
 *
 * The hook holds per-project state and does not reset when `projectName`
 * changes; the screen remounts it with a `key` instead, as the sessions and
 * projects screens do for their lists.
 */
import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { isAuthenticated } from '../auth/authState';
import { useSessionStore } from '../auth/sessionStore';
import { decodeAutoScanFrame } from '../protocol/autoscanFrames';
import { WS_CLOSE } from '../protocol/closeCodes';
import { AutoScanChannel, autoscanChannelName } from '../realtime/channels';
import { useConnectionStore } from '../realtime/connectionStore';
import { onResume } from '../realtime/resumeOnFocus';
import { onTabVisibility, tabHidden } from '../realtime/tabVisibility';
import {
  initialAutoScanDetailState,
  reduceAutoScanFrames,
  resumeFromState,
  type AutoScanDetailState,
  type DetailFrame,
} from './autoscanModel';

/**
 * How long the tab must stay hidden before the monitor's socket is closed.
 *
 * The same value as `resumeOnFocus`'s `HIDDEN_RESUME_MS`, and for the same
 * reason: it is the threshold at which a hide stops being an alt-tab and starts
 * being an absence worth acting on.
 */
const HIDDEN_DISCONNECT_MS = 60_000;

/**
 * How many buffered frames force a synchronous flush.
 *
 * `requestAnimationFrame` never fires in a hidden document, so without a cap the
 * buffer would grow unflushed and then reduce in one long task on return. The
 * socket now closes after {@link HIDDEN_DISCONNECT_MS}, which bounds that
 * window rather than removing it — the cap still has to cover the first minute
 * of a hide. Well above any burst a visible tab produces in a single frame, so
 * batching is unaffected.
 */
const FLUSH_CAP = 512;

export interface AutoScanDetailHandle {
  state: AutoScanDetailState;
  /**
   * The agent turned this stream down — relay close `1013`, sent after the
   * socket is already accepted (its ten-subscription cap, or a full send queue).
   *
   * Latched rather than read from the connection store, because acting on it
   * means disconnecting, and `disconnect()` clears `lastCloseCode`. The latch is
   * what stops the loop: a post-accept close means `onopen` already reset the
   * backoff ladder, so the default reconnect policy would re-handshake at the
   * ~1 s floor for as long as the screen stayed open.
   */
  declined: boolean;
  /** Clear the latch and try once more — the banner's retry. */
  retry: () => void;
}

export function useAutoScanDetail(agentId: string, projectName: string): AutoScanDetailHandle {
  const [state, dispatch] = useReducer(
    reduceAutoScanFrames,
    projectName,
    initialAutoScanDetailState,
  );
  const authenticated = useSessionStore((store) => isAuthenticated(store.authState));
  const name = autoscanChannelName(agentId);
  const registered = useConnectionStore((store) => store.channels[name] !== undefined);
  const lastCloseCode = useConnectionStore(
    (store) => store.channels[name]?.lastCloseCode ?? null,
  );
  const [declined, setDeclined] = useState(false);

  // The live reduction cursor, read at connect time so a reconnect resumes from
  // the true high-water mark rather than the value captured when the screen
  // mounted. Freezing that capture is half of the Android bug (CLA-119).
  const stateRef = useRef(state);
  stateRef.current = state;
  const channelRef = useRef<AutoScanChannel | null>(null);

  useEffect(() => {
    if (!authenticated) return;
    // A fresh channel starts undeclined. This effect also re-runs on an auth
    // flip, and a latch carried across that rebuild would show "the agent turned
    // this down" over a socket that is delivering frames.
    setDeclined(false);

    const pending: DetailFrame[] = [];
    let frameHandle: number | null = null;

    const flush = (): void => {
      frameHandle = null;
      if (pending.length === 0) return;
      dispatch(pending.splice(0, pending.length));
    };

    const enqueue = (frame: DetailFrame): void => {
      pending.push(frame);
      if (pending.length >= FLUSH_CAP) {
        // Nothing will drain this on its own in a hidden tab. Flushing keeps the
        // reduction incremental instead of one long task on return, and dropping
        // frames instead is not an option — the phase timeline is order-sensitive.
        if (frameHandle !== null) cancelAnimationFrame(frameHandle);
        flush();
        return;
      }
      frameHandle ??= requestAnimationFrame(flush);
    };

    const channel = new AutoScanChannel(agentId, {
      subscribe: [projectName],
      resumeFromSeq: resumeFromState(stateRef.current),
    });
    const unsubscribe = channel.channel.subscribe((event) => {
      switch (event.kind) {
        case 'connected':
          enqueue({ kind: 'connected' });
          break;
        case 'disconnected':
          enqueue({ kind: 'disconnected' });
          break;
        case 'message': {
          // The wire cursor lives on the channel and advances here, so a replay
          // the server could not trim never reaches the reducer at all. The
          // reducer's own cursor covers what this one cannot: reducing the same
          // frames twice across a remount.
          const frame = decodeAutoScanFrame(event.data, channel.cursor);
          if (frame !== null) enqueue(frame);
          break;
        }
        default:
          break;
      }
    });
    channelRef.current = channel;
    channel.connect();
    // A browser cannot see a half-open socket, and this one is silent for most
    // of a 300 s scan cycle — long enough that a sleep/wake would otherwise
    // leave the monitor rendering "Scanning" over a log that never resumes.
    // Safe to fire blindly: `forceReconnect` no-ops once `disconnect()` has
    // cleared `shouldReconnect` (so a declined channel stays declined), and the
    // replay it triggers is deduped by both cursors.
    const unsubscribeResume = onResume(() => {
      channel.channel.forceReconnect();
    });

    return () => {
      unsubscribeResume();
      unsubscribe();
      if (frameHandle !== null) cancelAnimationFrame(frameHandle);
      // Synchronously, not on the next frame: an effect re-run keeps this
      // component mounted, and dropping the buffer would lose the log lines that
      // arrived since the last flush.
      flush();
      channelRef.current = null;
      channel.dispose();
    };
  }, [agentId, projectName, authenticated]);

  useEffect(() => {
    if (!authenticated || registered || declined) return;
    // Not into a hidden tab. `onTabVisibility` only fires on transitions, so a
    // revival landing while hidden — an account handover disposed the channel,
    // say — would stream unwatched until the next hide *and* show.
    if (tabHidden()) return;
    // `connect()` on the wrapper, not on `channel`: it re-registers as a
    // disposable first, so a revived socket stays reachable by
    // `stopAllRealtime()` at the next account handover.
    channelRef.current?.connect();
  }, [authenticated, registered, declined]);

  // Latch, then stop. `disconnect()` rather than `dispose()`: dispose would
  // `forgetChannel`, which is exactly the signal the revival effect above waits
  // for, so it would re-open the socket the latch just closed.
  useEffect(() => {
    if (lastCloseCode !== WS_CLOSE.agentBusy) return;
    setDeclined(true);
    channelRef.current?.channel.disconnect();
  }, [lastCloseCode]);

  // Nobody reads a log pane in a hidden tab, and a scan in its active phase
  // streams every line it produces. Unlike the terminal this needs no budget to
  // be worth doing: there is no ring replay to amortise, because `onOpen`
  // rebuilds the subscribe frame from the live wire cursor, so a reconnect
  // resumes at the high-water mark and costs one handshake.
  //
  // `disconnect()` keeps the connection-store entry, so `registered` stays true
  // and the revival effect above does not race this back open.
  useEffect(() => {
    if (!authenticated) return;
    let pending: ReturnType<typeof setTimeout> | null = null;
    const cancelPending = (): void => {
      if (pending !== null) clearTimeout(pending);
      pending = null;
    };
    const unsubscribe = onTabVisibility((hidden) => {
      cancelPending();
      if (hidden) {
        // Delayed, not immediate. `resumeOnFocus` states the rule this would
        // otherwise break — "alt-tabbing is constant, and reconnecting on each
        // one would be its own handshake storm" — and the stakes are higher
        // here than bytes: the agent caps concurrent auto-scan streams, so
        // open/close churn can trip the cap and leave the pane declined.
        pending = setTimeout(() => {
          pending = null;
          channelRef.current?.channel.disconnect();
        }, HIDDEN_DISCONNECT_MS);
        return;
      }
      // A declined channel stays declined: the agent turned this subscription
      // down, and only the banner's retry may re-arm it. Reconnecting a channel
      // the timer never got to close is a no-op, so this needs no guard for it.
      if (!declined) channelRef.current?.connect();
    });
    return () => {
      cancelPending();
      unsubscribe();
    };
  }, [authenticated, declined]);

  const retry = useCallback(() => {
    setDeclined(false);
    channelRef.current?.connect();
  }, []);

  return { state, declined, retry };
}

/**
 * A 1 Hz clock, isolated so the tick re-renders only what shows a time. Same
 * split as iOS `CountdownView`, and for the same reason: re-rendering a
 * 2000-line log pane every second is not affordable.
 */
export function useSecondTicker(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const handle = setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => {
      clearInterval(handle);
    };
  }, [active]);
  return now;
}
