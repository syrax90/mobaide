import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { WorktreeBases } from '../../../api/gitRepository';
import type { WorkspaceSummary } from '../../../api/workspaceRepository';
import { agentVersionSchema, type AgentVersionInfo } from '../../../protocol/dto/agentVersion';
import { ConfigStep, type WorktreeBasesView } from './ConfigStep';
import {
  INITIAL_WIZARD_STATE,
  type WizardAction,
  type WizardMode,
  type WizardState,
  wizardReducer,
} from './wizardState';

const gitWorkspace: WorkspaceSummary = {
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
  selection: { kind: 'stored', name: gitWorkspace.name },
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
      workspace={gitWorkspace}
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

const terminalChoice = (): HTMLElement => screen.getByRole('radio', { name: /^Terminal/ });
const chatChoice = (): HTMLElement => screen.getByRole('radio', { name: /^Chat/ });

/** Terminal is held: chosen, and Chat cannot be picked. */
function expectTerminalHeld(): void {
  expect(terminalChoice()).toBeChecked();
  expect(chatChoice()).toBeDisabled();
}

afterEach(() => {
  cleanup();
});

/** Focus view exists only for a session that opens in the terminal. */
const TERMINAL_STATE: WizardState = { ...STATE, claude: { ...STATE.claude, terminalUi: true } };

describe('ConfigStep — Claude Code layout', () => {
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

describe('ConfigStep — Claude Code debug logging', () => {
  it('offers Debug logging in the chat view, with the log file as its hint', () => {
    const actions = renderStep('claude');

    expect(screen.getByText(/~\/\.claude\/debug\//)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Debug logging'));

    expect(actions).toContainEqual({
      type: 'setClaudeFlag',
      bucket: 'claude',
      flag: 'debug',
      value: true,
    });
  });

  it('keeps the terminal hint for a session that opens in the terminal', () => {
    renderStep('claude', TERMINAL_STATE);

    expect(screen.getByLabelText('Debug logging')).toBeInTheDocument();
    expect(screen.getByText(/into the terminal/)).toBeInTheDocument();
  });
});

describe('ConfigStep — Auto Scan layout', () => {
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

  it('holds Terminal where the PC has no Codex chat', () => {
    renderStep('codex', STATE, versionWithChat({ claude: { resume: true } }));

    expectTerminalHeld();
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
  it('shows Open sessions in with Chat first and chosen by default', () => {
    const actions = renderStep('antigravity');

    const radios = screen.getAllByRole('radio');
    expect(radios.indexOf(chatChoice())).toBeLessThan(radios.indexOf(terminalChoice()));
    expect(chatChoice()).toBeChecked();
    expect(chatChoice()).toBeEnabled();
    expect(screen.queryByText('Advanced', { selector: 'summary' })).not.toBeInTheDocument();

    fireEvent.click(terminalChoice());

    expect(actions).toContainEqual({ type: 'setAntigravityTerminalUi', value: true });
  });

  it('holds Terminal where the PC has no Antigravity chat, and says how to get it', () => {
    renderStep('antigravity', STATE, versionWithChat({ claude: { resume: true } }));

    expectTerminalHeld();
    expect(
      screen.getByText(
        'The chat view needs a newer Antigravity CLI on your PC: run agy update there.',
      ),
    ).toBeInTheDocument();
  });

});

describe('ConfigStep — OpenCode', () => {
  it('writes auto-approve, off by default, to the OpenCode bucket', () => {
    const actions = renderStep('opencode');

    const toggle = screen.getByLabelText('Auto-approve permissions (dangerous)');
    expect(toggle).not.toBeChecked();

    fireEvent.click(toggle);

    expect(actions).toEqual([{ type: 'setOpenCodeAutoApprove', value: true }]);
  });

  it('writes the Terminal choice to the OpenCode bucket', () => {
    const actions = renderStep('opencode');

    expect(chatChoice()).toBeChecked();
    fireEvent.click(terminalChoice());

    expect(actions).toEqual([{ type: 'setOpenCodeTerminalUi', value: true }]);
  });

  it('holds Terminal where the PC has no OpenCode chat', () => {
    renderStep('opencode', STATE, versionWithChat({ claude: { resume: true } }));

    expectTerminalHeld();
  });

  it('offers to continue an OpenCode session where the PC can resume one', () => {
    renderStep(
      'opencode',
      STATE,
      versionWithChat({ opencode: { resume: true, live: false } }),
      () => undefined,
    );

    expect(screen.getByRole('button', { name: 'Continue a session…' })).toBeEnabled();
  });
});

describe('ConfigStep — Pi', () => {
  it('writes the Terminal choice to the Pi bucket only', () => {
    const actions = renderStep('pi');

    fireEvent.click(terminalChoice());

    const next = actions.reduce(wizardReducer, STATE);
    expect(next.pi).toEqual({ terminalUi: true, codeMode: false });
    expect({ ...next, pi: STATE.pi }).toEqual(STATE);
  });

  it('holds Terminal where the PC has no Pi chat', () => {
    renderStep('pi', STATE, versionWithChat({ copilot: { resume: true } }));

    expectTerminalHeld();
  });

  it('offers to continue a Pi session only where the PC can resume one', () => {
    const resumes = versionWithChat({ pi: { resume: true, live: false } });

    renderStep('pi', STATE, resumes, () => undefined);
    expect(screen.getByRole('button', { name: 'Continue a session…' })).toBeEnabled();
    cleanup();

    renderStep('pi', { ...STATE, pi: { terminalUi: true, codeMode: false } }, resumes, () => undefined);
    expect(screen.queryByText('Continue a session…')).not.toBeInTheDocument();
    cleanup();

    renderStep('pi', STATE, versionWithChat({ pi: { resume: false, live: false } }), () => undefined);
    expect(screen.queryByText('Continue a session…')).not.toBeInTheDocument();
  });
});

describe('ConfigStep — Code Mode', () => {
  const CLAUDE_CHAT = versionWithChat({ claude: { resume: true, live: true } });

  it('writes Claude Code Mode to the Claude bucket from its Advanced section', () => {
    const actions = renderStep(
      'claude',
      { ...STATE, claude: { ...STATE.claude, terminalUi: true } },
      CLAUDE_CHAT,
    );

    const toggle = screen.getByLabelText('Code Mode');
    expect(screen.getByText('Advanced').closest('details')?.contains(toggle)).toBe(true);
    expect(toggle).not.toBeChecked();

    fireEvent.click(toggle);

    expect(actions).toContainEqual({
      type: 'setClaudeFlag',
      bucket: 'claude',
      flag: 'codeMode',
      value: true,
    });
  });

  it('keeps the Chat choice with Claude Code Mode on, and hides Code Mode in the chat view', () => {
    renderStep(
      'claude',
      { ...STATE, claude: { ...STATE.claude, codeMode: true } },
      CLAUDE_CHAT,
      () => undefined,
    );

    expect(chatChoice()).toBeChecked();
    expect(chatChoice()).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Continue a session…' })).toBeEnabled();
    expect(screen.queryByLabelText('Code Mode')).not.toBeInTheDocument();
  });

  it('offers no Code Mode for an Auto Scan', () => {
    renderStep('auto_scan', STATE, CLAUDE_CHAT);

    expect(screen.queryByLabelText('Code Mode')).not.toBeInTheDocument();
  });

  it('shows OpenCode Code Mode on by default and writes it to the OpenCode bucket', () => {
    const actions = renderStep('opencode');

    const toggle = screen.getByLabelText('Code Mode');
    expect(toggle).toBeChecked();

    fireEvent.click(toggle);

    const next = actions.reduce(wizardReducer, STATE);
    expect(next.opencode.codeMode).toBe(false);
    expect({ ...next, opencode: STATE.opencode }).toEqual(STATE);
  });

  it('shows Pi Code Mode off by default and writes it to the Pi bucket', () => {
    const actions = renderStep('pi');

    const toggle = screen.getByLabelText('Code Mode');
    expect(toggle).not.toBeChecked();

    fireEvent.click(toggle);

    const next = actions.reduce(wizardReducer, STATE);
    expect(next.pi.codeMode).toBe(true);
    expect({ ...next, pi: STATE.pi }).toEqual(STATE);
  });
});

describe('ConfigStep — GitHub Copilot', () => {
  it('writes the allow-all and Terminal choices to the Copilot bucket only', () => {
    const actions = renderStep('copilot');

    const allowAll = screen.getByLabelText(/^Allow all tools, paths and URLs/);
    expect(allowAll).not.toBeChecked();
    fireEvent.click(allowAll);
    fireEvent.click(terminalChoice());

    const next = actions.reduce(wizardReducer, STATE);
    expect(next.copilot).toEqual({ allowAll: true, terminalUi: true });
    expect({ ...next, copilot: STATE.copilot }).toEqual(STATE);
  });

  it('holds Terminal where the PC has no Copilot chat', () => {
    renderStep('copilot', STATE, versionWithChat({ claude: { resume: true } }));

    expectTerminalHeld();
  });

  it('offers to continue a Copilot session only where the PC can resume one', () => {
    renderStep(
      'copilot',
      STATE,
      versionWithChat({ copilot: { resume: true, live: false } }),
      () => undefined,
    );
    expect(screen.getByRole('button', { name: 'Continue a session…' })).toBeEnabled();
    cleanup();

    renderStep(
      'copilot',
      { ...STATE, copilot: { ...STATE.copilot, terminalUi: true } },
      versionWithChat({ copilot: { resume: true, live: false } }),
      () => undefined,
    );
    expect(screen.queryByText('Continue a session…')).not.toBeInTheDocument();
  });
});

describe('ConfigStep — Cursor', () => {
  it('writes the run-everything and Terminal choices to the Cursor bucket only', () => {
    const actions = renderStep('cursor');

    const force = screen.getByLabelText(/^Run everything without asking/);
    expect(force).not.toBeChecked();
    fireEvent.click(force);
    fireEvent.click(terminalChoice());

    const next = actions.reduce(wizardReducer, STATE);
    expect(next.cursor).toEqual({ force: true, terminalUi: true });
    expect({ ...next, cursor: STATE.cursor }).toEqual(STATE);
  });

  it('offers no model control: Cursor picks its model inside the session', () => {
    renderStep('cursor');

    expect(screen.queryByLabelText('Model')).not.toBeInTheDocument();
  });

  it('holds Terminal where the PC has no Cursor chat', () => {
    renderStep('cursor', STATE, versionWithChat({ claude: { resume: true } }));

    expectTerminalHeld();
  });

  it('offers to continue a Cursor session only where the PC can resume one', () => {
    renderStep(
      'cursor',
      STATE,
      versionWithChat({ cursor: { resume: true, live: false } }),
      () => undefined,
    );
    expect(screen.getByRole('button', { name: 'Continue a session…' })).toBeEnabled();
    cleanup();

    renderStep(
      'cursor',
      { ...STATE, cursor: { ...STATE.cursor, terminalUi: true } },
      versionWithChat({ cursor: { resume: true, live: false } }),
      () => undefined,
    );
    expect(screen.queryByText('Continue a session…')).not.toBeInTheDocument();
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
        workspace={gitWorkspace}
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

  it('shows a manual pick for this workspace over the preselection', () => {
    renderPicker({
      capabilities: CAPABLE,
      state: { ...WORKTREE, worktreeBasePick: { workspaceName: gitWorkspace.name, ref: 'wip' } },
    });
    expect(trigger()).toHaveTextContent('wip');
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
      pick: { workspaceName: gitWorkspace.name, ref: 'origin/main' },
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
      pick: { workspaceName: gitWorkspace.name, ref: null },
    });

    fireEvent.click(trigger());
    fireEvent.keyDown(screen.getByRole('combobox', { name: 'Filter branches' }), { key: 'Escape' });
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });
});
