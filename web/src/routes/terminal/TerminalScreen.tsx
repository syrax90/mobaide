import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router';
import { sessionChannelName } from '../../realtime/channels';
import { appShortcutFor } from '../../keyboard/appShortcuts';
import { currentKeyboardEnv } from '../../keyboard/browserEnv';
import { useAppShortcuts } from '../../keyboard/useAppShortcuts';
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
import { CommandPalette } from './CommandPalette';
import { Composer } from './Composer';
import { ConnectionOverlay, overlayMessage } from './ConnectionOverlay';
import { JumpToBottom } from './JumpToBottom';
import { SelectionActions } from './SelectionActions';
import { ShortcutBar } from './ShortcutBar';
import { ShortcutPicker } from './ShortcutPicker';
import { TemplatesPicker } from './TemplatesPicker';
import { TerminalSurface } from './TerminalSurface';
import { useSessionGone } from './useSessionGone';
import { useSessionHeaderLabel } from './useSessionHeaderLabel';
import { useSessionKind } from './useSessionKind';

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
  const kind = useSessionKind(agentId, sessionId);
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

  return <TerminalView model={model} kind={kind} headerLabel={headerLabel} />;
}

interface TerminalViewProps {
  model: TerminalSessionModel;
  kind: string | null;
  headerLabel: string;
}

function TerminalView({ model, kind, headerLabel }: TerminalViewProps) {
  const view = model.state();
  const passCtrlV = useTerminalPrefsStore((state) => state.passCtrlVToTerminal);
  const setPassCtrlV = useTerminalPrefsStore((state) => state.setPassCtrlVToTerminal);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  // Bumped on every template pick so the composer can take focus and select
  // the inserted body; it is already mounted, so `autoFocus` cannot help.
  const [insertToken, setInsertToken] = useState(0);
  const immersive = useKeyboardLock();

  const running = isSessionRunning(view.status);
  // `replayPainting` mirrors the model's own `pendingReplayWrites` term: after
  // `'e'` the connection flips live synchronously while the parser is still
  // consuming replayed history, so without it the screen would offer an enabled
  // Send that `submitComposer()` silently swallows. It is a time-superset of
  // the model's gate, so nothing that would have been allowed gets disabled.
  const live = view.connection === 'live' && !view.replayPainting;
  const overlay = overlayMessage(view);
  const canSend = live && running;

  // Ctrl+R is a chord people press in a terminal on purpose, and a reload costs
  // the whole replay. See `useReloadGuard` for why this is capture-phase and
  // still lets a focused terminal receive `0x12`.
  useReloadGuard(true);
  useUnloadGuard(canSend);

  const openPalette = useCallback(() => {
    setPaletteOpen(true);
  }, []);
  // Released while the chord recorder is open: both are document-capture
  // listeners, so the app would otherwise open the palette *and* record
  // `ctrl+shift+p` on the same keypress.
  useAppShortcuts(openPalette, !shortcutsOpen);

  // Plain setters: restoring focus after a dialog closes belongs to the dialog,
  // which knows whether it just handed focus to the composer.
  const closePalette = useCallback(() => {
    setPaletteOpen(false);
  }, []);
  const closeShortcuts = useCallback(() => {
    setShortcutsOpen(false);
  }, []);
  const insertTemplate = useCallback(
    (body: string) => {
      model.setComposer(body);
      model.setComposerVisible(true);
      setInsertToken((token) => token + 1);
    },
    [model],
  );

  // Memoised like the two sibling call sites: `TerminalView` re-renders once per
  // inbound PTY frame while the viewport is scrolled up, and `currentKeyboardEnv`
  // costs two `matchMedia` evaluations. The OS cannot change for the life of the
  // page.
  const keyboardEnv = useMemo(currentKeyboardEnv, []);
  const paletteHint = useMemo(
    () => appShortcutFor('commandPalette')?.hint(keyboardEnv.os),
    [keyboardEnv],
  );

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
            checked={passCtrlV}
            onChange={(event) => {
              setPassCtrlV(event.target.checked);
            }}
          />
          <span className="terminal-toggle-label">Pass Ctrl+V to terminal</span>
        </label>
        <button
          type="button"
          className="button"
          title={paletteHint === undefined ? undefined : `Command palette (${paletteHint})`}
          onClick={openPalette}
        >
          Commands
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
        {view.hasSelection && <SelectionActions model={model} canRun={live} />}
      </div>

      <ShortcutBar
        model={model}
        kind={kind}
        canSend={canSend}
        onOpenComposer={() => {
          setShortcutsOpen(true);
        }}
      />

      {view.composerVisible && (
        <Composer
          model={model}
          value={view.composer}
          canSend={canSend}
          insertToken={insertToken}
          onOpenTemplates={() => {
            setTemplatesOpen(true);
          }}
        />
      )}

      {shortcutsOpen && (
        <ShortcutPicker
          model={model}
          kind={kind}
          canSend={canSend}
          onClose={closeShortcuts}
        />
      )}

      {paletteOpen && (
        <CommandPalette
          model={model}
          kind={kind}
          canSend={canSend}
          onInsertTemplate={insertTemplate}
          onClose={closePalette}
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
            setInsertToken((token) => token + 1);
          }}
        />
      )}
    </section>
  );
}
