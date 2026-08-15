import { useEffect, useRef, useState } from 'react';
import { initGoogleIdentity, renderGoogleButton } from '../../auth/googleSignIn';
import { generateNonce } from '../../auth/nonce';
import { useResolvedTheme } from '../../app/useResolvedTheme';

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

/** Renders nothing when the server has no `GOOGLE_CLIENT_ID` configured. */
export function GoogleSignInButton({
  clientId,
  text,
  onCredential,
}: GoogleSignInButtonProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  const [armed, setArmed] = useState(false);
  // GIS offers no dark-surface fill, so the dark theme takes `outline` — a
  // white button — and the Apple button beside it flips to its white variant
  // to match. See the `[data-theme='dark']` rule in styles/global.css.
  const theme = useResolvedTheme() === 'dark' ? 'outline' : 'filled_black';

  // The callback identity changes on every parent render, so keeping it in a
  // ref lets the effect depend only on the client id — otherwise GIS would be
  // re-initialised and the button re-rendered on each keystroke in the form.
  const callbackRef = useRef(onCredential);
  callbackRef.current = onCredential;

  // Arming is keyed on the client id alone. The nonce minted here is the one
  // baked into every popup this screen opens, so re-running it for a cosmetic
  // reason — a theme flip, which the redraw effect below handles — would
  // strand an in-flight sign-in: the returning token carries the old nonce
  // while this component would report the new one, and the server compares
  // the two.
  useEffect(() => {
    if (clientId === null) return;
    let cancelled = false;
    setFailed(false);
    setArmed(false);

    void (async () => {
      try {
        const nonce = await generateNonce();
        if (cancelled) return;
        await initGoogleIdentity({
          clientId,
          nonce,
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
        if (cancelled) return;
        setArmed(true);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [clientId]);

  // Appearance only, so a theme flip redraws the button without disturbing an
  // attempt already in flight.
  useEffect(() => {
    const parent = containerRef.current;
    if (!armed || parent === null) return;
    let cancelled = false;
    setFailed(false);

    void (async () => {
      try {
        await renderGoogleButton({ parent, theme, text });
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [armed, text, theme]);

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
