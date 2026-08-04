import type { Agent } from '../../api/agentRepository';

interface AgentCardProps {
  agent: Agent;
  busy: boolean;
  onReregister: (agent: Agent) => void;
  onDelete: (agent: Agent) => void;
}

/**
 * One agent row.
 *
 * Android shows the agent id in monospace and iOS does not; web keeps it,
 * because a browser user pairing several hosts is the first person who can see
 * more than one agent at a time and the id is what the `.env` on each host
 * carries. The agent version is web-only for the same reason — mobile's
 * `AgentSummaryResponse` decodes it but neither client renders it, and it is
 * the fastest way to spot the host that missed an auto-update.
 */
export function AgentCard({ agent, busy, onReregister, onDelete }: AgentCardProps) {
  return (
    <li className="device-card">
      <div className="device-card-head">
        <span
          className={`agent-status${agent.online ? ' agent-status-online' : ''}`}
          aria-hidden="true"
        />
        <span className="device-name">{agent.name}</span>
        <span className="agent-state-label">{agent.online ? 'Online' : 'Offline'}</span>
        <button
          type="button"
          className="button"
          disabled={busy}
          onClick={() => {
            onReregister(agent);
          }}
        >
          Re-register
        </button>
        <button
          type="button"
          className="button button-danger"
          disabled={busy}
          onClick={() => {
            onDelete(agent);
          }}
        >
          Remove
        </button>
      </div>
      <dl className="device-meta">
        <div>
          <dt>Agent id</dt>
          <dd className="device-id">{agent.agentId}</dd>
        </div>
        <div>
          <dt>Version</dt>
          <dd>{agent.agentVersion ?? '—'}</dd>
        </div>
        <div>
          <dt>Added</dt>
          <dd>{agent.createdAt}</dd>
        </div>
        <div>
          <dt>Last seen</dt>
          <dd>{agent.lastSeenAt ?? '—'}</dd>
        </div>
      </dl>
    </li>
  );
}
