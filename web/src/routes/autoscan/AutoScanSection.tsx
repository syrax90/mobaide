import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router';
import { listAutoScans, runAutoScanNow, stopAutoScan } from '../../api/autoscanRepository';
import {
  autoScanStatusLabel,
  autoScanTone,
  countdownLabel,
  relativeTime,
  sortScans,
} from '../../autoscan/autoscanDisplay';
import { useSecondTicker } from '../../autoscan/useAutoScanDetail';
import { queryKeys } from '../../lib/queryKeys';
import type { AutoScanInstance } from '../../protocol/dto/autoscan';
import { scanFailureHandler } from './autoscanMutations';
import { autoScanDetailPath } from './autoscanPath';

interface AutoScanSectionProps {
  agentId: string;
  online: boolean;
}

/**
 * "Active Auto Scans" above the session list, mirroring where iOS mounts its
 * `AutoScanCard`. There is no separate list route: a scan is something running
 * on the agent you are already looking at, and burying it behind another tab
 * would hide the thing the monitor exists to watch.
 *
 * The list rides the shared agent-events bus that `SessionList` already opened —
 * `autoscan.list.changed` and `autoscan.status` are written into this exact key
 * by `realtime/autoscanCache.ts`. Only the detail screen opens the dedicated
 * `ws/autoscan` channel.
 *
 * Deliberately no `wsBackedQueryOptions`: this key is also invalidated on every
 * agent-channel connect, because the relay omits the Auto Scan list from its
 * connect snapshot (CLA-249), and pinning `staleTime: Infinity` would let an
 * invalidation landing during a cold fetch strand the stale result (CLA-234).
 */
export function AutoScanSection({ agentId, online }: AutoScanSectionProps) {
  const queryClient = useQueryClient();

  const list = useQuery({
    queryKey: queryKeys.autoscan(agentId),
    queryFn: () => listAutoScans(agentId),
    // The relay answers 503 for an offline agent, which the global query-error
    // toast would fire on every mount.
    enabled: online,
  });

  const scans = list.data === undefined ? [] : sortScans(list.data.scans);
  // One clock for the whole section, so two rows can never disagree about now —
  // and stopped while there is nothing to count down to.
  const now = useSecondTicker(scans.length > 0);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.autoscan(agentId) });

  const stop = useMutation({
    mutationFn: (projectName: string) => stopAutoScan(agentId, projectName),
    // The agent publishes `autoscan.list.changed` on stop, which is the honest
    // source; this only covers the socket being down.
    onSuccess: invalidate,
    onError: scanFailureHandler('stop', invalidate),
  });

  const runNow = useMutation({
    mutationFn: (projectName: string) => runAutoScanNow(agentId, projectName),
    onSuccess: invalidate,
    onError: scanFailureHandler('run', invalidate),
  });

  // Nothing running is the normal state, and an empty heading over an empty list
  // is noise on a screen whose subject is sessions.
  if (!online || scans.length === 0) return null;

  return (
    <section className="autoscan-section" aria-labelledby="autoscan-section-heading">
      <h2 id="autoscan-section-heading" className="autoscan-section-title">
        Active Auto Scans
      </h2>
      <ul className="device-list autoscan-list">
        {scans.map((scan) => (
          <AutoScanRow
            key={scan.project_name}
            agentId={agentId}
            scan={scan}
            now={now}
            busy={
              (stop.isPending && stop.variables === scan.project_name) ||
              (runNow.isPending && runNow.variables === scan.project_name)
            }
            onRunNow={runNow.mutate}
            onStop={stop.mutate}
          />
        ))}
      </ul>
    </section>
  );
}

interface AutoScanRowProps {
  agentId: string;
  scan: AutoScanInstance;
  now: number;
  busy: boolean;
  onRunNow: (projectName: string) => void;
  onStop: (projectName: string) => void;
}

function AutoScanRow({ agentId, scan, now, busy, onRunNow, onStop }: AutoScanRowProps) {
  const countdown = countdownLabel(scan.next_scan_at, now);
  const activity = relativeTime(scan.last_activity_at, now);

  return (
    <li className="autoscan-card">
      <div className="autoscan-card-head">
        <span
          className={`session-dot session-dot-${autoScanTone(scan.status)}`}
          aria-hidden="true"
        />
        <Link className="autoscan-card-name" to={autoScanDetailPath(agentId, scan.project_name)}>
          {scan.project_name}
        </Link>
        <span className={`autoscan-chip autoscan-chip-${autoScanTone(scan.status)}`}>
          {autoScanStatusLabel(scan.status)}
        </span>
      </div>

      <p className="autoscan-card-meta">
        <span>Cycle {scan.cycle_number}</span>
        <span>Processed: {scan.processed_count}</span>
        {activity !== '' && <span>{activity}</span>}
      </p>

      {countdown !== null && <p className="autoscan-countdown">{countdown}</p>}

      <div className="button-row">
        <button
          type="button"
          className="button"
          disabled={busy}
          onClick={() => {
            onRunNow(scan.project_name);
          }}
        >
          Run now
        </button>
        <button
          type="button"
          className="button button-danger"
          disabled={busy}
          onClick={() => {
            onStop(scan.project_name);
          }}
        >
          Stop
        </button>
      </div>
    </li>
  );
}
