import { describe, expect, it } from 'vitest';
import type { components } from '../../../api/generated/agent';
import type { WorkspaceSummary } from '../../../api/workspaceRepository';
import type { ChatSupport } from '../../../protocol/dto/agentVersion';
import type { ChatStoredSession } from '../../../protocol/dto/chatSessions';
import { createSessionRequestSchema } from '../../../protocol/dto/sessionCreate';
import {
  buildConnectRequest,
  buildCreateRequest,
  buildResumeRequest,
  buildStartScanRequest,
  type CreateSessionRequest,
} from './buildCreateRequest';
import {
  INITIAL_WIZARD_STATE,
  type AutoScanUi,
  type WizardMode,
  type WizardState,
} from './wizardState';

// `satisfies`, not a `WizardMode` annotation: the literal type is what keeps the
// mode tables below exhaustive rather than open-ended template-literal keys.
const CUSTOM = 'custom_0123456789ab' satisfies WizardMode;

const gitWorkspace: WorkspaceSummary = {
  name: 'mobaide',
  path: '/srv/mobaide',
  order: 0,
  has_linear_key: true,
  is_git: true,
  path_exists: true,
  auto_scan_prompt: undefined,
};

/**
 * Every bucket filled with values distinctive enough that a leak is visible —
 * the state of a user who dialled in all six branches before launching one.
 */
const POPULATED: WizardState = {
  ...INITIAL_WIZARD_STATE,
  selection: { kind: 'stored', name: gitWorkspace.name },
  claude: {
    model: 'sonnet',
    effort: 'medium',
    fastMode: false,
    ultracode: true,
    debug: true,
    focusMode: true,
    appendSystemPrompt: 'claude system',
    skipPermissions: true,
    noMemory: true,
    planMode: true,
    initialPrompt: 'claude prompt',
    terminalUi: false,
    codeMode: false,
  },
  autoScan: {
    model: 'haiku',
    effort: 'low',
    fastMode: false,
    ultracode: false,
    debug: false,
    focusMode: true,
    appendSystemPrompt: 'scan system',
    skipPermissions: true,
    noMemory: false,
    planMode: false,
    initialPrompt: 'scan the backlog',
    terminalUi: false,
    codeMode: false,
  },
  codex: {
    model: 'gpt-5.4',
    sandbox: 'danger-full-access',
    approval: 'never',
    fullAuto: true,
    reasoningEffort: null,
    terminalUi: false,
  },
  antigravity: {
    model: 'Gemini 3.1 Pro (High)',
    skipPermissions: true,
    sandbox: false,
    addDirsDraft: '/one, /two\n/three',
    terminalUi: false,
  },
  opencode: { autoApprove: true, terminalUi: false, codeMode: true },
  copilot: { allowAll: true, terminalUi: false },
  cursor: { force: true, terminalUi: false },
  junie: { planMode: true, brave: true, terminalUi: false },
  pi: { terminalUi: false, codeMode: false },
  git: { worktree: true, branchName: 'feature/x' },
  autoScanPromptSeededFor: gitWorkspace.name,
};

function build(mode: WizardMode, state: WizardState = POPULATED): CreateSessionRequest {
  const request = buildCreateRequest({ state, mode, workspace: gitWorkspace });
  if (request === null) throw new Error(`expected a request for mode ${mode}`);
  return request;
}

const REQUEST_MODES = [
  'claude',
  'codex',
  'antigravity',
  'opencode',
  'copilot',
  'cursor',
  'junie',
  'pi',
  'shell',
  CUSTOM,
] as const;
const OPTION_KEYS = [
  'claude_options',
  'codex_options',
  'antigravity_options',
  'opencode_options',
  'copilot_options',
  'cursor_options',
  'junie_options',
  'pi_options',
] as const;

const OWN_OPTIONS: Record<(typeof REQUEST_MODES)[number], (typeof OPTION_KEYS)[number] | null> =
  {
    claude: 'claude_options',
    codex: 'codex_options',
    antigravity: 'antigravity_options',
    opencode: 'opencode_options',
    copilot: 'copilot_options',
    cursor: 'cursor_options',
    junie: 'junie_options',
    pi: 'pi_options',
    shell: null,
    [CUSTOM]: null,
  };

describe('buildCreateRequest — no cross-branch leakage', () => {
  it.each(REQUEST_MODES)('a fully filled wizard launching %s sends only its own options block', (mode) => {
    const request = build(mode);
    const expected = OWN_OPTIONS[mode];

    expect(OPTION_KEYS.filter((key) => key in request)).toEqual(
      expected === null ? [] : [expected],
    );
  });

  it.each(REQUEST_MODES)('%s survives the strict outbound schema', (mode) => {
    // `createSessionRequestSchema` is `z.strictObject`, so any key that leaked
    // in from another branch throws here rather than reaching the agent.
    expect(() => createSessionRequestSchema.parse(build(mode))).not.toThrow();
  });

  it.each(REQUEST_MODES)('%s never names command or env', (mode) => {
    const request = build(mode);

    expect('command' in request).toBe(false);
    expect('env' in request).toBe(false);
  });

  it('keeps the Auto Scan bucket out of a Claude launch and vice versa', () => {
    const claude = build('claude').claude_options;
    const scan = buildStartScanRequest({
      state: POPULATED,
      mode: 'auto_scan',
      workspace: gitWorkspace,
    })?.claude_options;

    expect(claude).toMatchObject({
      model: 'sonnet',
      effort: 'medium',
      plan_mode: true,
      debug: true,
      append_system_prompt: 'claude system',
    });
    expect(scan).toMatchObject({
      model: 'haiku',
      effort: 'low',
      plan_mode: false,
      debug: false,
      append_system_prompt: 'scan system',
      initial_prompt: 'scan the backlog',
    });
  });
});

