/**
 * WebSocket events → TanStack Query cache.
 *
 * Two halves, both lifted from Android `UserEventBus` / `AgentEventBus`:
 *
 * **Refresh on connect.** Every (re)open reconciles the lists that could have
 * changed while the socket was down — the mobile buses hit REST directly; here
 * the equivalent is invalidating the keys, which refetches whatever is mounted
 * and marks the rest stale.
 *
 * **Per event, invalidate rather than patch.** `setQueryData` is reserved for
 * events carrying a complete, authoritative object; a *delta* applied to a cache
 * the client cannot prove is in sync is how lists drift. Invalidation costs one
 * request and is always right.
 *
 * The three agent-channel session events are the sanctioned exception, and
 * `sessionCache.ts` documents why they qualify: `sessions.changed` is a full
 * list and `session.updated` / `session.attention` carry a whole row each, so
 * there is no delta to drift. Everything else still invalidates.
 *
 * All payloads that reach a schema are validated before anything is written, so
 * a malformed payload is dropped exactly as it is on Android and iOS — and
 * dropped rather than thrown: this runs inside the socket's listener loop, where
 * an escaping `ZodError` would take out `onmessage`. The remaining agent-channel
 * types (project lists, auto-scan DTOs) are still routed by `type` alone, their
 * shapes belonging to WEB-09/10.
 *
 * Unknown envelope types are ignored — that is the forward-compatibility
 * contract every client honours.
 */
import type { QueryClient, QueryKey } from '@tanstack/react-query';
import type { z } from 'zod';
import { queryKeys } from '../lib/queryKeys';
import { decodeEventEnvelope, type EventEnvelope } from '../protocol/dto/envelope';
import { applySessionsSnapshot, applySessionUpsert } from './sessionCache';
import { markSessionsSnapshotApplied } from './sessionsSyncStore';
import {
  agentStatusChangedSchema,
  customAgentTypeSchema,
  customTypeDeletedSchema,
  sessionAttentionSchema,
  shortcutDeletedSchema,
  shortcutSchema,
  templateDeletedSchema,
  templateSchema,
} from '../protocol/dto/userEvents';

export type BridgeOutcome =
  /** Well-formed and routed; `keys` may be empty when nothing is cached for it. */
  | { kind: 'invalidated'; type: string; keys: readonly QueryKey[] }
  /**
   * Well-formed and written straight into the cache. `keys` is empty when there
   * was nothing to patch — an upsert never seeds a list it cannot complete.
   */
  | { kind: 'patched'; type: string; keys: readonly QueryKey[] }
  /** Well-formed envelope, unrecognised `type`. */
  | { kind: 'ignored'; type: string }
  /** Malformed envelope, or a payload that failed its schema. */
  | { kind: 'dropped' };

interface UserEventRule {
  schema: z.ZodType;
  /** Receives the payload after its schema accepted it. */
  keys: (payload: unknown) => readonly QueryKey[];
}

const constantKeys =
  (...keys: readonly QueryKey[]) =>
  (): readonly QueryKey[] =>
    keys;

const USER_EVENT_RULES: Readonly<Record<string, UserEventRule>> = {
  'agent.status_changed': {
    schema: agentStatusChangedSchema,
    keys: constantKeys(queryKeys.agents()),
  },
  'template.created': { schema: templateSchema, keys: constantKeys(queryKeys.templates()) },
  'template.updated': { schema: templateSchema, keys: constantKeys(queryKeys.templates()) },
  'template.deleted': {
    schema: templateDeletedSchema,
    keys: constantKeys(queryKeys.templates()),
  },
  'shortcut.created': { schema: shortcutSchema, keys: constantKeys(queryKeys.shortcuts()) },
  'shortcut.updated': { schema: shortcutSchema, keys: constantKeys(queryKeys.shortcuts()) },
  'shortcut.deleted': {
    schema: shortcutDeletedSchema,
    keys: constantKeys(queryKeys.shortcuts()),
  },
  'custom_type.created': {
    schema: customAgentTypeSchema,
    keys: constantKeys(queryKeys.customAgentTypes()),
  },
  'custom_type.updated': {
    schema: customAgentTypeSchema,
    keys: constantKeys(queryKeys.customAgentTypes()),
  },
  'custom_type.deleted': {
    schema: customTypeDeletedSchema,
    // Deleting a type soft-deletes its saved shortcuts in the same server
    // transaction, with no companion `shortcut.deleted` events — so the
    // shortcut list has to be refetched too or it keeps showing dead rows.
    keys: constantKeys(queryKeys.customAgentTypes(), queryKeys.shortcuts()),
  },
  'session.attention': {
    schema: sessionAttentionSchema,
    // `agent_id` is stamped by the server from the authenticated tunnel, so it
    // is the trustworthy way to find the affected session list. It is optional
    // on the wire; without it there is nothing specific to invalidate.
    keys: (payload) => {
      const agentId = (payload as { agent_id?: unknown }).agent_id;
      return typeof agentId === 'string' && agentId !== ''
        ? [queryKeys.sessions(agentId)]
        : [];
    },
  },
};

