// @vitest-environment node
import { describe, expect, it } from 'vitest';
import type { GitHubStatus } from '../../../api/autoscanRepository';
import { readFixture } from '../../../protocol/__fixtures__/load';
import {
  agentVersionSchema,
  autoScanCapability,
  type AutoScanCapability,
} from '../../../protocol/dto/agentVersion';
import { workspaceInfoSchema } from '../../../protocol/dto/workspaces';
import { parkAutoScanDraft, takeAutoScanDraft } from './autoScanDraft';
import {
  autoScanAgentOptions,
  autoScanBlocker,
  autoScanSources,
  autoScanTemplateSaveBlocker,
  templateNameError,
  templateNameKey,
} from './autoScanSetup';
import { autoScanTemplateSettings } from './buildCreateRequest';
import {
  AUTO_SCAN_AGENT_DEFAULTS,
  AUTO_SCAN_DEFAULT_INTERVAL_MINUTES,
  EMPTY_GITHUB_FILTER,
  EMPTY_LINEAR_FILTER,
  INITIAL_WIZARD_STATE,
  autoScanSeedKey,
  wizardReducer,
  type AutoScanSetup,
  type GitHubFilterDraft,
  type WizardAction,
  type WizardState,
} from './wizardState';

interface FixtureFile {
  cases: { name: string; wire: unknown }[];
}

function fixtureWire(path: string, name: string): unknown {
  const found = readFixture<FixtureFile>(path).cases.find((entry) => entry.name === name);
  if (found === undefined) throw new Error(`no case ${name} in ${path}`);
  return found.wire;
}

/** `auto_scan` exactly as the web client parses it off a `GET /api/projects` row. */
function savedChoices(caseName: string) {
  return workspaceInfoSchema.parse(fixtureWire('dto/agent/ProjectInfo.json', caseName)).auto_scan;
}

const reduce = (actions: WizardAction[], from: WizardState = INITIAL_WIZARD_STATE): WizardState =>
  actions.reduce(wizardReducer, from);

const version = agentVersionSchema.parse(
  fixtureWire('dto/agent/VersionInfoResponse.json', 'autoscan_capability'),
);

describe('capabilities.autoscan', () => {
  it('reads the fixture capability, and nothing from an older agent', () => {
    const older = agentVersionSchema.parse(
      fixtureWire('dto/agent/VersionInfoResponse.json', 'missing_capabilities_defaults_to_empty_object'),
    );

    expect(autoScanCapability(version)).toEqual({
      sources: ['linear', 'github'],
      clis: ['claude', 'codex', 'antigravity', 'opencode', 'copilot', 'cursor', 'junie', 'pi'],
      githubSetup: true,
    });
    expect(autoScanCapability(older)).toBeNull();
  });

  it('offers every listed agent, runnable only where its CLI is available', () => {
    const capability = autoScanCapability(version) as AutoScanCapability;
    const options = autoScanAgentOptions(capability, version);

    expect(options.map((option) => option.id)).toEqual(capability.clis);
    expect(options.filter((option) => option.available).map((option) => option.id)).toEqual([
      'claude',
      'codex',
    ]);
  });
});

