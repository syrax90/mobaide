import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router';
import { listCustomAgentTypes } from '../../api/customAgentTypeRepository';
import { listSessions, stopSession } from '../../api/sessionRepository';
import { isApiError } from '../../api/errors';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import type { SessionResponse } from '../../protocol/dto/session';
import {
  forceReconnectAgentEvents,
  useAgentEvents,
  useSessionsSynced,
} from '../../realtime';
import { useLastSessionStore } from '../../stores/lastSessionStore';
import { useToastStore } from '../../stores/toastStore';
import { evictTerminals } from '../../terminal/terminalEviction';
import { AutoScanSection } from '../autoscan/AutoScanSection';
import { SessionCard } from './SessionCard';
import { StopSessionDialog } from './StopSessionDialog';
import { customTypeNameMap, sessionKindLabel } from './sessionDisplay';
import { newSessionPath } from './terminalPath';
import { useAutoResume } from './useAutoResume';

interface SessionListProps {
  agentId: string;
  online: boolean;
}

export function SessionList({ agentId, online }: SessionListProps) {
  const queryClient = useQueryClient();
  const [stopTarget, setStopTarget] = useState<SessionResponse | null>(null);
  const setLastUsedSession = useLastSessionStore((state) => state.setLastUsedSession);

  // Held open even while the agent is offline: close code 4012 is the ordinary
  // attach-to-an-offline-agent case and reconnects, so the agent coming back
  // pushes its snapshot with no extra work here.
  useAgentEvents(agentId);
  const synced = useSessionsSynced(agentId);

  const sessions = useQuery({
    queryKey: queryKeys.sessions(agentId),
    queryFn: () => listSessions(agentId),
    // Disabled while offline: the relay answers 503 for an offline agent, which
    // the global query-error toast would fire on every mount.
    enabled: online,
    ...wsBackedQueryOptions,
  });

  const customTypes = useQuery({
    queryKey: queryKeys.customAgentTypes(),
    queryFn: listCustomAgentTypes,
    ...wsBackedQueryOptions,
  });

  // Passing `undefined` while offline leaves the module-level attempt
  // unclaimed, so auto-resume still fires once the agent returns. A disabled
  // query still serves cached rows, and a snapshot may have seeded them from
  // the wizard's channel, so without this the list could redirect into a
  // terminal on an unreachable agent and never show the offline state.
  useAutoResume(agentId, online ? sessions.data : undefined, synced);

  const stop = useMutation({
    mutationFn: ({ sessionId, force }: { sessionId: string; force: boolean }) =>
      stopSession(agentId, sessionId, force),
    onSuccess: async (_result, { sessionId }) => {
      setStopTarget(null);
      // Before the network settles: this is what actually kills a live PTY view.
      evictTerminals(agentId, [sessionId]);
      // The agent publishes `sessions.changed` immediately, which is the honest
      // source; this only covers the socket being down.
      await queryClient.invalidateQueries({ queryKey: queryKeys.sessions(agentId) });
    },
    onError: (error, { sessionId }) => {
      setStopTarget(null);
      // A 404 means the session is already gone — the user's intent is
      // satisfied, so reconcile rather than report a failure. Without this the
      // dead card stays on screen (`staleTime: Infinity` never refetches it) and
      // pressing Stop again just 404s. An unreachable agent answers 503 from the
      // relay, not 404, so this cannot swallow a real connectivity failure.
      if (isApiError(error) && error.status === 404) {
        evictTerminals(agentId, [sessionId]);
        void queryClient.invalidateQueries({ queryKey: queryKeys.sessions(agentId) });
        return;
      }
      useToastStore
        .getState()
        .show(
          isApiError(error)
            ? `Failed to stop session: ${error.message}`
            : 'Failed to stop session',
          'error',
        );
    },
  });

  const names = customTypeNameMap(customTypes.data);

  return (
    <>
      {/* Above the sessions, where iOS mounts its `AutoScanCard`. It rides the
          agent-events channel this component already opened, so it costs one
          REST seed and nothing else. */}
      <AutoScanSection agentId={agentId} online={online} />

      <div className="sessions-toolbar">
        <Link className="button button-primary" to={newSessionPath(agentId)}>
          New session
        </Link>
        <button
          type="button"
          className="button"
          onClick={() => {
            forceReconnectAgentEvents(agentId);
            // `refetch()` ignores `enabled`, so refetching while offline would
            // fire exactly the 503 (twice, then a toast) that the gate exists to
            // prevent. Reconnecting the socket is the whole of Refresh here —
            // the agent pushes a snapshot as soon as it is back.
            if (online) void sessions.refetch();
          }}
        >
          Refresh
        </button>
      </div>

      {/* Offline is checked before `isPending`: a disabled query with a cold
          cache reports pending forever, which would render a permanent spinner. */}
      {!online ? (
        <p className="empty-state">This agent is offline.</p>
      ) : sessions.isPending ? (
        <p className="page-note">Loading sessions…</p>
      ) : sessions.isError ? (
        <p className="auth-error">Failed to load sessions.</p>
      ) : sessions.data.length === 0 ? (
        <p className="empty-state">No active sessions</p>
      ) : (
        <ul className="device-list session-list">
          {sessions.data.map((session) => (
            <SessionCard
              key={session.session_id}
              agentId={agentId}
              session={session}
              kindLabel={sessionKindLabel(session, names)}
              onOpen={(sessionId) => {
                // Web writes this on open as well as on create — attaching to a
                // session started elsewhere is the browser's main job, so a
                // create-only write would leave auto-resume dormant. Android and
                // iOS follow in CLA-229 / CLA-230.
                setLastUsedSession(agentId, sessionId);
              }}
              onStop={setStopTarget}
            />
          ))}
        </ul>
      )}

      {stopTarget !== null && (
        <StopSessionDialog
          session={stopTarget}
          busy={stop.isPending}
          onStop={(force) => {
            stop.mutate({ sessionId: stopTarget.session_id, force });
          }}
          onCancel={() => {
            setStopTarget(null);
          }}
        />
      )}
    </>
  );
}