type RequiredBooleans<T> = {
  [K in keyof T]-?: undefined extends T[K] ? never : boolean extends T[K] ? K : never;
}[keyof T];

/**
 * Compile-time drift gates: a new required boolean on any generated option model
 * fails to type-check here until it is added, which is what keeps the runtime
 * assertions below exhaustive rather than merely sound.
 */
const CLAUDE_BOOLEANS: Record<
  RequiredBooleans<components['schemas']['ClaudeOptionsModel']>,
  true
> = {
  plan_mode: true,
  no_memory: true,
  skip_permissions: true,
  fast_mode: true,
  ultracode: true,
  debug: true,
  code_mode: true,
  worktree: true,
};

const CODEX_BOOLEANS: Record<
  RequiredBooleans<components['schemas']['CodexOptionsModel']>,
  true
> = { full_auto: true, worktree: true };

const ANTIGRAVITY_BOOLEANS: Record<
  RequiredBooleans<components['schemas']['AntigravityOptionsModel']>,
  true
> = { skip_permissions: true, sandbox: true, worktree: true };

const OPENCODE_BOOLEANS: Record<
  RequiredBooleans<components['schemas']['OpenCodeOptionsModel']>,
  true
> = { auto_approve: true, connect: true, code_mode: true, worktree: true };

const COPILOT_BOOLEANS: Record<
  RequiredBooleans<components['schemas']['CopilotOptionsModel']>,
  true
> = { allow_all: true, worktree: true };

const CURSOR_BOOLEANS: Record<
  RequiredBooleans<components['schemas']['CursorOptionsModel']>,
  true
> = { force: true, worktree: true };

const JUNIE_BOOLEANS: Record<
  RequiredBooleans<components['schemas']['JunieOptionsModel']>,
  true
> = { plan_mode: true, brave: true, worktree: true };

const PI_BOOLEANS: Record<RequiredBooleans<components['schemas']['PiOptionsModel']>, true> = {
  code_mode: true,
  worktree: true,
};

const REQUEST_BOOLEANS: Record<RequiredBooleans<CreateSessionRequest>, true> = {
  worktree: true,
};

function expectExplicitBooleans(block: unknown, required: Record<string, true>): void {
  const fields = block as Record<string, unknown>;
  for (const key of Object.keys(required)) {
    expect(typeof fields[key], `${key} must be sent explicitly`).toBe('boolean');
  }
}

describe('buildCreateRequest — every boolean explicit', () => {
  it.each(REQUEST_MODES)('%s declares the top-level booleans', (mode) => {
    expectExplicitBooleans(build(mode), REQUEST_BOOLEANS);
  });

  it('declares every Claude boolean', () => {
    expectExplicitBooleans(build('claude').claude_options, CLAUDE_BOOLEANS);
  });

  it('declares every Codex boolean', () => {
    expectExplicitBooleans(build('codex').codex_options, CODEX_BOOLEANS);
  });

  it('declares every Antigravity boolean', () => {
    expectExplicitBooleans(build('antigravity').antigravity_options, ANTIGRAVITY_BOOLEANS);
  });

  it('declares every OpenCode boolean', () => {
    expectExplicitBooleans(build('opencode').opencode_options, OPENCODE_BOOLEANS);
  });

  it('declares every Copilot boolean', () => {
    expectExplicitBooleans(build('copilot').copilot_options, COPILOT_BOOLEANS);
  });

  it('declares every Pi boolean', () => {
    expectExplicitBooleans(build('pi').pi_options, PI_BOOLEANS);
  });

  it('sends allow_all: false for a default Copilot launch', () => {
    const request = build('copilot', {
      ...INITIAL_WIZARD_STATE,
      selection: { kind: 'stored', name: gitWorkspace.name },
    });

    expect(request.copilot_options?.allow_all).toBe(false);
  });

  it('declares every Cursor boolean', () => {
    expectExplicitBooleans(build('cursor').cursor_options, CURSOR_BOOLEANS);
  });

  it('sends force: false for a default Cursor launch', () => {
    const request = build('cursor', {
      ...INITIAL_WIZARD_STATE,
      selection: { kind: 'stored', name: gitWorkspace.name },
    });

    expect(request.cursor_options?.force).toBe(false);
  });

  it('declares every Junie boolean', () => {
    expectExplicitBooleans(build('junie').junie_options, JUNIE_BOOLEANS);
  });

  it('sends brave: false and plan_mode: false for a default Junie launch', () => {
    const request = build('junie', {
      ...INITIAL_WIZARD_STATE,
      selection: { kind: 'stored', name: gitWorkspace.name },
    });

    expect(request.junie_options?.brave).toBe(false);
    expect(request.junie_options?.plan_mode).toBe(false);
  });

  it('declares every Claude boolean on the Auto Scan request too', () => {
    const scan = buildStartScanRequest({
      state: POPULATED,
      mode: 'auto_scan',
      workspace: gitWorkspace,
    });

    expectExplicitBooleans(scan?.claude_options, CLAUDE_BOOLEANS);
  });

  it('sends skip_permissions: false for a default Claude launch', () => {
    // The wire-absence default is `true`, so an omitted field would silently
    // launch with --dangerously-skip-permissions.
    const request = build('claude', {
      ...INITIAL_WIZARD_STATE,
      selection: { kind: 'stored', name: gitWorkspace.name },
    });

    expect(request.claude_options?.skip_permissions).toBe(false);
  });
});

