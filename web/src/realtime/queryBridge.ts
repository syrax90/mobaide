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
 * full-snapshot events; a delta applied to a cache the client cannot prove is
 * in sync is how lists drift. Invalidation costs one request and is always
 * right.
 *
 * The user-channel payloads are validated before anything is invalidated, so a
 * malformed payload is dropped exactly as it is on Android and iOS. The
 * agent-channel payloads are only routed by `type`: their shapes (session
 * lists, project lists, auto-scan DTOs) belong to WEB-07/09/10, and modelling
 * them here would create a second source of truth ahead of their consumers.
 *
 * Unknown envelope types are ignored — that is the forward-compatibility
 * contract every client honours.
 */
import type { QueryClient, QueryKey } from '@tanstack/react-query';
import type { z } from 'zod';
import { queryKeys } from '../lib/queryKeys';
import { decodeEventEnvelope, type EventEnvelope } from '../protocol/dto/envelope';
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
    case 'sessions.changed':
    case 'session.updated':
    case 'session.attention':
      return [queryKeys.sessions(agentId)];
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
  const keys = agentEventKeys(agentId, envelope.type);
  if (keys === null) return { kind: 'ignored', type: envelope.type };
  invalidate(client, keys);
  return { kind: 'invalidated', type: envelope.type, keys };
}

/**
 * The four account-scoped lists the user channel keeps fresh, reconciled on
 * every (re)connect. Order matches Android's `UserEventBus.handle`.
 */
export function refreshOnUserEventsConnect(client: QueryClient): void {
  invalidate(client, [
    queryKeys.agents(),
    queryKeys.templates(),
    queryKeys.shortcuts(),
    queryKeys.customAgentTypes(),
  ]);
}

/**
 * The agent tunnel re-pushes its own snapshot on connect, but the REST-backed
 * caches still need marking stale — the snapshot arrives as events this bridge
 * turns into invalidations anyway, and a connect with no changes would
 * otherwise leave a stale list mounted.
 */
export function refreshOnAgentEventsConnect(client: QueryClient, agentId: string): void {
  invalidate(client, [queryKeys.sessions(agentId), queryKeys.projects(agentId)]);
}
