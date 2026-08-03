import { useEffect, useRef, useState } from 'react';
import { renderGoogleButton } from '../../auth/googleSignIn';
import { generateNonce } from '../../auth/nonce';

interface GoogleSignInButtonProps {
  clientId: string | null;
  text?: 'signin_with' | 'signup_with' | 'continue_with';
  /**
   * Receives the Google credential and the nonce it was minted with. Account
   * deletion needs both — the server verifies the nonce against the token's
   * claim — so the pair always travels together.
   */
  onCredential: (credential: string, nonce: string) => void;
}

/**
 * Renders nothing when the server has no `GOOGLE_CLIENT_ID` configured, which
 * is the same condition under which the portal omits its own button.
 */
export function GoogleSignInButton({
  clientId,
  text,
  onCredential,
}: GoogleSignInButtonProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);

  // The callback identity changes on every parent render, so keeping it in a
  // ref lets the effect depend only on the client id — otherwise GIS would be
  // re-initialised and the button re-rendered on each keystroke in the form.
  const callbackRef = useRef(onCredential);
  callbackRef.current = onCredential;

  useEffect(() => {
    const parent = containerRef.current;
    if (clientId === null || parent === null) return;
    let cancelled = false;
    setFailed(false);

    void (async () => {
      try {
        const nonce = await generateNonce();
        if (cancelled) return;
        await renderGoogleButton({
          parent,
          clientId,
          nonce,
          text,
          onCredential: (credential) => {
            // GIS holds this callback globally once the button is rendered, so
            // a popup the user leaves open can complete long after this screen
            // is gone. `markUserLoggedIn` is unconditional, so without the
            // guard a late Google credential would swap out a session the user
            // has since established some other way.
            if (cancelled) return;
            callbackRef.current(credential, nonce);
          },
        });
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [clientId, text]);

  if (clientId === null) return null;

  return (
    <div className="google-signin">
      <div ref={containerRef} className="google-signin-slot" />
      {failed && (
        <p className="field-error">
          Google Sign-In could not be loaded. Check your connection or use your
          email and password.
        </p>
      )}
    </div>
  );
}
