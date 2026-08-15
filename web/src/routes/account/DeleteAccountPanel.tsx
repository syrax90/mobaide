import { useState } from 'react';
import { useServerInfo } from '../../api/useServerInfo';
import {
  deleteAccount,
  deleteAccountMessage,
  type DeletionMethod,
  type DeletionMode,
} from '../../api/authRepository';
import { disableGoogleAutoSelect } from '../../auth/googleSignIn';
import { useSessionStore } from '../../auth/sessionStore';
import { AppleSignInButton } from '../../components/auth/AppleSignInButton';
import { GoogleSignInButton } from '../../components/auth/GoogleSignInButton';

/**
 * `Explain -> ReAuth -> ConfirmEmail`, the same machine as Android's
 * `DeleteAccountDialogState` and iOS's `DeleteAccountStep`.
 *
 * Re-auth is not optional and not skippable: the server rejects the session
 * JWT alone, requiring either the current password or a Google/Apple
 * `id_token` minted within the last 5 minutes (bound to the nonce sent with
 * it). Apple is not a nicety here: an account created with Sign in with Apple
 * has no password and no Google identity, so without this branch it would have
 * no way to delete itself at all.
 */
type Step =
  | { name: 'explain' }
  | { name: 'reauthPassword' }
  | { name: 'reauthGoogle' }
  | { name: 'reauthApple' }
  | {
      name: 'confirmEmail';
      method: DeletionMethod;
      password?: string;
      idToken?: string;
      nonce?: string;
    };

interface DeleteAccountPanelProps {
  email: string;
  onClose: () => void;
}

