/**
 * `projects.changed`, applied straight to the query cache.
 *
 * It qualifies for the same exception `sessionCache.ts` carves out, on the same
 * evidence: the payload is the agent's **complete** list, rebuilt from
 * `store.list_public()` after every create, update, delete and reorder
 * (`agent/projects/service.py::_publish_projects_changed`), and republished on
 * every mobile events-socket connect — the relay sends `request_events_snapshot`
 * and the agent answers by publishing the whole list again
 * (`relay/client.py::_handle_request_events_snapshot`).
 *
 * There is therefore no delta to drift, and invalidating instead is actively
 * worse in two ways. It costs a `/proxy` round trip per event; and TanStack only
 * self-cancels a refetch once `dataUpdatedAt` is truthy, so an invalidation
 * landing during a **cold** fetch is deduped into that fetch — the older list
 * resolves, clears `isInvalidated`, and `staleTime: Infinity` then pins it
 * forever (CLA-234).
 */
import type { QueryClient } from '@tanstack/react-query';
import { queryKeys } from '../lib/queryKeys';
import { sortProjects } from '../api/projectRepository';
import { projectsChangedSchema, type ProjectInfo } from '../protocol/dto/projects';

/**
 * **May seed** a cache entry nothing has fetched: the payload is authoritative,
 * so a list mounting a moment later paints from it with no request at all. The
 * projects query must therefore not set `refetchOnMount: 'always'`, which would
 * throw the seed away.
 */
export function applyProjectsSnapshot(
  client: QueryClient,
  agentId: string,
  payload: unknown,
): 'applied' | 'dropped' {
  const decoded = projectsChangedSchema.safeParse(payload);
  if (!decoded.success) return 'dropped';

  const key = queryKeys.projects(agentId);
  // Cancel first, for the race the seed cannot cover: `listProjects` and the
  // events channel start together on mount, so a slower GET can resolve *after*
  // this snapshot and overwrite it with older rows. The agent only re-pushes on
  // the next change, so the list would stay wrong until then.
  void client.cancelQueries({ queryKey: key });
  client.setQueryData<ProjectInfo[]>(key, () => sortProjects(decoded.data.projects));
  return 'applied';
}