describe('buildCreateRequest — placement', () => {
  const nonGit: WorkspaceSummary = { ...gitWorkspace, is_git: false };

  it('keeps a managed CLI’s worktree intent inside its own options block', () => {
    const request = build('claude');

    expect(request.worktree).toBe(false);
    expect(request.claude_options).toMatchObject({
      worktree: true,
      branch_name: 'feature/x',
    });
    expect('branch_name' in request).toBe(false);
  });

  it('puts a shell launch’s worktree intent at the top level and omits cli', () => {
    const request = build('shell');

    expect(request.cli).toBeUndefined();
    expect(request.worktree).toBe(true);
    expect(request.branch_name).toBe('feature/x');
  });

  it('passes a catalog effort (ultra) through untouched', () => {
    // The wire schema is free-form on purpose: the effort vocabulary is
    // agent-enumerated per model, so nothing between the picker and the wire
    // may narrow it back to the static fallback list.
    const request = build('codex', {
      ...POPULATED,
      codex: { ...POPULATED.codex, reasoningEffort: 'ultra' },
    });

    expect(request.codex_options?.reasoning_effort).toBe('ultra');
  });

  it('sends a branch name even when no worktree is requested', () => {
    // Both mobile clients gate `branch_name` on git-eligibility alone.
    const request = build('codex', { ...POPULATED, git: { worktree: false, branchName: 'wip' } });

    expect(request.codex_options).toMatchObject({ worktree: false, branch_name: 'wip' });
  });

  it('drops worktree and branch for a workspace that is not a repo', () => {
    const request = buildCreateRequest({ state: POPULATED, mode: 'claude', workspace: nonGit });

    expect(request?.claude_options).toMatchObject({ worktree: false });
    expect(request?.claude_options?.branch_name).toBeUndefined();
  });

  it('sends a one-off path as cwd with no workspace and no git intent', () => {
    const request = build('claude', {
      ...POPULATED,
      selection: { kind: 'customPath', path: '/tmp/scratch' },
    });

    expect(request.cwd).toBe('/tmp/scratch');
    expect(request.project_name).toBeUndefined();
    expect(request.claude_options).toMatchObject({ worktree: false });
  });

  it('resolves the effective model and effort rather than the raw bucket', () => {
    const fast = build('claude', {
      ...POPULATED,
      claude: { ...POPULATED.claude, model: 'sonnet', fastMode: true },
    });
    const clamped = build('claude', {
      ...POPULATED,
      claude: { ...POPULATED.claude, model: 'haiku', effort: 'max' },
    });

    expect(fast.claude_options?.model).toBe('opus');
    expect(clamped.claude_options?.effort).toBe('high');
  });

  it('returns null when there is nowhere to launch, and for auto_scan', () => {
    expect(buildCreateRequest({ state: INITIAL_WIZARD_STATE, mode: 'shell', workspace: null })).toBeNull();
    expect(buildCreateRequest({ state: POPULATED, mode: 'auto_scan', workspace: gitWorkspace })).toBeNull();
  });
});

describe('buildStartScanRequest', () => {
  const scanContext = { state: POPULATED, mode: 'auto_scan' as WizardMode, workspace: gitWorkspace };

  it('hardcodes a worktree per issue and names no branch', () => {
    const request = buildStartScanRequest(scanContext);

    expect(request?.project_name).toBe('mobaide');
    expect(request?.claude_options).toMatchObject({ worktree: true });
    expect(request?.claude_options?.branch_name).toBeUndefined();
  });

  it('omits a blank prompt but leaves a real one untrimmed', () => {
    const blank = buildStartScanRequest({
      ...scanContext,
      state: { ...POPULATED, autoScan: { ...POPULATED.autoScan, initialPrompt: '  \n ' } },
    });
    const kept = buildStartScanRequest({
      ...scanContext,
      state: { ...POPULATED, autoScan: { ...POPULATED.autoScan, initialPrompt: 'scan\n\n' } },
    });

    expect(blank?.claude_options?.initial_prompt).toBeUndefined();
    // A trailing newline in a prompt is deliberate, so only blanks are dropped.
    expect(kept?.claude_options?.initial_prompt).toBe('scan\n\n');
  });

  it('refuses any mode but auto_scan, and a one-off path', () => {
    expect(buildStartScanRequest({ ...scanContext, mode: 'claude' })).toBeNull();
    expect(
      buildStartScanRequest({
        ...scanContext,
        state: { ...POPULATED, selection: { kind: 'customPath', path: '/tmp/scratch' } },
      }),
    ).toBeNull();
  });
});

