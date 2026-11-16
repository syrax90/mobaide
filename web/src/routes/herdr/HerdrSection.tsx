import { Link } from 'react-router';
import { herdrPaneRows, herdrRollup, herdrSummary } from '../../herdr/herdrDisplay';
import { useHerdrSeenStore } from '../../herdr/herdrSeenStore';
import { useHerdr } from '../../herdr/useHerdr';
import { HerdrStatusChip, HerdrStatusMark } from './HerdrStatus';
import { herdrPath } from './herdrPath';

interface HerdrSectionProps {
  agentId: string;
  online: boolean;
}

/**
 * The Herdr card above the session list, beside the Auto Scan section: one
 * line that says how many agents run in Herdr and whether one needs the user.
 *
 * Shown only while a herdr server runs on the agent's computer. An agent
 * without the dashboard, and a computer without herdr, show nothing.
 */
export function HerdrSection({ agentId, online }: HerdrSectionProps) {
  const { supported, snapshot } = useHerdr(agentId, online);
  const seen = useHerdrSeenStore((state) => state.seen);

  const servers = snapshot.data?.servers ?? [];
  if (!online || !supported || servers.length === 0) return null;

  const rows = herdrPaneRows(agentId, snapshot.data, seen);
  const status = herdrRollup(rows.map((row) => row.status));

  return (
    <section className="autoscan-section" aria-labelledby="herdr-section-heading">
      <h2 id="herdr-section-heading" className="autoscan-section-title">
        Herdr
      </h2>
      <ul className="device-list">
        <li className="device-card session-card">
          <Link className="session-card-link" to={herdrPath(agentId)}>
            <HerdrStatusMark status={status} />
            <span className="session-text">
              <span className="session-title">Herdr workspaces</span>
              <span className="session-kind">{herdrSummary(rows)}</span>
            </span>
          </Link>
          {/* Only a status that asks for the user: "Working" is in the summary. */}
          {(status === 'blocked' || status === 'failed' || status === 'done') && (
            <HerdrStatusChip status={status} />
          )}
        </li>
      </ul>
    </section>
  );
}
