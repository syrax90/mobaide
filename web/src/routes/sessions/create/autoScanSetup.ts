/**
 * The Auto Scan wizard's rules for an agent with `capabilities.autoscan`
 * (Delta 2026-11-18): which sources and agents to offer, how a GitHub label is
 * checked, and what still keeps Start disabled. Pure, so each rule is testable
 * without a DOM.
 */
import type { GitHubStatus } from '../../../api/autoscanRepository';
import {
  AUTO_SCAN_AGENTS,
  autoScanAgentName,
  type AutoScanAgentId,
} from '../../../autoscan/autoscanDisplay';
import {
  cliAvailable,
  type AgentVersionInfo,
  type AutoScanCapability,
} from '../../../protocol/dto/agentVersion';
import { filterError } from '../../workspaces/workspaceForm';
import {
  addDirsError,
  type AutoScanSetup,
  type AutoScanSource,
  type GitHubFilterDraft,
} from './wizardState';

/** The agent's limits on `github_filter` (`GitHubScanFilterModel`). */
export const GITHUB_LABELS_MAX = 20;
export const GITHUB_ISSUE_TYPES_MAX = 20;
export const GITHUB_TEXT_MAX = 100;
/** `StartScanRequest.initial_prompt`'s `max_length`. */
export const INITIAL_PROMPT_MAX = 32_000;
/** `SaveTemplateRequest.name`, after runs of white space become one space. */
export const TEMPLATE_NAME_MAX = 60;

/** Shown under an agent's "run without asking" switch while it is off. */
export const AUTO_SCAN_ASK_HINT =
  'When off, the agent may stop and wait for your approval. You get a notification and can open the session to approve.';
/** Codex has presets, not a switch: shown while Full access is off and Codex can ask. */
export const AUTO_SCAN_CODEX_ASK_HINT =
  'Without Full access, Codex may stop and wait for your approval. You get a notification and can open the session to approve.';

const SOURCE_ORDER: readonly AutoScanSource[] = ['github', 'linear'];

export const AUTO_SCAN_SOURCE_TITLES: Readonly<Record<AutoScanSource, string>> = {
  github: 'GitHub Issues',
  linear: 'Linear',
};

/** The sources the agent can scan, GitHub first. */
export function autoScanSources(capability: AutoScanCapability): AutoScanSource[] {
  return SOURCE_ORDER.filter((source) => capability.sources.includes(source));
}

export interface AutoScanAgentOption {
  id: AutoScanAgentId;
  name: string;
  /** `capabilities.cli[id].available` is `true`; anything else cannot run a scan. */
  available: boolean;
}

/**
 * The agents a scan can run, in the wizard's order. A kind the agent lists but
 * this client does not know is left out: it could not be named on the wire.
 */
export function autoScanAgentOptions(
  capability: AutoScanCapability,
  capabilities: AgentVersionInfo | undefined,
): AutoScanAgentOption[] {
  return AUTO_SCAN_AGENTS.filter((agent) => capability.clis.includes(agent.id)).map((agent) => ({
    id: agent.id,
    name: agent.name,
    available: cliAvailable(capabilities, agent.id) === true,
  }));
}

export function githubFilterError(filter: GitHubFilterDraft): string | null {
  if (filter.labels.length === 0) return 'Choose at least one label to pick up.';
  if (filter.labels.length > GITHUB_LABELS_MAX) {
    return `Pick up at most ${String(GITHUB_LABELS_MAX)} labels.`;
  }
  if (filter.issueTypes.length > GITHUB_ISSUE_TYPES_MAX) {
    return `Choose at most ${String(GITHUB_ISSUE_TYPES_MAX)} issue types.`;
  }
  if (filter.assignee.trim().length > GITHUB_TEXT_MAX) {
    return `The assignee can have at most ${String(GITHUB_TEXT_MAX)} characters.`;
  }
  const active = filter.activeLabel.trim();
  if (active.includes(',')) return 'The label to add cannot contain a comma.';
  if (active !== '' && filter.labels.some((label) => label.toLowerCase() === active.toLowerCase())) {
    return 'The label to add must not be one of the labels to pick up.';
  }
  if (active.length > GITHUB_TEXT_MAX) {
    return `The label to add can have at most ${String(GITHUB_TEXT_MAX)} characters.`;
  }
  return null;
}

/** The first blocking reason, or `null` when a GitHub scan can start. */
export function githubProblem(status: GitHubStatus): string | null {
  return status.problem ?? null;
}

/** `GET /api/autoscan/github/status` as the wizard sees it. */
export type GitHubStatusView =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; status: GitHubStatus };

export interface AutoScanBlockerInput {
  setup: AutoScanSetup;
  prompt: string;
  sources: readonly AutoScanSource[];
  agents: readonly AutoScanAgentOption[];
  github: GitHubStatusView;
  hasLinearKey: boolean;
  /** Auto Scan's Antigravity additional directories, checked when Antigravity runs the scan. */
  antigravityAddDirs?: string;
}

/**
 * The first thing that keeps Start disabled, in the order the sections appear,
 * or `null` when the scan can start. Shown under the button, so a disabled
 * Start always says why.
 */
