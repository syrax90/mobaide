import { describe, expect, it } from 'vitest';
import type { components } from '../../../api/generated/agent';
import type { ProjectSummary } from '../../../api/projectRepository';
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
import { INITIAL_WIZARD_STATE, type WizardMode, type WizardState } from './wizardState';

// `satisfies`, not a `WizardMode` annotation: the literal type is what keeps the
// mode tables below exhaustive rather than open-ended template-literal keys.
const CUSTOM = 'custom_0123456789ab' satisfies WizardMode;

const gitProject: ProjectSummary = {
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
  selection: { kind: 'stored', name: gitProject.name },
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
  opencode: { autoApprove: true, terminalUi: false },
  git: { worktree: true, branchName: 'feature/x' },
  autoScanPromptSeededFor: gitProject.name,
};

function build(mode: WizardMode, state: WizardState = POPULATED): CreateSessionRequest {
  const request = buildCreateRequest({ state, mode, project: gitProject });
  if (request === null) throw new Error(`expected a request for mode ${mode}`);
  return request;
}

const REQUEST_MODES = ['claude', 'codex', 'antigravity', 'opencode', 'shell', CUSTOM] as const;
const OPTION_KEYS = [
  'claude_options',
  'codex_options',
  'antigravity_options',
  'opencode_options',
] as const;

const OWN_OPTIONS: Record<(typeof REQUEST_MODES)[number], (typeof OPTION_KEYS)[number] | null> =
  {
    claude: 'claude_options',
    codex: 'codex_options',
    antigravity: 'antigravity_options',
    opencode: 'opencode_options',
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
      project: gitProject,
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
> = { auto_approve: true, connect: true, worktree: true };

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

  it('declares every Claude boolean on the Auto Scan request too', () => {
    const scan = buildStartScanRequest({
      state: POPULATED,
      mode: 'auto_scan',
      project: gitProject,
    });

    expectExplicitBooleans(scan?.claude_options, CLAUDE_BOOLEANS);
  });

  it('sends skip_permissions: false for a default Claude launch', () => {
    // The wire-absence default is `true`, so an omitted field would silently
    // launch with --dangerously-skip-permissions.
    const request = build('claude', {
      ...INITIAL_WIZARD_STATE,
      selection: { kind: 'stored', name: gitProject.name },
    });

    expect(request.claude_options?.skip_permissions).toBe(false);
  });
});

describe('buildCreateRequest — placement', () => {
  const nonGit: ProjectSummary = { ...gitProject, is_git: false };

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

  it('drops worktree and branch for a project that is not a repo', () => {
    const request = buildCreateRequest({ state: POPULATED, mode: 'claude', project: nonGit });

    expect(request?.claude_options).toMatchObject({ worktree: false });
    expect(request?.claude_options?.branch_name).toBeUndefined();
  });

  it('sends a one-off path as cwd with no project and no git intent', () => {
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
    expect(buildCreateRequest({ state: INITIAL_WIZARD_STATE, mode: 'shell', project: null })).toBeNull();
    expect(buildCreateRequest({ state: POPULATED, mode: 'auto_scan', project: gitProject })).toBeNull();
  });
});

describe('buildStartScanRequest', () => {
  const scanContext = { state: POPULATED, mode: 'auto_scan' as WizardMode, project: gitProject };

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

describe('focus_mode', () => {
  it('defaults to focus view on for both a Claude launch and a scan', () => {
    const fresh: WizardState = {
      ...INITIAL_WIZARD_STATE,
      selection: { kind: 'stored', name: gitProject.name },
    };

    expect(build('claude', fresh).claude_options?.focus_mode).toBe(true);
    expect(
      buildStartScanRequest({ state: fresh, mode: 'auto_scan', project: gitProject })
        ?.claude_options?.focus_mode,
    ).toBe(true);
  });

  it('sends an explicit false from each bucket independently', () => {
    const state: WizardState = {
      ...POPULATED,
      claude: { ...POPULATED.claude, focusMode: false },
      autoScan: { ...POPULATED.autoScan, focusMode: true },
    };
    const scan = buildStartScanRequest({ state, mode: 'auto_scan', project: gitProject });

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
      project: gitProject,
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
      project: gitProject,
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
      project: gitProject,
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
      selection: { kind: 'stored', name: gitProject.name },
    });

    expect(request.opencode_options?.auto_approve).toBe(false);
  });
});

