export type UserTokenProvider = () => string | null;
export type AgentTokenProvider = (agentId: string) => string | null;

let userTokenProvider: UserTokenProvider = () => null;
let agentTokenProvider: AgentTokenProvider = () => null;

/**
 * The two client axes authenticate differently (mirrors the Android split in
 * CentralAuthInterceptor vs AuthInterceptor): relay-axis routes take the
 * dashboard/user JWT, while `/proxy/{agentId}` routes require the per-agent
 * `mobile_access` JWT minted for that agent. WEB-03 wires the real sources;
 * until then requests go out unauthenticated.
 */
export function setUserTokenProvider(next: UserTokenProvider): void {
  userTokenProvider = next;
}

export function getUserToken(): string | null {
  return userTokenProvider();
}

export function setAgentTokenProvider(next: AgentTokenProvider): void {
  agentTokenProvider = next;
}

export function getAgentToken(agentId: string): string | null {
  return agentTokenProvider(agentId);
}
