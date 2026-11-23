import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router';
import { listAgents } from '../../api/agentRepository';
import { listAutoScans, runAutoScanNow, stopAutoScan } from '../../api/autoscanRepository';
import {
  autoScanStatusLabel,
  autoScanTone,
  countdownLabel,
  issueTone,
  phaseLabel,
  phaseTone,
  scanSourceAgentLabel,
} from '../../autoscan/autoscanDisplay';
import { orderedPhases } from '../../autoscan/autoscanModel';
import { useAutoScanDetail, useSecondTicker } from '../../autoscan/useAutoScanDetail';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import { useAgentEvents } from '../../realtime';
import { AutoScanLogPane } from './AutoScanLogPane';
import { scanFailureHandler } from './autoscanMutations';

/**
 * Live monitor for one workspace's Auto Scan.
 *
 * The route carries both ids, so a link is shareable and does not depend on the
 * selected-agent store. The inner component is remounted by key on either id
 * changing, because the reduction state — cursor, log ring, phase timeline — is
 * per workspace and there is nothing sensible to migrate.
 */
export function AutoScanDetailScreen() {
  const { agentId, workspaceName } = useParams();
  // Bumped when a stopped scan is started again elsewhere: `ws/autoscan` only
  // streams the scans that ran when it connected, and the new run's `seq`
  // starts over, so a fresh mount is the one clean way to follow it.
  const [run, setRun] = useState(0);
  if (agentId === undefined || workspaceName === undefined) {
    return <p className="auth-error">Unknown Auto Scan.</p>;
  }
  return (
    <AutoScanDetail
      key={`${agentId}:${workspaceName}:${String(run)}`}
      agentId={agentId}
      workspaceName={workspaceName}
      onRestarted={() => {
        setRun((n) => n + 1);
      }}
    />
  );
}

const CONNECTION_LABELS: Readonly<Record<string, string>> = {
  connecting: 'Connecting…',
  replaying: 'Catching up…',
  connected: 'Live',
  disconnected: 'Reconnecting…',
};