describe('buildResumeRequest', () => {
  const stored: ChatStoredSession = {
    session_id: 'external-2222',
    cwd: gitProject.path,
    title: 'Add retries to the sync job',
    updated_at: '2026-11-08T10:00:00Z',
    open_session_id: undefined,
    open_elsewhere: undefined,
  };

  it('continues a Codex session with the Codex options and never a worktree', () => {
    const request = buildResumeRequest(
      { state: POPULATED, mode: 'codex', project: gitProject },
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
      { state: POPULATED, mode: 'claude', project: gitProject },
      'claude',
      stored,
    );

    expect(request.cli).toBe('claude');
    expect(request.claude_options).toMatchObject({ worktree: false });
    expect('codex_options' in request).toBe(false);
  });

  it('continues an Antigravity conversation with the Antigravity options and never a worktree', () => {
    const request = buildResumeRequest(
      { state: POPULATED, mode: 'antigravity', project: gitProject },
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
      { state: POPULATED, mode: 'opencode', project: gitProject },
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
      worktree: false,
      branch_name: undefined,
      base_branch: null,
    });
    expect(OPTION_KEYS.filter((key) => key in request)).toEqual(['opencode_options']);
    expect(() => createSessionRequestSchema.parse(request)).not.toThrow();
  });

  it('sends the recorded folder as cwd when it is not the selected project', () => {
    const request = buildResumeRequest(
      { state: POPULATED, mode: 'codex', project: gitProject },
      'codex',
      { ...stored, cwd: '/srv/elsewhere' },
    );

    expect(request.cwd).toBe('/srv/elsewhere');
    expect(request.project_name).toBeUndefined();
  });
});

describe('buildConnectRequest', () => {
  const SIGN_IN = { auto_approve: false, connect: true, worktree: false };

  it('asks for a bare terminal sign-in in the project, whatever the wizard holds', () => {
    // `opencode auth login` is not an agent session: auto-approve, the
    // worktree and its branch must not ride along, and neither may the chat view.
    const request = buildConnectRequest({
      state: POPULATED,
      mode: 'opencode',
      project: gitProject,
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

  it('runs in a one-off folder, sent as cwd with no project', () => {
    const request = buildConnectRequest({
      state: { ...POPULATED, selection: { kind: 'customPath', path: '/tmp/scratch' } },
      mode: 'opencode',
      project: null,
    });

    expect(request).toMatchObject({ cwd: '/tmp/scratch', opencode_options: SIGN_IN, ui: 'terminal' });
    expect(request?.project_name).toBeUndefined();
  });

  it('is refused without a folder to run in', () => {
    expect(
      buildConnectRequest({
        state: { ...POPULATED, selection: { kind: 'none' } },
        mode: 'opencode',
        project: null,
      }),
    ).toBeNull();
  });
});

describe('base_branch', () => {
  const withBase = (mode: WizardMode, state: WizardState = POPULATED): CreateSessionRequest => {
    const request = buildCreateRequest({ state, mode, project: gitProject, worktreeBase: 'origin/dev' });
    if (request === null) throw new Error(`expected a request for mode ${mode}`);
    return request;
  };

  it('rides each managed CLI’s own options block, never the top level', () => {
    expect(withBase('claude').claude_options?.base_branch).toBe('origin/dev');
    expect(withBase('codex').codex_options?.base_branch).toBe('origin/dev');
    expect(withBase('antigravity').antigravity_options?.base_branch).toBe('origin/dev');
    expect(withBase('opencode').opencode_options?.base_branch).toBe('origin/dev');
    for (const mode of ['claude', 'codex', 'antigravity', 'opencode'] as const) {
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

  it('is null for a project that is not a repo, and for an unresolved base', () => {
    const nonGit = buildCreateRequest({
      state: POPULATED,
      mode: 'claude',
      project: { ...gitProject, is_git: false },
      worktreeBase: 'origin/dev',
    });

    expect(nonGit?.claude_options?.base_branch).toBeNull();
    expect(build('claude').claude_options?.base_branch).toBeNull();
  });

  it('is null on an Auto Scan start', () => {
    const scan = buildStartScanRequest({
      state: POPULATED,
      mode: 'auto_scan',
      project: gitProject,
      worktreeBase: 'origin/dev',
    });

    expect(scan?.claude_options?.base_branch).toBeNull();
  });
});
