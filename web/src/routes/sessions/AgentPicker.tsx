import type { Agent } from '../../api/agentRepository';

interface AgentPickerProps {
  agents: readonly Agent[];
  selectedAgentId: string;
  onSelect: (agentId: string) => void;
}

/**
 * Minimal agent selector — enough to make the sessions list reachable. Pairing,
 * renaming and revoking are WEB-08.
 */
export function AgentPicker({ agents, selectedAgentId, onSelect }: AgentPickerProps) {
  const selected = agents.find((agent) => agent.agentId === selectedAgentId);
  const dot = (
    <span
      className={`agent-status${selected?.online === true ? ' agent-status-online' : ''}`}
      aria-hidden="true"
    />
  );

  // A select with one option is noise, and the name is the only thing worth
  // showing when there is no choice to make.
  if (agents.length === 1) {
    return (
      <p className="agent-current">
        {dot}
        <span className="agent-name">{selected?.name ?? selectedAgentId}</span>
      </p>
    );
  }

  return (
    <div className="agent-current">
      {dot}
      <select
        className="field-input agent-select"
        aria-label="Agent"
        value={selectedAgentId}
        onChange={(event) => {
          onSelect(event.target.value);
        }}
      >
        {agents.map((agent) => (
          <option key={agent.agentId} value={agent.agentId}>
            {/* An <option> cannot carry the status dot, so offline is spelled out. */}
            {agent.online ? agent.name : `${agent.name} (offline)`}
          </option>
        ))}
      </select>
    </div>
  );
}
