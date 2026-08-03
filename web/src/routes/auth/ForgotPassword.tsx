import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useNavigate } from 'react-router';
import { forgotPassword } from '../../api/authRepository';
import { isApiError } from '../../api/errors';
import { TextField } from '../../components/form/TextField';
import { useToastStore } from '../../stores/toastStore';
import { networkRateLimitedMessage } from './authCopy';
import { useCooldown } from './useCooldown';

interface ForgotPasswordForm {
  email: string;
}

export function ForgotPassword() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [cooldown, startCooldown] = useCooldown();

  const { register, handleSubmit } = useForm<ForgotPasswordForm>({ defaultValues: { email: '' } });

  const onSubmit = handleSubmit(async (values) => {
    const email = values.email.trim();
    // The same minimal check the mobile clients use — the server is the real
    // validator, and a stricter regex here would reject addresses it accepts.
    if (email === '' || !email.includes('@')) {
      setError('Enter a valid email address');
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await forgotPassword(email);
      // Always the same message whether or not the account exists: the
      // endpoint is deliberately enumeration-resistant and returns 200 either
      // way, so the UI must not imply an answer.
      useToastStore
        .getState()
        .show('If an account exists for that email, we sent a code.', 'info');
      void navigate('/reset-password', { state: { email } });
    } catch (cause) {
      if (isApiError(cause) && cause.status === 429) {
        const seconds = cause.retryAfterSeconds ?? 60;
        startCooldown(seconds);
        setError(networkRateLimitedMessage(seconds));
      } else if (isApiError(cause)) {
        setError(`Couldn't request a reset code: ${cause.message}`);
      } else {
        setError("Couldn't request a reset code. Check your connection.");
      }
    } finally {
      setBusy(false);
    }
  });

  return (
    <div className="auth-card">
      <h1 className="auth-title">Reset your password</h1>
      <p className="auth-subtitle">
        Enter your email address and we&apos;ll send you a 6-digit code.
      </p>

      {error !== null && (
        <p className="auth-error" role="alert">
          {error}
        </p>
      )}

      <form onSubmit={(event) => void onSubmit(event)} noValidate>
        <TextField
          label="Email"
          type="email"
          placeholder="you@example.com"
          autoComplete="email"
          autoFocus
          registration={register('email')}
        />
        <button
          type="submit"
          className="button button-primary button-block"
          disabled={busy || cooldown > 0}
        >
          {cooldown > 0
            ? `Send reset code (${String(cooldown)}s)`
            : 'Send reset code'}
        </button>
      </form>

      <p className="auth-footer">
        <Link to="/login">Back to sign in</Link>
      </p>
    </div>
  );
}
