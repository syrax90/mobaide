import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useNavigate } from 'react-router';
import { useServerInfo } from '../../api/useServerInfo';
import {
  login,
  loginWithGoogle,
  VerificationRequiredError,
  type AuthOutcome,
} from '../../api/authRepository';
import { AdminLoginNotSupportedError } from '../../api/errors';
import { INVALIDATION_MESSAGES } from '../../auth/authState';
import { useSessionStore } from '../../auth/sessionStore';
import { GoogleSignInButton } from '../../components/auth/GoogleSignInButton';
import { PasswordField } from '../../components/form/PasswordField';
import { TextField } from '../../components/form/TextField';
import { loginMessage } from './authCopy';
import { useAfterAuth } from './useAfterAuth';

interface LoginForm {
  email: string;
  password: string;
}

export function Login() {
  const navigate = useNavigate();
  const afterAuth = useAfterAuth();
  const consumeInvalidation = useSessionStore((state) => state.consumeInvalidation);
  const { data: serverInfo } = useServerInfo();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const { register, handleSubmit } = useForm<LoginForm>({ defaultValues: { email: '', password: '' } });

  // Why the last session ended ("your session has expired", "your account was
  // removed"). One-shot, so a later re-render does not resurrect it.
  useEffect(() => {
    const reason = consumeInvalidation();
    if (reason !== null) {
      const message = INVALIDATION_MESSAGES[reason];
      if (message !== null) setError(message);
    }
  }, [consumeInvalidation]);

  const settle = (outcome: AuthOutcome) => {
    if (outcome.kind === 'verificationRequired') {
      void navigate('/verify-email', { state: { email: outcome.email } });
      return;
    }
    afterAuth(outcome.session);
  };

  const handleFailure = (cause: unknown) => {
    if (cause instanceof VerificationRequiredError) {
      void navigate('/verify-email', { state: { email: cause.email } });
      return;
    }
    if (cause instanceof AdminLoginNotSupportedError) {
      setError(cause.message);
      return;
    }
    setError(loginMessage(cause));
  };

  const onSubmit = handleSubmit(async (values) => {
    const email = values.email.trim();
    if (email === '' || values.password === '') {
      setError('Email and password are required');
      return;
    }
    setError(null);
    setBusy(true);
    try {
      settle(await login(email, values.password));
    } catch (cause) {
      handleFailure(cause);
    } finally {
      setBusy(false);
    }
  });

  return (
    <div className="auth-card">
      <h1 className="auth-title">Mobaide</h1>
      <p className="auth-subtitle">Sign in to your account</p>

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
        <PasswordField
          label="Password"
          autoComplete="current-password"
          registration={register('password')}
        />
        <Link className="auth-link" to="/forgot-password">
          Forgot password?
        </Link>
        <button
          type="submit"
          className="button button-primary button-block"
          disabled={busy}
        >
          {busy ? 'Signing in…' : 'Sign In'}
        </button>
      </form>

      {serverInfo?.googleClientId != null && (
        <>
          <div className="auth-divider">
            <span>or</span>
          </div>
          <GoogleSignInButton
            clientId={serverInfo.googleClientId}
            text="signin_with"
            onCredential={(credential) => {
              setError(null);
              setBusy(true);
              void loginWithGoogle(credential)
                .then(settle)
                .catch(handleFailure)
                .finally(() => {
                  setBusy(false);
                });
            }}
          />
        </>
      )}

      <p className="auth-footer">
        Don&apos;t have an account? <Link to="/register">Create one</Link>
      </p>
    </div>
  );
}
