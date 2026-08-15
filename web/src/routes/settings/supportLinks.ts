/**
 * Support and marketing-site surfaces. The support trio mirrors iOS `AppConfig`
 * and Android's `BuildConfig` defaults; the two marketing links below have no
 * mobile counterpart, because only a browser client needs a way back to the site
 * that sent it here.
 *
 * Plain constants rather than build-time configuration, matching the precedent
 * the store links set on iOS: the server publishes none of these, and the SPA
 * deliberately takes no build-time environment variables — `GET /api/version` is
 * the one anonymous bootstrap call and it carries only the protocol gate and the
 * Google client id.
 */
export const MARKETING_SITE_URL = 'https://mobaide.com/';
export const PRIVACY_POLICY_URL = 'https://mobaide.com/privacy.html';

/**
 * The public deletion-info page, served by the central server under the retired
 * portal's path. That path is registered in Google Play Console's Data Safety
 * form, so it is fixed — do not "tidy" it onto this origin.
 */
export const ACCOUNT_DELETION_INFO_URL =
  'https://mobaide.com/portal/delete-account-info';
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
