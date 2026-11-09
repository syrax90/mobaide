import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { ProjectSummary } from '../../../api/projectRepository';
import { agentVersionSchema, type AgentVersionInfo } from '../../../protocol/dto/agentVersion';
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

function renderStep(
  mode: WizardMode,
  state: WizardState = STATE,
  capabilities?: AgentVersionInfo,
  onContinueSession?: () => void,
): WizardAction[] {
  const actions: WizardAction[] = [];
  render(
    <ConfigStep
      mode={mode}
      state={state}
      selection={state.selection}
      project={gitProject}
      capabilities={capabilities}
      onContinueSession={onContinueSession}
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

/** Focus view and Debug logging exist only for a session that opens in the terminal. */
const TERMINAL_STATE: WizardState = { ...STATE, claude: { ...STATE.claude, terminalUi: true } };

describe('ConfigStep — Claude Code layout', () => {
  it('puts Focus view above Skip permissions and Advanced after the worktree section', () => {
    // The branch field only appears once a worktree is requested.
    renderStep('claude', { ...TERMINAL_STATE, git: { worktree: true, branchName: '' } });

    expectInOrder(
      screen.getByLabelText('Focus view'),
      screen.getByLabelText('Skip permissions'),
      screen.getByLabelText('Create worktree'),
      screen.getByLabelText('Branch name (optional)'),
      advancedSummary(),
    );
  });

  it('opens with focus view on and Advanced collapsed, Terminal UI its first item', () => {
    renderStep('claude', TERMINAL_STATE);

    expect(screen.getByLabelText('Focus view')).toBeChecked();
    const details = advancedSummary().closest('details');
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute('open');
    const first = details?.querySelector('input');
    expect(first).toBe(screen.getByLabelText('Terminal UI'));
  });

  it('offers no Plan mode control for a Claude Code launch', () => {
    renderStep('claude');

    expect(screen.queryByLabelText('Plan mode')).not.toBeInTheDocument();
  });

  it('writes the Focus view toggle to the Claude bucket', () => {
    const actions = renderStep('claude', TERMINAL_STATE);

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

function versionWithChat(chat: Record<string, unknown>): AgentVersionInfo {
  return agentVersionSchema.parse({
    name: 'mobaide_agent',
    server_version: '0.3.23',
    protocol_versions: [6, 7],
    min_client_version: 6,
    capabilities: { chat },
  });
}

describe('ConfigStep — Codex chat view', () => {
  const CODEX_RESUMES = versionWithChat({ codex: { resume: true, live: true } });

  it('puts Terminal UI first in the Codex Advanced, off by default', () => {
    const actions = renderStep('codex');

    const details = advancedSummary().closest('details');
    expect(details).not.toHaveAttribute('open');
    const toggle = screen.getByLabelText('Terminal UI');
    expect(details?.querySelector('input')).toBe(toggle);
    expect(toggle).not.toBeChecked();
    expect(toggle).toBeEnabled();

    fireEvent.click(toggle);

    expect(actions).toContainEqual({ type: 'setCodexTerminalUi', value: true });
  });

  it('holds Terminal UI on where the PC has no Codex chat', () => {
    renderStep('codex', STATE, versionWithChat({ claude: { resume: true } }));

    const toggle = screen.getByLabelText('Terminal UI');
    expect(toggle).toBeChecked();
    expect(toggle).toBeDisabled();
    expect(screen.getByText('The chat view needs Node.js on your PC.')).toBeInTheDocument();
  });

  it('offers to continue a Codex session where the PC can resume one', () => {
    renderStep('codex', STATE, CODEX_RESUMES, () => undefined);

    expect(
      screen.getByText('Open a Codex session started on your PC, for example in a terminal.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Continue a session…' })).toBeEnabled();
  });

  it('offers no Continue entry for a Codex launch in the terminal', () => {
    renderStep(
      'codex',
      { ...STATE, codex: { ...STATE.codex, terminalUi: true } },
      CODEX_RESUMES,
      () => undefined,
    );

    expect(screen.queryByText('Continue a session…')).not.toBeInTheDocument();
  });
});

describe('ConfigStep — Antigravity chat view', () => {
  const AGY_RESUMES = versionWithChat({ antigravity: { resume: true, live: false } });

  it('keeps Terminal UI in a collapsed Advanced, off by default', () => {
    const actions = renderStep('antigravity');

    const details = advancedSummary().closest('details');
    expect(details).not.toHaveAttribute('open');
    const toggle = screen.getByLabelText('Terminal UI');
    expect(details?.contains(toggle)).toBe(true);
    expect(toggle).not.toBeChecked();
    expect(toggle).toBeEnabled();

    fireEvent.click(toggle);

    expect(actions).toContainEqual({ type: 'setAntigravityTerminalUi', value: true });
  });

  it('holds Terminal UI on where the PC has no Antigravity chat, and says how to get it', () => {
    renderStep('antigravity', STATE, versionWithChat({ claude: { resume: true } }));

    const toggle = screen.getByLabelText('Terminal UI');
    expect(toggle).toBeChecked();
    expect(toggle).toBeDisabled();
    expect(
      screen.getByText(
        'The chat view needs a newer Antigravity CLI on your PC: run agy update there.',
      ),
    ).toBeInTheDocument();
  });

  it('offers to continue an Antigravity conversation where the PC can resume one', () => {
    renderStep('antigravity', STATE, AGY_RESUMES, () => undefined);

    expect(
      screen.getByText(
        'Open an Antigravity conversation started on your PC, for example with agy in a terminal.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Continue a session…' })).toBeEnabled();
  });

  it('offers no Continue entry for an Antigravity launch in the terminal', () => {
    renderStep(
      'antigravity',
      { ...STATE, antigravity: { ...STATE.antigravity, terminalUi: true } },
      AGY_RESUMES,
      () => undefined,
    );

    expect(screen.queryByText('Continue a session…')).not.toBeInTheDocument();
  });
});