describe('buildStartScanRequest — agent with capabilities.autoscan', () => {
  const autoScan = {
    sources: ['linear', 'github'],
    clis: ['claude', 'codex', 'antigravity', 'opencode', 'copilot', 'cursor', 'junie', 'pi'],
    githubSetup: true,
  };
  const linearFilter = {
    state_names: ['Todo'],
    label_names: ['bot'],
    active_state: 'Doing',
    done_state: 'Shipped',
    removal_label: 'bot',
  };
  const withSetup = (
    setup: Partial<WizardState['autoScanSetup']>,
    prompt = 'scan the backlog',
    autoScanChat?: ChatSupport,
  ) =>
    buildStartScanRequest({
      state: {
        ...POPULATED,
        autoScan: { ...POPULATED.autoScan, initialPrompt: prompt, skipPermissions: false },
        autoScanSetup: {
          ...POPULATED.autoScanSetup,
          source: 'github',
          cli: 'codex',
          linearFilter,
          githubFilter: {
            labels: [' ai-ready '],
            assignee: '  ',
            activeLabel: ' doing ',
            issueTypes: [],
          },
          ...setup,
        },
      },
      mode: 'auto_scan',
      workspace: gitWorkspace,
      autoScan,
      autoScanChat,
    });

  it('names every choice and sends only the chosen source’s filter', () => {
    expect(withSetup({}, '')).toEqual({
      project_name: 'mobaide',
      source: 'github',
      cli: 'codex',
      github_filter: {
        labels: ['ai-ready'],
        assignee: null,
        active_label: 'doing',
        issue_types: [],
      },
      // "" is sent: it clears the prompt the workspace saved.
      initial_prompt: '',
      codex_options: {
        model: null,
        sandbox: 'workspace-write',
        approval: 'never',
        reasoning_effort: 'high',
        full_auto: true,
      },
      ui: 'terminal',
      interval_minutes: 5,
    });
    expect(withSetup({ source: 'linear' })).toMatchObject({ linear_filter: linearFilter });
    expect(withSetup({ source: 'linear' })).not.toHaveProperty('github_filter');
  });

  it('gives Claude Code its own options and sends no other agent’s', () => {
    const request = withSetup({ cli: 'claude' });

    expect(request?.claude_options).toMatchObject({ worktree: true, skip_permissions: false });
    expect(request).not.toHaveProperty('codex_options');
    expect(withSetup({ cli: 'codex' })).not.toHaveProperty('claude_options');
  });

  it('builds nothing until a source and a known agent are chosen', () => {
    expect(withSetup({ source: null })).toBeNull();
    expect(withSetup({ cli: null })).toBeNull();
    expect(withSetup({ cli: 'aider' })).toBeNull();
  });

  // The PC refuses a chat scan for an agent without a chat view, so the chat
  // is sent only once the probe has shown one.
  it.each<[string, AutoScanUi, ChatSupport | undefined, AutoScanUi]>([
    ['chat chosen and available', 'chat', { kind: 'available', resume: true, live: true }, 'chat'],
    ['chat probe still out', 'chat', undefined, 'terminal'],
    ['no chat for the agent on this PC', 'chat', { kind: 'unavailable' }, 'terminal'],
    ['agent predates chat', 'chat', { kind: 'outdated' }, 'terminal'],
    ['terminal chosen', 'terminal', { kind: 'available', resume: true, live: true }, 'terminal'],
  ])('sends the session view: %s', (_name, ui, chat, sent) => {
    expect(withSetup({ ui }, 'scan', chat)?.ui).toBe(sent);
  });

  it('sends each agent’s Code Mode, and Claude Code Mode holds the terminal', () => {
    const scan = (cli: string, state: Partial<WizardState>) =>
      buildStartScanRequest({
        state: {
          ...POPULATED,
          ...state,
          autoScanSetup: { ...POPULATED.autoScanSetup, source: 'github', cli, ui: 'chat' },
        },
        mode: 'auto_scan',
        workspace: gitWorkspace,
        autoScan,
        autoScanChat: { kind: 'available', resume: true, live: true },
      });
    const agents = POPULATED.autoScanAgents;

    const claude = scan('claude', { autoScan: { ...POPULATED.autoScan, codeMode: true } });
    expect([claude?.claude_options?.code_mode, claude?.ui]).toEqual([true, 'terminal']);
    expect(scan('claude', {})?.ui).toBe('chat');
    expect(scan('opencode', {})?.opencode_options?.code_mode).toBe(true);
    expect(
      scan('opencode', { autoScanAgents: { ...agents, opencode: { ...agents.opencode, codeMode: false } } })
        ?.opencode_options?.code_mode,
    ).toBe(false);
    expect(scan('pi', {})?.pi_options).toEqual({ code_mode: false });
    expect(
      scan('pi', { autoScanAgents: { ...agents, pi: { ...agents.pi, codeMode: true } } })?.pi_options,
    ).toEqual({ code_mode: true });
  });

  it('sends the chosen time between scans', () => {
    expect(withSetup({ intervalMinutes: 240 })?.interval_minutes).toBe(240);
    expect(withSetup({ cli: 'claude', intervalMinutes: 1440 })?.interval_minutes).toBe(1440);
  });

  it('keeps the legacy Linear + Claude body for an agent without the capability', () => {
    const request = buildStartScanRequest({
      state: { ...POPULATED, autoScanSetup: { ...POPULATED.autoScanSetup, source: 'github', cli: 'codex' } },
      mode: 'auto_scan',
      workspace: gitWorkspace,
    });

    expect(request).not.toHaveProperty('source');
    expect(request).not.toHaveProperty('cli');
    expect(request?.claude_options?.initial_prompt).toBe('scan the backlog');
  });
});

describe('focus_mode', () => {
  it('defaults to focus view on for both a Claude launch and a scan', () => {
    const fresh: WizardState = {
      ...INITIAL_WIZARD_STATE,
      selection: { kind: 'stored', name: gitWorkspace.name },
    };

    expect(build('claude', fresh).claude_options?.focus_mode).toBe(true);
    expect(
      buildStartScanRequest({ state: fresh, mode: 'auto_scan', workspace: gitWorkspace })
        ?.claude_options?.focus_mode,
    ).toBe(true);
  });

  it('sends an explicit false from each bucket independently', () => {
    const state: WizardState = {
      ...POPULATED,
      claude: { ...POPULATED.claude, focusMode: false },
      autoScan: { ...POPULATED.autoScan, focusMode: true },
    };
    const scan = buildStartScanRequest({ state, mode: 'auto_scan', workspace: gitWorkspace });

    expect(build('claude', state).claude_options?.focus_mode).toBe(false);
    expect(scan?.claude_options?.focus_mode).toBe(true);
    expect(() => createSessionRequestSchema.parse(build('claude', state))).not.toThrow();
  });
});

describe('Codex — chat view', () => {
  const AVAILABLE: ChatSupport = { kind: 'available', resume: true, live: true };

  it.each<[string, boolean, ChatSupport | undefined, 'chat' | 'terminal']>([
    ['chat available', false, AVAILABLE, 'chat'],
    ['probe still out', false, undefined, 'chat'],
    ['Terminal UI on', true, AVAILABLE, 'terminal'],
    ['no chat for Codex on this PC', false, { kind: 'unavailable' }, 'terminal'],
    ['agent predates chat', false, { kind: 'outdated' }, 'terminal'],
  ])('%s', (_name, terminalUi, chat, ui) => {
    const request = buildCreateRequest({
      state: { ...POPULATED, codex: { ...POPULATED.codex, terminalUi } },
      mode: 'codex',
      workspace: gitWorkspace,
      chat,
    });

    expect(request?.ui).toBe(ui);
    expect(() => createSessionRequestSchema.parse(request)).not.toThrow();
  });
});

