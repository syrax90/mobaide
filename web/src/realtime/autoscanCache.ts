/**
 * `autoscan.list.changed` and `autoscan.status`, applied straight to the query
 * cache.
 *
 * Both qualify for the write-rather-than-invalidate exception, on the evidence
 * `queryBridge.ts` states as the general rule: `autoscan.list.changed` carries
 * the agent's **entire** `list_response()` — scans, capacity, available projects
 * and the default prompt — and `autoscan.status` carries a complete
 * `AutoScanInstanceDto`. Neither is a delta, so neither can drift.
 *
 * Invalidating would be far worse here than for projects, because of how often
 * these fire: `AutoScanInstance._emit` publishes an `autoscan.status` alongside
 * **every** scan event, so a verbose cycle would turn one `/proxy` GET per log
 * line into the cost of watching a scan.
 *
 * Unlike `projects.changed`, though, the list is **not** republished when the
 * events socket reopens: the direct `ws/events` handler sends it as a connect
 * snapshot, but `relay/client.py::_handle_request_events_snapshot` — the path a
 * browser always takes — republishes only sessions and projects (CLA-249). The
 * connect-time refresh in `refreshOnAgentEventsConnect` covers that gap, which
 * is also why the auto-scan query must not pin `staleTime: Infinity` (CLA-234).
 */
import type { QueryClient } from '@tanstack/react-query';
import { queryKeys } from '../lib/queryKeys';
import {
  autoScanInstanceSchema,
  autoScanListResponseSchema,
  type AutoScanListResponse,
} from '../protocol/dto/autoscan';

/**
 * **May seed** an entry nothing has fetched: the payload is authoritative, so a
 * monitor mounting a moment later paints from it with no request at all.
 */
export function applyAutoScanSnapshot(
  client: QueryClient,
  agentId: string,
  payload: unknown,
): 'applied' | 'dropped' {
  // The schema defaults every field, which it must — `listAutoScans` relies on
  // that for the relay's degraded body, and the golden pins a `{}` case. But that
  // makes `safeParse` succeed for *any* object, so an `autoscan.status` payload
  // misrouted here, or an event that lost its `scans`, would parse to an empty
  // list and silently wipe a good one. The sibling cannot do this because
  // `projectsChangedSchema` requires its array; here the discrimination has to
  // live at the write site.
  if (
    typeof payload !== 'object' ||
    payload === null ||
    !Array.isArray((payload as { scans?: unknown }).scans)
  ) {
    return 'dropped';
  }
  const decoded = autoScanListResponseSchema.safeParse(payload);
  if (!decoded.success) return 'dropped';

  const key = queryKeys.autoscan(agentId);
  // Cancel first, for the race the seed cannot cover: `listAutoScans` and the
  // events channel start together on mount, so a slower GET can resolve *after*
  // this snapshot and overwrite it with older rows.
  void client.cancelQueries({ queryKey: key });
  client.setQueryData<AutoScanListResponse>(key, () => decoded.data);
  return 'applied';
}

/**
 * Replace one scan's row.
 *
 * Deliberately never inserts, matching iOS `AutoScanRepository.apply`. A scan
 * this client has not seen start is not a row this event can complete — the
 * payload has no `capacity`, `max_scans` or `available_projects` — and the
 * `autoscan.list.changed` that every start publishes is what introduces it.
 *
 * Returns `skipped` rather than `applied` when there is nothing cached to patch,
 * so the bridge does not report a cache write that did not happen. The name
 * matches `applySessionUpsert`'s third outcome, which means the same thing.
 */
export function applyAutoScanStatus(
  client: QueryClient,
  agentId: string,
  payload: unknown,
): 'applied' | 'skipped' | 'dropped' {
  const decoded = autoScanInstanceSchema.safeParse(payload);
  if (!decoded.success) return 'dropped';

  const key = queryKeys.autoscan(agentId);
  const cached = client.getQueryData<AutoScanListResponse>(key);
  if (cached === undefined) return 'skipped';
  const index = cached.scans.findIndex(
    (scan) => scan.project_name === decoded.data.project_name,
  );
  if (index === -1) return 'skipped';

  // Same race `applySessionUpsert` guards: `refreshOnAgentEventsConnect`
  // invalidates this key on every agent-channel connect, so a `listAutoScans`
  // GET is routinely in flight when these frames arrive, and one resolving
  // afterwards reverts the row. Safe unguarded here — the cached-entry check
  // above already rules out cancelling a cold initial fetch.
  void client.cancelQueries({ queryKey: key });
  const scans = [...cached.scans];
  scans[index] = decoded.data;
  client.setQueryData<AutoScanListResponse>(key, { ...cached, scans });
  return 'applied';
}
