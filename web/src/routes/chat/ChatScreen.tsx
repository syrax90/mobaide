import { useCallback, useEffect, useRef, useState, type DragEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { useStore } from 'zustand';
import { acquireChat, releaseChat, useChatStore } from '../../chat/chatStore';
import type { ChatSessionModel } from '../../chat/chatModel';
import { chatStatusLine, type StatusLine } from '../../chat/chatFormat';
import { imageFilesFrom } from '../../chat/chatImages';
import { MenuButton, type MenuGroup } from '../../components/MenuButton';
import { useFullscreen } from '../../lib/useFullscreen';
import { isChatSession, type SessionResponse } from '../../protocol/dto/session';
import { chatChannelName } from '../../realtime/channels';
import { useChatPrefsStore } from '../../stores/chatPrefsStore';
import { useLastSessionStore } from '../../stores/lastSessionStore';
import { useToastStore } from '../../stores/toastStore';
import { clearMountedTerminal, setMountedTerminal } from '../../terminal/watchedTerminal';
import { sessionDialogLabel, sessionKindLabel } from '../sessions/sessionDisplay';
import { StopSessionDialog } from '../sessions/StopSessionDialog';
import { useStopSession } from '../sessions/useStopSession';
import { ConnectionOverlay } from '../terminal/ConnectionOverlay';
import { useSessionGitMenu } from '../terminal/git/useSessionGitMenu';
import { useSessionGone } from '../terminal/useSessionGone';
import { useSessionHeaderLabel } from '../terminal/useSessionHeaderLabel';
import { useSessionRow } from '../terminal/useSessionRow';
import { useSessionsPanelHost } from '../terminal/useSessionsPanelHost';
import { CollapseIcon, ExpandIcon } from './ChatIcons';
import { ChatTimelineView } from './ChatTimelineView';
import { Composer, type ComposerHandle } from './Composer';
import { ConfigChips, UsageIndicator } from './ConfigChips';
import { PlanBar, QueueList, toastFailure } from './DockPanels';
import { RequestDock } from './RequestCard';
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
  const session = useSessionRow(agentId, sessionId);
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
  return <ChatView key={key} model={model} session={session} headerLabel={headerLabel} />;
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

function dotTone(line: StatusLine, status: string, live: boolean, closed: boolean): string {
  if (closed || !live) return 'muted';
  if (line.tone === 'danger') return 'danger';
  if (line.tone === 'accent' || status === 'running') return 'accent';
  if (status === 'starting' || status === 'loading') return 'pending';
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
  const now = useNow(state.status === 'running' && state.turn_started_at !== null);
  const line = chatStatusLine(state, live, gone, now);
  return (
    <>
      <span
        className={`chat-dot chat-dot-${dotTone(line, state.status, live, closed)}`}
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
}

function ChatView({ model, session, headerLabel }: ChatViewProps) {
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
  const [dragging, setDragging] = useState(false);
  const restarting = useStore(model.store, (view) => view.restarting);
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
    ...(state.acp_session_id !== null ? [{ label: 'ACP session ID', value: state.acp_session_id }] : []),
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

        <ChatTimelineView model={model} kindLabel={kindLabel} onRestart={restart}>
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
              commands={state.commands}
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
        {stopOpen && (
          <StopSessionDialog
            label={session === undefined ? title : sessionDialogLabel(session, new Map())}
            busy={stop.isPending}
            onStop={(force) => {
              stop.mutate({ sessionId: model.sessionId, force });
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