describe('Antigravity — chat view', () => {
  const AVAILABLE: ChatSupport = { kind: 'available', resume: true, live: false };

  it.each<[string, boolean, ChatSupport | undefined, 'chat' | 'terminal']>([
    ['chat available', false, AVAILABLE, 'chat'],
    ['Terminal UI on', true, AVAILABLE, 'terminal'],
    ['agy on this PC has no chat', false, { kind: 'unavailable' }, 'terminal'],
  ])('%s', (_name, terminalUi, chat, ui) => {
    const request = buildCreateRequest({
      state: { ...POPULATED, antigravity: { ...POPULATED.antigravity, terminalUi } },
      mode: 'antigravity',
      workspace: gitWorkspace,
      chat,
    });

    expect(request?.ui).toBe(ui);
    expect(request?.antigravity_options).toMatchObject({ worktree: true, branch_name: 'feature/x' });
    expect(() => createSessionRequestSchema.parse(request)).not.toThrow();
  });
});

describe('OpenCode — chat view', () => {
  const AVAILABLE: ChatSupport = { kind: 'available', resume: true, live: false };

  it.each<[string, boolean, ChatSupport | undefined, 'chat' | 'terminal']>([
    ['chat available', false, AVAILABLE, 'chat'],
    ['Terminal UI on', true, AVAILABLE, 'terminal'],
    ['no chat for OpenCode on this PC', false, { kind: 'unavailable' }, 'terminal'],
    ['agent predates chat', false, { kind: 'outdated' }, 'terminal'],
  ])('%s', (_name, terminalUi, chat, ui) => {
    const request = buildCreateRequest({
      state: { ...POPULATED, opencode: { ...POPULATED.opencode, terminalUi } },
      mode: 'opencode',
      workspace: gitWorkspace,
      chat,
    });

    expect(request?.cli).toBe('opencode');
    expect(request?.ui).toBe(ui);
    expect(request?.worktree).toBe(false);
    expect(request?.opencode_options).toMatchObject({
      auto_approve: true,
      connect: false,
      worktree: true,
      branch_name: 'feature/x',
    });
    expect(() => createSessionRequestSchema.parse(request)).not.toThrow();
  });

  it('asks before each permission unless the user turned auto-approve on', () => {
    const request = build('opencode', {
      ...INITIAL_WIZARD_STATE,
      selection: { kind: 'stored', name: gitWorkspace.name },
    });

    expect(request.opencode_options?.auto_approve).toBe(false);
  });
});

describe('Copilot — chat view', () => {
  const AVAILABLE: ChatSupport = { kind: 'available', resume: true, live: false };

  it.each<[string, boolean, ChatSupport | undefined, 'chat' | 'terminal']>([
    ['chat available', false, AVAILABLE, 'chat'],
    ['probe still out', false, undefined, 'chat'],
    ['Terminal UI on', true, AVAILABLE, 'terminal'],
    ['no chat for Copilot on this PC', false, { kind: 'unavailable' }, 'terminal'],
    ['agent predates chat', false, { kind: 'outdated' }, 'terminal'],
  ])('%s', (_name, terminalUi, chat, ui) => {
    const request = buildCreateRequest({
      state: { ...POPULATED, copilot: { ...POPULATED.copilot, terminalUi } },
      mode: 'copilot',
      workspace: gitWorkspace,
      chat,
    });

    expect(request?.cli).toBe('copilot');
    expect(request?.ui).toBe(ui);
    expect(request?.worktree).toBe(false);
    expect(request?.copilot_options).toEqual({
      allow_all: true,
      worktree: true,
      branch_name: 'feature/x',
      base_branch: null,
    });
    expect(() => createSessionRequestSchema.parse(request)).not.toThrow();
  });
});

describe('Cursor — chat view', () => {
  const AVAILABLE: ChatSupport = { kind: 'available', resume: true, live: false };

  it.each<[string, boolean, ChatSupport | undefined, 'chat' | 'terminal']>([
    ['chat available', false, AVAILABLE, 'chat'],
    ['probe still out', false, undefined, 'chat'],
    ['Terminal UI on', true, AVAILABLE, 'terminal'],
    ['no chat for Cursor on this PC', false, { kind: 'unavailable' }, 'terminal'],
    ['agent predates chat', false, { kind: 'outdated' }, 'terminal'],
  ])('%s', (_name, terminalUi, chat, ui) => {
    const request = buildCreateRequest({
      state: { ...POPULATED, cursor: { ...POPULATED.cursor, terminalUi } },
      mode: 'cursor',
      workspace: gitWorkspace,
      chat,
    });

    expect(request?.cli).toBe('cursor');
    expect(request?.ui).toBe(ui);
    expect(request?.worktree).toBe(false);
    expect(request?.cursor_options).toEqual({
      force: true,
      worktree: true,
      branch_name: 'feature/x',
      base_branch: null,
    });
    expect(() => createSessionRequestSchema.parse(request)).not.toThrow();
  });

  it('never carries the options of the Copilot bucket', () => {
    const request = build('cursor', {
      ...POPULATED,
      cursor: { force: false, terminalUi: false },
      copilot: { allowAll: true, terminalUi: true },
    });

    expect(request.cursor_options?.force).toBe(false);
    expect('copilot_options' in request).toBe(false);
  });
});

