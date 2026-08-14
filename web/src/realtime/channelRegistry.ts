/**
 * Ownership of the long-lived channels.
 *
 * The user-events channel is a singleton owned by `RealtimeProvider` and runs
 * for as long as there is a session. Agent-scoped channels are started on
 * demand — `useAgentEvents` opens one per selected agent, the terminal screen
 * the session I/O channel, WEB-10 the auto-scan stream — and each one is
 * registered here so a second request reuses the live socket instead of opening
 * a rival.
 *
 * Kept out of `index.ts` so the barrel stays a barrel: `RealtimeProvider`
 * imports these directly, and re-exporting the provider from a module it
 * imports from would close an import cycle — the same trap `src/api/auth.ts`
 * and `src/auth/install.ts` exist to avoid.
 */
import type { QueryClient } from '@tanstack/react-query';
import { useSessionStore } from '../auth/sessionStore';
import {
  clearAttentionNotifications,
  dismissResolvedAttention,
  handleAttentionEvent,
} from '../notifications/attentionNotifications';
import { decodeEventEnvelope } from '../protocol/dto/envelope';
import { createAgentEventsChannel, createUserEventsChannel } from './channels';
import { useConnectionStore } from './connectionStore';
import { disposeAllChannels } from './liveChannels';
import {
  applyAgentEventEnvelope,
  applyUserEventEnvelope,
  refreshOnAgentEventsConnect,
  refreshOnUserEventsConnect,
} from './queryBridge';
import { observeReachability, reportRealtimeStopped } from './reachability';
import { clearSessionsSnapshot } from './sessionsSyncStore';
import type { WebSocketChannel } from './webSocketChannel';

interface RunningChannel {
  channel: WebSocketChannel;
  dispose: () => void;
}

let userEvents: RunningChannel | null = null;
const agentEvents = new Map<string, RunningChannel>();

function tearDown(running: RunningChannel): void {
  running.channel.disconnect();
  running.dispose();
  useConnectionStore.getState().forgetChannel(running.channel.name);
}

/**
 * Start (or return) the user-scoped events channel.
 *
 * Listeners are attached before `connect()` so the burst that follows the open
 * — `connected`, then whatever the server pushes immediately — is never missed.
 */
export function startUserEvents(client: QueryClient): WebSocketChannel {
  const running = userEvents;
  if (running !== null) {
    // Re-arm rather than hand back whatever state it is in. A channel reaches a
    // permanent stop whenever `buildUrl` resolves to null or throws — a token
    // mint failing on a momentary blip is enough — and `connect()` is
    // idempotent, so this is a no-op on a healthy channel and the only way out
    // of a dead one short of a page reload.
    running.channel.connect();
    return running.channel;
  }

  const channel = createUserEventsChannel();
  const unsubscribeEvents = channel.subscribe((event) => {
    const session = useSessionStore.getState();
    switch (event.kind) {
      case 'connected':
        refreshOnUserEventsConnect(client);
        break;
      case 'message': {
        // Decoded once and routed twice: `parseWireJson`'s source-access reviver
        // makes a second `JSON.parse` several times the cost of a plain one.
        const envelope = decodeEventEnvelope(event.data);
        if (envelope === null) break;
        applyUserEventEnvelope(client, envelope);
        // After the cache routing, so the sessions list has at least been marked
        // stale before an alert points the user at it.
        handleAttentionEvent(envelope);
        break;
      }
      case 'tokenRejected':
        session.invalidateUserSession('tokenRejected');
        break;
      case 'accountRemoved':
        session.invalidateUserSession('accountRemoved');
        break;
      case 'incompatibleServer':
        // `minClientVersion: 0` matches iOS: a close frame carries no number,
        // and the real one arrives with the next `/api/version` probe. Neither
        // field reaches the screen — the takeover shows fixed first-party copy,
        // and `reason` is server-controlled text kept only for state-model
        // parity with Android's `SessionManager.AuthState` and iOS's
        // `AuthState`. Do not render it.
        session.markIncompatibleServer({ reason: event.reason, minClientVersion: 0 });
        break;
      default:
        break;
    }
  });
  const unsubscribeReachability = observeReachability((listener) =>
    channel.subscribe(listener),
  );

  userEvents = {
    channel,
    dispose: () => {
      unsubscribeEvents();
      unsubscribeReachability();
    },
  };
  channel.connect();
  return channel;
}

export function stopUserEvents(): void {
  const running = userEvents;
  if (running === null) return;
  userEvents = null;
  tearDown(running);
  // Attention alerts are scoped to this account's agents. One left on screen
  // after a sign-out or an account handover would deep-link into a session the
  // next user cannot open.
  clearAttentionNotifications();
  // Stopping on purpose is the signed-out state, where REST-only flows (login,
  // register, password reset) must never be paused as "offline".
  reportRealtimeStopped();
}

