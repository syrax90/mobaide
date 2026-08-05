/**
 * Support surfaces, mirroring iOS `AppConfig` and Android's `BuildConfig`
 * defaults.
 *
 * Plain constants rather than build-time configuration, matching the precedent
 * the store links set on iOS: the server publishes none of these, and the SPA
 * deliberately takes no build-time environment variables — `GET /api/version` is
 * the one anonymous bootstrap call and it carries only the protocol gate and the
 * Google client id.
 */
export const PRIVACY_POLICY_URL = 'https://mobaide.com/privacy.html';
export const SUPPORT_EMAIL = 'support@mobaide.com';
export const SUPPORT_COMMUNITY_URL =
  'https://github.com/syrax90/mobaide-community/discussions';

/**
 * `mailto:` for the support address.
 *
 * The subject carries the protocol version, which is the one diagnostic worth
 * having up front: nearly every "it stopped working" report is a version gate.
 * iOS attaches a fuller block because it can compose the mail itself; a browser
 * hands off to whatever the user has, so the URL stays short enough to survive.
 */
export function supportMailtoUrl(protocolVersion: number): string {
  const subject = encodeURIComponent(`Mobaide web support (protocol v${String(protocolVersion)})`);
  return `mailto:${SUPPORT_EMAIL}?subject=${subject}`;
}
