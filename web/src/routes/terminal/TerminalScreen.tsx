import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';
import { sessionChannelName } from '../../realtime/channels';
import { acquireTerminal, useTerminalStore } from '../../terminal/terminalStore';
import { isSessionRunning } from '../../protocol/dto/terminalState';
import { useTerminalPrefsStore } from '../../stores/terminalPrefsStore';
import type { TerminalSessionModel } from '../../terminal/sessionModel';
import { Composer } from './Composer';
import { ConnectionOverlay, overlayMessage } from './ConnectionOverlay';
import { JumpToBottom } from './JumpToBottom';
import { SelectionActions } from './SelectionActions';
import { TemplatesPicker } from './TemplatesPicker';
import { TerminalSurface } from './TerminalSurface';

export function TerminalScreen() {
  const { agentId, sessionId } = useParams();
  const key =
    agentId === undefined || sessionId === undefined
      ? null
      : sessionChannelName(agentId, sessionId);
  const model = useTerminalStore((state) =>
    key === null ? undefined : state.models[key],
  );

  // Re-acquires whenever the model goes missing, not only on mount:
  // `stopAllRealtime()` runs on every session change and disposes the model out
  // from under a screen that is still open (`channelRegistry.ts` documents the
  // contract for exactly this reason).
  useEffect(() => {
    if (agentId === undefined || sessionId === undefined) return;
    if (model === undefined) acquireTerminal(agentId, sessionId);
  }, [agentId, sessionId, model]);

  if (agentId === undefined || sessionId === undefined) {
    return (
      <section className="page">
        <h1>Session not specified</h1>
        <Link to="/">Back home</Link>
      </section>
    );
  }

  if (model === undefined) {
    return <section className="terminal-page terminal-page-empty">Attaching…</section>;
  }

  return <TerminalView model={model} />;
}

interface TerminalViewProps {
  model: TerminalSessionModel;
}

function TerminalView({ model }: TerminalViewProps) {
  const view = model.state();
  const passCtrlV = useTerminalPrefsStore((state) => state.passCtrlVToTerminal);
  const setPassCtrlV = useTerminalPrefsStore((state) => state.setPassCtrlVToTerminal);
  const [templatesOpen, setTemplatesOpen] = useState(false);

  const running = isSessionRunning(view.status);
  const live = view.connection === 'live';
  const overlay = overlayMessage(view);

  return (
    <section className="terminal-page">
      <header className="terminal-bar">
        <Link to="/" className="terminal-back">
          ← Sessions
        </Link>
        <span
          className={`terminal-state terminal-state-${view.status?.status ?? 'unknown'}`}
        >
          {view.status?.status ?? 'connecting'}
        </span>
        <span className="terminal-session-id">{model.sessionId}</span>
        <span className="terminal-bar-spacer" />
        <label className="terminal-toggle">
          <input
            type="checkbox"
            checked={passCtrlV}
            onChange={(event) => {
              setPassCtrlV(event.target.checked);
            }}
          />
          Pass Ctrl+V to terminal
        </label>
        <button
          type="button"
          className="button"
          aria-pressed={view.composerVisible}
          onClick={() => {
            model.setComposerVisible(!view.composerVisible);
          }}
        >
          {view.composerVisible ? 'Hide composer' : 'Composer'}
        </button>
      </header>

      <div className="terminal-stage">
        <TerminalSurface model={model} />
        <ConnectionOverlay message={overlay} />
        {!view.pinned && (
          <JumpToBottom
            unread={view.unread}
            onJump={() => {
              model.jumpToBottom();
            }}
          />
        )}
      </div>

      {view.hasSelection && (
        <SelectionActions model={model} canRun={live} />
      )}

      {view.composerVisible && (
        <Composer
          model={model}
          value={view.composer}
          canSend={live && running}
          onOpenTemplates={() => {
            setTemplatesOpen(true);
          }}
        />
      )}

      {templatesOpen && (
        <TemplatesPicker
          onClose={() => {
            setTemplatesOpen(false);
          }}
          onPick={(template) => {
            model.setComposer(template.body);
            model.setComposerVisible(true);
            setTemplatesOpen(false);
          }}
        />
      )}
    </section>
  );
}
