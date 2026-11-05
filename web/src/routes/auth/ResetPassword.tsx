import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { Link, Navigate, useLocation, useNavigate } from 'react-router';
import { forgotPassword, resetPassword } from '../../api/authRepository';
import { isApiError } from '../../api/errors';
import { CodeInputField } from '../../components/form/CodeInputField';
import { PasswordField } from '../../components/form/PasswordField';
import { useToastStore } from '../../stores/toastStore';
import {
  maskEmail,
  networkRateLimitedMessage,
  passwordLengthError,
  resetPasswordMessage,
  shouldClearCode,
} from './authCopy';
import { useCooldown } from './useCooldown';

interface ResetPasswordState {
  email?: string;
}

interface ResetPasswordForm {
  newPassword: string;
  confirmPassword: string;
}

export function ResetPassword() {
  const location = useLocation();
  const navigate = useNavigate();
  const email = (location.state as ResetPasswordState | null)?.email ?? null;

  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [resending, setResending] = useState(false);
  const [cooldown, startCooldown] = useCooldown();

  const { register, handleSubmit } = useForm<ResetPasswordForm>({
    defaultValues: { newPassword: '', confirmPassword: '' },
  });

  const onSubmit = handleSubmit(async (values) => {
    // Validation order matters: the code is checked before the password so a
    // user who mistyped both is told about the code first, matching Android.
    if (code.length !== 6) {
      setError('Enter the 6-digit code we emailed you');
      return;
    }
    const lengthError = passwordLengthError(values.newPassword);
    if (lengthError !== null) {
      setError(lengthError);
      return;
    }
    if (values.newPassword !== values.confirmPassword) {
      setError("Passwords don't match");
      return;
    }
    if (email === null) return;
    setError(null);
    setBusy(true);
    try {
      await resetPassword(email, code, values.newPassword);
      useToastStore
        .getState()
        .show('Password updated. Sign in with your new password.', 'success');
      void navigate('/login', { replace: true });
    } catch (cause) {
      setError(resetPasswordMessage(cause));
      if (shouldClearCode(cause)) setCode('');
    } finally {
      setBusy(false);
    }
  });

  const resend = async () => {
    if (email === null) return;
    setResending(true);
    try {
      await forgotPassword(email);
      startCooldown(60);
      useToastStore.getState().show('A new code is on its way.', 'success');
    } catch (cause) {
      // Only a 429 starts a cooldown. Treating every failure as rate limiting
      // would tell an offline user they had made too many requests and then
      // lock the button for 60s over what was just a dropped connection —
      // Android keeps these branches separate too, as does the verify-email
      // resend in this same milestone.
      if (isApiError(cause) && cause.status === 429) {
        const seconds = cause.retryAfterSeconds ?? 60;
        startCooldown(seconds);
        useToastStore.getState().show(networkRateLimitedMessage(seconds), 'error');
      } else {
        useToastStore
          .getState()
          .show("Couldn't request a new code. Check your connection.", 'error');
      }
    } finally {
      setResending(false);
    }
  };

  if (email === null) {
    return <Navigate to="/forgot-password" replace />;
  }

  return (
    <div className="auth-card">
      <h1 className="auth-title">Choose a new password</h1>
      <p className="auth-subtitle">
        We sent a 6-digit code to {maskEmail(email)}.
      </p>

      {error !== null && (
        <p className="auth-error" role="alert">
          {error}
        </p>
      )}

      <form onSubmit={(event) => void onSubmit(event)} noValidate>
        <CodeInputField label="Reset code" value={code} onChange={setCode} />
        <PasswordField
          label="New password"
          autoComplete="new-password"
          registration={register('newPassword')}
        />
        <PasswordField
          label="Confirm new password"
          autoComplete="new-password"
          registration={register('confirmPassword')}
        />
        <button
          type="submit"
          className="button button-primary button-block"
          disabled={busy}
        >
          {busy ? 'Resetting…' : 'Reset password'}
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
        <Link to="/login">Back to sign in</Link>
      </p>
    </div>
  );
}
