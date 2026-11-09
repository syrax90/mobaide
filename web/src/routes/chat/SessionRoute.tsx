import { Suspense, lazy, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';
import { closeAttentionNotification } from '../../notifications/notificationService';
import { isChatSession } from '../../protocol/dto/session';
import { useAgentEvents, useConnectionStore, useSessionsSynced } from '../../realtime';
import { agentEventsChannelName } from '../../realtime/channels';
import { useVisibleSessionStore } from '../../stores/visibleSessionStore';
import { useSessionRow } from '../terminal/useSessionRow';

// Both lazy: xterm and its WebGL addon must not ride along for a chat session.
const TerminalScreen = lazy(async () => {
  const module = await import('../terminal/TerminalScreen');
  return { default: module.TerminalScreen };
});
const ChatScreen = lazy(async () => {
  const module = await import('./ChatScreen');
  return { default: module.ChatScreen };
});

type SessionUi = 'chat' | 'terminal';

/**
 * `/agents/:agentId/sessions/:sessionId` — picks the chat or the terminal
 * screen from the session row's `ui`, before either attaches a socket.
 *
 * The decision is latched per route: a session that ends while open leaves the
 * list, and must stay on the screen it was opened in.
 */
export function SessionRoute() {
  const { agentId, sessionId } = useParams();
  if (agentId === undefined || sessionId === undefined) {
    return (
      <section className="page">
        <h1>Session not specified</h1>
        <Link to="/">Back to sessions</Link>
      </section>
    );
  }
  return <SessionUiSwitch agentId={agentId} sessionId={sessionId} />;
}

function SessionUiSwitch({ agentId, sessionId }: { agentId: string; sessionId: string }) {
  useAgentEvents(agentId);
  const synced = useSessionsSynced(agentId);
  const row = useSessionRow(agentId, sessionId);
  const connected = useConnectionStore(
    (state) => state.channels[agentEventsChannelName(agentId)]?.status === 'connected',
  );
  const route = `${agentId}/${sessionId}`;
  const [latched, setLatched] = useState<{ route: string; ui: SessionUi } | null>(null);

  // The suppression input both mobile clients call a `ForegroundSessionTracker`:
  // an attention alert for the session already on screen is noise. Set here,
  // not in the lazy screens, so an alert that beats their chunk (a new session
  // asking to trust its folder) is suppressed too. Session id, not the channel
  // key — that is what the event carries.
  useEffect(() => {
    const { setVisibleSession, clearVisibleSession } = useVisibleSessionStore.getState();
    setVisibleSession(sessionId);
    closeAttentionNotification(sessionId);
    return () => {
      clearVisibleSession(sessionId);
    };
  }, [sessionId]);

  let ui: SessionUi | null = latched?.route === route ? latched.ui : null;
  if (ui === null && row !== undefined) ui = isChatSession(row) ? 'chat' : 'terminal';
  // Synced and still absent: the terminal screen owns "That session has ended."
  if (ui === null && synced) ui = 'terminal';

  useEffect(() => {
    if (ui !== null && (latched?.route !== route || latched.ui !== ui)) {
      setLatched({ route, ui });
    }
  }, [ui, route, latched]);

  const placeholder = (
    <section className="terminal-page terminal-page-empty">
      <p className="state-loading" role="status">
        {connected ? 'Opening the session' : 'Connecting to your computer'}
      </p>
    </section>
  );
  if (ui === null) return placeholder;
  return (
    <Suspense fallback={placeholder}>{ui === 'chat' ? <ChatScreen /> : <TerminalScreen />}</Suspense>
  );
}
