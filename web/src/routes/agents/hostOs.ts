/**
 * Where an agent's `.env` lives and how to restart it, per host OS.
 *
 * Byte-identical to Android `AgentCredentialsSheet.AgentHostOs` and iOS
 * `AgentCredentialsSheet.AgentHostOS`, including the order (Linux, macOS,
 * Windows) and the default. These are the `deploy/` install defaults; a
 * `--prefix` install differs, which the panel says out loud.
 *
 * Deliberately not auto-detected from the user agent, for the same reason the
 * install-commands sheet lists both one-liners: the agent host is a *different*
 * machine from the browser, so guessing from `navigator.userAgent` would be
 * wrong exactly as often as it is right.
 */
export interface AgentHostOs {
  id: 'linux' | 'macos' | 'windows';
  label: string;
  envPath: string;
  restartCommand: string;
}

export const AGENT_HOST_OSES: readonly AgentHostOs[] = [
  {
    id: 'linux',
    label: 'Linux',
    envPath: '/opt/mobaide/mobaide_agent/.env',
    restartCommand: 'sudo systemctl restart mobaide-agent',
  },
  {
    id: 'macos',
    label: 'macOS',
    envPath: '/usr/local/mobaide/mobaide_agent/.env',
    restartCommand:
      'launchctl unload ~/Library/LaunchAgents/com.mobaide.agent.plist && launchctl load ~/Library/LaunchAgents/com.mobaide.agent.plist',
  },
  {
    id: 'windows',
    label: 'Windows',
    envPath: String.raw`C:\Program Files\MobaideAgent\mobaide_agent\.env`,
    restartCommand: 'Stop-ScheduledTask MobaideAgent; Start-ScheduledTask MobaideAgent',
  },
];

export const DEFAULT_AGENT_HOST_OS = AGENT_HOST_OSES[0] as AgentHostOs;

/** The two lines the operator has to add to the agent's `.env`. */
export function envSnippet(agentId: string, token: string): string {
  return `RELAY_AGENT_ID=${agentId}\nRELAY_AGENT_TOKEN=${token}`;
}

export interface InstallCommand {
  label: string;
  command: string;
}

export const INSTALL_COMMANDS: readonly InstallCommand[] = [
  { label: 'Linux / macOS', command: 'curl -fsSL https://get.mobaide.com/install.sh | bash' },
  { label: 'Windows (PowerShell)', command: 'irm https://get.mobaide.com/install.ps1 | iex' },
];
