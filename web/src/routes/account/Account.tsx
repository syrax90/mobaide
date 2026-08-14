import { useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Link } from 'react-router';
import { useSessionStore } from '../../auth/sessionStore';
import { signOutAllBrowsers } from '../../api/authRepository';
import { GraceBanner } from '../../components/account/GraceBanner';
import { Modal } from '../../components/Modal';
import { useToastStore } from '../../stores/toastStore';
import { DeleteAccountPanel } from './DeleteAccountPanel';

export function Account() {
  const session = useSessionStore((state) => state.session);
  const invalidateUserSession = useSessionStore(
    (state) => state.invalidateUserSession,
  );
  const [deleting, setDeleting] = useState(false);
  const [confirmingSignOutAll, setConfirmingSignOutAll] = useState(false);
  const signOutAllCancelRef = useRef<HTMLButtonElement>(null);

  const signOutAll = useMutation({
    mutationFn: signOutAllBrowsers,
    // Local teardown only after the server confirms, so a failed revoke leaves
    // the user signed in rather than stranding live sessions elsewhere behind
    // a signed-out-looking UI.
    onSuccess: () => {
      invalidateUserSession('manual');
    },
    onError: () => {
      setConfirmingSignOutAll(false);
      useToastStore
        .getState()
        .show('Could not sign out other browsers. Try again.', 'error');
    },
  });

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

      <button
        type="button"
        className="button button-block"
        onClick={() => {
          setConfirmingSignOutAll(true);
        }}
      >
        Sign out of all browsers
      </button>

      {confirmingSignOutAll && (
        <Modal
          role="alertdialog"
          labelledBy="sign-out-all-title"
          dismissible={!signOutAll.isPending}
          initialFocus={signOutAllCancelRef}
          onClose={() => {
            setConfirmingSignOutAll(false);
          }}
        >
          <h2 id="sign-out-all-title">Sign out of all browsers?</h2>
          <p>
            Every browser signed in to this account will be signed out, including
            this one. Other browsers stop working within 15 minutes. Your phones
            and tablets stay signed in.
          </p>
          <div className="button-row">
            <button
              type="button"
              className="button button-danger"
              disabled={signOutAll.isPending}
              onClick={() => {
                signOutAll.mutate();
              }}
            >
              {signOutAll.isPending ? 'Signing out…' : 'Sign out of all browsers'}
            </button>
            <button
              type="button"
              ref={signOutAllCancelRef}
              className="button"
              disabled={signOutAll.isPending}
              onClick={() => {
                setConfirmingSignOutAll(false);
              }}
            >
              Cancel
            </button>
          </div>
        </Modal>
      )}

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