function AutoScanDetail({
  agentId,
  workspaceName,
  onRestarted,
}: {
  agentId: string;
  workspaceName: string;
  onRestarted: () => void;
}) {
  const queryClient = useQueryClient();
  const { state, declined, retry, markStopped, markRescheduled } = useAutoScanDetail(
    agentId,
    workspaceName,
  );

  // Not for this screen's own state — `state.instance` comes from the
  // `ws/autoscan` snapshot alone, so the chip and counts only move when the
  // socket re-snapshots (iOS behaves the same way). It is held open so the
  // agent channel survives the navigation here: that channel is what refreshes
  // the list cache and what dismisses answered attention notifications.
  useAgentEvents(agentId);

  const agents = useQuery({
    queryKey: queryKeys.agents(),
    queryFn: listAgents,
    ...wsBackedQueryOptions,
  });
  const online = agents.data?.find((agent) => agent.agentId === agentId)?.online ?? false;

  // The Sessions list cache, which the agent channel above keeps fresh. Only
  // read to notice a stopped scan running again.
  const scans = useQuery({
    queryKey: queryKeys.autoscan(agentId),
    queryFn: () => listAutoScans(agentId),
    enabled: online && state.stopped,
  });
  const listedStartedAt = scans.data?.scans.find(
    (scan) => scan.project_name === workspaceName,
  )?.started_at;
  // The run that was listed when the scan stopped: the list can still show it
  // for a moment after a Stop, and that row is no restart.
  const stoppedRunRef = useRef<number | undefined>(undefined);
  const wasStoppedRef = useRef(false);
  if (state.stopped && !wasStoppedRef.current) stoppedRunRef.current = listedStartedAt;
  wasStoppedRef.current = state.stopped;
  useEffect(() => {
    if (!state.stopped || listedStartedAt === undefined) return;
    if (listedStartedAt !== stoppedRunRef.current) onRestarted();
  }, [state.stopped, listedStartedAt, onRestarted]);

  // Stopped wins over the last instance row, as on iOS and Android: after a
  // Stop the row can linger until the next snapshot.
  const status = state.stopped ? 'stopped' : state.instance?.status;

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.autoscan(agentId) });

  const runNow = useMutation({
    mutationFn: () => runAutoScanNow(agentId, workspaceName),
    onSuccess: (response) => {
      markRescheduled(response.next_scan_at ?? undefined);
      return invalidate();
    },
    onError: scanFailureHandler('run', invalidate, markStopped),
  });

  const stop = useMutation({
    mutationFn: () => stopAutoScan(agentId, workspaceName),
    onSuccess: () => {
      // Locally as well, as Android does: the stream's `scan_stopped` can be
      // missed (the socket was reconnecting), and Run now / Stop would 404.
      markStopped();
      return invalidate();
    },
    onError: scanFailureHandler('stop', invalidate, markStopped),
  });

  const busy = runNow.isPending || stop.isPending;

  return (
    <section className="page autoscan-detail">
      <header className="page-header">
        <div>
          <h1>{workspaceName}</h1>
          <p className="page-note">
            <Link to="/">Sessions</Link>
            {' · '}
            {status === undefined
              ? (CONNECTION_LABELS[state.connection] ?? state.connection)
              : autoScanStatusLabel(status)}
            {state.cycleNumber > 0 && ` · cycle #${String(state.cycleNumber)}`}
          </p>
        </div>
        {/* A stopped scan has nothing to run or stop: both would 404. */}
        {!state.stopped && (
          <div className="button-row">
            <button
              type="button"
              className="button"
              disabled={busy || !online}
              onClick={() => {
                runNow.mutate();
              }}
            >
              Run now
            </button>
            <button
              type="button"
              className="button button-danger"
              disabled={busy || !online}
              onClick={() => {
                stop.mutate();
              }}
            >
              Stop
            </button>
          </div>
        )}
      </header>

      {!online && <p className="empty-state">This agent is offline.</p>}

      {/* The one close a reconnect loop must not paper over. `1013` arrives
          *after* the socket was accepted, so the backoff ladder has already been
          reset — left to the default policy it re-handshakes at the ~1 s floor
          for as long as this screen stays open. The hook latches it and stops;
          retrying is the user's call. */}
      {declined && (
        <div className="auth-error">
          <p>
            The agent turned down this log stream — it may already be running as many
            Auto Scan monitors as it allows. Close one elsewhere, then try again.
          </p>
          <button type="button" className="button" onClick={retry}>
            Try again
          </button>
        </div>
      )}

      <div className="autoscan-status-row">
        <span className={`autoscan-chip autoscan-chip-${autoScanTone(status ?? state.connection)}`}>
          {status === undefined
            ? (CONNECTION_LABELS[state.connection] ?? state.connection)
            : autoScanStatusLabel(status)}
        </span>
        {!state.stopped && <AutoScanCountdown nextScanAt={state.nextScanAt} />}
        {state.instance !== undefined && (
          <span className="page-note">{scanSourceAgentLabel(state.instance)}</span>
        )}
        {state.instance !== undefined && (
          <span className="page-note">Processed: {state.instance.processed_count}</span>
        )}
      </div>

      <h2 className="autoscan-heading">Pipeline</h2>
      <ol className="autoscan-phases">
        {orderedPhases(state).map((phase) => (
          <li
            key={phase.key}
            className={`autoscan-phase autoscan-phase-${phaseTone(phase.status)}`}
            aria-current={state.activePhase === phase.key ? 'step' : undefined}
          >
            <span className="autoscan-phase-name">{phaseLabel(phase.key)}</span>
            <span className="autoscan-phase-status">{phase.status}</span>
          </li>
        ))}
      </ol>

      <h2 className="autoscan-heading">Recent issues</h2>
      {state.recentIssues.length === 0 ? (
        <p className="empty-state">No issues processed yet.</p>
      ) : (
        <ul className="autoscan-issues">
          {/* The last five, like iOS: the model keeps fifty so a scroll-back
              would have something to show, but the screen's job is what is
              happening now. */}
          {state.recentIssues.slice(-5).map((issue, index) => (
            <li key={`${issue.identifier}:${issue.step}:${String(index)}`} className="autoscan-issue">
              <span className={`autoscan-chip autoscan-chip-${issueTone(issue.status)}`}>
                {issue.status}
              </span>
              <span className="autoscan-issue-id">{issue.identifier}</span>
              <span className="autoscan-issue-title">{issue.title}</span>
              <span className="autoscan-issue-step">{issue.step}</span>
              {issue.error !== undefined && (
                <span className="autoscan-issue-error">{issue.error}</span>
              )}
            </li>
          ))}
        </ul>
      )}

      <h2 className="autoscan-heading">Output</h2>
      <AutoScanLogPane lines={state.logLines} stopped={state.stopped} />
    </section>
  );
}

/**
 * The countdown, and the 1 Hz clock that drives it, isolated in their own
 * component — the split iOS makes with `CountdownView`, for the same reason.
 * Ticking in the screen component would re-render the log pane every second, a
 * cost proportional to the 2000-line ring and independent of how much the scan
 * is actually saying.
 */
function AutoScanCountdown({ nextScanAt }: { nextScanAt: string | undefined }) {
  const now = useSecondTicker(nextScanAt !== undefined);
  const countdown = countdownLabel(nextScanAt, now);
  if (countdown === null) return null;
  return <span className="autoscan-countdown">{countdown}</span>;
}
