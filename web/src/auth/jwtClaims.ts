/**
 * The payload claims of a JWT, or null when the token cannot be decoded.
 *
 * Unverified by design: the browser never holds the signing key, and the
 * server checks every token it is sent. Read claims only to steer the client
 * (when to refresh, which device a session is bound to), never to decide what
 * the user may do.
 */
export function jwtClaims(token: string): Record<string, unknown> | null {
  const payload = token.split('.')[1];
  if (payload === undefined) return null;
  try {
    const claims: unknown = JSON.parse(
      atob(payload.replace(/-/g, '+').replace(/_/g, '/')),
    );
    return typeof claims === 'object' && claims !== null
      ? (claims as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}
