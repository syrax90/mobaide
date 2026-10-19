import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { ProjectSummary } from '../../../api/projectRepository';
import { ConfigStep } from './ConfigStep';
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