describe('seeding the Auto Scan choices', () => {
  it('pre-fills what the workspace saved', () => {
    const state = reduce([
      { type: 'seedAutoScanSetup', seedKey: 'proj', saved: savedChoices('auto_scan_choices') },
    ]);

    expect(state.autoScanSetup).toMatchObject({
      source: 'github',
      cli: 'codex',
      githubFilter: { labels: ['ai-ready', 'bot'], assignee: '@me', activeLabel: '' },
      linearFilter: {
        state_names: ['Todo'],
        label_names: ['bot'],
        active_state: 'Doing',
        done_state: 'Shipped',
        removal_label: 'bot',
      },
    });
  });

  it('starts every choice empty when nothing was chosen, never at the legacy defaults', () => {
    const state = reduce([
      { type: 'seedAutoScanSetup', seedKey: 'p', saved: savedChoices('auto_scan_nothing_chosen') },
    ]);

    expect(state.autoScanSetup).toMatchObject({
      source: null,
      cli: null,
      linearFilter: EMPTY_LINEAR_FILTER,
      githubFilter: EMPTY_GITHUB_FILTER,
    });
  });

  it('pre-fills the saved session view and time between scans, and a new workspace starts at the defaults', () => {
    const saved = savedChoices('auto_scan_choices');
    const seeded = reduce([
      { type: 'seedAutoScanSetup', seedKey: 'proj', saved: saved && { ...saved, ui: 'terminal' } },
    ]);
    const other = reduce(
      [{ type: 'seedAutoScanSetup', seedKey: 'other', saved: undefined }],
      seeded,
    );

    expect([seeded.autoScanSetup.ui, seeded.autoScanSetup.intervalMinutes]).toEqual([
      'terminal',
      15,
    ]);
    expect([other.autoScanSetup.ui, other.autoScanSetup.intervalMinutes]).toEqual([
      'chat',
      AUTO_SCAN_DEFAULT_INTERVAL_MINUTES,
    ]);
  });

  it('keeps edits for the same workspace and re-seeds for another one', () => {
    const saved = savedChoices('auto_scan_choices');
    const edited = reduce([
      { type: 'seedAutoScanSetup', seedKey: 'proj', saved },
      { type: 'setAutoScanSource', source: 'linear' },
      { type: 'setAutoScanCli', cli: 'pi' },
    ]);

    const again = reduce([{ type: 'seedAutoScanSetup', seedKey: 'proj', saved }], edited);
    const other = reduce(
      [{ type: 'seedAutoScanSetup', seedKey: 'other', saved: undefined }],
      edited,
    );

    expect([again.autoScanSetup.source, again.autoScanSetup.cli]).toEqual(['linear', 'pi']);
    expect([other.autoScanSetup.source, other.autoScanSetup.cli]).toEqual([null, null]);
  });

  it('re-seeds a workspace of the same name on another agent', () => {
    const saved = savedChoices('auto_scan_choices');
    const first = reduce([
      { type: 'seedAutoScanSetup', seedKey: autoScanSeedKey('agent-a', 'proj'), saved: undefined },
      { type: 'setAutoScanCli', cli: 'pi' },
    ]);

    const second = reduce(
      [{ type: 'seedAutoScanSetup', seedKey: autoScanSeedKey('agent-b', 'proj'), saved }],
      first,
    );

    expect(second.autoScanSetup.cli).toBe(saved?.cli ?? null);
    expect(second.autoScanSetup.cli).not.toBe('pi');
  });

  it('lets a draft parked for the GitHub setup terminal win over the saved choices', () => {
    const parked = reduce([
      { type: 'seedAutoScanSetup', seedKey: 'proj', saved: undefined },
      { type: 'setAutoScanSource', source: 'github' },
      { type: 'setAutoScanCli', cli: 'cursor' },
      {
        type: 'setAutoScanGitHubFilter',
        filter: { labels: ['ready'], assignee: '', activeLabel: '', issueTypes: [] },
      },
    ]);
    parkAutoScanDraft('agent-1', 'proj', {
      setup: parked.autoScanSetup,
      bucket: parked.autoScan,
      agents: parked.autoScanAgents,
    });

    // Back from the terminal: a fresh wizard restores the draft, and the seed
    // that follows must not overwrite it.
    const draft = takeAutoScanDraft('agent-1', 'proj');
    if (draft === null) throw new Error('the parked draft was lost');
    const restored = reduce([
      { type: 'restoreAutoScanDraft', seedKey: 'proj', draft },
      { type: 'seedAutoScanSetup', seedKey: 'proj', saved: savedChoices('auto_scan_choices') },
    ]);

    expect(restored.autoScanSetup).toMatchObject({
      source: 'github',
      cli: 'cursor',
      githubFilter: { labels: ['ready'] },
    });
    expect(takeAutoScanDraft('agent-1', 'proj')).toBeNull();
  });
});

