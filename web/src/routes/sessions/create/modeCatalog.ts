/**
 * The mode picker's cards: titles, subtitles, and what disables each one.
 *
 * Copy is ported verbatim from `CreateSessionScreen.kt` / `.swift` so the three
 * clients read identically.
 */
import { cliAvailable, type AgentVersionInfo } from '../../../protocol/dto/agentVersion';
import type { ProjectSummary } from '../../../api/projectRepository';
import type { CustomAgentType } from '../../../api/customAgentTypeRepository';
import { BUILT_IN_MODES, type BuiltInMode, type WizardMode } from './wizardState';

export interface ModeCard {
  mode: WizardMode;
  title: string;
  subtitle: string;
  /** Monospaced, one line — the custom type's command. */
  command?: string;
  enabled: boolean;
  /** Rendered in warning yellow when the card is disabled. */
  disabledReason?: string;
}

const BUILT_IN_COPY: Readonly<Record<BuiltInMode, { title: string; subtitle: string }>> = {
  claude: { title: 'Claude Code', subtitle: 'Configure and launch a Claude session.' },
  codex: { title: 'Codex', subtitle: 'Configure and launch an OpenAI Codex session.' },
  antigravity: {
    title: 'Antigravity',
    subtitle: 'Configure and launch a Google Antigravity session.',
  },
  auto_scan: {
    title: 'Auto Scan',
    subtitle: 'Scan Linear for matching issues and launch sessions.',
  },
  shell: { title: 'Terminal', subtitle: 'Bash / cmd in the chosen directory' },
};

const MISSING_CLI: Readonly<Record<string, string>> = {
  claude: 'Claude CLI is not installed on the agent.',
  codex: 'Codex CLI is not installed on the agent.',
  antigravity: 'Antigravity CLI (agy) is not installed on the agent.',
};

/**
 * Only an explicit `false` disables.
 *
 * An absent probe, an unreachable agent or a capability block a newer agent
 * reshaped all read as "unknown", and unknown must stay launchable: greying out
 * a mode that would have worked is worse than surfacing the agent's own error.
 */
function cliEnabled(capabilities: AgentVersionInfo | undefined, cliId: string): boolean {
  return cliAvailable(capabilities, cliId) !== false;
}

export function buildModeCards(input: {
  capabilities: AgentVersionInfo | undefined;
  project: ProjectSummary | null;
  customTypes: readonly CustomAgentType[];
}): ModeCard[] {
  const claudeEnabled = cliEnabled(input.capabilities, 'claude');

  const builtIns = BUILT_IN_MODES.map((mode): ModeCard => {
    const copy = BUILT_IN_COPY[mode];
    switch (mode) {
      case 'claude':
      case 'codex':
      case 'antigravity': {
        const enabled = cliEnabled(input.capabilities, mode);
        return {
          mode,
          ...copy,
          enabled,
          disabledReason: enabled ? undefined : MISSING_CLI[mode],
        };
      }
      case 'auto_scan': {
        // Auto Scan drives Claude against Linear issues, so it needs both.
        const hasLinearKey = input.project?.hasLinearKey === true;
        const enabled = claudeEnabled && hasLinearKey;
        return {
          mode,
          ...copy,
          enabled,
          disabledReason: enabled
            ? undefined
            : claudeEnabled
              ? 'Auto Scan needs a Linear API key for this project.'
              : "Auto Scan needs the Claude CLI, which isn't installed on the agent.",
        };
      }
      default:
        // A shell always exists; nothing to probe.
        return { mode, ...copy, enabled: true };
    }
  });

  const custom = input.customTypes.map(
    (type): ModeCard => ({
      mode: `custom_${type.id}`,
      title: type.name,
      subtitle: type.command,
      command: type.command,
      enabled: true,
    }),
  );

  return [...builtIns, ...custom];
}
