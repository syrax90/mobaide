import { useEffect, useRef, useState } from 'react';
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
import { useToastStore } from '../../stores/toastStore';
import { StopSessionDialog } from '../sessions/StopSessionDialog';
import {
  attentionChip,
  canStopSession,
  customTypeNameMap,
  sessionDialogLabel,
  sessionKindLabel,
  sessionStateTone,
  sessionTitle,
} from '../sessions/sessionDisplay';
import { newSessionPath, terminalPath } from '../sessions/terminalPath';
import { useStopSession } from '../sessions/useStopSession';

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
 * one-click switching or stopping, a shortcut to the create flow, and the
 * account's other agents. Both mobile clients render the same panel as a swipe-in drawer.
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
  const clearLastUsedSession = useLastSessionStore((state) => state.clearLastUsedSession);
  const setSelectedAgent = useSelectedAgentStore((state) => state.setSelectedAgent);

  const [stopTarget, setStopTarget] = useState<SessionResponse | null>(null);
  const stop = useStopSession(agentId, {
    onSettled: () => {
      setStopTarget(null);
    },
    onStopped: (stoppedId) => {
      // The terminal's own gone-check never ejects a session it has seen alive,
      // so leaving the one on screen is this panel's job — and it must happen
      // before the eviction tears the live view down under the user. Clearing
      // last-used first stops auto-resume, which may not have been claimed yet,
      // from sending the list straight back into the still-cached session.
      if (stoppedId === sessionId) {
        clearLastUsedSession(agentId);
        void navigate('/', { replace: true });
      }
      useToastStore.getState().show('Session stopped.', 'success');
    },
  });

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
          aria-label={pinned ? 'Unpin' : 'Pin'}
          title={pinned ? 'Unpin the panel' : 'Keep the panel open beside the terminal'}
          onClick={() => {
            onPinnedChange(!pinned);
          }}
        >
          <PinIcon pinned={pinned} />
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
                // The item, not the row button, carries the frame so the stop
                // control sits inside it, as on both mobile clients; a button
                // cannot nest inside another.
                <li
                  key={session.session_id}
                  className={`sessions-panel-item${selected ? ' sessions-panel-item-current' : ''}`}
                >
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
                  {canStopSession(session.state) && (
                    <button
                      type="button"
                      className="button button-icon sessions-panel-stop"
                      aria-label="Stop session"
                      title="Stop session"
                      disabled={stop.isPending}
                      onClick={() => {
                        setStopTarget(session);
                      }}
                    >
                      <StopIcon />
                    </button>
                  )}
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

      {/* Rendered inside the panel so the overlay form, a `popover="auto"`,
          stays open beneath the modal: showModal() only hides popovers that
          are not its ancestors, and clicks in the dialog are not light
          dismissals of an ancestor. */}
      {stopTarget !== null && (
        <StopSessionDialog
          label={sessionDialogLabel(stopTarget, names)}
          busy={stop.isPending}
          onStop={(force) => {
            stop.mutate({ sessionId: stopTarget.session_id, force });
          }}
          onCancel={() => {
            setStopTarget(null);
          }}
        />
      )}
    </div>
  );
}

/** A filled square, the media "stop" glyph, on the same 16-unit pixel grid. */
function StopIcon() {
  return (
    <svg
      className="sessions-panel-stop-icon"
      viewBox="0 0 16 16"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="3" y="3" width="10" height="10" />
    </svg>
  );
}

/** A 2px-wide diagonal, bottom-left to top-right, on the 16-unit grid. */
const SLASH =
  'M13 2h2v1h-2zM12 3h2v1h-2zM11 4h2v1h-2zM10 5h2v1h-2zM9 6h2v1h-2zM8 7h2v1h-2z' +
  'M7 8h2v1h-2zM6 9h2v1h-2zM5 10h2v1h-2zM4 11h2v1h-2zM3 12h2v1h-2zM2 13h2v1h-2z';

/**
 * The pin toggle's glyph: a hollow pushpin while the panel is loose, a filled
 * one struck through once it is pinned, so the icon shows the action the
 * press performs, as the former "Pin"/"Unpin" labels did. Drawn on a pixel
 * grid to match the rest of the chrome; `aria-hidden` because the button's
 * `aria-label` already names the action.
 */
function PinIcon({ pinned }: { pinned: boolean }) {
  return (
    <svg
      className="sessions-panel-pin-icon"
      viewBox="0 0 16 16"
      aria-hidden="true"
      focusable="false"
    >
      {pinned ? (
        <rect x="5" y="1" width="6" height="6" />
      ) : (
        <>
          <rect x="5" y="1" width="6" height="1" />
          <rect x="5" y="2" width="1" height="5" />
          <rect x="10" y="2" width="1" height="5" />
        </>
      )}
      <rect x="3" y="7" width="10" height="2" />
      <rect x="7" y="9" width="2" height="5" />
      {pinned && (
        <>
          {/* A one-pixel gap either side keeps the slash legible over the body. */}
          <path className="sessions-panel-pin-gap" transform="translate(-1 0)" d={SLASH} />
          <path className="sessions-panel-pin-gap" transform="translate(1 0)" d={SLASH} />
          <path d={SLASH} />
        </>
      )}
    </svg>
  );
}
