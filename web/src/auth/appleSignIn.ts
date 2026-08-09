/**
 * Sign in with Apple JS integration.
 *
 * Sibling of `googleSignIn.ts` and deliberately the same shape — one memoised
 * loader, a poll because `onload` precedes the global being populated, and a
 * reset on failure so a later attempt can retry. Three decisions are specific
 * to Apple and were read out of the shipped SDK bundle rather than the docs:
 *
 *  - `usePopup: true`. Without it the SDK calls `window.location.assign`,
 *    which leaves the SPA entirely and comes back as a cross-site form POST.
 *    With it, the SDK opens a real popup window (`response_mode=web_message`)
 *    and resolves a promise from a `postMessage` listener — which is also why
 *    the SPA needs `Cross-Origin-Opener-Policy: same-origin-allow-popups` and
 *    no `frame-src`/`connect-src` concession at all.
 *  - `redirectURI` is required even though the browser never navigates to it;
 *    Apple only checks it against the Return URLs registered for the Services
 *    ID. Deriving it from the current origin lets one Services ID serve every
 *    host the SPA is deployed to, provided each is registered.
 *  - The nonce is passed per call, not at `init`. The SDK copies a `signIn()`
 *    config onto a throwaway object, so concurrent mounts cannot clobber each
 *    other's nonce, and it forwards the value untouched — Apple echoes it back
 *    in the `nonce` claim verbatim, exactly as the iOS client relies on.
 *
 * The Services ID comes from `GET /api/version`, so a deployment configures
 * Apple in exactly one place (the server's `APPLE_WEB_CLIENT_ID`).
 */
const APPLE_SCRIPT_URL =
  'https://appleid.cdn-apple.com/appleauth/static/jsapi/appleid/1/en_US/appleid.auth.js';

/**
 * Path of the registered Apple Return URL.
 *
 * `/login` exists as a real route, so if Apple ever falls back to a redirect
 * the user lands on the sign-in screen rather than a 404. Every caller uses
 * this one value — the delete-account panel included — so one Return URL per
 * origin is all an operator registers.
 */
const APPLE_REDIRECT_PATH = '/login';

interface AppleSignInResponse {
  authorization?: { id_token?: string };
}

interface AppleIdAuthApi {
  init: (config: {
    clientId: string;
    scope: string;
    redirectURI: string;
    usePopup: true;
  }) => void;
  signIn: (config: { nonce: string }) => Promise<AppleSignInResponse>;
}

declare global {
  interface Window {
    AppleID?: { auth?: AppleIdAuthApi };
  }
}

/** Thrown when the user dismissed Apple's popup — never surfaced as an error. */
export class AppleSignInCancelledError extends Error {
  constructor() {
    super('Apple sign-in was cancelled');
    this.name = 'AppleSignInCancelledError';
  }
}

let loader: Promise<AppleIdAuthApi> | null = null;

/** Load Apple's SDK once and resolve when its API is actually reachable. */
export function loadAppleIdentity(): Promise<AppleIdAuthApi> {
  if (loader !== null) return loader;
  loader = new Promise<AppleIdAuthApi>((resolve, reject) => {
    const ready = (): AppleIdAuthApi | null => window.AppleID?.auth ?? null;
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
          // Drop the memoised promise so a later attempt can retry, exactly as
          // the GIS loader does: one timeout must not disable Apple sign-in
          // for the rest of the page's life.
          loader = null;
          reject(new Error('Sign in with Apple did not finish loading'));
          return;
        }
        setTimeout(poll, 50);
      };
      poll();
    };

    const selector = `script[src="${APPLE_SCRIPT_URL}"]`;
    const already = document.querySelector<HTMLScriptElement>(selector);
    if (already !== null) {
      settleWhenReady();
      return;
    }
    const script = document.createElement('script');
    script.src = APPLE_SCRIPT_URL;
    script.async = true;
    script.defer = true;
    script.onload = settleWhenReady;
    script.onerror = () => {
      // Remove the dead element as well as resetting the memo: leaving it in
      // the head makes the next attempt take the `already` branch above and
      // wait out the full poll deadline against a script that never loaded.
      script.remove();
      loader = null;
      reject(new Error('Sign in with Apple could not be loaded'));
    };
    document.head.appendChild(script);
  });
  return loader;
}

/**
 * Load the SDK and configure it for this deployment.
 *
 * `init` is global and idempotent, so calling it per mount is safe; the
 * per-attempt nonce is not passed here precisely because it is not.
 */
export async function initAppleIdentity(clientId: string): Promise<void> {
  const auth = await loadAppleIdentity();
  auth.init({
    clientId,
    // Matches iOS's `requestedScopes = [.email]`. Requesting `name` would only
    // return anything on a user's very first authorization, which anyone
    // arriving from the app has already spent, and there is nowhere to store it.
    scope: 'email',
    redirectURI: `${window.location.origin}${APPLE_REDIRECT_PATH}`,
    usePopup: true,
  });
}

/**
 * Open Apple's popup and resolve the identity token it returns.
 *
 * Must be reached synchronously from a click handler — anything awaited first
 * spends the user activation and the browser blocks the popup. That is why
 * `initAppleIdentity` and the nonce are prepared at mount.
 */
export async function signInWithApple(nonce: string): Promise<string> {
  const auth = window.AppleID?.auth;
  if (auth === undefined) {
    throw new Error('Sign in with Apple is not ready');
  }
  let response: AppleSignInResponse;
  try {
    response = await auth.signIn({ nonce });
  } catch (cause) {
    // The SDK rejects with `{error: string}` for every terminal state. Closing
    // the popup, dismissing it, or opening a second flow are all the user
    // walking away; only a blocked popup is worth telling them about, because
    // it is the one they can fix.
    const code = (cause as { error?: unknown } | null)?.error;
    if (code === 'popup_blocked_by_browser') {
      throw new Error(
        'Your browser blocked the Apple sign-in window. Allow pop-ups for this site and try again.',
      );
    }
    throw new AppleSignInCancelledError();
  }
  const idToken = response.authorization?.id_token;
  if (idToken === undefined || idToken === '') {
    throw new Error('Apple returned no identity token');
  }
  return idToken;
}

/** Test seam — drops the memoised loader. */
export function resetAppleIdentityLoader(): void {
  loader = null;
}