describe('Junie — chat view', () => {
  const AVAILABLE: ChatSupport = { kind: 'available', resume: true, live: false };

  it.each<[string, boolean, ChatSupport | undefined, 'chat' | 'terminal']>([
    ['chat available', false, AVAILABLE, 'chat'],
    ['probe still out', false, undefined, 'chat'],
    ['Terminal UI on', true, AVAILABLE, 'terminal'],
    ['no chat for Junie on this PC', false, { kind: 'unavailable' }, 'terminal'],
    ['agent predates chat', false, { kind: 'outdated' }, 'terminal'],
  ])('%s', (_name, terminalUi, chat, ui) => {
    const request = buildCreateRequest({
      state: { ...POPULATED, junie: { ...POPULATED.junie, terminalUi } },
      mode: 'junie',
      workspace: gitWorkspace,
      chat,
    });

    expect(request?.cli).toBe('junie');
    expect(request?.ui).toBe(ui);
    expect(request?.worktree).toBe(false);
    expect(request?.junie_options).toEqual({
      plan_mode: true,
      brave: true,
      worktree: true,
      branch_name: 'feature/x',
      base_branch: null,
    });
    expect(() => createSessionRequestSchema.parse(request)).not.toThrow();
  });

  it('never carries the options of the Cursor bucket', () => {
    const request = build('junie', {
      ...POPULATED,
      junie: { planMode: false, brave: false, terminalUi: false },
      cursor: { force: true, terminalUi: true },
    });

    expect(request.junie_options?.brave).toBe(false);
    expect('cursor_options' in request).toBe(false);
  });
});

describe('Pi — chat view', () => {
  const AVAILABLE: ChatSupport = { kind: 'available', resume: true, live: false };

  it.each<[string, boolean, ChatSupport | undefined, 'chat' | 'terminal']>([
    ['chat available', false, AVAILABLE, 'chat'],
    ['probe still out', false, undefined, 'chat'],
    ['Terminal UI on', true, AVAILABLE, 'terminal'],
    ['no chat for Pi on this PC', false, { kind: 'unavailable' }, 'terminal'],
    ['agent predates chat', false, { kind: 'outdated' }, 'terminal'],
  ])('%s', (_name, terminalUi, chat, ui) => {
    const request = buildCreateRequest({
      state: { ...POPULATED, pi: { terminalUi, codeMode: false } },
      mode: 'pi',
      workspace: gitWorkspace,
      chat,
    });

    expect(request?.cli).toBe('pi');
    expect(request?.ui).toBe(ui);
    expect(request?.worktree).toBe(false);
    expect(request?.pi_options).toEqual({
      code_mode: false,
      worktree: true,
      branch_name: 'feature/x',
      base_branch: null,
    });
    expect(() => createSessionRequestSchema.parse(request)).not.toThrow();
  });
});