function agentEventKeys(agentId: string, type: string): readonly QueryKey[] | null {
  switch (type) {
    case 'projects.changed':
    case 'git.changed':
      return [queryKeys.projects(agentId)];
    case 'autoscan.status':
    case 'autoscan.list.changed':
    case 'autoscan.event':
      return [queryKeys.autoscan(agentId)];
    default:
      return null;
  }
}

function invalidate(client: QueryClient, keys: readonly QueryKey[]): void {
  for (const queryKey of keys) {
    void client.invalidateQueries({ queryKey });
  }
}

/** Route one user-events envelope. Exported for tests; prefer `applyUserEvent`. */
export function routeUserEvent(envelope: EventEnvelope): BridgeOutcome {
  // `Object.hasOwn`, not a bare index: `type` is wire data, and a frame naming
  // an `Object.prototype` key (`constructor`, `toString`, …) would otherwise
  // resolve to an inherited function and throw out of the message listener
  // instead of being ignored as the unknown type it is.
  if (!Object.hasOwn(USER_EVENT_RULES, envelope.type)) {
    return { kind: 'ignored', type: envelope.type };
  }
  const rule = USER_EVENT_RULES[envelope.type];
  if (rule === undefined) return { kind: 'ignored', type: envelope.type };
  if (!rule.schema.safeParse(envelope.payload).success) return { kind: 'dropped' };
  return { kind: 'invalidated', type: envelope.type, keys: rule.keys(envelope.payload) };
}

export function applyUserEvent(client: QueryClient, text: string): BridgeOutcome {
  const envelope = decodeEventEnvelope(text);
  if (envelope === null) return { kind: 'dropped' };
  const outcome = routeUserEvent(envelope);
  if (outcome.kind === 'invalidated') invalidate(client, outcome.keys);
  return outcome;
}

export function applyAgentEvent(
  client: QueryClient,
  agentId: string,
  text: string,
): BridgeOutcome {
  const envelope = decodeEventEnvelope(text);
  if (envelope === null) return { kind: 'dropped' };

  switch (envelope.type) {
    case 'sessions.changed': {
      const outcome = applySessionsSnapshot(client, agentId, envelope.payload);
      if (outcome === 'dropped') return { kind: 'dropped' };
      markSessionsSnapshotApplied(agentId);
      return {
        kind: 'patched',
        type: envelope.type,
        keys: [queryKeys.sessions(agentId)],
      };
    }
    // The agent channel's `session.attention` carries the whole row, so it
    // upserts exactly like `session.updated`. iOS does the same; Android decodes
    // the event but never applies it to the list, which is why its cards cannot
    // show an attention chip.
    case 'session.updated':
    case 'session.attention': {
      const outcome = applySessionUpsert(client, agentId, envelope.payload);
      if (outcome === 'dropped') return { kind: 'dropped' };
      return {
        kind: 'patched',
        type: envelope.type,
        keys: outcome === 'applied' ? [queryKeys.sessions(agentId)] : [],
      };
    }
    default: {
      const keys = agentEventKeys(agentId, envelope.type);
      if (keys === null) return { kind: 'ignored', type: envelope.type };
      invalidate(client, keys);
      return { kind: 'invalidated', type: envelope.type, keys };
    }
  }
}

/**
 * The four account-scoped lists the user channel keeps fresh, reconciled on
 * every (re)connect. Order matches Android's `UserEventBus.handle`.
 */
export function refreshOnUserEventsConnect(client: QueryClient): void {
  invalidate(client, [
    // Re-probe the version gate. `useServerInfo` pins `staleTime: Infinity`, so
    // without this a long-lived tab would never re-evaluate it — and close code
    // 4002 cannot cover the gap: no server path sends it to a user socket (it
    // is the relay↔agent handshake only). A relay redeploy necessarily drops
    // this socket, which makes reconnect the honest trigger, and matches
    // Android re-probing on every launch.
    queryKeys.serverInfo(),
    queryKeys.agents(),
    queryKeys.templates(),
    queryKeys.shortcuts(),
    queryKeys.customAgentTypes(),
  ]);
}

/**
 * Reconcile the agent-scoped caches the tunnel does not repair by itself.
 *
 * Sessions are deliberately absent: the agent pushes a `sessions.changed`
 * snapshot on every connect and that snapshot now *replaces* the cache
 * authoritatively, so invalidating as well would fire a `/proxy` GET racing it —
 * and a slow response landing afterwards would roll the list back. Projects
 * still need it, since `projects.changed` only fires on an actual change.
 */
export function refreshOnAgentEventsConnect(client: QueryClient, agentId: string): void {
  invalidate(client, [queryKeys.projects(agentId)]);
}
