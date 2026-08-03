import { useState } from 'react';
import { Link, Navigate, useLocation } from 'react-router';
import {
  resendVerification,
  verifyEmail,
  type AuthOutcome,
} from '../../api/authRepository';
import { AdminLoginNotSupportedError, isApiError } from '../../api/errors';
import { CodeInputField } from '../../components/form/CodeInputField';
import { useToastStore } from '../../stores/toastStore';
import { maskEmail, verificationMessage } from './authCopy';
import { useAfterAuth } from './useAfterAuth';
import { useCooldown } from './useCooldown';

interface VerifyEmailState {
  email?: string;
}

export function VerifyEmail() {
  const location = useLocation();
  const afterAuth = useAfterAuth();
  const email = (location.state as VerifyEmailState | null)?.email ?? null;

  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [resending, setResending] = useState(false);
  const [cooldown, startCooldown] = useCooldown();

  // Reached without an email (deep link, reload): there is nothing to verify
  // against, and the server keys the code by address.
  if (email === null) {
    return <Navigate to="/login" replace />;
  }

  const settle = (outcome: AuthOutcome) => {
    if (outcome.kind === 'verificationRequired') {
      setError("That code isn't right. Try again.");
      setCode('');
      return;
    }
    afterAuth(outcome.session);
  };

  const submit = async () => {
    if (code.length !== 6) {
      setError('Enter the 6-digit code we emailed you');
      return;
    }
    setError(null);
    setBusy(true);
    try {
      settle(await verifyEmail(email, code));
    } catch (cause) {
      if (cause instanceof AdminLoginNotSupportedError) {
        setError(cause.message);
      } else {
        setError(verificationMessage(cause));
        // A rejected code is cleared so the next attempt starts from an empty
        // field; an expired or rate-limited one is left alone, since the user
        // needs a new code either way.
        if (isApiError(cause) && cause.status === 400) setCode('');
      }
    } finally {
      setBusy(false);
    }
  };

  const resend = async () => {
    setResending(true);
    try {
      const outcome = await resendVerification(email);
      startCooldown(outcome.cooldownSeconds);
      useToastStore
        .getState()
        .show(
          outcome.rateLimited
            ? `Please wait ${String(outcome.cooldownSeconds)}s before requesting another code.`
            : 'A new code is on its way.',
          outcome.rateLimited ? 'info' : 'success',
        );
    } catch {
      useToastStore.getState().show('Could not resend the code.', 'error');
    } finally {
      setResending(false);
    }
  };

  return (
    <div className="auth-card">
      <h1 className="auth-title">Check your email</h1>
      <p className="auth-subtitle">
        We sent a 6-digit code to {maskEmail(email)}.
      </p>

      {error !== null && (
        <p className="auth-error" role="alert">
          {error}
        </p>
      )}

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <CodeInputField label="Verification code" value={code} onChange={setCode} />
        <button
          type="submit"
          className="button button-primary button-block"
          disabled={busy || code.length !== 6}
        >
          {busy ? 'Verifying…' : 'Verify'}
        </button>
      </form>

      <button
        type="button"
        className="button button-block"
        disabled={resending || cooldown > 0}
        onClick={() => void resend()}
      >
        {resending
          ? 'Sending…'
          : cooldown > 0
            ? `Resend code (${String(cooldown)}s)`
            : 'Resend code'}
      </button>

      <p className="auth-footer">
        <Link to="/register">Use a different email</Link>
      </p>
    </div>
  );
}
