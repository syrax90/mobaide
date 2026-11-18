import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { GitHubSetupAction, GitHubStatus } from '../../../../api/autoscanRepository';
import type { AutoScanSourcesView } from '../useAutoScanSources';
import { GitHubSourcePanel } from './GitHubSourcePanel';

const SIGNED_OUT: GitHubStatus = {
  installed: true,
  version: '2.83.0',
  signed_in: false,
  can_install: false,
};

const SIGNED_IN: GitHubStatus = {
  ...SIGNED_OUT,
  signed_in: true,
  host: 'github.com',
  repo: 'acme/app',
};

function renderPanel(
  status: GitHubStatus,
  { repoLabels = [], chosen = [] }: { repoLabels?: string[]; chosen?: string[] } = {},
): GitHubSetupAction[] {
  const opened: GitHubSetupAction[] = [];
  const sources: AutoScanSourcesView = {
    github: { kind: 'ready', status },
    githubChecking: false,
    githubLabels: {
      status: repoLabels.length > 0 ? 'ready' : 'idle',
      labels: repoLabels.map((name) => ({ name, color: '' })),
      error: null,
    },
    checkGitHub: () => undefined,
    openGitHubSetup: (action) => {
      opened.push(action);
    },
    githubSetupPending: false,
    githubSetupError: null,
    linear: undefined,
    linearLoading: false,
    refreshLinear: () => undefined,
    saveLinearKey: () => undefined,
    linearKeySaving: false,
    linearKeyError: null,
  };
  render(
    <GitHubSourcePanel
      filter={{ labels: chosen, assignee: '', activeLabel: '' }}
      sources={sources}
      submitting={false}
      dispatch={() => undefined}
    />,
  );
  return opened;
}

afterEach(() => {
  cleanup();
});

describe('GitHubSourcePanel — sign-in', () => {
  it.each(['github.com', 'github.acme.dev'])('opens gh auth login for a %s remote', (host) => {
    const opened = renderPanel({ ...SIGNED_OUT, host, problem: `Not signed in to ${host}.` });

    fireEvent.click(screen.getByRole('button', { name: 'Sign in to GitHub' }));

    expect(opened).toEqual(['sign_in']);
  });

  // Signing in to GitHub cannot fix these: the agent names no GitHub host.
  it.each([
    ['a remote on another host', 'gitlab.com', "The repository's remote is on gitlab.com, not on GitHub."],
    ['a folder without a remote', null, 'This workspace is not a git repository.'],
  ])('offers no sign-in for %s', (_case, host, problem) => {
    renderPanel({ ...SIGNED_OUT, host, problem });

    expect(screen.queryByRole('button', { name: 'Sign in to GitHub' })).not.toBeInTheDocument();
    expect(screen.getByText(problem)).toBeInTheDocument();
  });
});

describe('GitHubSourcePanel — label chips', () => {
  // A large repository: more unchosen labels than the panel offers at once.
  const REPO_LABELS = Array.from(
    { length: 40 },
    (_, i) => `label-${String(i + 1).padStart(2, '0')}`,
  );
  const CHOSEN = ['label-40', 'custom'];
  const range = (from: number, to: number): string[] => REPO_LABELS.slice(from - 1, to);

  const chips = (): string[] =>
    within(screen.getByRole('group', { name: 'Labels to pick up' }))
      .getAllByRole('checkbox')
      .map((box) => box.parentElement?.textContent ?? '');

  it('caps the unchosen labels and keeps every chosen one, in discovery order', () => {
    renderPanel(SIGNED_IN, { repoLabels: REPO_LABELS, chosen: CHOSEN });

    expect(chips()).toEqual([...range(1, 30), 'label-40', 'custom']);
    expect(screen.getByText('9 more labels: type to find them.')).toBeInTheDocument();
  });

  it('filters the unchosen labels by any part of the typed text, ignoring case', () => {
    renderPanel(SIGNED_IN, { repoLabels: REPO_LABELS, chosen: CHOSEN });

    fireEvent.change(screen.getByRole('combobox', { name: 'Add a label' }), {
      target: { value: 'EL-3' },
    });

    expect(chips()).toEqual([...range(30, 39), 'label-40', 'custom']);
    expect(screen.queryByText(/more labels: type to find them/)).not.toBeInTheDocument();
  });
});