export function autoScanBlocker({
  setup,
  prompt,
  sources,
  agents,
  github,
  hasLinearKey,
  antigravityAddDirs = '',
}: AutoScanBlockerInput): string | null {
  const source = setup.source;
  if (source === null || !sources.includes(source)) return 'Choose where to find issues.';

  if (source === 'github') {
    if (github.kind === 'loading') return 'Checking GitHub on the PC…';
    if (github.kind === 'error') return 'Could not check GitHub on the PC. Check again.';
    if (githubProblem(github.status) !== null) {
      return 'Fix the GitHub problem above, then check again.';
    }
    const error = githubFilterError(setup.githubFilter);
    if (error !== null) return error;
  } else {
    if (!hasLinearKey) return 'Save a Linear API key for this workspace.';
    const error = filterError(setup.linearFilter);
    if (error !== null) return `${error}.`;
  }

  const cli = setup.cli;
  const agent = cli === null ? undefined : agents.find((entry) => entry.id === cli);
  if (agent === undefined) return 'Choose an AI agent.';
  if (!agent.available) return `${autoScanAgentName(agent.id)} is not installed on the PC.`;
  if (agent.id === 'antigravity') {
    const error = addDirsError(antigravityAddDirs);
    if (error !== null) return `Additional directories: ${error}.`;
  }
  if (prompt.length > INITIAL_PROMPT_MAX) return INSTRUCTIONS_TOO_LONG;
  return null;
}

const INSTRUCTIONS_TOO_LONG = 'The instructions can have at most 32,000 characters.';

/** Why a name in the GitHub filter cannot be saved, or `null`; `what` names the field. */
function githubNameError(what: string, name: string, commaAllowed: boolean): string | null {
  if (!commaAllowed && name.includes(',')) return `${what} cannot contain a comma.`;
  if (name.length > GITHUB_TEXT_MAX) {
    return `${what} can have at most ${String(GITHUB_TEXT_MAX)} characters.`;
  }
  return null;
}

/**
 * Why the wizard's choices cannot be saved as a template, or `null`: exactly
 * what the agent's draft models refuse with a 422. A template may hold a
 * half-made filter, so this is far looser than `autoScanBlocker` — and a label
 * to add that is also a trigger label is allowed here (the scan refuses it).
 */
export function autoScanTemplateSaveBlocker(input: {
  setup: AutoScanSetup;
  prompt: string;
  antigravityAddDirs: string;
}): string | null {
  const { githubFilter, cli } = input.setup;
  if (githubFilter.labels.length > GITHUB_LABELS_MAX) {
    return `Pick up at most ${String(GITHUB_LABELS_MAX)} labels.`;
  }
  for (const label of githubFilter.labels) {
    const error = githubNameError('A label to pick up', label.trim(), false);
    if (error !== null) return error;
  }
  const active = githubNameError('The label to add', githubFilter.activeLabel.trim(), false);
  if (active !== null) return active;
  if (githubFilter.issueTypes.length > GITHUB_ISSUE_TYPES_MAX) {
    return `Choose at most ${String(GITHUB_ISSUE_TYPES_MAX)} issue types.`;
  }
  for (const type of githubFilter.issueTypes) {
    const error = githubNameError('An issue type', type.trim(), true);
    if (error !== null) return error;
  }
  if (githubFilter.assignee.trim().length > GITHUB_TEXT_MAX) {
    return `The assignee can have at most ${String(GITHUB_TEXT_MAX)} characters.`;
  }
  if (input.prompt.length > INITIAL_PROMPT_MAX) return INSTRUCTIONS_TOO_LONG;
  if (cli === 'antigravity') {
    const error = addDirsError(input.antigravityAddDirs);
    if (error !== null) return `Additional directories: ${error}.`;
  }
  return null;
}

/** How templates compare names: ignoring case and runs of white space, as the agent does. */
export function templateNameKey(name: string): string {
  return name.split(/\s+/).filter((part) => part !== '').join(' ').toLocaleLowerCase();
}

/** A template's name as the agent stores it: trimmed, runs of white space as one space. */
export function cleanTemplateName(name: string): string {
  return name.split(/\s+/).filter((part) => part !== '').join(' ');
}

/** Why `name` cannot name a template, or `null`. */
export function templateNameError(name: string): string | null {
  const clean = cleanTemplateName(name);
  if (clean === '') return 'Type a name.';
  if (clean.length > TEMPLATE_NAME_MAX) {
    return `A name can have at most ${String(TEMPLATE_NAME_MAX)} characters.`;
  }
  return null;
}

/** A template's one-line summary in the picker: "GitHub · Codex". */
export function autoScanTemplateSummary(settings: {
  source?: string | null;
  cli?: string | null;
}): string {
  const source =
    settings.source === 'github' ? 'GitHub' : settings.source === 'linear' ? 'Linear' : null;
  const cli = settings.cli ?? null;
  const agent = cli === null ? null : autoScanAgentName(cli);
  return [source, agent].filter((part): part is string => part !== null).join(' · ');
}
