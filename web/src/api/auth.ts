export type AuthTokenProvider = () => string | null;

let provider: AuthTokenProvider = () => null;

/**
 * Registers the source of the bearer token attached to every API request.
 * WEB-03 wires the real auth store here; until then requests go out
 * unauthenticated.
 */
export function setAuthTokenProvider(next: AuthTokenProvider): void {
  provider = next;
}

export function getAuthToken(): string | null {
  return provider();
}