describe('buildResumeRequest', () => {
  const stored: ChatStoredSession = {
    session_id: 'external-2222',
    cwd: gitWorkspace.path,
    title: 'Add retries to the sync job',
    updated_at: '2026-11-08T10:00:00Z',
    open_session_id: undefined,
    open_elsewhere: undefined,
  };

  it('continues a Codex session with the Codex options and never a worktree', () => {
    const request = buildResumeRequest(
      { state: POPULATED, mode: 'codex', workspace: gitWorkspace },
      'codex',
      stored,
    );

    expect(request).toMatchObject({
      project_name: 'mobaide',
      cli: 'codex',
      ui: 'chat',
      chat_resume_id: 'external-2222',
      worktree: false,
    });
    expect(request.codex_options).toMatchObject({
      model: 'gpt-5.4',
      sandbox: 'danger-full-access',
      approval: 'never',
      full_auto: true,
      worktree: false,
    });
    expect(request.codex_options?.branch_name).toBeUndefined();
    expect('claude_options' in request).toBe(false);
    expect(() => createSessionRequestSchema.parse(request)).not.toThrow();
  });

  it('keeps Codex options out of a Claude Code resume', () => {
    const request = buildResumeRequest(
      { state: POPULATED, mode: 'claude', workspace: gitWorkspace },
      'claude',
      stored,
    );

    expect(request.cli).toBe('claude');
    expect(request.claude_options).toMatchObject({ worktree: false });
    expect('codex_options' in request).toBe(false);
  });

  it('continues an Antigravity conversation with the Antigravity options and never a worktree', () => {
    const request = buildResumeRequest(
      { state: POPULATED, mode: 'antigravity', workspace: gitWorkspace },
      'antigravity',
      stored,
    );

    expect(request).toMatchObject({
      project_name: 'mobaide',
      cli: 'antigravity',
      ui: 'chat',
      chat_resume_id: 'external-2222',
      worktree: false,
    });
    expect(request.antigravity_options).toEqual({
      model: 'Gemini 3.1 Pro (High)',
      skip_permissions: true,
      sandbox: false,
      add_dirs: ['/one', '/two', '/three'],
      worktree: false,
      branch_name: undefined,
      base_branch: null,
    });
    expect('claude_options' in request).toBe(false);
    expect('codex_options' in request).toBe(false);
    expect(() => createSessionRequestSchema.parse(request)).not.toThrow();
  });

  it('continues an OpenCode session with the OpenCode options and never a worktree', () => {
    const request = buildResumeRequest(
      { state: POPULATED, mode: 'opencode', workspace: gitWorkspace },
      'opencode',
      stored,
    );

    expect(request).toMatchObject({
      project_name: 'mobaide',
      cli: 'opencode',
      ui: 'chat',
      chat_resume_id: 'external-2222',
      worktree: false,
    });
    expect(request.opencode_options).toEqual({
      auto_approve: true,
      connect: false,
      code_mode: true,
      worktree: false,
      branch_name: undefined,
      base_branch: null,
    });
    expect(OPTION_KEYS.filter((key) => key in request)).toEqual(['opencode_options']);
    expect(() => createSessionRequestSchema.parse(request)).not.toThrow();
  });

  it('continues a Pi session with the Pi options and never a worktree', () => {
    const request = buildResumeRequest(
      { state: POPULATED, mode: 'pi', workspace: gitWorkspace },
      'pi',
      stored,
    );

    expect(request).toMatchObject({
      project_name: 'mobaide',
      cli: 'pi',
      ui: 'chat',
      chat_resume_id: 'external-2222',
      worktree: false,
    });
    expect(request.pi_options).toEqual({
      code_mode: false,
      worktree: false,
      branch_name: undefined,
      base_branch: null,
    });
    expect('claude_options' in request).toBe(false);
    expect('copilot_options' in request).toBe(false);
    expect(() => createSessionRequestSchema.parse(request)).not.toThrow();
  });

  it('continues a Copilot session with the Copilot options and never a worktree', () => {
    const request = buildResumeRequest(
      { state: POPULATED, mode: 'copilot', workspace: gitWorkspace },
      'copilot',
      stored,
    );

    expect(request).toMatchObject({
      project_name: 'mobaide',
      cli: 'copilot',
      ui: 'chat',
      chat_resume_id: 'external-2222',
      worktree: false,
    });
    expect(request.copilot_options).toEqual({
      allow_all: true,
      worktree: false,
      branch_name: undefined,
      base_branch: null,
    });
    expect('claude_options' in request).toBe(false);
    expect('codex_options' in request).toBe(false);
    expect('antigravity_options' in request).toBe(false);
    expect(() => createSessionRequestSchema.parse(request)).not.toThrow();
  });

  it('continues a Cursor session with the Cursor options and never a worktree', () => {
    const request = buildResumeRequest(
      { state: POPULATED, mode: 'cursor', workspace: gitWorkspace },
      'cursor',
      stored,
    );

    expect(request).toMatchObject({
      project_name: 'mobaide',
      cli: 'cursor',
      ui: 'chat',
      chat_resume_id: 'external-2222',
      worktree: false,
    });
    expect(request.cursor_options).toEqual({
      force: true,
      worktree: false,
      branch_name: undefined,
      base_branch: null,
    });
    expect('claude_options' in request).toBe(false);
    expect('codex_options' in request).toBe(false);
    expect('copilot_options' in request).toBe(false);
    expect(() => createSessionRequestSchema.parse(request)).not.toThrow();
  });

  it('continues a Junie session with the Junie options and never a worktree', () => {
    const request = buildResumeRequest(
      { state: POPULATED, mode: 'junie', workspace: gitWorkspace },
      'junie',
      stored,
    );

    expect(request).toMatchObject({
      project_name: 'mobaide',
      cli: 'junie',
      ui: 'chat',
      chat_resume_id: 'external-2222',
      worktree: false,
    });
    expect(request.junie_options).toEqual({
      plan_mode: true,
      brave: true,
      worktree: false,
      branch_name: undefined,
      base_branch: null,
    });
    expect('claude_options' in request).toBe(false);
    expect('cursor_options' in request).toBe(false);
    expect(() => createSessionRequestSchema.parse(request)).not.toThrow();
  });

  it('sends the recorded folder as cwd when it is not the selected workspace', () => {
    const request = buildResumeRequest(
      { state: POPULATED, mode: 'codex', workspace: gitWorkspace },
      'codex',
      { ...stored, cwd: '/srv/elsewhere' },
    );

    expect(request.cwd).toBe('/srv/elsewhere');
    expect(request.project_name).toBeUndefined();
  });
});

describe('buildConnectRequest', () => {
  const SIGN_IN = { auto_approve: false, connect: true, code_mode: true, worktree: false };

  it('asks for a bare terminal sign-in in the workspace, whatever the wizard holds', () => {
    // `opencode auth login` is not an agent session: auto-approve, the
    // worktree and its branch must not ride along, and neither may the chat view.
    const request = buildConnectRequest({
      state: POPULATED,
      mode: 'opencode',
      workspace: gitWorkspace,
      worktreeBase: 'origin/dev',
      chat: { kind: 'available', resume: true, live: false },
    });

    expect(request).toEqual({
      project_name: 'mobaide',
      cwd: undefined,
      rows: 24,
      cols: 80,
      cli: 'opencode',
      opencode_options: SIGN_IN,
      worktree: false,
      ui: 'terminal',
    });
    expect(() => createSessionRequestSchema.parse(request)).not.toThrow();
  });

  it('runs in a one-off folder, sent as cwd with no workspace', () => {
    const request = buildConnectRequest({
      state: { ...POPULATED, selection: { kind: 'customPath', path: '/tmp/scratch' } },
      mode: 'opencode',
      workspace: null,
    });

    expect(request).toMatchObject({ cwd: '/tmp/scratch', opencode_options: SIGN_IN, ui: 'terminal' });
    expect(request?.project_name).toBeUndefined();
  });

  it('is refused without a folder to run in', () => {
    expect(
      buildConnectRequest({
        state: { ...POPULATED, selection: { kind: 'none' } },
        mode: 'opencode',
        workspace: null,
      }),
    ).toBeNull();
  });
});

