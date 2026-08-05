import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import { listAgents } from '../../api/agentRepository';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import { useSelectedAgentStore } from '../../stores/selectedAgentStore';
import { AgentPicker } from '../sessions/AgentPicker';
import { resolveSelectedAgent } from '../sessions/selectAgent';
import { ProjectList } from './ProjectList';

/**
 * Projects are agent-scoped, so this screen wears the same agent picker as the
 * sessions list and shares its selection (`mobaide-agent`) — switching agents in
 * one place should not silently disagree with the other.
 */
export function ProjectsScreen() {
  const storedAgentId = useSelectedAgentStore((state) => state.selectedAgentId);
  const setSelectedAgent = useSelectedAgentStore((state) => state.setSelectedAgent);

  const agents = useQuery({
    queryKey: queryKeys.agents(),
    queryFn: listAgents,
    ...wsBackedQueryOptions,
  });

  const resolved = resolveSelectedAgent(agents.data ?? [], storedAgentId);

  useEffect(() => {
    if (resolved !== null && resolved !== storedAgentId) setSelectedAgent(resolved);
  }, [resolved, storedAgentId, setSelectedAgent]);

  const selected = agents.data?.find((agent) => agent.agentId === resolved);

  return (
    <section className="page">
      <header className="page-header">
        <h1>Projects</h1>
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
        <p className="empty-state">
          No agents yet. <Link to="/pair">Pair an agent</Link> to manage its projects
          here.
        </p>
      ) : (
        <ProjectList
          // Remount on agent change so the list, its channel and any open undo
          // window all start clean.
          key={resolved}
          agentId={resolved}
          online={selected?.online ?? false}
        />
      )}
    </section>
  );
}
