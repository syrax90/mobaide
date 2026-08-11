import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import { listAgents } from '../../api/agentRepository';
import { ErrorState } from '../../components/ErrorState';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import { useSelectedAgentStore } from '../../stores/selectedAgentStore';
import { AgentPicker } from './AgentPicker';
import { SessionList } from './SessionList';
import { resolveSelectedAgent } from './selectAgent';

export function SessionsScreen() {
  const storedAgentId = useSelectedAgentStore((state) => state.selectedAgentId);
  const setSelectedAgent = useSelectedAgentStore((state) => state.setSelectedAgent);

  const agents = useQuery({
    queryKey: queryKeys.agents(),
    queryFn: listAgents,
    // `agent.status_changed` on the user channel invalidates this, and every
    // user-channel reconnect refreshes it, so time-based staleness adds nothing.
    ...wsBackedQueryOptions,
  });

  const resolved = resolveSelectedAgent(agents.data ?? [], storedAgentId);

  // Write the resolution back, which makes the automatic pick sticky. Without
  // it, "first online agent" would silently move the user's list whenever an
  // agent went offline and move it back when it returned.
  useEffect(() => {
    if (resolved !== null && resolved !== storedAgentId) setSelectedAgent(resolved);
  }, [resolved, storedAgentId, setSelectedAgent]);

  const selected = agents.data?.find((agent) => agent.agentId === resolved);

  return (
    <section className="page">
      <header className="page-header">
        <h1>Sessions</h1>
        {agents.data !== undefined && resolved !== null && (
          <AgentPicker
            agents={agents.data}
            selectedAgentId={resolved}
            onSelect={setSelectedAgent}
          />
        )}
      </header>

      {agents.isPending ? (
        <p className="page-note state-loading">Loading agents…</p>
      ) : agents.isError ? (
        <ErrorState
          message="Failed to load agents."
          onRetry={() => void agents.refetch()}
        />
      ) : resolved === null ? (
        <p className="empty-state">
          No agents yet. <Link to="/pair">Pair an agent</Link> to see its sessions here.
        </p>
      ) : (
        <SessionList
          // Remount on agent change so the list, its channel and the
          // once-per-agent auto-resume attempt all start clean.
          key={resolved}
          agentId={resolved}
          online={selected?.online ?? false}
        />
      )}
    </section>
  );
}
