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

  // Called unconditionally, not only when the model is missing. Two jobs:
  // it re-acquires after `stopAllRealtime()` disposes the model out from under
  // a screen that is still open (`channelRegistry.ts` documents that contract),
  // and it refreshes the model's LRU position on a revisit — without that the
  // resident set degrades from least-recently-used to creation-order, evicting
  // the session the user just came back to and charging them a fresh replay.
  // `acquireTerminal` is idempotent per key and returns the same instance, so
  // the selector below sees no change and this cannot loop.
  useEffect(() => {
    if (agentId === undefined || sessionId === undefined) return;
    acquireTerminal(agentId, sessionId);
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
  // `replayPainting` mirrors the model's own `pendingReplayWrites` term: after
  // `'e'` the connection flips live synchronously while the parser is still
  // consuming replayed history, so without it the screen would offer an enabled
  // Send that `submitComposer()` silently swallows. It is a time-superset of
  // the model's gate, so nothing that would have been allowed gets disabled.
  const live = view.connection === 'live' && !view.replayPainting;
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
        <ConnectionOverlay
          message={overlay}
          onRetry={
            view.channelStopped
              ? () => {
                  model.retryConnection();
                }
              : undefined
          }
        />
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
