/**
 * What the Auto Scan config step reads from the PC for the chosen source: the
 * GitHub CLI's readiness and the repository's labels, or the Linear labels and
 * states — plus the two actions that fix a source that is not ready (open the
 * `gh` setup terminal, save a Linear key).
 *
 * Lifted out of the panel into the wizard so the Start button can be gated on
 * the same answers the panel shows.
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getGitHubStatus,
  listGitHubLabels,
  startGitHubSetup,
  type GitHubLabel,
  type GitHubSetupAction,
} from '../../../api/autoscanRepository';
import { isApiError } from '../../../api/errors';
import { updateWorkspace, type WorkspaceInfo, type WorkspaceSummary } from '../../../api/workspaceRepository';
import { queryKeys } from '../../../lib/queryKeys';
import type { SessionResponse } from '../../../protocol/dto/session';
import { upsertWorkspace } from '../../../realtime/workspacesCache';
import { useLinearMetadata, type LinearMetadata } from '../../workspaces/useLinearMetadata';
import { githubProblem, type GitHubStatusView } from './autoScanSetup';
import { errorDetailString, githubSetupErrorMessage } from './createErrors';
import type { AutoScanSource } from './wizardState';

export interface GitHubLabelsView {
  status: 'idle' | 'loading' | 'ready' | 'error';
  labels: readonly GitHubLabel[];
  error: string | null;
}

export interface AutoScanSourcesView {
  github: GitHubStatusView;
  /** A re-check is in flight while the last answer stays on screen. */
  githubChecking: boolean;
  githubLabels: GitHubLabelsView;
  checkGitHub: () => void;
  /** `null` while the agent has no setup route (`capabilities.autoscan.github_setup`). */
  openGitHubSetup: ((action: GitHubSetupAction) => void) | null;
  githubSetupPending: boolean;
  githubSetupError: string | null;
  linear: LinearMetadata | undefined;
  linearLoading: boolean;
  refreshLinear: () => void;
  saveLinearKey: (key: string) => void;
  linearKeySaving: boolean;
  linearKeyError: string | null;
}

function failureMessage(error: unknown): string {
  if (isApiError(error)) return errorDetailString(error.body) ?? error.message;
  return error instanceof Error ? error.message : String(error);
}

export function useAutoScanSources(input: {
  agentId: string;
  workspace: WorkspaceSummary | null;
  /** The config step of an Auto Scan against an agent with `capabilities.autoscan`. */
  active: boolean;
  source: AutoScanSource | null;
  githubSetup: boolean;
  /** The setup terminal opened; the wizard parks its draft and shows the session. */
  onSetupSession: (session: SessionResponse) => void;
}): AutoScanSourcesView {
  const { agentId, workspace, active, source } = input;
  const queryClient = useQueryClient();
  const workspaceName = workspace?.name ?? '';

  const githubActive = active && source === 'github' && workspace !== null;
  // Fresh on every look: the user fixes `gh` outside this page — in the setup
  // terminal, or in a shell on the PC — and comes back by Back or by switching
  // windows, so both a remount and a window focus re-check.
  const status = useQuery({
    queryKey: queryKeys.githubStatus(agentId, workspaceName),
    queryFn: () => getGitHubStatus(agentId, workspaceName),
    enabled: githubActive,
    staleTime: 0,
    refetchOnMount: 'always',
    refetchOnWindowFocus: true,
    // Rendered inline; a toast would repeat it.
    meta: { silent: true },
  });

  const ready = status.data !== undefined && githubProblem(status.data) === null;
  const labels = useQuery({
    queryKey: queryKeys.githubLabels(agentId, workspaceName),
    queryFn: () => listGitHubLabels(agentId, workspaceName),
    enabled: githubActive && ready,
    meta: { silent: true },
  });

  const github: GitHubStatusView =
    status.data !== undefined
      ? { kind: 'ready', status: status.data }
      : status.isError
        ? { kind: 'error', message: failureMessage(status.error) }
        : { kind: 'loading' };

  const githubLabels: GitHubLabelsView = !(githubActive && ready)
    ? { status: 'idle', labels: [], error: null }
    : labels.data !== undefined
      ? { status: 'ready', labels: labels.data, error: null }
      : labels.isError
        ? { status: 'error', labels: [], error: failureMessage(labels.error) }
        : { status: 'loading', labels: [], error: null };

  const setup = useMutation({
    mutationFn: (action: GitHubSetupAction) => startGitHubSetup(agentId, workspaceName, action),
    onSuccess: (session) => {
      input.onSetupSession(session);
    },
  });

  const hasKey = workspace?.has_linear_key === true;
  const linear = useLinearMetadata(agentId, workspaceName, active && source === 'linear' && hasKey);

  // Kept outside the mutation, which resets as soon as it settles.
  const [linearKeyError, setLinearKeyError] = useState<string | null>(null);
  const linearKey = useMutation({
    mutationFn: (key: string) => updateWorkspace(agentId, workspaceName, { linear_api_key: key }),
    // The variables are the plaintext key: drop the entry as soon as it settles,
    // as the workspace form's save does.
    gcTime: 0,
    onSettled: () => {
      linearKey.reset();
    },
    onError: (error) => {
      setLinearKeyError(`Save failed: ${failureMessage(error)}`);
    },
    onSuccess: async (updated) => {
      // Seeded so the panel turns into the filter at once; the
      // `projects.changed` echo replaces it idempotently.
      queryClient.setQueryData<WorkspaceInfo[]>(queryKeys.workspaces(agentId), (current) =>
        upsertWorkspace(current ?? [], updated),
      );
      // A lookup from before the key existed failed; read again with it.
      await queryClient.invalidateQueries({
        queryKey: queryKeys.linearMetadata(agentId, workspaceName),
      });
    },
  });

  return {
    github,
    githubChecking: status.isFetching,
    githubLabels,
    checkGitHub: () => {
      void status.refetch();
    },
    openGitHubSetup: input.githubSetup
      ? (action) => {
          setup.mutate(action);
        }
      : null,
    githubSetupPending: setup.isPending,
    githubSetupError: setup.isError ? githubSetupErrorMessage(setup.error) : null,
    linear: linear.data,
    linearLoading: linear.isFetching,
    refreshLinear: () => {
      void linear.refetch();
    },
    saveLinearKey: (key) => {
      setLinearKeyError(null);
      linearKey.mutate(key);
    },
    linearKeySaving: linearKey.isPending,
    linearKeyError,
  };
}
