import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchAgentCapabilities } from '../../api/agentCapabilities';
import { isApiError } from '../../api/errors';
import { listDirs } from '../../api/fsRepository';
import { queryKeys } from '../../lib/queryKeys';
import {
  DirListingCache,
  MAX_VISIBLE_SUGGESTIONS,
  SUGGESTION_DEBOUNCE_MS,
  nextListingRequest,
  rankDirs,
  splitPathQuery,
} from '../../lib/pathCompletion';
import { fsCapability, type AgentVersionInfo } from '../../protocol/dto/agentVersion';

const FS_CAPABILITY_STALE_MS = 5 * 60_000;

// Statuses that describe the directory itself (relative, missing, denied):
// remembered as an empty listing so further keystrokes under it stay local.
// A 503 (timed-out scan) or a network failure is transient and simply retried later.
const DEFINITIVE_FAILURES = new Set([400, 403, 404]);

const caches = new Map<string, DirListingCache>();

function cacheFor(agentId: string): DirListingCache {
  let cache = caches.get(agentId);
  if (cache === undefined) {
    cache = new DirListingCache();
    caches.set(agentId, cache);
  }
  return cache;
}

export interface PathSuggestions {
  /** At most `MAX_VISIBLE_SUGGESTIONS` names, best match first. */
  names: string[];
  /** Matches beyond the visible ones. */
  overflow: number;
  /** The directory `names` live in, for `acceptSuggestion`. */
  listedPath: string;
}

const NONE: PathSuggestions = { names: [], overflow: 0, listedPath: '' };

/**
 * Folder suggestions for `text` on `agentId`, or none while `active` is false,
 * the agent lacks `capabilities.fs.dirs`, or the text is not absolute.
 */
export function usePathSuggestions(
  agentId: string,
  text: string,
  active: boolean,
): PathSuggestions {
  const queryClient = useQueryClient();
  const supported = useQuery({
    queryKey: queryKeys.agentFsDirs(agentId),
    queryFn: async () => fsCapability(await fetchAgentCapabilities(agentId), 'dirs'),
    enabled: active && agentId !== '',
    staleTime: FS_CAPABILITY_STALE_MS,
    // The wizard has usually probed `/api/version` already; reuse its answer.
    initialData: () => {
      const info = queryClient.getQueryData<AgentVersionInfo>(
        queryKeys.agentCapabilities(agentId),
      );
      return info === undefined ? undefined : fsCapability(info, 'dirs');
    },
    initialDataUpdatedAt: () =>
      queryClient.getQueryState(queryKeys.agentCapabilities(agentId))?.dataUpdatedAt,
    meta: { silent: true },
  });

  const enabled = active && supported.data === true;
  const query = useMemo(() => (enabled ? splitPathQuery(text) : null), [enabled, text]);
  // Bumped when a fetch lands, so the render re-reads the cache.
  const [revision, setRevision] = useState(0);
  // Derived per render rather than per keystroke: typing inside an already
  // requested directory leaves `dir`/`q` unchanged, so the in-flight fetch
  // survives it. A truncated listing's follow-up request appears here once the
  // first one lands.
  const request = useMemo(() => {
    void revision;
    return query === null ? null : nextListingRequest(cacheFor(agentId), query);
  }, [agentId, query, revision]);
  const requestDir = request?.dir ?? null;
  const requestQ = request?.q ?? null;

  useEffect(() => {
    if (requestDir === null) return;
    const cache = cacheFor(agentId);
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const listing = await listDirs(agentId, requestDir, requestQ, controller.signal);
          cache.store(requestDir, requestQ, listing);
        } catch (error) {
          if (controller.signal.aborted) return;
          if (!(isApiError(error) && DEFINITIVE_FAILURES.has(error.status))) return;
          cache.store(requestDir, requestQ, { path: requestDir, dirs: [], truncated: false });
        }
        if (!controller.signal.aborted) setRevision((value) => value + 1);
      })();
    }, SUGGESTION_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [agentId, requestDir, requestQ]);

  return useMemo(() => {
    void revision;
    if (query === null) return NONE;
    const hit = cacheFor(agentId).lookup(query.dir, query.fragment);
    if (hit === undefined) return NONE;
    const ranked = rankDirs(hit.listing.dirs, query.fragment);
    return {
      names: ranked.slice(0, MAX_VISIBLE_SUGGESTIONS),
      overflow: Math.max(0, ranked.length - MAX_VISIBLE_SUGGESTIONS),
      listedPath: hit.listing.path,
    };
  }, [agentId, query, revision]);
}
