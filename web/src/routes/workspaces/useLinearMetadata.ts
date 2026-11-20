import { useQuery } from '@tanstack/react-query';
import {
  listLinearLabels,
  listLinearStates,
  type LinearLabel,
  type LinearState,
} from '../../api/workspaceRepository';
import { queryKeys } from '../../lib/queryKeys';

export interface LinearMetadata {
  labels: LinearLabel[];
  states: LinearState[];
  failed: boolean;
}

/**
 * One workspace's Linear labels and workflow states, read with its stored key,
 * for the Auto Scan filter chips. Shared by the workspace form and the Auto Scan
 * wizard, which read the same key and therefore the same cache entry.
 */
export function useLinearMetadata(agentId: string, workspaceName: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.linearMetadata(agentId, workspaceName),
    queryFn: async (): Promise<LinearMetadata> => {
      try {
        const [labels, states] = await Promise.all([
          listLinearLabels(agentId, workspaceName),
          listLinearStates(agentId, workspaceName),
        ]);
        return { labels, states, failed: false };
      } catch {
        // Swallowed rather than thrown: a rejected query trips the global
        // error toast, and a Linear outage should degrade the chip lists to
        // "what this workspace already selects", not shout at the user.
        return { labels: [], states: [], failed: true };
      }
    },
    enabled,
  });
}
