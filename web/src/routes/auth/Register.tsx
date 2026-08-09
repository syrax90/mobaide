import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useNavigate } from 'react-router';
import { useServerInfo } from '../../api/useServerInfo';
import {
  loginWithApple,
  loginWithGoogle,
  register as registerAccount,
  type AuthOutcome,
} from '../../api/authRepository';
import { AdminLoginNotSupportedError } from '../../api/errors';
import { AppleSignInButton } from '../../components/auth/AppleSignInButton';
import { GoogleSignInButton } from '../../components/auth/GoogleSignInButton';
import { PasswordField } from '../../components/form/PasswordField';
import { TextField } from '../../components/form/TextField';
import { registerMessage } from './authCopy';
import { useAfterAuth } from './useAfterAuth';

interface RegisterForm {
  email: string;
  password: string;
  confirmPassword: string;
}

export function Register() {
  const navigate = useNavigate();
  const afterAuth = useAfterAuth();
  const { data: serverInfo } = useServerInfo();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const { register, handleSubmit } = useForm<RegisterForm>({
    defaultValues: { email: '', password: '', confirmPassword: '' },
  });

  const settle = (outcome: AuthOutcome) => {
    if (outcome.kind === 'verificationRequired') {
      void navigate('/verify-email', { state: { email: outcome.email } });
      return;
    }
    afterAuth(outcome.session);
  };

  const handleFailure = (cause: unknown) => {
    if (cause instanceof AdminLoginNotSupportedError) {
      setError(cause.message);
      return;
    }
    setError(registerMessage(cause));
  };

  const onSubmit = handleSubmit(async (values) => {
    const email = values.email.trim();
    // Deliberately no minimum length here — the server does not enforce one on
    // register (only reset-password does), and inventing a client-side rule
    // would reject passwords the account can legitimately hold.
    if (email === '' || values.password === '') {
      setError('Email and password are required');
      return;
    }
    if (values.password !== values.confirmPassword) {
      setError('Passwords do not match');
      return;
    }
    setError(null);
    setBusy(true);
    try {
      settle(await registerAccount(email, values.password));
    } catch (cause) {
      handleFailure(cause);
    } finally {
      setBusy(false);
    }
  });

  return (
    <div className="auth-card">
      <h1 className="auth-title">Mobaide</h1>
      <p className="auth-subtitle">Create your account</p>

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
          autoComplete="new-password"
          registration={register('password')}
        />
        <PasswordField
          label="Confirm Password"
          autoComplete="new-password"
          registration={register('confirmPassword')}
        />
        <button
          type="submit"
          className="button button-primary button-block"
          disabled={busy}
        >
          {busy ? 'Creating account…' : 'Create Account'}
        </button>
      </form>

      {(serverInfo?.googleClientId != null ||
        serverInfo?.appleWebClientId != null) && (
        <div className="auth-divider">
          <span>or</span>
        </div>
      )}
      {serverInfo?.googleClientId != null && (
        <GoogleSignInButton
          clientId={serverInfo.googleClientId}
          text="signup_with"
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
      )}
      {serverInfo?.appleWebClientId != null && (
        <AppleSignInButton
          clientId={serverInfo.appleWebClientId}
          label="Sign up with Apple"
          disabled={busy}
          onError={setError}
          onCredential={(idToken, nonce) => {
            setError(null);
            setBusy(true);
            void loginWithApple(idToken, nonce)
              .then(settle)
              .catch(handleFailure)
              .finally(() => {
                setBusy(false);
              });
          }}
        />
      )}

      <p className="auth-footer">
        Already have an account? <Link to="/login">Sign in</Link>
      </p>
    </div>
  );
}