describe('autoScanBlocker', () => {
  const capability = autoScanCapability(version) as AutoScanCapability;
  const sources = autoScanSources(capability);
  const agents = autoScanAgentOptions(capability, version);
  const readyStatus: GitHubStatus = {
    installed: true,
    version: '2.83.0',
    signed_in: true,
    account: 'octocat',
    host: 'github.com',
    repo: 'acme/app',
    repo_url: 'https://github.com/acme/app',
    problem: null,
    can_install: false,
    install_command: null,
  };
  const ready: AutoScanSetup = {
    seededFor: 'proj',
    source: 'github',
    cli: 'codex',
    linearFilter: EMPTY_LINEAR_FILTER,
    githubFilter: { labels: ['ai-ready'], assignee: '', activeLabel: '', issueTypes: [] },
    ui: 'terminal',
    intervalMinutes: 5,
  };
  const blocker = (setup: Partial<AutoScanSetup>, github: GitHubStatus = readyStatus) =>
    autoScanBlocker({
      setup: { ...ready, ...setup },
      prompt: '',
      sources,
      agents,
      github: { kind: 'ready', status: github },
      hasLinearKey: false,
    });

  it('lets a scan start only once every choice is made and usable', () => {
    expect(blocker({})).toBeNull();
    expect(blocker({ source: null })).not.toBeNull();
    expect(blocker({}, { ...readyStatus, signed_in: false, problem: 'Sign in.' })).not.toBeNull();
    expect(blocker({ githubFilter: EMPTY_GITHUB_FILTER })).not.toBeNull();
    // The agent answers 422: the added label would make the issue match again.
    expect(
      blocker({
        githubFilter: {
          labels: ['ai-ready'],
          assignee: '',
          activeLabel: ' AI-Ready ',
          issueTypes: [],
        },
      }),
    ).not.toBeNull();
    expect(blocker({ cli: null })).not.toBeNull();
    expect(blocker({ cli: 'pi' })).not.toBeNull(); // listed, but not installed on the PC
    expect(blocker({ source: 'linear' })).not.toBeNull(); // no Linear key, no filter
  });
});

describe('Auto Scan agent options', () => {
  it('seeds each agent from its saved options, and one with none runs without asking', () => {
    const state = reduce([
      { type: 'seedAutoScanSetup', seedKey: 'proj', saved: savedChoices('auto_scan_choices') },
    ]);

    expect(state.autoScanAgents.codex).toEqual({
      model: 'gpt-5.5',
      sandbox: 'workspace-write',
      approval: 'never',
      fullAuto: true,
      autoReview: false,
      reasoningEffort: 'high',
      terminalUi: false,
    });
    expect(state.autoScanAgents.copilot.allowAll).toBe(false);
    expect(state.autoScanAgents.cursor).toEqual(AUTO_SCAN_AGENT_DEFAULTS.cursor);
    expect(state.autoScan).toMatchObject({ model: 'opus', focusMode: true, skipPermissions: true });
    expect(state.autoScanSetup.githubFilter.issueTypes).toEqual(['Bug']);
  });

  it('drops the edits of one workspace when another one is seeded', () => {
    const edited = reduce([
      { type: 'seedAutoScanSetup', seedKey: 'proj', saved: undefined },
      { type: 'setCopilotAllowAll', value: false, target: 'autoScan' },
    ]);

    const other = reduce(
      [{ type: 'seedAutoScanSetup', seedKey: 'other', saved: undefined }],
      edited,
    );

    expect(other.autoScanAgents).toEqual(AUTO_SCAN_AGENT_DEFAULTS);
  });

  it('keeps the Auto Scan copy of an agent apart from the normal session’s', () => {
    const state = reduce([
      { type: 'setCopilotAllowAll', value: false, target: 'autoScan' },
      { type: 'setCodexSandbox', sandbox: 'read-only' },
    ]);

    expect(state.autoScanAgents.copilot.allowAll).toBe(false);
    expect(state.copilot).toEqual(INITIAL_WIZARD_STATE.copilot);
    expect(state.codex.sandbox).toBe('read-only');
    expect(state.autoScanAgents.codex).toEqual(AUTO_SCAN_AGENT_DEFAULTS.codex);
  });

  it('reads options it cannot decode as unsaved and still decodes the workspace', () => {
    const workspace = workspaceInfoSchema.parse({
      name: 'proj',
      path: '/srv/proj',
      auto_scan: {
        source: 'github',
        cli: 'codex',
        codex_options: { sandbox: 'everything', full_auto: false },
        claude_options: { effort: 'beyond-max' },
        copilot_options: { allow_all: false },
      },
    });

    expect(workspace.auto_scan?.codex_options).toBeUndefined();
    expect(workspace.auto_scan?.claude_options).toBeUndefined();
    expect(workspace.auto_scan?.copilot_options).toEqual({ allow_all: false });
    expect(workspace.auto_scan?.cli).toBe('codex');
  });

  it('reads Codex options saved with the retired untrusted approval as on-request', () => {
    const workspace = workspaceInfoSchema.parse({
      name: 'proj',
      path: '/srv/proj',
      auto_scan: {
        source: 'github',
        cli: 'codex',
        codex_options: { sandbox: 'read-only', approval: 'untrusted', full_auto: false },
      },
    });

    expect(workspace.auto_scan?.codex_options).toMatchObject({
      sandbox: 'read-only',
      approval: 'on-request',
      full_auto: false,
    });
  });
});

