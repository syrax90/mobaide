import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { WorktreeBases } from '../../../api/gitRepository';
import type { ProjectSummary } from '../../../api/projectRepository';
import type { AgentVersionInfo } from '../../../protocol/dto/agentVersion';
import { ConfigStep, type WorktreeBasesView } from './ConfigStep';
import {
  INITIAL_WIZARD_STATE,
  type WizardAction,
  type WizardMode,
  type WizardState,
} from './wizardState';

const gitProject: ProjectSummary = {
  name: 'mobaide',
  path: '/srv/mobaide',
  order: 0,
  has_linear_key: true,
  is_git: true,
  path_exists: true,
  auto_scan_prompt: undefined,
};

const STATE: WizardState = {
  ...INITIAL_WIZARD_STATE,
  selection: { kind: 'stored', name: gitProject.name },
};

function renderStep(mode: WizardMode, state: WizardState = STATE): WizardAction[] {
  const actions: WizardAction[] = [];
  render(
    <ConfigStep
      mode={mode}
      state={state}
      selection={state.selection}
      project={gitProject}
      capabilities={undefined}
      customTypes={[]}
      submitting={false}
      error={null}
      dispatch={(action) => {
        actions.push(action);
      }}
      onSubmit={() => undefined}
      onBack={() => undefined}
    />,
  );
  return actions;
}