/**
 * Start (or return) the per-agent events channel.
 *
 * A successful open promotes the session to `fullyConnected`. The reverse is
 * deliberately not wired: `agentDisconnect()` clears *every* agent's tokens,
 * which is wrong for a network blip the backoff loop is about to recover from —
 * and wrong for removing one agent too, which is why the agents screen pairs
 * `clearAgentToken(agentId)` with `disconnectAgentEvents(agentId)` instead.
 */
export function connectAgentEvents(
  client: QueryClient,
  agentId: string,
): WebSocketChannel {
  const existing = agentEvents.get(agentId);
  if (existing !== undefined) {
    existing.channel.connect();
    return existing.channel;
  }

  const channel = createAgentEventsChannel(agentId);
  const dispose = channel.subscribe((event) => {
    switch (event.kind) {
      case 'connected':
        // This connection has not delivered its snapshot yet; until it does,
        // the cached list is whatever survived the drop.
        clearSessionsSnapshot(agentId);
        useSessionStore.getState().markAgentConnected();
        refreshOnAgentEventsConnect(client, agentId);
        break;
      case 'message': {
        const envelope = decodeEventEnvelope(event.data);
        if (envelope === null) break;
        applyAgentEventEnvelope(client, agentId, envelope);
        // The clearing half of the notification surface. Only this channel
        // carries the full session row, so only this channel can tell that an
        // attention has been answered.
        dismissResolvedAttention(envelope);
        break;
      }
      default:
        break;
    }
  });

  agentEvents.set(agentId, { channel, dispose });
  channel.connect();
  return channel;
}

export function disconnectAgentEvents(agentId: string): void {
  const running = agentEvents.get(agentId);
  if (running === undefined) return;
  agentEvents.delete(agentId);
  clearSessionsSnapshot(agentId);
  tearDown(running);
}

/**
 * Drop and reopen an agent's socket now — the sessions list's Refresh button,
 * mirroring both mobile clients' pull-to-refresh (`AgentEventBus.forceReconnect`).
 *
 * `connect()` first, deliberately: `forceReconnect` returns early on a channel
 * that stopped for good, which is precisely the state a user reaches for
 * Refresh in. The redundant attempt on a healthy channel is discarded by the
 * generation guard `forceReconnect` then advances.
 */
export function forceReconnectAgentEvents(agentId: string): void {
  const running = agentEvents.get(agentId);
  if (running === undefined) return;
  running.channel.connect();
  running.channel.forceReconnect();
}

/**
 * Force every long-lived events socket to re-handshake.
 *
 * The two channels this module owns. Both are idle by design — user-events
 * delivers nothing between account changes, an agent channel nothing between
 * session changes — so neither notices a dead path on its own.
 *
 * Channels built by their consumers are not reachable from here (`liveChannels`
 * models disposal only). The auto-scan monitor opts in through `onResume`
 * instead, which it needs at least as much: at the default 300 s scan interval
 * that socket is silent for most of every cycle, and its screen is one a user
 * deliberately leaves open. The terminal is left out on purpose — it is
 * user-driven, so a dead socket announces itself the moment someone types.
 *
 * `connect()` before `forceReconnect()`, as the per-agent Refresh does:
 * `forceReconnect` returns early on a channel that stopped for good, which is
 * one of the states this exists to recover from.
 */
export function forceReconnectAllEvents(): void {
  if (userEvents !== null) {
    userEvents.channel.connect();
    userEvents.channel.forceReconnect();
  }
  for (const agentId of [...agentEvents.keys()]) forceReconnectAgentEvents(agentId);
}

/**
 * Tear everything down — leaving the authenticated state, and the test seam.
 *
 * Agent channels must go too, not just the user channel: the relay authenticates
 * a mobile socket only at handshake, so one left open after sign-out stays fully
 * authorised for the previous account, and its reconnect loop would keep minting
 * tokens against a session that no longer exists.
 *
 * Contract for WEB-05/07/10, which own the agent channels: `RealtimeProvider`
 * calls this on every session change, so an agent channel is closed out from
 * under a component that is still mounted. Re-establish on the session
 * changing, not only on mount.
 */
export function stopAllRealtime(): void {
  // Session I/O and auto-scan channels are constructed by their consumers, not
  // here, so they are only reachable through the disposable registry.
  disposeAllChannels();
  for (const agentId of [...agentEvents.keys()]) disconnectAgentEvents(agentId);
  stopUserEvents();
}
