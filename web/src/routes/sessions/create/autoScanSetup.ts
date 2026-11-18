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
import type { AutoScanSetup, AutoScanSource, GitHubFilterDraft } from './wizardState';

/** The agent's limits on `github_filter` (`GitHubScanFilterModel`). */
export const GITHUB_LABELS_MAX = 20;
export const GITHUB_TEXT_MAX = 100;
/** `StartScanRequest.initial_prompt`'s `max_length`. */
export const INITIAL_PROMPT_MAX = 32_000;

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

/**
 * Why `label` cannot be added to the trigger labels, or `null`. `gh` splits an
 * option value on commas, so a comma would silently become two labels.
 */
export function githubLabelError(label: string, existing: readonly string[]): string | null {
  const name = label.trim();
  if (name === '') return 'Type a label name.';
  if (name.includes(',')) return 'A label name cannot contain a comma.';
  if (name.length > GITHUB_TEXT_MAX) {
    return `A label name can have at most ${String(GITHUB_TEXT_MAX)} characters.`;
  }
  if (existing.includes(name)) return 'This label is already in the list.';
  if (existing.length >= GITHUB_LABELS_MAX) {
    return `You can pick up at most ${String(GITHUB_LABELS_MAX)} labels.`;
  }
  return null;
}

export function githubFilterError(filter: GitHubFilterDraft): string | null {
  if (filter.labels.length === 0) return 'Choose at least one label to pick up.';
  if (filter.labels.length > GITHUB_LABELS_MAX) {
    return `Pick up at most ${String(GITHUB_LABELS_MAX)} labels.`;
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
  if (prompt.length > INITIAL_PROMPT_MAX) {
    return 'The initial prompt can have at most 32,000 characters.';
  }
  return null;
}
