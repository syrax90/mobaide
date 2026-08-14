import { useEffect, useId, useRef, useState } from 'react';
import { Link, useParams } from 'react-router';
import { writeClipboardText } from '../../lib/clipboard';
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
import { useToastStore } from '../../stores/toastStore';
import { useVisibleSessionStore } from '../../stores/visibleSessionStore';
import type { TerminalSessionModel } from '../../terminal/sessionModel';
import { stripControls } from '../../terminal/terminalPaste';
import { ConnectionOverlay, overlayMessage } from './ConnectionOverlay';
import { JumpToBottom } from './JumpToBottom';
import { TemplatesPicker } from './TemplatesPicker';
import { TerminalScrollbar } from './TerminalScrollbar';
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
  // Shared with the scrollbar, whose `role="scrollbar"` has to name what it
  // controls.
  const surfaceId = useId();

  const running = isSessionRunning(view.status);
  const overlay = overlayMessage(view);
  // `replayPainting` mirrors the model's own `pendingReplayWrites` term: after
  // `'e'` the connection flips live synchronously while the parser is still
  // consuming replayed history, so without it the screen would offer an enabled
  // send path that the model silently swallows. It is a time-superset of the
  // model's gate, so nothing that would have been allowed gets disabled.
  const canSend = view.connection === 'live' && !view.replayPainting && running;

  // Ctrl+R is a chord people press in a terminal on purpose, and a reload costs
  // the whole replay. See `useReloadGuard` for why this is capture-phase and
  // still lets a focused terminal receive `0x12`.
  useReloadGuard(true);
  useUnloadGuard(canSend);

  // The grid takes focus back on every dismissal path, and it has to happen
  // here rather than in the callbacks: `Modal` closes its `<dialog>` from a
  // layout-effect cleanup, and the native close algorithm hands focus to the
  // element that opened it — so a `focus()` called while dismissing is
  // overwritten a moment later. A passive effect runs after that cleanup.
  const templatesWereOpen = useRef(false);
  useEffect(() => {
    if (templatesWereOpen.current && !templatesOpen) model.focus();
    templatesWereOpen.current = templatesOpen;
  }, [templatesOpen, model]);

  // A clipboard permission prompt can hold the write promise open long enough
  // for the user to navigate away, and its continuation would then toast onto
  // whatever screen replaced this one — the same reason `copySelection` checks
  // `disposed`. Set on mount rather than only cleared on unmount, or
  // StrictMode's simulated unmount latches it `false` for the real mount.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

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
        <TerminalSurface model={model} id={surfaceId} />
        {/* Sits in the gutter FitAddon already reserves for a scrollbar, so it
            costs no columns — see `terminalScrollbar.ts`. */}
        <TerminalScrollbar model={model} surfaceId={surfaceId} />
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
      </div>

      {templatesOpen && (
        <TemplatesPicker
          canSend={canSend}
          onClose={() => {
            setTemplatesOpen(false);
          }}
          onPick={(template) => {
            // A refused insert keeps the picker open: the body stays on
            // screen for a retry instead of silently vanishing while the
            // recency stamp advertises success. The picker disables its rows
            // while the gate is shut and explains a refusal inline — a toast
            // would render below the dialog's top layer, unseen.
            if (!canSend) return false;
            // Paste, never execute: the body lands on the running program's
            // input line for review, mirroring the mobile clients' insert-
            // into-composer semantics.
            if (!model.pasteText(template.body)) return false;
            setTemplatesOpen(false);
            // The paste bypasses xterm's user-input scroll, so an insert made
            // while scrolled up would otherwise land off-screen.
            model.jumpToBottom();
            // Claude Code collapses a paste over 800 chars, or with more than
            // `min(rows - 10, 2)` newlines, to a `[Pasted text #n +m lines]`
            // placeholder and tells the user to paste again to expand it — an
            // instruction nothing could act on, because the pick never touched
            // the system clipboard.
            //
            // ESC and C1 come off first. `stripControls` is idempotent and the
            // newline pass needs an `\n`, so `sanitizePaste(stripControls(b))`
            // equals `sanitizePaste(b)` — a second paste re-runs this same
            // encoder and still converges on the byte-identical input the CLI's
            // equality check needs. What the strip buys is the consumer that
            // encoder can never reach: paste into a native terminal instead and
            // a live `ESC[201~` in the body closes the bracket early and runs
            // its tail (the CVE-2021-31701 class `terminalPaste` defends).
            //
            // Called without an await in front of it, so Safari and Firefox
            // still see the click's transient user activation. Only after
            // `pasteText` reported the frame sent — with nothing on the input
            // line there is nothing to expand, and clobbering the clipboard
            // would be pure cost.
            void writeClipboardText(stripControls(template.body)).then((written) => {
              if (!mounted.current) return;
              useToastStore
                .getState()
                .show(
                  // Claims only the half the client can verify. Delivery past
                  // the relay is not confirmable from here, and the Templates
                  // button is unconditional — this screen cannot know whether
                  // the session runs Claude Code or a plain shell, so it must
                  // not name either.
                  //
                  // The refusal names the *copy* as what failed. A bare
                  // "browser refused the clipboard write" reads as a failed
                  // pick, and the obvious recovery — pick again — appends a
                  // duplicate for any template the program did not collapse.
                  written
                    ? 'Template copied — paste again if it was collapsed.'
                    : 'Could not copy the template — the browser refused the clipboard write.',
                  written ? 'success' : 'error',
                );
            });
            return true;
          }}
        />
      )}
    </section>
  );
}
