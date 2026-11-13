import { CheckboxField } from '../../../../components/form/CheckboxField';
import type { ChatSupport } from '../../../../protocol/dto/agentVersion';
import type { ChatAgentKind } from '../buildCreateRequest';

/** The chat view cannot start on this PC, so the agent opens in the terminal. */
export function chatBlocked(chat: ChatSupport | undefined): boolean {
  return chat?.kind === 'outdated' || chat?.kind === 'unavailable';
}

/** The hint under a "Terminal UI" toggle. */
export function terminalUiHint(chat: ChatSupport, agent: ChatAgentKind = 'claude'): string {
  if (chat.kind === 'outdated') return 'Update Mobaide on your PC to use the chat view.';
  if (chat.kind === 'unavailable') {
    switch (agent) {
      case 'antigravity':
        return 'The chat view needs a newer Antigravity CLI on your PC: run agy update there.';
      case 'opencode':
        return 'The chat view is not available for OpenCode on your PC.';
      case 'copilot':
        return 'The chat view is not available for GitHub Copilot on your PC.';
      case 'cursor':
        return 'The chat view is not available for Cursor on your PC.';
      default:
        return 'The chat view needs Node.js on your PC.';
    }
  }
  return 'Use the classic terminal instead of the chat view.';
}

interface TerminalUiFieldProps {
  chat: ChatSupport;
  agent: ChatAgentKind;
  checked: boolean;
  onChange: (value: boolean) => void;
}

/**
 * "Terminal UI" under an agent's Advanced options: the classic terminal
 * instead of the chat view. Locked on while the chat view cannot start.
 */
export function TerminalUiField({ chat, agent, checked, onChange }: TerminalUiFieldProps) {
  const blocked = chatBlocked(chat);
  return (
    <CheckboxField
      label="Terminal UI"
      checked={checked || blocked}
      disabled={blocked}
      hint={terminalUiHint(chat, agent)}
      onChange={onChange}
    />
  );
}
