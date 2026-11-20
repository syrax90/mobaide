import { useId, type ReactNode } from 'react';

import type { ChatSupport } from '../../../../protocol/dto/agentVersion';
import type { ChatAgentKind } from '../buildCreateRequest';

/** The chat view cannot start on this PC, so the agent opens in the terminal. */
export function chatBlocked(chat: ChatSupport | undefined): boolean {
  return chat?.kind === 'outdated' || chat?.kind === 'unavailable';
}

/** Why the chat view cannot start on this PC, shown under "Open sessions in". */
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
      case 'junie':
        return 'The chat view is not available for Junie on your PC.';
      case 'pi':
        return 'The chat view is not available for Pi on your PC.';
      default:
        return 'The chat view needs Node.js on your PC.';
    }
  }
  return 'Use the classic terminal instead of the chat view.';
}

export type SessionView = 'chat' | 'terminal';

const SESSION_VIEW_CHOICES: readonly { value: SessionView; title: string; hint: string }[] = [
  { value: 'chat', title: 'Chat', hint: 'Messages, tool steps and approvals in the app.' },
  { value: 'terminal', title: 'Terminal', hint: "The agent's own terminal app." },
];

interface SessionViewChoiceProps {
  /** The view shown as chosen. */
  view: SessionView;
  /** False while the PC has no chat view for the agent: Chat is disabled. */
  chatEnabled: boolean;
  onChange: (view: SessionView) => void;
  /** Hints under the choices, for example why Chat is disabled. */
  children?: ReactNode;
}

/** "Open sessions in": Chat or Terminal, for a new session or an Auto Scan. */
export function SessionViewChoice({ view, chatEnabled, onChange, children }: SessionViewChoiceProps) {
  const name = useId();
  return (
    <fieldset className="autoscan-choices">
      <legend className="field-label">Open sessions in</legend>
      {SESSION_VIEW_CHOICES.map((choice) => {
        const disabled = choice.value === 'chat' && !chatEnabled;
        return (
          <label
            key={choice.value}
            className={`autoscan-choice${disabled ? ' autoscan-choice-disabled' : ''}`}
          >
            <input
              type="radio"
              name={name}
              value={choice.value}
              checked={view === choice.value}
              disabled={disabled}
              onChange={() => {
                onChange(choice.value);
              }}
            />
            <span className="autoscan-choice-text">
              <strong>{choice.title}</strong>
              <small>{choice.hint}</small>
            </span>
          </label>
        );
      })}
      {children}
    </fieldset>
  );
}

interface SessionViewFieldProps {
  chat: ChatSupport;
  agent: ChatAgentKind;
  terminalUi: boolean;
  onChange: (terminalUi: boolean) => void;
}

/**
 * "Open sessions in" for a new agent session. Terminal is held while the chat
 * view cannot start on this PC.
 */
export function SessionViewField({ chat, agent, terminalUi, onChange }: SessionViewFieldProps) {
  const blocked = chatBlocked(chat);
  return (
    <div className="option-group">
      <SessionViewChoice
        view={terminalUi || blocked ? 'terminal' : 'chat'}
        chatEnabled={!blocked}
        onChange={(view) => {
          onChange(view === 'terminal');
        }}
      >
        {blocked && <p className="field-hint">{terminalUiHint(chat, agent)}</p>}
      </SessionViewChoice>
    </div>
  );
}
