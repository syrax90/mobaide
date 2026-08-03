export type UserTokenProvider = () => string | null;
export type AgentTokenProvider = (agentId: string) => string | null;
/** Resolves once the user access token is fresh enough to send. */
export type UserTokenRefresher = () => Promise<void>;

let userTokenProvider: UserTokenProvider = () => null;
let agentTokenProvider: AgentTokenProvider = () => null;
let userTokenRefresher: UserTokenRefresher = () => Promise.resolve();

/**
 * The two client axes authenticate differently (mirrors the Android split in
 * CentralAuthInterceptor vs AuthInterceptor): relay-axis routes take the
 * dashboard/user JWT, while `/proxy/{agentId}` routes require the per-agent
 * `mobile_access` JWT minted for that agent.
 *
 * `installAuthProviders` (src/auth/install.ts) wires both to the real session
 * at startup. They stay indirections rather than direct imports so that
 * `clients.ts` does not depend on the auth module — that would make the import
 * cycle clients -> auth -> clients, and would drag session state into every
 * test that only wants to assert on request shape.
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

/**
 * Register the pre-emptive refresh the relay client awaits before attaching a
 * bearer. A separate seam from the provider because the provider must stay
 * synchronous — it is read on the response path too.
 */
export function setUserTokenRefresher(next: UserTokenRefresher): void {
  userTokenRefresher = next;
}

export function refreshUserTokenIfNeeded(): Promise<void> {
  return userTokenRefresher();
}

/** Test seam — restores the unauthenticated defaults. */
export function resetTokenProviders(): void {
  userTokenProvider = () => null;
  agentTokenProvider = () => null;
  userTokenRefresher = () => Promise.resolve();
}
