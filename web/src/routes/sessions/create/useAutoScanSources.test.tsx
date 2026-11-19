import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { GitHubLabelsResponse, GitHubStatus } from '../../../api/autoscanRepository';

// The collaborator is the repository: each labels read answers the next
// queued list, as GitHub would after a label is made there.
const api = vi.hoisted(() => ({
  labels: [] as GitHubLabelsResponse[],
  labelReads: 0,
}));

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
  startGitHubSetup: () => Promise.reject(new Error('setup is not under test')),
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
