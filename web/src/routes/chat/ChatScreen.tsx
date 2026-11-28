import { useCallback, useEffect, useRef, useState, type DragEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router';
import { useStore } from 'zustand';
import { acquireChat, releaseChat, useChatStore } from '../../chat/chatStore';
import type { ChatSessionModel } from '../../chat/chatModel';
import { chatStatusLine, type StatusLine } from '../../chat/chatFormat';
import { imageFilesFrom } from '../../chat/chatImages';
import { parseForkResult, type ChatState } from '../../chat/chatWire';
import { MenuButton, type MenuGroup } from '../../components/MenuButton';
import { queryKeys } from '../../lib/queryKeys';
import { useFullscreen } from '../../lib/useFullscreen';
import { isChatSession, type SessionResponse } from '../../protocol/dto/session';
import { chatChannelName } from '../../realtime/channels';
import { upsertSession } from '../../realtime/sessionCache';
import { useChatPrefsStore } from '../../stores/chatPrefsStore';
import { useLastSessionStore } from '../../stores/lastSessionStore';
import { useToastStore } from '../../stores/toastStore';
import { clearMountedTerminal, setMountedTerminal } from '../../terminal/watchedTerminal';
import { sessionDialogLabel, sessionKindLabel, worktreeInUse } from '../sessions/sessionDisplay';
import { terminalPath } from '../sessions/terminalPath';
import { StopSessionDialog } from '../sessions/StopSessionDialog';
import { useStopSession } from '../sessions/useStopSession';
import { ConnectionOverlay } from '../terminal/ConnectionOverlay';
import { useSessionGitMenu } from '../terminal/git/useSessionGitMenu';
import { useSessionGone } from '../terminal/useSessionGone';
import { useSessionHeaderLabel } from '../terminal/useSessionHeaderLabel';
import { useSessionRows } from '../terminal/useSessionRow';
import { useSessionsPanelHost } from '../terminal/useSessionsPanelHost';
import { CollapseIcon, ExpandIcon } from './ChatIcons';
import { ChatTimelineView } from './ChatTimelineView';
import { Composer, type ComposerHandle } from './Composer';
import { ConfigChips, UsageIndicator } from './ConfigChips';
import { PlanBar, QueueList, toastFailure } from './DockPanels';
import { RequestDock } from './RequestCard';
import { ForkDialog } from './ForkDialog';
import { NewConversationDialog } from './NewConversationDialog';
import { SessionInfoDialog } from './SessionInfoDialog';

export function ChatScreen() {
  const { agentId, sessionId } = useParams();
  if (agentId === undefined || sessionId === undefined) {
    return (
      <section className="page">
        <h1>Session not specified</h1>
        <Link to="/">Back to sessions</Link>
      </section>
    );
  }
  return <ChatRoute agentId={agentId} sessionId={sessionId} />;
}

function ChatRoute({ agentId, sessionId }: { agentId: string; sessionId: string }) {
  useSessionGone(agentId, sessionId);
  const headerLabel = useSessionHeaderLabel(agentId, sessionId);
  const sessionRows = useSessionRows(agentId);
  const session = sessionRows?.find((row) => row.session_id === sessionId);
  const key = chatChannelName(agentId, sessionId);
  const model = useChatStore((state) => state.models[key]);

  // Unconditional, like the terminal's: re-acquires after `stopAllRealtime()`
  // disposed the model, and refreshes its place in the resident set.
  useEffect(() => {
    acquireChat(agentId, sessionId);
  }, [agentId, sessionId, model]);

  useEffect(() => {
    setMountedTerminal(key);
    return () => {
      clearMountedTerminal(key);
      releaseChat(key);
    };
  }, [key]);

  if (model === undefined) {
    return (
      <section className="terminal-page terminal-page-empty">
        <p className="state-loading" role="status">
          Opening the session
        </p>
      </section>
    );
  }
  return (
    <ChatView
      key={key}
      model={model}
      session={session}
      headerLabel={headerLabel}
      worktreeShared={session !== undefined && worktreeInUse(session, sessionRows)}
    />
  );
}

/** Ticks once a second while `active`, for the running turn's elapsed time. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const id = setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => {
      clearInterval(id);
    };
  }, [active]);
  return now;
}

function dotTone(line: StatusLine, state: ChatState, live: boolean, closed: boolean): string {
  if (closed || !live) return 'muted';
  if (line.tone === 'danger') return 'danger';
  const { status, background } = state;
  if (line.tone === 'accent' || status === 'running' || background === 'working') return 'accent';
  if (status === 'starting' || status === 'loading' || background === 'waiting') return 'pending';
  return 'ok';
}

/**
 * The title, the status line under it and the dot. Its own component so the
 * running turn's one-second tick re-renders only this, not the whole screen.
 */
function HeaderStatus({ model, title }: { model: ChatSessionModel; title: string }) {
  const state = useStore(model.store, (view) => view.timeline.state);
  const live = useStore(model.store, (view) => view.connection === 'live');
  const gone = useStore(model.store, (view) => view.gone);
  const closed = gone || state.status === 'stopped';
  const forking = useStore(model.store, (view) => view.forking);
  const now = useNow(state.status === 'running' && state.turn_started_at !== null);
  const line: StatusLine =
    forking && live && !closed
      ? { text: 'Forking the chat…', tone: 'muted' }
      : chatStatusLine(state, live, gone, now);
  return (
    <>
      <span
        className={`chat-dot chat-dot-${dotTone(line, state, live, closed)}`}
        aria-hidden="true"
      />
      <div className="chat-title-block">
        <span className="terminal-title chat-title">{title}</span>
        <span className={`chat-status chat-status-${line.tone}`}>{line.text}</span>
      </div>
      {/* Without the elapsed time: a region that changed every second would
          be read out every second. */}
      <span className="visually-hidden" role="status" aria-live="polite">
        {line.text.split(' · ')[0]}
      </span>
    </>
  );
}

interface ChatViewProps {
  model: ChatSessionModel;
  session: SessionResponse | undefined;
  headerLabel: string;
  /** Another session works in this session's worktree. */
  worktreeShared: boolean;
}

function ChatView({ model, session, headerLabel, worktreeShared }: ChatViewProps) {
  const navigate = useNavigate();
  const state = useStore(model.store, (view) => view.timeline.state);
  const connection = useStore(model.store, (view) => view.connection);
  const channelStopped = useStore(model.store, (view) => view.channelStopped);
  const gone = useStore(model.store, (view) => view.gone);
  const overrides = useStore(model.store, (view) => view.overrides);
  const live = connection === 'live';
  const closed = gone || state.status === 'stopped';
  const running = state.status === 'running';
  const kindLabel = session === undefined ? 'the agent' : sessionKindLabel(session, new Map());
  const title = state.title !== null && state.title !== '' ? state.title : headerLabel;
  const composerRef = useRef<ComposerHandle>(null);

  const { handOff, toggle: sessionsToggle, rail: sessionsRail } = useSessionsPanelHost({
    agentId: model.agentId,
    sessionId: model.sessionId,
    onPrepareSession: (row) => {
      if (isChatSession(row)) acquireChat(model.agentId, row.session_id);
    },
  });
  const moreRef = useRef<HTMLButtonElement>(null);
  const git = useSessionGitMenu({
    agentId: model.agentId,
    sessionId: model.sessionId,
    session,
    moreRef,
  });

  const [infoOpen, setInfoOpen] = useState(false);
  const [stopOpen, setStopOpen] = useState(false);
  const [newOpen, setNewOpen] = useState(false);
  /** The fork the dialog asks about: up to an answer, or (`null`) the whole chat. */
  const [forkAt, setForkAt] = useState<{ itemId: string | null } | null>(null);
  const [dragging, setDragging] = useState(false);
  const restarting = useStore(model.store, (view) => view.restarting);
  const startingNew = useStore(model.store, (view) => view.startingNew);
  const forking = useStore(model.store, (view) => view.forking);
  const hasConversation = useStore(model.store, (view) =>
    view.timeline.items.some((item) => item.type === 'user' || item.type === 'agent'),
  );
  // The agent refuses a fork while a turn runs or the chat starts: say so by
  // the disabled control, not by a refusal after the dialog.
  const canFork =
    live && !closed && !restarting && !startingNew && !forking && state.status === 'ready';
  const canStartNew =
    live &&
    !restarting &&
    !startingNew &&
    (state.status === 'ready' || state.status === 'running' || state.status === 'error');
  const suggestion =
    live &&
    !closed &&
    !restarting &&
    !startingNew &&
    state.status === 'ready' &&
    state.requests.length === 0 &&
    state.queue.length === 0
      ? (state.prompt_suggestions[0]?.text ?? null)
      : null;
  const focusView = useChatPrefsStore((prefs) => prefs.focusView);
  const setFocusView = useChatPrefsStore((prefs) => prefs.setFocusView);
  const fullscreen = useFullscreen();

  useEffect(() => {
    if (fullscreen.error !== null) useToastStore.getState().show(fullscreen.error, 'error');
  }, [fullscreen.error]);

  const clearLastUsedSession = useLastSessionStore((store) => store.clearLastUsedSession);
  const lastUsed = useLastSessionStore((store) => store.lastUsedSessionIds[model.agentId]);
  const stop = useStopSession(model.agentId, {
    onSettled: () => {
      setStopOpen(false);
    },
    onStopped: () => {
      if (lastUsed === model.sessionId) clearLastUsedSession(model.agentId);
      void navigate('/', { replace: true });
    },
  });

  const restart = useCallback(() => {
    if (model.state().restarting) return;
    void model.restart().then((result) => {
      toastFailure(result, 'Could not restart the agent');
    });
  }, [model]);

  const queryClient = useQueryClient();
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const requestFork = useCallback((itemId: string) => {
    setForkAt({ itemId });
  }, []);
  const fork = useCallback(
    (itemId: string | null) => {
      setForkAt(null);
      void model.fork(itemId ?? undefined).then((result) => {
        if (!result.ok) {
          toastFailure(result, 'Could not fork the chat');
          return;
        }
        const forked = parseForkResult(result.data);
        if (forked === null) {
          useToastStore.getState().show('Could not open the forked chat', 'error');
          return;
        }
        // Seeded before navigating, as after a create: a synced list without
        // the new row would read as "that session has ended".
        const row = forked.session;
        if (row !== null) {
          queryClient.setQueryData<SessionResponse[]>(queryKeys.sessions(model.agentId), (current) =>
            upsertSession(current ?? [], row),
          );
        }
        void queryClient.invalidateQueries({ queryKey: queryKeys.sessions(model.agentId) });
        if (!mounted.current) {
          useToastStore.getState().show('Forked the chat. Open it from Sessions.', 'success');
          return;
        }
        try {
          // The chat the user goes on in, as after a create.
          useLastSessionStore.getState().setLastUsedSession(model.agentId, forked.sessionId);
        } catch {
          // Storage refused; auto-resume simply will not return here.
        }
        // Replace, as switching sessions does: Back goes to the sessions list.
        void navigate(terminalPath(model.agentId, forked.sessionId), { replace: true });
      });
    },
    [model, navigate, queryClient],
  );

  const startNew = useCallback(() => {
    setNewOpen(false);
    if (model.state().startingNew) return;
    void model.newConversation().then((result) => {
      toastFailure(result, 'Could not start a new conversation');
    });
  }, [model]);

  // Desktop is keyboard-first: land in the composer. Not on touch screens,
  // where focusing would pop the keyboard over the conversation.
  useEffect(() => {
    if (handOff?.overlayOpen === true) return;
    if (window.matchMedia('(pointer: fine)').matches) composerRef.current?.focus();
  }, [handOff]);

  const viewGroup: MenuGroup = {
    id: 'view',
    label: 'View',
    items: [
      {
        id: 'focus',
        label: 'Focus view',
        description: 'Answers and questions only; the steps fold into one line',
        checked: focusView,
        toggle: true,
        onSelect: () => {
          setFocusView(!focusView);
        },
      },
    ],
  };

  const sessionGroup: MenuGroup = {
    id: 'session',
    label: 'Session',
    items: [
      ...(state.status === 'error' && !closed
        ? [{ id: 'restart', label: 'Restart agent', disabled: !live || restarting, onSelect: restart }]
        : []),
      ...(state.new_conversation && !closed
        ? [
            {
              id: 'new-conversation',
              label: 'New conversation',
              description: 'Clear the chat; keep the folder and settings',
              disabled: !canStartNew,
              onSelect: () => {
                // Ask first only when this stops a turn or drops queued messages.
                if (running || state.queue.length > 0) setNewOpen(true);
                else startNew();
              },
            },
          ]
        : []),
      ...(state.fork.chat && !closed
        ? [
            {
              id: 'fork',
              label: 'Fork chat',
              description: 'Copy the conversation into a new chat; this one stays',
              disabled: !canFork || !hasConversation,
              onSelect: () => {
                setForkAt({ itemId: null });
              },
            },
          ]
        : []),
      {
        id: 'info',
        label: 'Session info',
        onSelect: () => {
          setInfoOpen(true);
        },
      },
      ...(closed
        ? []
        : [
            {
              id: 'close',
              label: 'Close session',
              onSelect: () => {
                setStopOpen(true);
              },
            },
          ]),
    ],
  };

  const infoRows = [
    ...(state.agent !== null
      ? [
          {
            label: 'Agent',
            value: state.agent.version === '' ? state.agent.title : `${state.agent.title} ${state.agent.version}`,
          },
        ]
      : []),
    ...(state.cwd !== null || session?.cwd !== undefined
      ? [{ label: 'Folder', value: state.cwd ?? session?.cwd ?? '' }]
      : []),
    ...(state.acp_session_id !== null ? [{ label: 'Session ID', value: state.acp_session_id }] : []),
  ];

  const overlay = closed
    ? null
    : channelStopped
      ? 'Disconnected'
      : live
        ? null
        : 'Reconnecting…';

  const hasFiles = (event: DragEvent) => event.dataTransfer.types.includes('Files');

  return (
    <>
      {sessionsRail}
      <section
        className={`terminal-page chat-page${handOff !== null ? ' terminal-page-arriving' : ''}`}
        onDragOver={(event) => {
          if (!hasFiles(event) || closed || !state.prompt_caps.image) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = 'copy';
          setDragging(true);
        }}
        onDragLeave={(event) => {
          const next = event.relatedTarget;
          if (!(next instanceof Node) || !event.currentTarget.contains(next)) setDragging(false);
        }}
        onDrop={(event) => {
          if (!hasFiles(event)) return;
          event.preventDefault();
          setDragging(false);
          composerRef.current?.addImages(imageFilesFrom(event.dataTransfer.files));
        }}
      >
        <header className="terminal-bar chat-bar">
          {sessionsToggle}
          <Link to="/" className="terminal-back" aria-label="Back to sessions">
            ← <span className="chat-back-label">Sessions</span>
          </Link>
          <HeaderStatus model={model} title={title} />
          <span className="terminal-bar-spacer" />
          <UsageIndicator usage={state.usage} />
          {fullscreen.supported && (
            <button
              type="button"
              className="button button-icon"
              aria-label={fullscreen.active ? 'Leave full screen' : 'Full screen'}
              title={fullscreen.active ? 'Leave full screen' : 'Full screen'}
              aria-pressed={fullscreen.active}
              onClick={fullscreen.active ? fullscreen.exit : fullscreen.enter}
            >
              {fullscreen.active ? <CollapseIcon /> : <ExpandIcon />}
            </button>
          )}
          <MenuButton
            label="More options"
            className="button button-icon"
            triggerRef={moreRef}
            groups={
              git.group === null ? [viewGroup, sessionGroup] : [git.group, viewGroup, sessionGroup]
            }
          >
            ⋯
          </MenuButton>
        </header>

        <ChatTimelineView
          model={model}
          kindLabel={kindLabel}
          onRestart={restart}
          onFork={state.fork.at_answer && !closed ? requestFork : undefined}
          forkDisabled={!canFork}
        >
          <ConnectionOverlay
            message={overlay}
            onRetry={
              channelStopped
                ? () => {
                    model.retryConnection();
                  }
                : undefined
            }
          />
        </ChatTimelineView>

        <div className="chat-dock">
          <div className="chat-column chat-dock-column">
            <RequestDock model={model} requests={state.requests} live={live} />
            <PlanBar plan={state.plan} />
            <QueueList
              model={model}
              queue={state.queue}
              live={live}
              canSteer={state.steering && state.status === 'running'}
              limitWait={state.limit_wait}
            />
            <ConfigChips
              model={model}
              config={state.config}
              overrides={overrides}
              live={live && !closed && state.status !== 'error'}
            />
            <Composer
              model={model}
              kindLabel={kindLabel}
              sessionKind={session?.kind}
              live={live}
              closed={closed}
              halted={state.status === 'error'}
              running={running}
              cancelling={state.cancelling}
              acceptsImages={state.prompt_caps.image}
              acceptsMentions={state.prompt_caps.mentions}
              commands={state.commands}
              suggestion={suggestion}
              latestSuggestion={state.prompt_suggestions[0]?.text ?? null}
              handleRef={composerRef}
            />
          </div>
        </div>

        {dragging && (
          <div className="chat-drop" aria-hidden="true">
            Drop images to attach
          </div>
        )}

        {infoOpen && (
          <SessionInfoDialog
            rows={infoRows}
            onClose={() => {
              setInfoOpen(false);
            }}
          />
        )}
        {newOpen && (
          <NewConversationDialog
            kindLabel={kindLabel}
            running={running}
            queued={state.queue.length > 0}
            onConfirm={startNew}
            onCancel={() => {
              setNewOpen(false);
            }}
          />
        )}
        {forkAt !== null && (
          <ForkDialog
            atAnswer={forkAt.itemId !== null}
            onConfirm={() => {
              fork(forkAt.itemId);
            }}
            onCancel={() => {
              setForkAt(null);
            }}
          />
        )}
        {stopOpen && (
          <StopSessionDialog
            label={session === undefined ? title : sessionDialogLabel(session, new Map())}
            worktreeBranch={session?.worktree_branch}
            worktreeInUse={worktreeShared}
            busy={stop.isPending}
            onStop={(force, removeWorktree) => {
              stop.mutate({ sessionId: model.sessionId, force, removeWorktree });
            }}
            onCancel={() => {
              setStopOpen(false);
            }}
          />
        )}
        {git.panel}
      </section>
    </>
  );
}
