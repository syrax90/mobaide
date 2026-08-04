import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listAgents } from '../../api/agentRepository';
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
        <p className="page-note">Loading agents…</p>
      ) : agents.isError ? (
        <p className="auth-error">Failed to load agents.</p>
      ) : resolved === null ? (
        // No link: pairing lives in the mobile apps until WEB-08 lands.
        <p className="empty-state">
          No agents yet. Pair an agent from the Mobaide mobile app to see its sessions here.
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
