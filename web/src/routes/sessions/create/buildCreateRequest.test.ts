import { describe, expect, it } from 'vitest';
import type { components } from '../../../api/generated/agent';
import type { ProjectSummary } from '../../../api/projectRepository';
import { createSessionRequestSchema } from '../../../protocol/dto/sessionCreate';
import {
  buildCreateRequest,
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
  },
  antigravity: {
    model: 'Gemini 3.1 Pro (High)',
    skipPermissions: true,
    sandbox: false,
    addDirsDraft: '/one, /two\n/three',
  },
  git: { worktree: true, branchName: 'feature/x' },
  autoScanPromptSeededFor: gitProject.name,
};

function build(mode: WizardMode, state: WizardState = POPULATED): CreateSessionRequest {
  const request = buildCreateRequest({ state, mode, project: gitProject });
  if (request === null) throw new Error(`expected a request for mode ${mode}`);
  return request;
}

const REQUEST_MODES = ['claude', 'codex', 'antigravity', 'shell', CUSTOM] as const;
const OPTION_KEYS = ['claude_options', 'codex_options', 'antigravity_options'] as const;

const OWN_OPTIONS: Record<(typeof REQUEST_MODES)[number], (typeof OPTION_KEYS)[number] | null> =
  {
    claude: 'claude_options',
    codex: 'codex_options',
    antigravity: 'antigravity_options',
    shell: null,
    [CUSTOM]: null,
  };

const ORDERED_PAIRS = REQUEST_MODES.flatMap((from) =>
  REQUEST_MODES.filter((to) => to !== from).map((to) => [from, to] as const),
);

describe('buildCreateRequest — no cross-branch leakage', () => {
  it.each(ORDERED_PAIRS)(
    'filling %s then launching %s sends only the launched branch',
    (from, to) => {
      const request = build(to);
      const expected = OWN_OPTIONS[to];

      expect(OPTION_KEYS.filter((key) => key in request)).toEqual(
        expected === null ? [] : [expected],
      );

      const abandoned = OWN_OPTIONS[from];
      if (abandoned !== null) {
        expect(Object.hasOwn(request, abandoned)).toBe(false);
      }
    },
  );

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

  it('sends nothing but the discriminator for a custom agent type', () => {
    expect(build(CUSTOM).cli).toBe(CUSTOM);
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

  it('always sizes the PTY the same', () => {
    expect(build('shell')).toMatchObject({ rows: 24, cols: 80 });
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
