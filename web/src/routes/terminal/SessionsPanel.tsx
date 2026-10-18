import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router';
import { listAgents } from '../../api/agentRepository';
import { listCustomAgentTypes } from '../../api/customAgentTypeRepository';
import { listSessions } from '../../api/sessionRepository';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import type { SessionResponse } from '../../protocol/dto/session';
import { useSessionsSynced } from '../../realtime';
import { useLastSessionStore } from '../../stores/lastSessionStore';
import { useSelectedAgentStore } from '../../stores/selectedAgentStore';
import {
  attentionChip,
  customTypeNameMap,
  sessionKindLabel,
  sessionStateTone,
  sessionTitle,
} from '../sessions/sessionDisplay';
import { newSessionPath, terminalPath } from '../sessions/terminalPath';

interface SessionsPanelProps {
  agentId: string;
  sessionId: string;
  /** Docked beside the grid (no scrim, no light-dismiss) or an overlay. */
  docked: boolean;
  pinned: boolean;
  onPinnedChange: (pinned: boolean) => void;
  onClose: () => void;
}

/**
 * The terminal's left panel (Herdr's sidebar): the route agent's sessions for
 * one-click switching, a shortcut to the create flow, and the account's other
 * agents. Both mobile clients render the same panel as a swipe-in drawer.
 *
 * The sessions query is the one `useSessionGone` already runs for this route,
 * so the panel costs no extra fetch and sees every `sessions.changed` push.
 */
export function SessionsPanel({
  agentId,
  sessionId,
  docked,
  pinned,
  onPinnedChange,
  onClose,
}: SessionsPanelProps) {
  const navigate = useNavigate();
  const synced = useSessionsSynced(agentId);
  const sessions = useQuery({
    queryKey: queryKeys.sessions(agentId),
    queryFn: () => listSessions(agentId),
    enabled: synced,
    ...wsBackedQueryOptions,
  });
  const customTypes = useQuery({
    queryKey: queryKeys.customAgentTypes(),
    queryFn: listCustomAgentTypes,
    ...wsBackedQueryOptions,
  });
  const agents = useQuery({
    queryKey: queryKeys.agents(),
    queryFn: listAgents,
    ...wsBackedQueryOptions,
  });
  const setLastUsedSession = useLastSessionStore((state) => state.setLastUsedSession);
  const setSelectedAgent = useSelectedAgentStore((state) => state.setSelectedAgent);

  const names = customTypeNameMap(customTypes.data);
  const current = agents.data?.find((agent) => agent.agentId === agentId);
  const others = agents.data?.filter((agent) => agent.agentId !== agentId) ?? [];

  // The heading takes focus on open so a keyboard user lands inside the panel;
  // the docked rail is part of the page and must not steal it.
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (!docked) headingRef.current?.focus();
  }, [docked]);

  const open = (session: SessionResponse) => {
    if (session.session_id === sessionId) {
      onClose();
      return;
    }
    setLastUsedSession(agentId, session.session_id);
    // Replace, never stack: Back still returns to wherever the terminal was
    // opened from, as on both mobile clients.
    void navigate(terminalPath(agentId, session.session_id), { replace: true });
  };

  return (
    <div className="sessions-panel-body">
      <header className="sessions-panel-head">
        <h2 ref={headingRef} tabIndex={-1}>
          Sessions
        </h2>
        <button
          type="button"
          className="button button-icon"
          aria-pressed={pinned}
          title={pinned ? 'Unpin the panel' : 'Keep the panel open beside the terminal'}
          onClick={() => {
            onPinnedChange(!pinned);
          }}
        >
          {pinned ? 'Unpin' : 'Pin'}
        </button>
        {!docked && (
          <button
            type="button"
            className="button button-icon"
            aria-label="Close sessions panel"
            onClick={onClose}
          >
            ✕
          </button>
        )}
      </header>

      <p className="sessions-panel-agent">
        <span
          className={`agent-status${current?.online === true ? ' agent-status-online' : ''}`}
          aria-hidden="true"
        />
        <span className="sessions-panel-agent-name">{current?.name ?? 'Connected agent'}</span>
        <span className="sessions-panel-agent-state">
          {current?.online === true ? 'Online' : 'Offline'}
        </span>
      </p>

      <div className="sessions-panel-scroll">
        {!synced || sessions.data === undefined ? (
          <p className="sessions-panel-note">Reconnecting…</p>
        ) : sessions.data.length === 0 ? (
          <p className="sessions-panel-note">No active sessions</p>
        ) : (
          <ul className="sessions-panel-list" aria-label="Sessions">
            {sessions.data.map((session) => {
              const selected = session.session_id === sessionId;
              const chip = attentionChip(session.attention);
              const kindLabel = sessionKindLabel(session, names);
              return (
                <li key={session.session_id}>
                  <button
                    type="button"
                    className="sessions-panel-row"
                    aria-current={selected ? 'true' : undefined}
                    onClick={() => {
                      open(session);
                    }}
                  >
                    <span
                      className={`session-dot session-dot-${sessionStateTone(session.state)}`}
                      aria-hidden="true"
                    />
                    <span className="session-text">
                      <span className="session-title">{sessionTitle(session, kindLabel)}</span>
                      <span className="session-kind">
                        {[kindLabel, session.project_name].filter(Boolean).join(' · ')}
                      </span>
                    </span>
                    {chip !== null && (
                      <span className={`session-attention session-attention-${chip.tone}`}>
                        {chip.text}
                      </span>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        <Link className="sessions-panel-new" to={newSessionPath(agentId)}>
          + New session
        </Link>

        {others.length > 0 && (
          <>
            <h3 className="sessions-panel-section">Agents</h3>
            <ul className="sessions-panel-list" aria-label="Other agents">
              {others.map((agent) => (
                <li key={agent.agentId}>
                  <button
                    type="button"
                    className="sessions-panel-row sessions-panel-row-agent"
                    disabled={!agent.online}
                    onClick={() => {
                      // The sessions list for that agent, as on mobile, where a
                      // switch reconnects the app to it.
                      setSelectedAgent(agent.agentId);
                      void navigate('/');
                    }}
                  >
                    <span
                      className={`agent-status${agent.online ? ' agent-status-online' : ''}`}
                      aria-hidden="true"
                    />
                    <span className="session-text">
                      <span className="session-title">{agent.name}</span>
                    </span>
                    <span className="sessions-panel-agent-state">
                      {agent.online ? 'Online' : 'Offline'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}