/** Labels and the Advanced summary, in document order. */
function expectInOrder(...nodes: Element[]): void {
  for (let i = 1; i < nodes.length; i += 1) {
    const before = nodes[i - 1];
    const after = nodes[i];
    if (before === undefined || after === undefined) throw new Error('missing node');
    expect(before.compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  }
}

const advancedSummary = (): Element => screen.getByText('Advanced', { selector: 'summary' });

afterEach(() => {
  cleanup();
});

describe('ConfigStep — Claude Code layout', () => {
  it('puts Focus view above Skip permissions and Advanced after the worktree section', () => {
    // The branch field only appears once a worktree is requested.
    renderStep('claude', { ...STATE, git: { worktree: true, branchName: '' } });

    expectInOrder(
      screen.getByLabelText('Focus view'),
      screen.getByLabelText('Skip permissions'),
      screen.getByLabelText('Create worktree'),
      screen.getByLabelText('Branch name (optional)'),
      advancedSummary(),
    );
  });

  it('opens with focus view on and Advanced collapsed, No memory its first item', () => {
    renderStep('claude');

    expect(screen.getByLabelText('Focus view')).toBeChecked();
    const details = advancedSummary().closest('details');
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute('open');
    const first = details?.querySelector('input');
    expect(first).toBe(screen.getByLabelText('No memory'));
  });

  it('offers no Plan mode control for a Claude Code launch', () => {
    renderStep('claude');

    expect(screen.queryByLabelText('Plan mode')).not.toBeInTheDocument();
  });

  it('writes the Focus view toggle to the Claude bucket', () => {
    const actions = renderStep('claude');

    fireEvent.click(screen.getByLabelText('Focus view'));

    expect(actions).toContainEqual({
      type: 'setClaudeFlag',
      bucket: 'claude',
      flag: 'focusMode',
      value: false,
    });
  });
});

describe('ConfigStep — Auto Scan layout', () => {
  it('orders Plan mode, Focus view, Skip permissions, Advanced, then Initial prompt', () => {
    renderStep('auto_scan');

    expectInOrder(
      screen.getByLabelText('Plan mode'),
      screen.getByLabelText('Focus view'),
      screen.getByLabelText('Skip permissions'),
      advancedSummary(),
      screen.getByLabelText('Initial prompt (optional)'),
    );
    expect(screen.getAllByText('Advanced', { selector: 'summary' })).toHaveLength(1);
  });

  it('reflects and writes the Auto Scan bucket’s own focus view', () => {
    const actions = renderStep('auto_scan', {
      ...STATE,
      autoScan: { ...STATE.autoScan, focusMode: false },
    });

    expect(screen.getByLabelText('Focus view')).not.toBeChecked();
    fireEvent.click(screen.getByLabelText('Focus view'));

    expect(actions).toContainEqual({
      type: 'setClaudeFlag',
      bucket: 'autoScan',
      flag: 'focusMode',
      value: true,
    });
  });
});

describe('ConfigStep — worktree base branch', () => {
  const WORKTREE: WizardState = { ...STATE, git: { worktree: true, branchName: '' } };
  const CAPABLE: AgentVersionInfo = {
    name: 'mobaide',
    server_version: '1.0.0',
    protocol_versions: [7],
    min_client_version: 1,
    capabilities: { git: { worktree_base: true } },
    latest_agent_version: undefined,
  };
  const BASES: WorktreeBases = {
    default: 'origin/main',
    last_used: 'origin/develop',
    refs: [
      { ref: 'origin/main', kind: 'remote', subject: null, committed_at: null },
      { ref: 'origin/develop', kind: 'remote', subject: null, committed_at: null },
      { ref: 'wip', kind: 'local', subject: null, committed_at: null },
    ],
  };

  function renderPicker(options: {
    state?: WizardState;
    capabilities?: AgentVersionInfo;
    view?: Partial<WorktreeBasesView>;
    mode?: WizardMode;
  }): { actions: WizardAction[]; retries: number[] } {
    const actions: WizardAction[] = [];
    const retries: number[] = [];
    const state = options.state ?? WORKTREE;
    render(
      <ConfigStep
        mode={options.mode ?? 'claude'}
        state={state}
        selection={state.selection}
        project={gitProject}
        capabilities={options.capabilities}
        customTypes={[]}
        worktreeBases={{
          status: 'ready',
          bases: BASES,
          retry: () => {
            retries.push(1);
          },
          ...options.view,
        }}
        submitting={false}
        error={null}
        dispatch={(action) => {
          actions.push(action);
        }}
        onSubmit={() => undefined}
        onBack={() => undefined}
      />,
    );
    return { actions, retries };
  }

  const options = (): HTMLElement[] =>
    within(screen.getByRole('listbox')).getAllByRole('option');
  const trigger = (): HTMLElement => screen.getByRole('button', { name: /^Base branch/ });

  it('is hidden without the worktree_base capability', () => {
    renderPicker({ capabilities: undefined });
    expect(screen.queryByText('Base branch')).not.toBeInTheDocument();

    cleanup();
    renderPicker({ capabilities: { ...CAPABLE, capabilities: { git: { branches: true } } } });
    expect(screen.queryByText('Base branch')).not.toBeInTheDocument();
  });

  it('is hidden while no worktree is requested, and for Auto Scan', () => {
    renderPicker({ capabilities: CAPABLE, state: STATE });
    expect(screen.queryByText('Base branch')).not.toBeInTheDocument();

    cleanup();
    renderPicker({ capabilities: CAPABLE, mode: 'auto_scan' });
    expect(screen.queryByText('Base branch')).not.toBeInTheDocument();
  });

  it('preselects last_used, then default, then Default branch', () => {
    renderPicker({ capabilities: CAPABLE });
    expect(trigger()).toHaveTextContent('origin/develop');

    cleanup();
    renderPicker({ capabilities: CAPABLE, view: { bases: { ...BASES, last_used: null } } });
    expect(trigger()).toHaveTextContent('origin/main');

    cleanup();
    renderPicker({
      capabilities: CAPABLE,
      view: { bases: { ...BASES, last_used: null, default: null } },
    });
    expect(trigger()).toHaveTextContent('Default branch');
  });

  it('shows a manual pick for this project over the preselection', () => {
    renderPicker({
      capabilities: CAPABLE,
      state: { ...WORKTREE, worktreeBasePick: { projectName: gitProject.name, ref: 'wip' } },
    });
    expect(trigger()).toHaveTextContent('wip');
  });

  it('is disabled while loading', () => {
    renderPicker({ capabilities: CAPABLE, view: { status: 'loading', bases: undefined } });
    expect(trigger()).toBeDisabled();
    expect(trigger()).toHaveTextContent('Loading branches…');
  });

  it('offers Retry when the list failed', () => {
    const { retries } = renderPicker({
      capabilities: CAPABLE,
      view: { status: 'error', bases: undefined },
    });

    expect(screen.getByText('Couldn’t load branches')).toBeInTheDocument();
    expect(trigger()).toHaveTextContent('Default branch');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(retries).toHaveLength(1);
  });

  it('filters and picks from the keyboard', () => {
    const { actions } = renderPicker({ capabilities: CAPABLE });

    fireEvent.click(trigger());
    const filter = screen.getByRole('combobox', { name: 'Filter branches' });
    expect(filter).toHaveFocus();
    // Opens on the current value: Default branch, then the refs in agent order.
    expect(options().map((option) => option.textContent)).toEqual([
      'Default branch',
      'origin/maindefault',
      '✓origin/develop',
      'wiplocal',
    ]);
    expect(within(screen.getByRole('listbox')).getByRole('option', { selected: true })).toHaveTextContent('origin/develop');

    fireEvent.keyDown(filter, { key: 'ArrowDown' });
    expect(within(screen.getByRole('listbox')).getByRole('option', { selected: true })).toHaveTextContent('wip');

    fireEvent.change(filter, { target: { value: 'MAIN' } });
    expect(options()).toHaveLength(1);
    fireEvent.keyDown(filter, { key: 'Enter' });

    expect(actions).toContainEqual({
      type: 'setWorktreeBase',
      pick: { projectName: gitProject.name, ref: 'origin/main' },
    });
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(trigger()).toHaveFocus();
  });

  it('picks Default branch as null and closes on Escape', () => {
    const { actions } = renderPicker({ capabilities: CAPABLE });

    fireEvent.click(trigger());
    fireEvent.click(within(screen.getByRole('listbox')).getByRole('option', { name: /Default branch/ }));
    expect(actions).toContainEqual({
      type: 'setWorktreeBase',
      pick: { projectName: gitProject.name, ref: null },
    });

    fireEvent.click(trigger());
    fireEvent.keyDown(screen.getByRole('combobox', { name: 'Filter branches' }), { key: 'Escape' });
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });
});
