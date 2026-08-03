import { useState } from 'react';
import { Link } from 'react-router';
import { useSessionStore } from '../../auth/sessionStore';
import { GraceBanner } from '../../components/account/GraceBanner';
import { DeleteAccountPanel } from './DeleteAccountPanel';

export function Account() {
  const session = useSessionStore((state) => state.session);
  const invalidateUserSession = useSessionStore(
    (state) => state.invalidateUserSession,
  );
  const [deleting, setDeleting] = useState(false);

  if (session === null) return null;

  return (
    <section className="page">
      <header className="page-header">
        <h1>Account</h1>
      </header>

      <GraceBanner />

      <dl className="account-summary">
        <div>
          <dt>Signed in as</dt>
          <dd>{session.email}</dd>
        </div>
        <div>
          <dt>Role</dt>
          <dd>{session.role}</dd>
        </div>
      </dl>

      <nav className="account-links">
        <Link to="/devices">My devices</Link>
      </nav>

      <button
        type="button"
        className="button button-block"
        onClick={() => {
          // `manual` is the silent reason: the user asked to sign out, so the
          // login screen must not greet them with "your session expired".
          invalidateUserSession('manual');
        }}
      >
        Sign out
      </button>

      <section className="danger-zone">
        <h2 className="danger-title">Danger zone</h2>
        <p>
          Permanently delete your account, all owned agents, and every saved
          template.
        </p>
        {deleting ? (
          <DeleteAccountPanel
            email={session.email}
            onClose={() => {
              setDeleting(false);
            }}
          />
        ) : (
          <button
            type="button"
            className="button button-danger"
            onClick={() => {
              setDeleting(true);
            }}
          >
            Delete account
          </button>
        )}
      </section>
    </section>
  );
}
