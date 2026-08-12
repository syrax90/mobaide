import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';
import { sessionChannelName } from '../../realtime/channels';
import { useKeyboardLock } from '../../keyboard/useKeyboardLock';
import { useReloadGuard } from '../../keyboard/useReloadGuard';
import { useUnloadGuard } from '../../keyboard/useUnloadGuard';
import {
  acquireTerminal,
  clearMountedTerminal,
  setMountedTerminal,
  useTerminalStore,
} from '../../terminal/terminalStore';
import { isSessionRunning } from '../../protocol/dto/terminalState';
import { useTerminalPrefsStore } from '../../stores/terminalPrefsStore';
import { useVisibleSessionStore } from '../../stores/visibleSessionStore';
import type { TerminalSessionModel } from '../../terminal/sessionModel';
import { ConnectionOverlay, overlayMessage } from './ConnectionOverlay';
import { JumpToBottom } from './JumpToBottom';
import { SelectionActions } from './SelectionActions';
import { TemplatesPicker } from './TemplatesPicker';
import { TerminalSurface } from './TerminalSurface';
import { useSessionGone } from './useSessionGone';
import { useSessionHeaderLabel } from './useSessionHeaderLabel';

export function TerminalScreen() {
  const { agentId, sessionId } = useParams();

  // Split so everything below can take both ids as plain strings — the
  // session-gone check owns an agent channel keyed on the id, and an empty
  // string would open one for an agent that does not exist.
  if (agentId === undefined || sessionId === undefined) {
    return (
      <section className="page">
        <h1>Session not specified</h1>
        <Link to="/">Back to sessions</Link>
      </section>
    );
  }

  return <TerminalRoute agentId={agentId} sessionId={sessionId} />;
}

interface TerminalRouteProps {
  agentId: string;
  sessionId: string;
}

function TerminalRoute({ agentId, sessionId }: TerminalRouteProps) {
  useSessionGone(agentId, sessionId);
  const headerLabel = useSessionHeaderLabel(agentId, sessionId);
  const key = sessionChannelName(agentId, sessionId);
  const model = useTerminalStore((state) => state.models[key]);

  // Called unconditionally, not only when the model is missing. Two jobs:
  // it re-acquires after `stopAllRealtime()` disposes the model out from under
  // a screen that is still open (`channelRegistry.ts` documents that contract),
  // and it refreshes the model's LRU position on a revisit — without that the
  // resident set degrades from least-recently-used to creation-order, evicting
  // the session the user just came back to and charging them a fresh replay.
  // `acquireTerminal` is idempotent per key and returns the same instance, so
  // the selector below sees no change and this cannot loop.
  useEffect(() => {
    acquireTerminal(agentId, sessionId);
  }, [agentId, sessionId, model]);

  // Exempt this terminal from death-driven eviction while it is on screen, so a
  // session ending under the user leaves its output readable instead of being
  // disposed and re-acquired as an empty emulator.
  useEffect(() => {
    setMountedTerminal(key);
    return () => {
      clearMountedTerminal(key);
    };
  }, [key]);

  // The suppression input both mobile clients call a `ForegroundSessionTracker`:
  // an attention alert for the session already on screen is noise. Session id,
  // not the channel key — that is what the event carries.
  useEffect(() => {
    const { setVisibleSession, clearVisibleSession } = useVisibleSessionStore.getState();
    setVisibleSession(sessionId);
    return () => {
      clearVisibleSession(sessionId);
    };
  }, [sessionId]);

  if (model === undefined) {
    return <section className="terminal-page terminal-page-empty">Attaching…</section>;
  }

  return <TerminalView model={model} headerLabel={headerLabel} />;
}

interface TerminalViewProps {
  model: TerminalSessionModel;
  headerLabel: string;
}

function TerminalView({ model, headerLabel }: TerminalViewProps) {
  const view = model.state();
  const passCtrlV = useTerminalPrefsStore((state) => state.passCtrlVToTerminal);
  const setPassCtrlV = useTerminalPrefsStore((state) => state.setPassCtrlVToTerminal);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const immersive = useKeyboardLock();

  const running = isSessionRunning(view.status);
  // `replayPainting` mirrors the model's own `pendingReplayWrites` term: after
  // `'e'` the connection flips live synchronously while the parser is still
  // consuming replayed history, so without it the screen would offer enabled
  // selection actions that the model silently swallows. It is a time-superset
  // of the model's gate, so nothing that would have been allowed gets disabled.
  const live = view.connection === 'live' && !view.replayPainting;
  const overlay = overlayMessage(view);
  const canSend = live && running;

  // Ctrl+R is a chord people press in a terminal on purpose, and a reload costs
  // the whole replay. See `useReloadGuard` for why this is capture-phase and
  // still lets a focused terminal receive `0x12`.
  useReloadGuard(true);
  useUnloadGuard(canSend);

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
        {headerLabel !== '' && <span className="terminal-title">{headerLabel}</span>}
        <span className="terminal-bar-spacer" />
        <label className="terminal-toggle" title="Pass Ctrl+V to terminal">
          <input
            type="checkbox"
            aria-label="Pass Ctrl+V to terminal"
            checked={passCtrlV}
            onChange={(event) => {
              setPassCtrlV(event.target.checked);
            }}
          />
          {/* aria-label pins the accessible name, so the visible text can
              shrink to "Ctrl+V" on narrow screens without renaming the
              control for a screen reader. */}
          <span className="terminal-toggle-label" aria-hidden="true">
            <span className="terminal-toggle-label-full">Pass Ctrl+V to terminal</span>
            <span className="terminal-toggle-label-short">Ctrl+V</span>
          </span>
        </label>
        <button
          type="button"
          className="button"
          title="Insert a prompt template at the cursor"
          onClick={() => {
            setTemplatesOpen(true);
          }}
        >
          Templates
        </button>
        {immersive.supported && (
          <button
            type="button"
            className="button"
            aria-pressed={immersive.active}
            // Chromium-only; the permission prompt Chrome announced for 131 was
            // never shipped (see `keyboard/useKeyboardLock.ts`). It is the only
            // way Ctrl+W, Ctrl+T and F11 ever reach the terminal, so it is
            // opt-in per session rather than a persisted preference.
            title="Capture every key, including the ones the browser normally takes"
            onClick={immersive.active ? immersive.exit : immersive.enter}
          >
            {immersive.active ? 'Leave immersive' : 'Immersive'}
          </button>
        )}
      </header>

      <div className="terminal-stage">
        <TerminalSurface model={model} />
        {/* Overlaid, never a flex sibling: an in-flow error would change the
            stage height, and a terminal row-count change clears the selection. */}
        {immersive.error !== null && <p className="terminal-error">{immersive.error}</p>}
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
        {/*
          Inside the stage, floating, alongside the other two overlays. As a
          flex sibling it stole ~3 rows from the terminal the moment a selection
          existed, and xterm clears the selection on any row-count change — so
          the bar that appears because there is a selection destroyed it, then
          unmounted itself and gave the rows back.
        */}
        {view.hasSelection && (
          <SelectionActions model={model} canRun={live} canPaste={canSend} />
        )}
      </div>

      {templatesOpen && (
        <TemplatesPicker
          onClose={() => {
            setTemplatesOpen(false);
            model.focus();
          }}
          onPick={(template) => {
            // Paste, never execute: the body lands on the running program's
            // input line for review, mirroring the mobile clients' insert-
            // into-composer semantics.
            model.pasteText(template.body);
            setTemplatesOpen(false);
            model.focus();
          }}
        />
      )}
    </section>
  );
}