describe('Auto Scan templates', () => {
  /** A template as the PC sends it back: JSON, with every `undefined` gone. */
  const throughTheWire = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

  it('brings back the same choices and keeps only the chosen agent’s options', () => {
    const dialled = reduce([
      { type: 'seedAutoScanSetup', seedKey: 'proj', saved: undefined },
      { type: 'setAutoScanSource', source: 'github' },
      { type: 'setAutoScanCli', cli: 'junie' },
      // A template may keep a filter the wizard still has to finish.
      {
        type: 'setAutoScanGitHubFilter',
        filter: { labels: [], assignee: ' @me ', activeLabel: '', issueTypes: ['Bug'] },
      },
      { type: 'setClaudeText', bucket: 'autoScan', field: 'initialPrompt', value: 'Fix it.' },
      { type: 'setJuniePlanMode', value: true, target: 'autoScan' },
      { type: 'setJunieBrave', value: false, target: 'autoScan' },
      { type: 'setCodexSandbox', sandbox: 'read-only', target: 'autoScan' },
    ]);
    const other = reduce([
      { type: 'seedAutoScanSetup', seedKey: 'other', saved: savedChoices('auto_scan_choices') },
    ]);

    const settings = throughTheWire(autoScanTemplateSettings(dialled));
    const applied = wizardReducer(other, { type: 'applyAutoScanSettings', settings });

    expect(settings.junie_options).toEqual({ plan_mode: true, brave: false });
    expect(settings.codex_options).toBeNull();
    expect(settings.claude_options).toBeNull();
    expect(applied.autoScanSetup).toMatchObject({
      source: 'github',
      cli: 'junie',
      githubFilter: { labels: [], assignee: '@me', activeLabel: '', issueTypes: ['Bug'] },
    });
    expect(applied.autoScan.initialPrompt).toBe('Fix it.');
    expect(applied.autoScanAgents.junie).toEqual(dialled.autoScanAgents.junie);
    // Options the template does not hold stay as the wizard had them.
    expect(applied.autoScanAgents.codex).toEqual(other.autoScanAgents.codex);
  });

  it('brings back Claude Code’s scan options', () => {
    const dialled = reduce([
      { type: 'setAutoScanSource', source: 'github' },
      { type: 'setAutoScanCli', cli: 'claude' },
      { type: 'setClaudeModel', bucket: 'autoScan', model: 'sonnet' },
      { type: 'setClaudeEffort', bucket: 'autoScan', effort: 'low' },
      { type: 'setClaudeFlag', bucket: 'autoScan', flag: 'planMode', value: true },
      { type: 'setClaudeFlag', bucket: 'autoScan', flag: 'skipPermissions', value: false },
    ]);

    const settings = throughTheWire(autoScanTemplateSettings(dialled));
    const applied = wizardReducer(INITIAL_WIZARD_STATE, { type: 'applyAutoScanSettings', settings });

    expect(settings.claude_options).not.toHaveProperty('worktree');
    expect(settings.claude_options).not.toHaveProperty('initial_prompt');
    expect(applied.autoScan).toMatchObject({
      model: 'sonnet',
      effort: 'low',
      planMode: true,
      skipPermissions: false,
    });
    expect(applied.claude).toEqual(INITIAL_WIZARD_STATE.claude);
  });

  it.each([
    ['claude', { type: 'setClaudeFlag', bucket: 'autoScan', flag: 'codeMode', value: true }],
    ['opencode', { type: 'setOpenCodeCodeMode', value: false, target: 'autoScan' }],
    ['pi', { type: 'setPiCodeMode', value: true, target: 'autoScan' }],
  ] as const)('brings back %s Code Mode', (cli, action) => {
    const dialled = reduce([{ type: 'setAutoScanCli', cli }, action]);

    const settings = throughTheWire(autoScanTemplateSettings(dialled));
    const applied = wizardReducer(INITIAL_WIZARD_STATE, { type: 'applyAutoScanSettings', settings });

    const codeMode = (state: typeof applied) =>
      cli === 'claude' ? state.autoScan.codeMode : state.autoScanAgents[cli].codeMode;
    expect(codeMode(applied)).toBe(codeMode(dialled));
    expect(codeMode(applied)).not.toBe(codeMode(INITIAL_WIZARD_STATE));
    // A normal session's options never change.
    expect([applied.claude, applied.opencode, applied.pi]).toEqual([
      INITIAL_WIZARD_STATE.claude,
      INITIAL_WIZARD_STATE.opencode,
      INITIAL_WIZARD_STATE.pi,
    ]);
  });

  it('brings back the session view and time between scans, and ignores ones the wizard cannot offer', () => {
    const dialled = reduce([
      { type: 'setAutoScanUi', ui: 'chat' },
      { type: 'setAutoScanInterval', minutes: 240 },
    ]);
    const settings = throughTheWire(autoScanTemplateSettings(dialled));

    const applied = wizardReducer(INITIAL_WIZARD_STATE, { type: 'applyAutoScanSettings', settings });

    expect([applied.autoScanSetup.ui, applied.autoScanSetup.intervalMinutes]).toEqual(['chat', 240]);
    // A template from another client, or a hand-edited one, keeps the wizard's choice.
    for (const bad of [
      { ui: 'web', interval_minutes: 0 },
      { ui: 'Chat', interval_minutes: 1441 },
      { ui: null, interval_minutes: 2.5 },
    ]) {
      const kept = wizardReducer(dialled, { type: 'applyAutoScanSettings', settings: bad });
      expect([kept.autoScanSetup.ui, kept.autoScanSetup.intervalMinutes]).toEqual(['chat', 240]);
    }
  });

  it('matches and checks names the way the PC does', () => {
    expect(templateNameKey('  Small \t BUGS ')).toBe(templateNameKey('small bugs'));
    expect(templateNameError(` ${'x'.repeat(60)} `)).toBeNull();
    expect(templateNameError(' \n ')).not.toBeNull();
    expect(templateNameError('x'.repeat(61))).not.toBeNull();
  });

  it('blocks a save only for what the PC refuses in a template', () => {
    const check = (
      filter: Partial<GitHubFilterDraft>,
      { cli = 'codex', prompt = '', addDirs = '' }: { cli?: string; prompt?: string; addDirs?: string } = {},
    ) =>
      autoScanTemplateSaveBlocker({
        setup: {
          ...INITIAL_WIZARD_STATE.autoScanSetup,
          source: 'github',
          cli,
          githubFilter: { ...EMPTY_GITHUB_FILTER, ...filter },
        },
        prompt,
        antigravityAddDirs: addDirs,
      });
    const long = 'x'.repeat(101);

    // A template may keep a filter the scan would refuse: no label yet, or a
    // label to add that is also a label to pick up.
    expect(check({ labels: [] })).toBeNull();
    expect(check({ labels: ['ai-ready'], activeLabel: ' AI-Ready ' })).toBeNull();
    expect(check({ issueTypes: ['Bug, urgent'] })).toBeNull();
    expect(check({}, { prompt: 'x'.repeat(32_000) })).toBeNull();
    expect(check({}, { addDirs: '-x' })).toBeNull(); // the directories of an agent not chosen

    expect(check({ labels: ['ai,ready'] })).not.toBeNull();
    expect(check({ labels: Array.from({ length: 21 }, (_, i) => `l${String(i)}`) })).not.toBeNull();
    expect(check({ activeLabel: 'in,progress' })).not.toBeNull();
    expect(check({ activeLabel: long })).not.toBeNull();
    expect(check({ issueTypes: [long] })).not.toBeNull();
    expect(check({ assignee: long })).not.toBeNull();
    expect(check({}, { prompt: 'x'.repeat(32_001) })).not.toBeNull();
    expect(check({}, { cli: 'antigravity', addDirs: '-x' })).not.toBeNull();
  });
});
