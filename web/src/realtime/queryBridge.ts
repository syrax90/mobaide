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
 * Full-snapshot events are the sanctioned exception, documented in
 * `sessionCache.ts` and `projectsCache.ts`: `sessions.changed` and
 * `projects.changed` are complete lists, and `session.updated` /
 * `session.attention` carry a whole row each, so there is no delta to drift.
 *
 * That split is the rule for every agent-channel event: an event carrying the
 * new state is **written**, an event carrying only a pointer to it is
 * **invalidated** — and a pointer event's query must not pin
 * `staleTime: Infinity`, because an invalidation landing during a cold fetch is
 * deduped into that fetch and leaves the stale result pinned (CLA-234).
 * `git.changed` is the pointer case; `git.job.progress` / `git.job.done` carry
 * a whole `GitJobResponse` each and are written (`sessionGitJobCache.ts`), as is
 * `voice.changed`, a whole `VoiceStatusResponse` (`voiceCache.ts`).
 *
 * WEB-10 judged the three `autoscan.*` types by the same rule and none of them
 * is an invalidation: `autoscan.list.changed` and `autoscan.status` carry full
 * state and are written (`autoscanCache.ts`), while `autoscan.event` is the
 * detail log's own stream and names no cache at all — the monitor screen reads
 * it from the dedicated `ws/autoscan` channel, where it arrives filtered by
 * subscription. Invalidating on it, as this bridge did before, meant a `/proxy`
 * GET per emitted log line.
 *
 * All payloads that reach a schema are validated before anything is written, so
 * a malformed payload is dropped exactly as it is on Android and iOS — and
 * dropped rather than thrown: this runs inside the socket's listener loop, where
 * an escaping `ZodError` would take out `onmessage`.
 *
 * Unknown envelope types are ignored — that is the forward-compatibility
 * contract every client honours.
 */
