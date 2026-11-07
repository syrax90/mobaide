import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { createTemplate } from '../../api/templateRepository';
import { isApiError } from '../../api/errors';
import { MenuButton } from '../../components/MenuButton';
import { writeClipboardText } from '../../lib/clipboard';
import { queryKeys } from '../../lib/queryKeys';
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
import type { SessionResponse } from '../../protocol/dto/session';
import { isSessionRunning } from '../../protocol/dto/terminalState';
import { useSessionsPanelStore } from '../../stores/sessionsPanelStore';
import { peekStopHandOff, useStopHandOffStore } from '../../stores/stopHandOffStore';
import { useTerminalPrefsStore } from '../../stores/terminalPrefsStore';
import { useToastStore } from '../../stores/toastStore';
import { useVisibleSessionStore } from '../../stores/visibleSessionStore';
import type { TerminalSessionModel } from '../../terminal/sessionModel';
import { stripControls } from '../../terminal/terminalPaste';
import { TemplateEditorDialog } from '../templates/TemplateEditorDialog';
import { ConnectionOverlay, overlayMessage } from './ConnectionOverlay';
import { GitChangesPanel } from './git/GitChangesPanel';
import { NO_GIT_CAPABILITIES, hasGitSync } from '../../protocol/dto/agentVersion';
import { useSessionGitCapabilities } from './git/useSessionGit';
import { JumpToBottom } from './JumpToBottom';
import { SessionsPanel } from './SessionsPanel';
import { TemplatesPicker } from './TemplatesPicker';
import { TerminalScrollbar } from './TerminalScrollbar';
import { TerminalSurface } from './TerminalSurface';
import { useSessionGone } from './useSessionGone';
import { useSessionHeaderLabel } from './useSessionHeaderLabel';
import { useSessionRow } from './useSessionRow';
import { DictationDialog } from './voice/DictationDialog';
import { VoiceButton } from './voice/VoiceButton';
import { VoiceSetupDialog } from './voice/VoiceSetupDialog';
import { spliceTranscript, useAgentName, useDictation } from '../../voice/useDictation';
import type { DictationState } from '../../voice/dictationController';
import {
  useVoiceCapability,
  useVoiceCleanupCapability,
  useVoiceStatus,
} from '../../voice/useVoiceStatus';
import {
  useCleanup,
  useMicHidden,
  useSkipReview,
  useVoicePrefsStore,
} from '../../voice/voicePrefsStore';
import { voiceMicMode } from '../../voice/voiceStatusView';
import { useWideViewport } from './useWideViewport';

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
  const session = useSessionRow(agentId, sessionId);
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

  // Keyed on the channel: the sessions panel swaps the route in place, and the
  // view's dialog state and git request counters belong to one session.
  return <TerminalView key={key} model={model} headerLabel={headerLabel} session={session} />;
}

/**
 * Which template dialog is up, if any.
 *
 * A union rather than two booleans, because the picker and the editor must
 * never be mounted at once: `TemplatesPicker` hangs its key handler on the
 * ancestor of everything it renders, and a `<dialog>` on the top layer still
 * propagates events through its DOM parent — so an editor nested inside would
 * lose its textarea's arrow keys to the row walk and its draft to the picker's
 * Escape.
 */
type TemplatePanel =
  | { kind: 'none' }
  /**
   * `selection` is the terminal selection as it stood when the picker opened,
   * snapshotted there rather than read when the editor opens. Blur is not the
   * risk — xterm keeps the selection in its own model, not the document's — but
   * PTY output keeps arriving behind the modal, and a mouse-protocol change,
   * an alternate-screen switch, a row-count change and the replay wipe each
   * clear it. Nothing is traded away for the snapshot: the grid is inert while
   * the dialog is up, so the user cannot change the selection after it.
   */
  | { kind: 'picker'; selection: string }
  | { kind: 'editor'; initialBody: string };

interface TerminalViewProps {
  model: TerminalSessionModel;
  headerLabel: string;
  /** The session's own row, once the list has synced; `cwd` gates the git menu. */
  session: SessionResponse | undefined;
}

