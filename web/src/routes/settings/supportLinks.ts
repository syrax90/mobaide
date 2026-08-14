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
 * The subject carries no version diagnostic. It used to name the protocol
 * version, but the browser hands the URL to whatever mail client the user has,
 * so the number ended up in front of the one person it means nothing to.
 */
export const SUPPORT_MAILTO_URL = `mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(
  'Mobaide web support',
)}`;