import type { QueryClient, QueryKey } from '@tanstack/react-query';
import type { z } from 'zod';
import type { SessionGitStatus } from '../api/sessionGitRepository';
import { queryKeys } from '../lib/queryKeys';
import { decodeEventEnvelope, type EventEnvelope } from '../protocol/dto/envelope';
import { gitChangedSchema, type GitChanged } from '../protocol/dto/projects';
import type { SessionResponse } from '../protocol/dto/session';
import { applyAutoScanSnapshot, applyAutoScanStatus } from './autoscanCache';
import { applyProjectsSnapshot } from './projectsCache';
import { applySessionsSnapshot, applySessionUpsert } from './sessionCache';
import { applySessionGitJobEvent } from './sessionGitJobCache';
import { applyVoiceSnapshot } from './voiceCache';
import { markSessionsSnapshotApplied } from './sessionsSyncStore';
import {
  agentStatusChangedSchema,
  customAgentTypeSchema,
  customTypeDeletedSchema,
  firstAgentPairedSchema,
  sessionAttentionSchema,
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
  'account.first_agent_paired': {
    schema: firstAgentPairedSchema,
    keys: constantKeys(queryKeys.agents()),
  },
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
    keys: constantKeys(queryKeys.customAgentTypes()),
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

/**
 * Which in-session Changes viewers a `git.changed` touches, as their
 * `sessionGit` prefixes (the status poll and any open diff alike).
 *
 * Android `GitRepository.matchesSession`: a viewer that has no repo answer yet
 * always refreshes; otherwise its session's `project_name`, or its resolved
 * `toplevel` / `common_dir` (a linked worktree shares the common dir with the
 * project it was cut from), has to match the event. Read from the cache rather
 * than tracked by the viewers, so this stays a pure function of what is cached.
 */
function sessionGitKeysFor(
  client: QueryClient,
  agentId: string,
  event: GitChanged,
): readonly QueryKey[] {
  const sessions = client.getQueryData<SessionResponse[]>(queryKeys.sessions(agentId));
  const keys: QueryKey[] = [];
  for (const query of client.getQueryCache().findAll({
    queryKey: queryKeys.sessionGitRoot(agentId),
  })) {
    const sessionId = query.queryKey[3];
    if (query.queryKey[4] !== 'status' || typeof sessionId !== 'string') continue;
    const status = query.state.data as SessionGitStatus | undefined;
    const repo = status?.repo ?? null;
    const projectName = sessions?.find((row) => row.session_id === sessionId)?.project_name;
    const matches =
      repo === null ||
      (projectName !== undefined && projectName === event.project_name) ||
      (event.toplevel !== undefined && event.toplevel === repo.toplevel) ||
      (event.common_dir !== undefined && event.common_dir === repo.common_dir);
    if (matches) keys.push(queryKeys.sessionGit(agentId, sessionId));
  }
  return keys;
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

export function applyUserEventEnvelope(
  client: QueryClient,
  envelope: EventEnvelope,
): BridgeOutcome {
  const outcome = routeUserEvent(envelope);
  if (outcome.kind === 'invalidated') invalidate(client, outcome.keys);
  return outcome;
}

/**
 * Decode-and-route. Callers that need the envelope for something else — the
 * notification surface does — should decode once and use the `*Envelope`
 * variants instead: `parseWireJson`'s source-access reviver makes a second
 * `JSON.parse` several times the cost of a plain one.
 */
export function applyUserEvent(client: QueryClient, text: string): BridgeOutcome {
  const envelope = decodeEventEnvelope(text);
  if (envelope === null) return { kind: 'dropped' };
  return applyUserEventEnvelope(client, envelope);
}

export function applyAgentEvent(
  client: QueryClient,
  agentId: string,
  text: string,
): BridgeOutcome {
  const envelope = decodeEventEnvelope(text);
  if (envelope === null) return { kind: 'dropped' };
  return applyAgentEventEnvelope(client, agentId, envelope);
}

export function applyAgentEventEnvelope(
  client: QueryClient,
  agentId: string,
  envelope: EventEnvelope,
): BridgeOutcome {
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
    case 'projects.changed': {
      const outcome = applyProjectsSnapshot(client, agentId, envelope.payload);
      if (outcome === 'dropped') return { kind: 'dropped' };
      return {
        kind: 'patched',
        type: envelope.type,
        keys: [queryKeys.projects(agentId)],
      };
    }
    case 'autoscan.list.changed': {
      const outcome = applyAutoScanSnapshot(client, agentId, envelope.payload);
      if (outcome === 'dropped') return { kind: 'dropped' };
      return {
        kind: 'patched',
        type: envelope.type,
        keys: [queryKeys.autoscan(agentId)],
      };
    }
    case 'autoscan.status': {
      const outcome = applyAutoScanStatus(client, agentId, envelope.payload);
      if (outcome === 'dropped') return { kind: 'dropped' };
      return {
        kind: 'patched',
        type: envelope.type,
        keys: outcome === 'applied' ? [queryKeys.autoscan(agentId)] : [],
      };
    }
    // Deliberately inert. This is one scan event — the payload the detail screen
    // consumes from its own `ws/autoscan` channel, where it arrives filtered by
    // subscription. It reaches the shared bus too because that bus is
    // unfiltered, and `_emit` publishes one per log line: invalidating here, as
    // this bridge did before WEB-10, cost a `/proxy` GET per line of output.
    case 'autoscan.event':
      return { kind: 'ignored', type: envelope.type };
    case 'git.changed': {
      // Per project, not the whole agent: the payload names exactly one, and
      // invalidating the others would refetch every open Git screen's list.
      // The in-session viewers it touches are found by repo, not by project.
      const decoded = gitChangedSchema.safeParse(envelope.payload);
      if (!decoded.success) return { kind: 'dropped' };
      const keys = [
        queryKeys.gitItems(agentId, decoded.data.project_name),
        ...sessionGitKeysFor(client, agentId, decoded.data),
      ];
      invalidate(client, keys);
      return { kind: 'invalidated', type: envelope.type, keys };
    }
    // A whole job snapshot per frame — written, like the autoscan pair, and
    // for the same reason: the agent emits up to four a second while a push
    // runs, and a `/proxy` GET per frame is exactly the storm the throttle
    // exists to prevent.
    case 'git.job.progress':
    case 'git.job.done': {
      const outcome = applySessionGitJobEvent(client, agentId, envelope.payload);
      if (outcome === 'dropped') return { kind: 'dropped' };
      const job = envelope.payload as { session_id: string; job_id: string };
      return {
        kind: 'patched',
        type: envelope.type,
        keys: [queryKeys.sessionGitJob(agentId, job.session_id, job.job_id)],
      };
    }
    case 'voice.changed': {
      const outcome = applyVoiceSnapshot(client, agentId, envelope.payload);
      if (outcome === 'dropped') return { kind: 'dropped' };
      return {
        kind: 'patched',
        type: envelope.type,
        keys: [queryKeys.voiceStatus(agentId)],
      };
    }
    default:
      return { kind: 'ignored', type: envelope.type };
  }
}

/**
 * The account-scoped lists the user channel keeps fresh, reconciled on every
 * (re)connect. Order matches Android's `UserEventBus.handle`.
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
    queryKeys.customAgentTypes(),
  ]);
}

/**
 * Reconcile the agent-scoped caches the tunnel does not repair by itself.
 *
 * Sessions and projects are deliberately absent: the relay asks the agent for an
 * events snapshot whenever a client opens the events socket, and the agent
 * answers by republishing both lists in full
 * (`relay/client.py::_handle_request_events_snapshot`). Those snapshots now
 * *replace* their caches authoritatively, so invalidating as well would fire a
 * `/proxy` GET racing them — and a slow response landing afterwards would roll a
 * list back.
 *
 * Git items need it: `git.changed` fires only on an actual change and carries no
 * snapshot, so a list cached before the socket dropped cannot otherwise be
 * trusted. The prefix key covers every project at once.
 *
 * Auto Scan needs it for a different reason — not a missing payload but a
 * missing *transport*. `autoscan.list.changed` is a full snapshot and the agent
 * does send it on connect, but only on the direct `ws/events` path;
 * `relay/client.py::_handle_request_events_snapshot` republishes sessions and
 * projects and stops there, and the relay is the only path a browser takes
 * (CLA-249). Without this, a scan started while the socket was down stays
 * invisible until the next start or stop.
 *
 * The in-session Changes viewers are git items' case again: `git.changed` is
 * their only push and it carries no snapshot, so a status polled before the
 * socket dropped cannot be trusted either.
 */
export function refreshOnAgentEventsConnect(client: QueryClient, agentId: string): void {
  invalidate(client, [
    queryKeys.gitItemsRoot(agentId),
    queryKeys.sessionGitRoot(agentId),
    queryKeys.autoscan(agentId),
  ]);
}