function TerminalView({ model, headerLabel, session }: TerminalViewProps) {
  const view = model.state();
  const passCtrlV = useTerminalPrefsStore((state) => state.passCtrlVToTerminal);
  const setPassCtrlV = useTerminalPrefsStore((state) => state.setPassCtrlVToTerminal);
  const queryClient = useQueryClient();
  const [panel, setPanel] = useState<TemplatePanel>({ kind: 'none' });
  const [saveError, setSaveError] = useState<string | null>(null);
  // Separate from `panel`: the Changes viewer is non-modal and may stay open
  // under the template dialogs, and it returns focus to the menu that opened
  // it rather than to the grid.
  const [changesOpen, setChangesOpen] = useState(false);
  // Read once, then cleared: this visit took over from a session stopped in the
  // panel, which stays open and finishes the switch's motion.
  const [handOff] = useState(() => peekStopHandOff(model.agentId, model.sessionId));
  useEffect(() => {
    if (handOff !== null) useStopHandOffStore.getState().clearHandOff(handOff);
  }, [handOff]);
  // The sessions panel: `open` is per visit, `pinned` persists. Docked beside
  // the grid only when pinned on a wide viewport; otherwise it slides over.
  const [sessionsOpen, setSessionsOpen] = useState(handOff?.overlayOpen === true);
  // An overlay carried over from the previous terminal is already in place and
  // must not slide in again; any later opening does.
  const [overlayCarried, setOverlayCarried] = useState(handOff?.overlayOpen === true);
  useEffect(() => {
    if (!sessionsOpen) setOverlayCarried(false);
  }, [sessionsOpen]);
  const pinned = useSessionsPanelStore((state) => state.pinned);
  const setPinned = useSessionsPanelStore((state) => state.setPinned);
  const wide = useWideViewport();
  const docked = pinned && wide;
  const panelButtonRef = useRef<HTMLButtonElement>(null);
  // A counter, not a flag: the panel may already be open when *Commit…* is
  // picked again, and only a new value re-opens its dialog.
  const [commitRequest, setCommitRequest] = useState(0);
  const [syncRequest, setSyncRequest] = useState(0);
  const [rebaseRequest, setRebaseRequest] = useState(0);
  const moreRef = useRef<HTMLButtonElement>(null);
  // The menu's *Commit…* entry exists only on an agent that advertises
  // `capabilities.git.commit`, *Fetch / Pull / Push…* on any of the three
  // job keys; the panel gates its own controls the same way.
  const gitCapabilities =
    useSessionGitCapabilities(model.agentId, session?.cwd !== undefined).data ??
    NO_GIT_CAPABILITIES;
  const canCommit = gitCapabilities.commit;
  const canSync = hasGitSync(gitCapabilities);
  // *Branches…* is a link to the project's Git screen, so it needs a
  // project-backed session on top of the capability.
  const branchesProject =
    gitCapabilities.branches && session?.project_name !== undefined
      ? session.project_name
      : null;
  // Voice input: the mic shows only on an agent that advertises
  // `capabilities.voice`, styled by its live status (`voice.changed` arrives
  // on the agent channel `useSessionGone` already holds open).
  const voiceCapability = useVoiceCapability(model.agentId).data ?? null;
  const voiceStatus = useVoiceStatus(model.agentId, voiceCapability !== null);
  const micHidden = useMicHidden(model.agentId);
  const micMode = voiceMicMode(voiceCapability, voiceStatus.data, micHidden);
  // Transcript cleanup runs the session's AI agent on the agent's computer;
  // only Claude Code can do it so far, and there the CLI is known to be there.
  const cleanupAvailable =
    useVoiceCleanupCapability(model.agentId).data === true && session?.kind === 'claude';
  const cleanUp = useCleanup(model.agentId);
  const setCleanUp = useVoicePrefsStore((state) => state.setCleanup);
  // A capability only while voice is ready: any other state disposes the
  // controller, which cancels a recording the agent can no longer take.
  const dictation = useDictation(
    model.agentId,
    model.sessionId,
    micMode === 'ready' ? voiceCapability : null,
    cleanupAvailable && cleanUp,
  );
  const [voiceSetupOpen, setVoiceSetupOpen] = useState(false);
  const agentName = useAgentName(model.agentId);
  const navigate = useNavigate();
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
  // A transcript can only be pasted into a running session. An unknown status
  // (still connecting) or a reconnect keeps recording available.
  const canRecord = view.status === null || running;

  // Pasted like a template — never with Enter — so the text waits on the
  // program's input line for the user to send.
  const { carried, finish: finishDictation } = dictation;
  const insertTranscript = useCallback(
    (text: string): boolean => {
      if (!canSend || !model.pasteText(text)) return false;
      model.jumpToBottom();
      finishDictation();
      return true;
    },
    [canSend, model, finishDictation],
  );
  // With the review turned off a result goes straight to the prompt; only a
  // paste the session refuses falls back to showing it for review. Decided
  // once per result: turning the review off from the review itself must not
  // paste the text under the user's edits. A failed cleanup is always shown,
  // so the user learns why the text was not cleaned up.
  const skipReview = useSkipReview(model.agentId);
  const setSkipReview = useVoicePrefsStore((state) => state.setSkipReview);
  const result = dictation.state.kind === 'result' ? dictation.state : null;
  const insertDirectly = skipReview && result?.cleanupError === undefined;
  const [reviewed, setReviewed] = useState<DictationState | null>(null);
  const autoInserting = insertDirectly && result !== null && result !== reviewed;
  // A layout effect, so a result never paints as a dialog before it lands.
  useLayoutEffect(() => {
    if (result === null || result === reviewed) return;
    const text = carried === null ? result.text : spliceTranscript(carried, result.text).text;
    if (insertDirectly && insertTranscript(text)) return;
    setReviewed(result);
  }, [result, reviewed, insertDirectly, carried, insertTranscript]);
  const dictationShown = (dictation.state.kind !== 'idle' || carried !== null) && !autoInserting;

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
  //
  // Keyed on whether *any* panel is open rather than on the picker alone, so
  // the picker-to-editor hop is not mistaken for a dismissal and does not pull
  // focus out of the editor it just opened.
  const panelOpen =
    panel.kind !== 'none' || voiceSetupOpen || dictationShown;
  const panelWasOpen = useRef(false);
  useEffect(() => {
    if (panelWasOpen.current && !panelOpen) model.focus();
    panelWasOpen.current = panelOpen;
  }, [panelOpen, model]);

  const save = useMutation({
    mutationFn: ({ name, body }: { name: string; body: string }) =>
      createTemplate(name, body),
    onSuccess: async (_data, variables) => {
      // Closed first, then announced: `.toast-viewport` paints below the top
      // layer, so a toast raised with the editor still open cannot be seen.
      // Both land in one commit, so there is no frame showing neither.
      //
      // Deliberately without the `mounted` guard the clipboard toast below
      // carries. That one reports a local side effect belonging to a screen the
      // user has left; this one reports durable account state that stays true
      // wherever they went.
      setPanel({ kind: 'none' });
      setSaveError(null);
      useToastStore.getState().show(`Saved template "${variables.name}"`, 'success');
      await queryClient.invalidateQueries({ queryKey: queryKeys.templates() });
    },
    // Inline, never a toast: this renders while the editor is still up, which
    // is exactly where a toast would be invisible.
    onError: (error: unknown) => {
      setSaveError(
        isApiError(error)
          ? `Failed to save template: ${error.message}`
          : 'Failed to save template',
      );
    },
  });

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

  const closeSessions = () => {
    setSessionsOpen(false);
    panelButtonRef.current?.focus();
  };

  const sessionsPanel = (
    <SessionsPanel
      agentId={model.agentId}
      sessionId={model.sessionId}
      docked={docked}
      pinned={pinned}
      onPinnedChange={(next) => {
        setPinned(next);
        // Unpinning on a wide screen turns the rail into an overlay; keep it
        // on screen rather than making it vanish under the click.
        if (!next && wide) setSessionsOpen(true);
      }}
      onClose={closeSessions}
      handOff={handOff}
    />
  );

  return (
    <>
      {/* Beside `.terminal-page`, never inside it: that one is a column, and a
          child there would stack under the grid. `.app-main` lays the two out
          in a row, and the narrower stage refits the grid once. */}
      {docked && <aside className="sessions-panel sessions-panel-docked">{sessionsPanel}</aside>}
      {sessionsOpen && !docked && (
        <SessionsPanelOverlay onClose={closeSessions} carried={overlayCarried}>
          {sessionsPanel}
        </SessionsPanelOverlay>
      )}
      <section className={`terminal-page${handOff !== null ? ' terminal-page-arriving' : ''}`}>
        <header className="terminal-bar">
          <button
            type="button"
            className="button button-icon"
            ref={panelButtonRef}
            aria-label="Sessions"
            aria-expanded={docked || sessionsOpen}
            title="Switch session"
            onClick={() => {
              if (docked) {
                setPinned(false);
              } else {
                setSessionsOpen((open) => !open);
              }
            }}
          >
            ☰
          </button>
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
          {micMode !== 'hidden' && (
            <VoiceButton
              mode={micMode}
              status={voiceStatus.data}
              disabled={micMode === 'ready' && !canRecord}
              onClick={() => {
                // Synchronous on purpose: the recorder opens audio inside this
                // click's user activation.
                if (micMode === 'ready') dictation.toggle();
                else setVoiceSetupOpen(true);
              }}
            />
          )}
          <button
            type="button"
            className="button"
            title="Insert a prompt template at the cursor"
            onClick={() => {
              // Read here, before any dialog exists, so a busy PTY cannot clear
              // the selection out from under the editor — see `TemplatePanel`.
              setPanel({ kind: 'picker', selection: model.selectionText() });
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
          {/* Only once the row carries a cwd, as on both mobile clients: the
              git routes answer for the directory the session was spawned in,
              and a row without one has nothing to show. */}
          {session?.cwd !== undefined && (
            <MenuButton
              label="More options"
              className="button button-icon"
              triggerRef={moreRef}
              groups={[
                {
                  id: 'git',
                  label: 'Git',
                  items: [
                    {
                      id: 'changes',
                      label: 'Changes…',
                      onSelect: () => {
                        setChangesOpen(true);
                      },
                    },
                    ...(canCommit
                      ? [
                          {
                            id: 'commit',
                            label: 'Commit…',
                            onSelect: () => {
                              setChangesOpen(true);
                              setCommitRequest((n) => n + 1);
                            },
                          },
                        ]
                      : []),
                    ...(canSync
                      ? [
                          {
                            id: 'sync',
                            label: 'Fetch / Pull / Push…',
                            onSelect: () => {
                              setChangesOpen(true);
                              setSyncRequest((n) => n + 1);
                            },
                          },
                        ]
                      : []),
                    ...(branchesProject !== null
                      ? [
                          {
                            id: 'branches',
                            label: 'Branches…',
                            onSelect: () => {
                              void navigate(
                                `/agents/${encodeURIComponent(model.agentId)}/projects/${encodeURIComponent(branchesProject)}/git`,
                              );
                            },
                          },
                        ]
                      : []),
                    ...(gitCapabilities.rebase
                      ? [
                          {
                            id: 'rebase',
                            label: 'Rebase…',
                            onSelect: () => {
                              setChangesOpen(true);
                              setRebaseRequest((n) => n + 1);
                            },
                          },
                        ]
                      : []),
                  ],
                },
              ]}
            >
              ⋯
            </MenuButton>
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

        {panel.kind === 'picker' && (
          <TemplatesPicker
            canSend={canSend}
            onClose={() => {
              setPanel({ kind: 'none' });
            }}
            onNewTemplate={() => {
              setSaveError(null);
              setPanel({ kind: 'editor', initialBody: panel.selection });
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
              setPanel({ kind: 'none' });
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

        {/* No `key`: the panel passes through `none` on every exit, so each open
            is already a fresh mount with a fresh draft. */}
        {panel.kind === 'editor' && (
          <TemplateEditorDialog
            editing={null}
            initialBody={panel.initialBody}
            busy={save.isPending}
            error={saveError}
            onSave={(name, body) => {
              save.mutate({ name, body });
            }}
            // Back to the terminal rather than to the picker. A save has to close
            // everything for its toast to be visible, and two exits that land in
            // different places would be the surprising half of the pair.
            onCancel={() => {
              setPanel({ kind: 'none' });
              setSaveError(null);
            }}
          />
        )}

        {voiceSetupOpen && (
          <VoiceSetupDialog
            agentId={model.agentId}
            agentName={agentName}
            status={voiceStatus.data}
            loadFailed={voiceStatus.isError}
            cleanupOffered={cleanupAvailable}
            onClose={() => {
              setVoiceSetupOpen(false);
            }}
          />
        )}

        {dictationShown && (
          <DictationDialog
            state={dictation.state}
            carried={carried}
            agentName={agentName}
            canSend={canSend}
            canRecord={canRecord}
            onStop={dictation.stop}
            onCancel={dictation.cancel}
            onRetry={dictation.retry}
            onRecordAgain={dictation.toggle}
            onDismiss={dictation.dismiss}
            onContinue={dictation.continueFrom}
            onDiscard={dictation.finish}
            onInsert={insertTranscript}
            skipReview={skipReview}
            onSkipReviewChange={(skip) => {
              setSkipReview(model.agentId, skip);
            }}
            cleanupAvailable={cleanupAvailable}
            cleanUp={cleanUp}
            onCleanUpChange={(on) => {
              setCleanUp(model.agentId, on);
            }}
            onCleanUpText={dictation.cleanUpText}
          />
        )}

        {changesOpen && (
          <GitChangesPanel
            agentId={model.agentId}
            sessionId={model.sessionId}
            commitRequest={commitRequest}
            syncRequest={syncRequest}
            rebaseRequest={rebaseRequest}
            projectName={session?.project_name ?? null}
            onClose={() => {
              setChangesOpen(false);
              setCommitRequest(0);
              setSyncRequest(0);
              setRebaseRequest(0);
              // Back to the menu button, not the grid: the viewer was reached
              // from the header, and a keyboard user is still up there.
              moreRef.current?.focus();
            }}
          />
        )}
      </section>
    </>
  );
}

interface SessionsPanelOverlayProps {
  onClose: () => void;
  /** Already on screen before this mount (a stop hand-off): skip the slide-in. */
  carried: boolean;
  children: ReactNode;
}

/**
 * The slide-over form of the panel. `popover="auto"`, unlike the Changes
 * viewer's `manual`: a click into the terminal or Escape should dismiss it,
 * and the UA `::backdrop` is the scrim. Mounted only while open, the way that
 * viewer and `Modal` are, so the top-layer entry comes and goes with it.
 */
function SessionsPanelOverlay({ onClose, carried, children }: SessionsPanelOverlayProps) {
  const ref = useRef<HTMLElement>(null);
  // Set while this component closes the popover itself (unmount: a switch, or
  // a pin that turns it into the docked rail). The toggle event that follows
  // is not a dismissal, and acting on it would pull focus out of the rail.
  const unmounting = useRef(false);

  useLayoutEffect(() => {
    // Re-armed on every mount: StrictMode replays mount → cleanup → mount on
    // one instance, and a latch left set would swallow every real dismissal.
    unmounting.current = false;
    const panel = ref.current;
    panel?.showPopover();
    return () => {
      unmounting.current = true;
      panel?.hidePopover();
    };
  }, []);

  return (
    <aside
      ref={ref}
      className={`sessions-panel${carried ? ' sessions-panel-carried' : ''}`}
      popover="auto"
      aria-label="Sessions panel"
      onToggle={(event) => {
        // Light dismiss and Escape close the popover without going through
        // React; mirror that into state so the next click opens it again.
        if (event.newState === 'closed' && !unmounting.current) onClose();
      }}
    >
      {children}
    </aside>
  );
}
