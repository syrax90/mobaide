/**
 * Google Identity Services integration.
 *
 * Ported from `central_server/static/site/portal.js`, which is the proven
 * browser path for this deployment — same client id, same popup flow. Two of
 * its decisions carry over and should not be revisited casually:
 *
 *  - `renderButton`, never `google.accounts.id.prompt()`. On Chrome M125+
 *    `prompt()` routes through FedCM, which silently suppresses the UI when
 *    the per-origin FedCM state is cooled down or disabled, leaving the user
 *    with no recoverable path.
 *  - `ux_mode: 'popup'`. The redirect mode would navigate away from the SPA
 *    and lose in-memory state.
 *
 * The client id comes from `GET /api/version`, so a deployment configures
 * Google in exactly one place (the server's `GOOGLE_CLIENT_ID`). The origin
 * serving the SPA must also be listed as an Authorized JavaScript origin on
 * that OAuth client, and the SPA's CSP must allow accounts.google.com — see
 * the `(app_mobaide)` block in `deploy/prod/Caddyfile.*`.
 */
const GIS_SCRIPT_URL = 'https://accounts.google.com/gsi/client';

export interface GoogleCredentialResponse {
  credential: string;
}

interface GisIdApi {
  initialize: (config: {
    client_id: string;
    nonce: string;
    ux_mode: 'popup';
    callback: (response: GoogleCredentialResponse) => void;
  }) => void;
  renderButton: (
    parent: HTMLElement,
    options: {
      type: 'standard';
      theme: string;
      size: string;
      shape: string;
      logo_alignment: string;
      text: string;
      width: number;
    },
  ) => void;
  disableAutoSelect: () => void;
}

declare global {
  interface Window {
    google?: { accounts?: { id?: GisIdApi } };
  }
}

let loader: Promise<GisIdApi> | null = null;

/**
 * Load the GIS client once and resolve when its API is actually reachable.
 *
 * The `onload` event fires before `window.google.accounts.id` is necessarily
 * populated, so a short poll follows — the same belt-and-braces `portal.js`
 * uses, minus its long budget: there the script tag is `async defer` in the
 * document head and the poll may start before the download does, whereas here
 * the injection and the wait are the same call.
 */
export function loadGoogleIdentity(): Promise<GisIdApi> {
  if (loader !== null) return loader;
  loader = new Promise<GisIdApi>((resolve, reject) => {
    const ready = (): GisIdApi | null => window.google?.accounts?.id ?? null;
    const existing = ready();
    if (existing !== null) {
      resolve(existing);
      return;
    }

    const settleWhenReady = () => {
      const deadline = Date.now() + 10_000;
      const poll = () => {
        const api = ready();
        if (api !== null) {
          resolve(api);
          return;
        }
        if (Date.now() > deadline) {
          // Drop the memoised promise so a later attempt can retry. Without
          // this, one timeout makes Google Sign-In permanently unavailable
          // for the rest of the page's life.
          loader = null;
          reject(new Error('Google Sign-In did not finish loading'));
          return;
        }
        setTimeout(poll, 50);
      };
      poll();
    };

    const selector = `script[src="${GIS_SCRIPT_URL}"]`;
    const already = document.querySelector<HTMLScriptElement>(selector);
    if (already !== null) {
      settleWhenReady();
      return;
    }
    const script = document.createElement('script');
    script.src = GIS_SCRIPT_URL;
    script.async = true;
    script.defer = true;
    script.onload = settleWhenReady;
    script.onerror = () => {
      // Remove the dead element as well as resetting the memo: leaving it in
      // the head makes the next attempt take the `already` branch above and
      // wait out the full poll deadline against a script that never loaded.
      script.remove();
      loader = null;
      reject(new Error('Google Sign-In could not be loaded'));
    };
    document.head.appendChild(script);
  });
  return loader;
}

export interface InitGoogleIdentityOptions {
  clientId: string;
  nonce: string;
  onCredential: (credential: string) => void;
}

/**
 * Arm GIS with the client id, the nonce, and the callback that answers.
 *
 * Deliberately separate from `renderGoogleButton`: `initialize` is what binds
 * the nonce a popup's token will be minted against, so re-running it while an
 * attempt is in flight would leave the returning credential paired with a
 * nonce it does not carry. Appearance changes must go through the renderer
 * alone.
 */
export async function initGoogleIdentity(
  options: InitGoogleIdentityOptions,
): Promise<void> {
  const api = await loadGoogleIdentity();
  api.initialize({
    client_id: options.clientId,
    nonce: options.nonce,
    ux_mode: 'popup',
    callback: (response) => {
      options.onCredential(response.credential);
    },
  });
}

export interface RenderGoogleButtonOptions {
  parent: HTMLElement;
  /**
   * Chosen by the caller so this button can match the Apple one beside it,
   * which is drawn in our own CSS. GIS has no light-on-dark fill, so the pair
   * is black on a light surface and white on a dark one.
   */
  theme: 'filled_black' | 'outline';
  /** Google owns the visible label per their branding rules. */
  text?: 'signin_with' | 'signup_with' | 'continue_with';
}

/**
 * Draw a GIS button into `parent`, which `initGoogleIdentity` must have armed.
 *
 * `width` makes GIS emit an inline `style=` attribute on the button — covered
 * by the `style-src 'unsafe-inline'` the SPA CSP concedes, which the GIS
 * client needs regardless because it appends a `<style>` to `<head>` on load.
 */
export async function renderGoogleButton(
  options: RenderGoogleButtonOptions,
): Promise<void> {
  const api = await loadGoogleIdentity();
  // The element can be unmounted while the script was loading; rendering into
  // a detached node is wasted work and leaves GIS holding a stale reference.
  if (!options.parent.isConnected) return;
  // A theme change redraws into the same container; GIS appends rather than
  // replacing, so without this the old button survives underneath the new one.
  options.parent.replaceChildren();
  api.renderButton(options.parent, {
    type: 'standard',
    theme: options.theme,
    size: 'large',
    shape: 'rectangular',
    logo_alignment: 'left',
    text: options.text ?? 'continue_with',
    width: 320,
  });
}

/**
 * Forget the auto-selected account.
 *
 * Called before re-authenticating for account deletion: without it GIS may
 * hand back a cached credential, and the server requires an `id_token` minted
 * within the last 5 minutes for that destructive action.
 */
export function disableGoogleAutoSelect(): void {
  window.google?.accounts?.id?.disableAutoSelect();
}

/** Test seam — drops the memoised loader. */
export function resetGoogleIdentityLoader(): void {
  loader = null;
}
