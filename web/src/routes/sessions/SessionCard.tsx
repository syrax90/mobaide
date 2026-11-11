import { Link } from 'react-router';
import type { SessionResponse } from '../../protocol/dto/session';
import { SessionStatusChip, SessionStatusIndicator } from './SessionStatus';
import { canStopSession, sessionTitle } from './sessionDisplay';
import { terminalPath } from './terminalPath';

interface SessionCardProps {
  agentId: string;
  session: SessionResponse;
  kindLabel: string;
  onOpen: (sessionId: string) => void;
  onStop: (session: SessionResponse) => void;
}

export function SessionCard({
  agentId,
  session,
  kindLabel,
  onOpen,
  onStop,
}: SessionCardProps) {
  return (
    <li className="device-card session-card">
      {/*
        The Stop button is a sibling of this link, never a child: nesting
        interactive elements is invalid HTML and breaks keyboard traversal.
      */}
      <Link
        className="session-card-link"
        to={terminalPath(agentId, session.session_id)}
        onClick={() => {
          onOpen(session.session_id);
        }}
      >
        <SessionStatusIndicator session={session} />
        <span className="session-text">
          <span className="session-title">{sessionTitle(session, kindLabel)}</span>
          <span className="session-kind">{kindLabel}</span>
          {session.cwd !== undefined && <span className="session-cwd">{session.cwd}</span>}
        </span>
      </Link>

      <SessionStatusChip session={session} />

      {canStopSession(session.state) && (
        <button
          type="button"
          className="button button-danger session-stop"
          onClick={() => {
            onStop(session);
          }}
        >
          Stop
        </button>
      )}
    </li>
  );
}
