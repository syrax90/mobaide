import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AppleSignInCancelledError,
  initAppleIdentity,
  signInWithApple,
} from '../../auth/appleSignIn';
import { generateNonce } from '../../auth/nonce';

interface AppleSignInButtonProps {
  clientId: string | null;
  /** Apple's guidelines fix the wording to one of these three. */
  label?:
    | 'Sign in with Apple'
    | 'Sign up with Apple'
    | 'Continue with Apple';
  disabled?: boolean;
  /**
   * Receives the Apple identity token and the nonce it was minted with. Both
   * sign-in and account deletion need the pair — the server verifies the nonce
   * against the token's claim — so they always travel together.
   */
  onCredential: (idToken: string, nonce: string) => void;
  onError: (message: string) => void;
}

/**
 * A hand-drawn button, not `AppleID.auth.renderButton`.
 *
 * Apple's rendered button attaches its own click handler that signs in with
 * the globally-initialised nonce and reports through document events, leaving
 * no place to bind a per-attempt nonce or to disable the control mid-request.
 * Only the flow comes from the SDK; the appearance follows Apple's Human
 * Interface Guidelines directly.
 *
 * Renders nothing when the server has no `APPLE_WEB_CLIENT_ID` configured,
 * which is the same condition under which the portal omits its own button.
 */
export function AppleSignInButton({
  clientId,
  label = 'Continue with Apple',
  disabled = false,
  onCredential,
  onError,
}: AppleSignInButtonProps) {
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [pending, setPending] = useState(false);
  // Held in a ref, not state: the click handler must reach `signIn()` without
  // awaiting anything, or the browser has already spent the user activation
  // and blocks Apple's popup.
  const nonceRef = useRef<string | null>(null);

  const callbackRef = useRef(onCredential);
  callbackRef.current = onCredential;
  const errorRef = useRef(onError);
  errorRef.current = onError;

  // Apple's SDK resolves through a `postMessage` listener that outlives this
  // component, and its bundle has no timeout for a popup left open — so a
  // sign-in the user walked away from can land minutes later, after they have
  // navigated on. `markUserLoggedIn` is unconditional, so an ungated late
  // credential would swap out a session established some other way since.
  // `handleClick` cannot close over an effect-local flag, hence a ref — and
  // the setup half is load-bearing: refs survive a remount, so a cleanup-only
  // effect would latch off the first time StrictMode runs setup → cleanup →
  // setup and leave the button dead for the rest of the page's life.
  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (clientId === null) return;
    let cancelled = false;
    setFailed(false);
    setReady(false);

    void (async () => {
      try {
        const [nonce] = await Promise.all([
          generateNonce(),
          initAppleIdentity(clientId),
        ]);
        if (cancelled) return;
        nonceRef.current = nonce;
        setReady(true);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [clientId]);

  const handleClick = useCallback(() => {
    const nonce = nonceRef.current;
    if (nonce === null) return;
    setPending(true);
    signInWithApple(nonce)
      .then((idToken) => {
        if (!aliveRef.current) return;
        callbackRef.current(idToken, nonce);
      })
      .catch((cause: unknown) => {
        // Dismissing the popup is not a failure — leave the screen untouched.
        if (cause instanceof AppleSignInCancelledError) return;
        if (!aliveRef.current) return;
        errorRef.current(
          cause instanceof Error
            ? cause.message
            : 'Sign in with Apple failed. Try again.',
        );
      })
      .finally(() => {
        if (!aliveRef.current) return;
        setPending(false);
        // A nonce is single-use by intent: the server binds it to one token,
        // so a retry gets a fresh one rather than replaying the last.
        void generateNonce().then((next) => {
          nonceRef.current = next;
        });
      });
  }, []);

  if (clientId === null) return null;

  return (
    <div className="apple-signin">
      <button
        type="button"
        className="apple-signin-button"
        disabled={disabled || !ready || pending}
        onClick={handleClick}
      >
        {/* Duplicated in central_server/templates/_portal_apple_mark.html —
            the two projects share no asset pipeline. Change one, change the
            other. aria-hidden: the button's text already names the action. */}
        <svg
          className="apple-signin-logo"
          viewBox="0 0 14 17"
          aria-hidden="true"
          focusable="false"
        >
          <path
            fill="currentColor"
            d="M11.62 8.99c-.02-1.96 1.6-2.9 1.67-2.95-.91-1.33-2.33-1.51-2.83-1.53-1.2-.12-2.35.71-2.96.71-.61 0-1.56-.69-2.56-.67-1.32.02-2.53.77-3.21 1.95-1.37 2.38-.35 5.9 .99 7.83.65.94 1.43 2 2.45 1.96.98-.04 1.35-.63 2.54-.63 1.19 0 1.52.63 2.56.61 1.06-.02 1.73-.96 2.38-1.91.75-1.09 1.06-2.15 1.08-2.2-.02-.01-2.07-.8-2.09-3.17zM9.68 3.24c.54-.66.91-1.57.81-2.48-.78.03-1.73.52-2.29 1.18-.5.58-.94 1.51-.82 2.4.87.07 1.76-.44 2.3-1.1z"
          />
        </svg>
        <span>{label}</span>
      </button>
      {failed && (
        <p className="field-error">
          Sign in with Apple could not be loaded. Check your connection or use
          your email and password.
        </p>
      )}
    </div>
  );
}
