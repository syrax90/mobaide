import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { GitHubLabelsResponse, GitHubStatus } from '../../../api/autoscanRepository';
import type { WorkspaceInfo } from '../../../api/workspaceRepository';
import { queryKeys } from '../../../lib/queryKeys';
import type { SessionResponse } from '../../../protocol/dto/session';

// The collaborator is the repository: each labels read answers the next
// queued list, as GitHub would after a label is made there.
// The setup and the key save answer only once the test releases them, so the
// route can switch agents while they are in flight.
const api = vi.hoisted(() => ({
  labels: [] as GitHubLabelsResponse[],
  labelReads: 0,
  release: null as (() => void) | null,
}));
let answer: unknown = null;

function gated<T>(): Promise<T> {
  return new Promise<T>((resolve) => {
    api.release = () => {
      resolve(answer as T);
    };
  });
}

vi.mock('../../../api/autoscanRepository', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../api/autoscanRepository')>()),
  getGitHubStatus: (): Promise<GitHubStatus> =>
    Promise.resolve({
      installed: true,
      version: '2.83.0',
      signed_in: true,
      can_install: false,
      host: 'github.com',
      repo: 'acme/app',
    }),
  listGitHubLabels: (): Promise<GitHubLabelsResponse> => {
    api.labelReads += 1;
    const next = api.labels.shift();
    return next === undefined
      ? Promise.reject(new Error('unexpected labels read'))
      : Promise.resolve(next);
  },
  startGitHubSetup: () => gated(),
}));

vi.mock('../../../api/workspaceRepository', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../api/workspaceRepository')>()),
  updateWorkspace: () => gated(),
}));

const { workspaceInfoSchema } = await import('../../../protocol/dto/workspaces');
const { useAutoScanSources } = await import('./useAutoScanSources');

const WORKSPACE = workspaceInfoSchema.parse({ name: 'app', path: '/srv/app', is_git: true });

const labelsOf = (...names: string[]): GitHubLabelsResponse => ({
  labels: names.map((name) => ({ name, color: '' })),
  issue_types: [],
});

let client: QueryClient;
const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.labels.length = 0;
  api.labelReads = 0;
  api.release = null;
  answer = null;
});

describe('useAutoScanSources — Check again', () => {
  // A label made on GitHub after the wizard opened can only be picked once
  // the list is read again.
  it('reads the repository labels again with the GitHub status', async () => {
    api.labels.push(labelsOf('bug'), labelsOf('bug', 'auto-scan'));
    const { result } = renderHook(
      () =>
        useAutoScanSources({
          agentId: 'a1',
          workspace: WORKSPACE,
          active: true,
          source: 'github',
          githubSetup: false,
          onSetupSession: () => undefined,
        }),
      { wrapper },
    );
    await waitFor(() => {
      expect(result.current.githubLabels.status).toBe('ready');
    });

    act(() => {
      result.current.checkGitHub();
    });

    await waitFor(() => {
      expect(result.current.githubLabels.labels.map((label) => label.name)).toEqual([
        'bug',
        'auto-scan',
      ]);
    });
    expect(api.labelReads).toBe(2);
  });
});

describe('useAutoScanSources — an agent switch while a fix is in flight', () => {
  // The new-session page stays mounted when the route moves to another
  // agent, so a late answer must still land on the PC that was asked.
  const render = (onSetupSession: (session: SessionResponse, origin: unknown) => void) =>
    renderHook(
      ({ agentId }: { agentId: string }) =>
        useAutoScanSources({
          agentId,
          workspace: WORKSPACE,
          active: true,
          source: 'linear',
          githubSetup: true,
          onSetupSession,
        }),
      { wrapper, initialProps: { agentId: 'a1' } },
    );

  it('writes a saved Linear key to the workspaces of the agent that saved it', async () => {
    const other = workspaceInfoSchema.parse({ name: 'app', path: '/home/b/app', is_git: true });
    client.setQueryData<WorkspaceInfo[]>(queryKeys.workspaces('a2'), [other]);
    answer = { ...WORKSPACE, has_linear_key: true };
    const { result, rerender } = render(() => undefined);

    act(() => {
      result.current.saveLinearKey('lin_api_key');
    });
    await waitFor(() => {
      expect(api.release).not.toBeNull();
    });
    rerender({ agentId: 'a2' });
    act(() => {
      api.release?.();
    });

    await waitFor(() => {
      expect(client.getQueryData<WorkspaceInfo[]>(queryKeys.workspaces('a1'))).toEqual([
        answer,
      ]);
    });
    expect(client.getQueryData<WorkspaceInfo[]>(queryKeys.workspaces('a2'))).toEqual([other]);
  });

  it('hands the setup terminal over with the agent that opened it', async () => {
    const onSetupSession = vi.fn();
    answer = { session_id: 's1' };
    const { result, rerender } = render(onSetupSession);

    act(() => {
      result.current.openGitHubSetup?.('sign_in');
    });
    await waitFor(() => {
      expect(api.release).not.toBeNull();
    });
    rerender({ agentId: 'a2' });
    act(() => {
      api.release?.();
    });

    await waitFor(() => {
      expect(onSetupSession).toHaveBeenCalledWith(answer, {
        agentId: 'a1',
        workspaceName: 'app',
      });
    });
  });
});