describe('base_branch', () => {
  const withBase = (mode: WizardMode, state: WizardState = POPULATED): CreateSessionRequest => {
    const request = buildCreateRequest({ state, mode, workspace: gitWorkspace, worktreeBase: 'origin/dev' });
    if (request === null) throw new Error(`expected a request for mode ${mode}`);
    return request;
  };

  it('rides each managed CLI’s own options block, never the top level', () => {
    expect(withBase('claude').claude_options?.base_branch).toBe('origin/dev');
    expect(withBase('codex').codex_options?.base_branch).toBe('origin/dev');
    expect(withBase('antigravity').antigravity_options?.base_branch).toBe('origin/dev');
    expect(withBase('opencode').opencode_options?.base_branch).toBe('origin/dev');
    expect(withBase('copilot').copilot_options?.base_branch).toBe('origin/dev');
    expect(withBase('cursor').cursor_options?.base_branch).toBe('origin/dev');
    expect(withBase('junie').junie_options?.base_branch).toBe('origin/dev');
    expect(withBase('pi').pi_options?.base_branch).toBe('origin/dev');
    for (const mode of [
      'claude',
      'codex',
      'antigravity',
      'opencode',
      'copilot',
      'cursor',
      'junie',
      'pi',
    ] as const) {
      expect('base_branch' in withBase(mode)).toBe(false);
    }
  });

  it.each(['shell', CUSTOM] as const)('goes top level for %s', (mode) => {
    expect(withBase(mode).base_branch).toBe('origin/dev');
  });

  it.each(REQUEST_MODES)('is sent as null without a worktree for %s', (mode) => {
    const request = withBase(mode, { ...POPULATED, git: { worktree: false, branchName: 'wip' } });
    const own = OWN_OPTIONS[mode];
    const block = (own === null ? request : request[own]) as Record<string, unknown>;

    expect(Object.hasOwn(block, 'base_branch')).toBe(true);
    expect(block.base_branch).toBeNull();
  });

  it('is null for a workspace that is not a repo, and for an unresolved base', () => {
    const nonGit = buildCreateRequest({
      state: POPULATED,
      mode: 'claude',
      workspace: { ...gitWorkspace, is_git: false },
      worktreeBase: 'origin/dev',
    });

    expect(nonGit?.claude_options?.base_branch).toBeNull();
    expect(build('claude').claude_options?.base_branch).toBeNull();
  });

  it('is null on an Auto Scan start', () => {
    const scan = buildStartScanRequest({
      state: POPULATED,
      mode: 'auto_scan',
      workspace: gitWorkspace,
      worktreeBase: 'origin/dev',
    });

    expect(scan?.claude_options?.base_branch).toBeNull();
  });
});

describe('Code Mode', () => {
  const AVAILABLE: ChatSupport = { kind: 'available', resume: true, live: true };
  const FRESH: WizardState = {
    ...INITIAL_WIZARD_STATE,
    selection: { kind: 'stored', name: gitWorkspace.name },
  };
  const CLAUDE_CODE_MODE: WizardState = {
    ...FRESH,
    claude: { ...FRESH.claude, codeMode: true },
  };
  const stored: ChatStoredSession = {
    session_id: 'external-3333',
    cwd: gitWorkspace.path,
    title: 'Refactor the parser',
    updated_at: '2026-11-20T10:00:00Z',
    open_session_id: undefined,
    open_elsewhere: undefined,
  };

  it('opens Claude Code in the terminal, the one place it runs Code Mode', () => {
    const request = buildCreateRequest({
      state: CLAUDE_CODE_MODE,
      mode: 'claude',
      workspace: gitWorkspace,
      chat: AVAILABLE,
    });

    expect(request?.ui).toBe('terminal');
    expect(request?.claude_options?.code_mode).toBe(true);
  });

  it('sends Claude Code Mode off explicitly by default, in the chat view', () => {
    const request = buildCreateRequest({
      state: FRESH,
      mode: 'claude',
      workspace: gitWorkspace,
      chat: AVAILABLE,
    });

    expect(request?.ui).toBe('chat');
    expect(request?.claude_options?.code_mode).toBe(false);
  });

  it('never sends Claude Code Mode with a Continue, which is always a chat', () => {
    const request = buildResumeRequest(
      { state: CLAUDE_CODE_MODE, mode: 'claude', workspace: gitWorkspace },
      'claude',
      stored,
    );

    expect(request.ui).toBe('chat');
    expect(request.claude_options?.code_mode).toBe(false);
  });

  it('never sends Code Mode with an older agent’s Auto Scan, whatever the buckets hold', () => {
    const scan = buildStartScanRequest({
      state: { ...CLAUDE_CODE_MODE, autoScan: { ...FRESH.autoScan, codeMode: true } },
      mode: 'auto_scan',
      workspace: gitWorkspace,
    });

    expect(scan?.claude_options?.code_mode).toBe(false);
  });

  it('sends OpenCode Code Mode on by default and off when the user turns it off', () => {
    expect(build('opencode', FRESH).opencode_options?.code_mode).toBe(true);
    expect(
      build('opencode', { ...FRESH, opencode: { ...FRESH.opencode, codeMode: false } })
        .opencode_options?.code_mode,
    ).toBe(false);
  });

  it('sends Pi Code Mode off by default, and on with a launch and a Continue', () => {
    const on: WizardState = { ...FRESH, pi: { ...FRESH.pi, codeMode: true } };

    expect(build('pi', FRESH).pi_options?.code_mode).toBe(false);
    expect(build('pi', on).pi_options?.code_mode).toBe(true);
    expect(
      buildResumeRequest({ state: on, mode: 'pi', workspace: gitWorkspace }, 'pi', stored)
        .pi_options?.code_mode,
    ).toBe(true);
  });

  // `createSession` re-parses every body through the strict wire schema, so a
  // key the schema does not know fails the launch before it is sent.
  it.each<[string, () => CreateSessionRequest, (wire: CreateSessionRequest) => unknown]>([
    [
      'Claude Code',
      () => build('claude', CLAUDE_CODE_MODE),
      (wire) => wire.claude_options?.code_mode,
    ],
    [
      'OpenCode',
      () => build('opencode', FRESH),
      (wire) => wire.opencode_options?.code_mode,
    ],
    [
      'Pi',
      () => build('pi', { ...FRESH, pi: { ...FRESH.pi, codeMode: true } }),
      (wire) => wire.pi_options?.code_mode,
    ],
  ])('keeps %s Code Mode through the strict wire schema', (_agent, request, codeMode) => {
    const wire = createSessionRequestSchema.parse(request()) as CreateSessionRequest;

    expect(codeMode(wire)).toBe(true);
  });
});
