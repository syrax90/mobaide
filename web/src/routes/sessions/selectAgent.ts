import type { Agent } from '../../api/agentRepository';

/**
 * Which agent the sessions list should show.
 *
 * The caller writes the result back to the store, which makes the automatic
 * pick **sticky**. Without that, "first online agent" is unstable: agent A
 * dropping offline would silently move the user's list to agent B, and A
 * returning would move it back.
 */
export function resolveSelectedAgent(
  agents: readonly Agent[],
  storedId: string | null,
): string | null {
  if (agents.length === 0) return null;
  if (storedId !== null && agents.some((agent) => agent.agentId === storedId)) {
    return storedId;
  }
  const online = agents.find((agent) => agent.online);
  return (online ?? agents[0])?.agentId ?? null;
}
