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
import { autoScanAgentOptions, autoScanBlocker, autoScanSources } from './autoScanSetup';
import {
  EMPTY_GITHUB_FILTER,
  EMPTY_LINEAR_FILTER,
  INITIAL_WIZARD_STATE,
  wizardReducer,
  type AutoScanSetup,
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
      { type: 'seedAutoScanSetup', workspaceName: 'proj', saved: savedChoices('auto_scan_choices') },
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
      { type: 'seedAutoScanSetup', workspaceName: 'p', saved: savedChoices('auto_scan_nothing_chosen') },
    ]);

    expect(state.autoScanSetup).toMatchObject({
      source: null,
      cli: null,
      linearFilter: EMPTY_LINEAR_FILTER,
      githubFilter: EMPTY_GITHUB_FILTER,
    });
  });

  it('keeps edits for the same workspace and re-seeds for another one', () => {
    const saved = savedChoices('auto_scan_choices');
    const edited = reduce([
      { type: 'seedAutoScanSetup', workspaceName: 'proj', saved },
      { type: 'setAutoScanSource', source: 'linear' },
      { type: 'setAutoScanCli', cli: 'pi' },
    ]);

    const again = reduce([{ type: 'seedAutoScanSetup', workspaceName: 'proj', saved }], edited);
    const other = reduce(
      [{ type: 'seedAutoScanSetup', workspaceName: 'other', saved: undefined }],
      edited,
    );

    expect([again.autoScanSetup.source, again.autoScanSetup.cli]).toEqual(['linear', 'pi']);
    expect([other.autoScanSetup.source, other.autoScanSetup.cli]).toEqual([null, null]);
  });

  it('lets a draft parked for the GitHub setup terminal win over the saved choices', () => {
    const parked = reduce([
      { type: 'seedAutoScanSetup', workspaceName: 'proj', saved: undefined },
      { type: 'setAutoScanSource', source: 'github' },
      { type: 'setAutoScanCli', cli: 'cursor' },
      {
        type: 'setAutoScanGitHubFilter',
        filter: { labels: ['ready'], assignee: '', activeLabel: '' },
      },
    ]);
    parkAutoScanDraft('agent-1', 'proj', { setup: parked.autoScanSetup, bucket: parked.autoScan });

    // Back from the terminal: a fresh wizard restores the draft, and the seed
    // that follows must not overwrite it.
    const draft = takeAutoScanDraft('agent-1', 'proj');
    if (draft === null) throw new Error('the parked draft was lost');
    const restored = reduce([
      { type: 'restoreAutoScanDraft', workspaceName: 'proj', draft },
      { type: 'seedAutoScanSetup', workspaceName: 'proj', saved: savedChoices('auto_scan_choices') },
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
    githubFilter: { labels: ['ai-ready'], assignee: '', activeLabel: '' },
    autoApprove: true,
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
      blocker({ githubFilter: { labels: ['ai-ready'], assignee: '', activeLabel: ' AI-Ready ' } }),
    ).not.toBeNull();
    expect(blocker({ cli: null })).not.toBeNull();
    expect(blocker({ cli: 'pi' })).not.toBeNull(); // listed, but not installed on the PC
    expect(blocker({ source: 'linear' })).not.toBeNull(); // no Linear key, no filter
  });
});
