/**
 * Generate a Google Sign-In nonce: SHA-256 over 32 random bytes, lowercase hex.
 *
 * Byte-identical to Android's `GoogleAuthClient.generateNonce()` and iOS's
 * `AuthNonce.generateNonce()`, so all three clients present the same shape to
 * `_verify_google_token_claims`.
 *
 * At sign-in the server does not check it (`/api/auth/google` passes
 * `expected_nonce=None`), but account deletion does: the nonce sent alongside
 * the fresh `id_token` must equal the one bound into that token, within a
 * 5-minute `iat` window. The caller therefore has to keep the nonce it used.
 *
 * `crypto.subtle` requires a secure context — the same constraint as
 * `crypto.randomUUID`, so it holds wherever Google Sign-In itself works.
 */
export async function generateNonce(): Promise<string> {
  const seed = crypto.getRandomValues(new Uint8Array(32));
  const digest = await crypto.subtle.digest('SHA-256', seed);
  return Array.from(new Uint8Array(digest), (b) =>
    b.toString(16).padStart(2, '0'),
  ).join('');
}