export function DeleteAccountPanel({ email, onClose }: DeleteAccountPanelProps) {
  const { data: serverInfo } = useServerInfo();
  const invalidateUserSession = useSessionStore(
    (state) => state.invalidateUserSession,
  );

  const [mode, setMode] = useState<DeletionMode>('scheduled');
  const [step, setStep] = useState<Step>({ name: 'explain' });
  const [password, setPassword] = useState('');
  const [typedEmail, setTypedEmail] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (step: Extract<Step, { name: 'confirmEmail' }>) => {
    // Exact, case-sensitive match — same gate as both mobile clients.
    if (typedEmail !== email) {
      setError('Type your email exactly to confirm.');
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await deleteAccount({
        method: step.method,
        mode,
        password: step.password,
        idToken: step.idToken,
        nonce: step.nonce,
      });
      // Sign out locally rather than waiting for the server's WebSocket 4010
      // close: a backgrounded tab or a network blip can drop that frame and
      // leave a locally-authenticated session holding a revoked account.
      // `invalidateUserSession` is idempotent, so a later 4010 is a no-op.
      //
      // Only the scheduled mode gets a login-screen notice: immediate deletion
      // leaves no grace window, so telling the user to sign back in to cancel
      // would send them at an account that no longer exists.
      invalidateUserSession(mode === 'scheduled' ? 'deletionScheduled' : 'manual');
    } catch (cause) {
      setError(deleteAccountMessage(cause));
      setBusy(false);
    }
  };

  return (
    <div className="confirm-panel" role="alertdialog" aria-labelledby="delete-title">
      <h2 id="delete-title" className="danger-title">
        Delete account
      </h2>

      {error !== null && (
        <p className="auth-error" role="alert">
          {error}
        </p>
      )}

      {step.name === 'explain' && (
        <>
          <p>You&apos;re about to delete:</p>
          <p className="delete-email">{email}</p>

          <fieldset className="delete-modes">
            <legend>How should we process this?</legend>
            <label className="radio-row">
              <input
                type="radio"
                name="mode"
                checked={mode === 'scheduled'}
                onChange={() => {
                  setMode('scheduled');
                }}
              />
              <span>
                <strong>Schedule for 14 days (recommended)</strong>
                {/* Signing in does not itself cancel — it only surfaces the
                    banner carrying the cancel button. Android and iOS both
                    still offer "sign back in" as a cancellation method, which
                    is false on the same evidence as the grace banner. */}
                <small>
                  You can cancel any time during the grace window — sign back in
                  and use the banner, or follow the emailed link.
                </small>
              </span>
            </label>
            <label className="radio-row">
              <input
                type="radio"
                name="mode"
                checked={mode === 'immediate'}
                onChange={() => {
                  setMode('immediate');
                }}
              />
              <span>
                <strong>Delete immediately</strong>
                <small>
                  Irreversible. Every agent, pairing, and prompt template is
                  purged now.
                </small>
              </span>
            </label>
          </fieldset>

          <p className="delete-reauth-label">Re-verify with</p>
          <div className="button-row">
            <button
              type="button"
              className="button"
              onClick={() => {
                setStep({ name: 'reauthPassword' });
              }}
            >
              Continue with password
            </button>
            {serverInfo?.googleClientId != null && (
              <button
                type="button"
                className="button"
                onClick={() => {
                  // Drop the cached auto-selected account so GIS actually
                  // re-prompts; otherwise it can hand back a credential too
                  // old for the server's 5-minute freshness window.
                  disableGoogleAutoSelect();
                  setStep({ name: 'reauthGoogle' });
                }}
              >
                Continue with Google
              </button>
            )}
            {serverInfo?.appleWebClientId != null && (
              // Apple has no auto-select to disable: its popup always runs a
              // fresh authorization, so the token is inside the server's
              // 5-minute window by construction.
              <button
                type="button"
                className="button"
                onClick={() => {
                  setStep({ name: 'reauthApple' });
                }}
              >
                Continue with Apple
              </button>
            )}
          </div>
          <button type="button" className="button button-block" onClick={onClose}>
            Cancel
          </button>
        </>
      )}

      {step.name === 'reauthPassword' && (
        <>
          <p>Enter your current password to confirm:</p>
          <input
            className="field-input"
            type="password"
            autoComplete="current-password"
            aria-label="Current password"
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
            }}
          />
          <div className="button-row">
            <button
              type="button"
              className="button button-primary"
              onClick={() => {
                if (password === '') {
                  setError('Enter your password.');
                  return;
                }
                setError(null);
                setStep({ name: 'confirmEmail', method: 'password', password });
              }}
            >
              Next
            </button>
            <button type="button" className="button" onClick={onClose}>
              Cancel
            </button>
          </div>
        </>
      )}

      {step.name === 'reauthGoogle' && (
        <>
          <p>
            Sign in with Google again to confirm it&apos;s you. A fresh sign-in
            is required — your existing session alone is not enough to confirm
            this destructive action.
          </p>
          <GoogleSignInButton
            clientId={serverInfo?.googleClientId ?? null}
            text="continue_with"
            onCredential={(credential, nonce) => {
              setError(null);
              setStep({
                name: 'confirmEmail',
                method: 'google',
                idToken: credential,
                nonce,
              });
            }}
          />
          <button type="button" className="button button-block" onClick={onClose}>
            Cancel
          </button>
        </>
      )}

      {step.name === 'reauthApple' && (
        <>
          <p>
            Sign in with Apple again to confirm it&apos;s you. A fresh sign-in
            is required — your existing session alone is not enough to confirm
            this destructive action.
          </p>
          <AppleSignInButton
            clientId={serverInfo?.appleWebClientId ?? null}
            onError={setError}
            onCredential={(idToken, nonce) => {
              setError(null);
              setStep({
                name: 'confirmEmail',
                method: 'apple',
                idToken,
                nonce,
              });
            }}
          />
          <button type="button" className="button button-block" onClick={onClose}>
            Cancel
          </button>
        </>
      )}

      {step.name === 'confirmEmail' && (
        <>
          {mode === 'immediate' && (
            <p className="danger-title">
              Final step — this will purge your account immediately.
            </p>
          )}
          <p>Type your email to confirm:</p>
          <input
            className="field-input"
            type="email"
            autoComplete="off"
            aria-label="Confirm your email address"
            value={typedEmail}
            onChange={(event) => {
              setTypedEmail(event.target.value);
            }}
          />
          <div className="button-row">
            <button
              type="button"
              className="button button-danger"
              disabled={busy || typedEmail !== email}
              onClick={() => void submit(step)}
            >
              {mode === 'immediate' ? 'Delete immediately' : 'Schedule deletion'}
            </button>
            <button type="button" className="button" onClick={onClose}>
              Cancel
            </button>
          </div>
        </>
      )}
    </div>
  );
}
