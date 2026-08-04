import { Link } from 'react-router';
import type { SessionResponse } from '../../protocol/dto/session';
import {
  attentionChip,
  canStopSession,
  sessionStateTone,
  sessionTitle,
} from './sessionDisplay';
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
  const chip = attentionChip(session.attention);
  const tone = sessionStateTone(session.state);

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
        <span className={`session-dot session-dot-${tone}`} aria-hidden="true" />
        <span className="session-text">
          <span className="session-title">{sessionTitle(session, kindLabel)}</span>
          <span className="session-kind">{kindLabel}</span>
          {session.cwd !== undefined && <span className="session-cwd">{session.cwd}</span>}
        </span>
      </Link>

      {chip !== null && (
        <span className={`session-attention session-attention-${chip.tone}`}>{chip.text}</span>
      )}

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
