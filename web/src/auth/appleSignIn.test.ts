import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  AppleSignInCancelledError,
  initAppleIdentity,
  loadAppleIdentity,
  resetAppleIdentityLoader,
  signInWithApple,
} from './appleSignIn';

// Byte-identical to the URL `security_headers.APPLE_JS_SCRIPT_URL` names and
// both prod Caddyfiles allow: the locale segment is part of the policy, so a
// change here without a change there is a blocked script.
const APPLE_SCRIPT_URL =
  'https://appleid.cdn-apple.com/appleauth/static/jsapi/appleid/1/en_US/appleid.auth.js';
const SELECTOR = `script[src="${APPLE_SCRIPT_URL}"]`;

type AppleAuth = NonNullable<NonNullable<Window['AppleID']>['auth']>;
type InitConfig = Parameters<AppleAuth['init']>[0];
type SignInConfig = Parameters<AppleAuth['signIn']>[0];
type SignInResult = ReturnType<AppleAuth['signIn']>;

const initCalls: InitConfig[] = [];
const signInCalls: SignInConfig[] = [];
let answerSignIn: () => SignInResult;

/** Publish a stand-in for Apple's SDK on the window, as the real script does. */
function installAppleSdk(): AppleAuth {
  const auth: AppleAuth = {
    init: (config) => {
      initCalls.push(config);
    },
    signIn: (config) => {
      signInCalls.push(config);
      return answerSignIn();
    },
  };
  window.AppleID = { auth };
  return auth;
}

function injectedScript(): HTMLScriptElement | null {
  return document.head.querySelector<HTMLScriptElement>(SELECTOR);
}

beforeEach(() => {
  initCalls.length = 0;
  signInCalls.length = 0;
  answerSignIn = () =>
    Promise.resolve({ authorization: { id_token: 'apple-id-token' } });
});

afterEach(() => {
  resetAppleIdentityLoader();
  delete window.AppleID;
  document.head.querySelectorAll(SELECTOR).forEach((node) => {
    node.remove();
  });
});

describe('loadAppleIdentity', () => {
  it('injects the SDK once and settles when the global is actually reachable', () => {
    const first = loadAppleIdentity();
    const script = injectedScript();

    expect(script).not.toBeNull();
    // `onload` fires before `window.AppleID` is necessarily populated, which
    // is why the loader polls rather than resolving on the event.
    expect(loadAppleIdentity()).toBe(first);

    const auth = installAppleSdk();
    script?.dispatchEvent(new Event('load'));

    expect(document.head.querySelectorAll(SELECTOR)).toHaveLength(1);
    return expect(first).resolves.toBe(auth);
  });

  it('lets a later attempt retry after the download fails', async () => {
    const first = loadAppleIdentity();
    injectedScript()?.dispatchEvent(new Event('error'));

    await expect(first).rejects.toThrow('could not be loaded');
    // The dead element has to go too: leaving it in the head makes the next
    // attempt find a script that never loaded and wait out the full poll
    // deadline against it.
    expect(injectedScript()).toBeNull();

    const second = loadAppleIdentity();

    expect(second).not.toBe(first);
    expect(injectedScript()).not.toBeNull();
  });

  it('resolves without a second script when the SDK is already on the page', async () => {
    const auth = installAppleSdk();

    await expect(loadAppleIdentity()).resolves.toBe(auth);
    expect(injectedScript()).toBeNull();
  });
});

describe('initAppleIdentity', () => {
  it('configures popup mode against a return URL derived from this origin', async () => {
    installAppleSdk();

    await initAppleIdentity('com.mobaide.app.web');

    expect(initCalls).toEqual([
      {
        clientId: 'com.mobaide.app.web',
        // Matches iOS's `requestedScopes = [.email]`; `name` would only ever
        // arrive on a user's first authorization, already spent in the app.
        scope: 'email',
        // Never navigated to under `usePopup`, but Apple still checks it
        // against the Services ID's registered Return URLs.
        redirectURI: `${window.location.origin}/login`,
        usePopup: true,
      },
    ]);
  });
});

describe('signInWithApple', () => {
  it('refuses before the SDK is reachable rather than throwing on undefined', async () => {
    await expect(signInWithApple('nonce-1')).rejects.toThrow('not ready');
  });

  it('binds the nonce it was handed to each attempt', async () => {
    installAppleSdk();

    await expect(signInWithApple('nonce-1')).resolves.toBe('apple-id-token');
    await expect(signInWithApple('nonce-2')).resolves.toBe('apple-id-token');

    // Per call, never at `init`: the server compares this value with the
    // token's own `nonce` claim, so a stale one is a 401.
    expect(signInCalls).toEqual([{ nonce: 'nonce-1' }, { nonce: 'nonce-2' }]);
  });

  it('treats a dismissed popup as a cancellation, not a failure', async () => {
    installAppleSdk();
    // Apple's bundle rejects with a bare `{error}` object, not an Error — the
    // whole reason `signInWithApple` normalises at all. Rejecting with a real
    // Error here would test a shape the SDK never produces.
    // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
    answerSignIn = () => Promise.reject({ error: 'popup_closed_by_user' });

    await expect(signInWithApple('nonce-1')).rejects.toBeInstanceOf(
      AppleSignInCancelledError,
    );
  });

  it('reports a blocked popup as the one thing the user can act on', async () => {
    installAppleSdk();
    // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
    answerSignIn = () => Promise.reject({ error: 'popup_blocked_by_browser' });

    const failure = await signInWithApple('nonce-1').catch((cause: unknown) => cause);

    // Swallowing this one as a cancellation would leave the user clicking a
    // button that visibly does nothing.
    expect(failure).not.toBeInstanceOf(AppleSignInCancelledError);
    expect((failure as Error).message).toContain('Allow pop-ups');
  });

  it('fails when Apple answers without an identity token', async () => {
    installAppleSdk();
    answerSignIn = () => Promise.resolve({});

    await expect(signInWithApple('nonce-1')).rejects.toThrow(
      'no identity token',
    );
  });
});
