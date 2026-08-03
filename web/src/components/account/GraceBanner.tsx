import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { cancelAccountDeletion } from '../../api/authRepository';
import { useSessionStore } from '../../auth/sessionStore';
import { useToastStore } from '../../stores/toastStore';

/**
 * Soft-delete grace-window banner. Copy mirrors Android's
 * `GraceBannerSection` and its iOS twin.
 *
 * Renders nothing unless the session carries a `scheduled_deletion_at`, which
 * every auth response supplies — so it survives a reload without needing its
 * own query.
 */
export function GraceBanner() {
  const scheduledDeletionAt = useSessionStore(
    (state) => state.session?.scheduledDeletionAt ?? null,
  );
  const updateScheduledDeletion = useSessionStore(
    (state) => state.updateScheduledDeletion,
  );
  const [confirming, setConfirming] = useState(false);

  const cancel = useMutation({
    mutationFn: cancelAccountDeletion,
    onSuccess: () => {
      updateScheduledDeletion(null);
      setConfirming(false);
      useToastStore.getState().show('Your account stays active.', 'success');
    },
    onError: () => {
      useToastStore
        .getState()
        .show(
          'Could not cancel the scheduled deletion. Check your connection and try again.',
          'error',
        );
    },
  });

  if (scheduledDeletionAt === null) return null;

  return (
    <section className="grace-banner" aria-live="polite">
      <h2 className="grace-banner-title">⚠ Account scheduled for deletion</h2>
      <p className="grace-banner-body">
        Final purge on {scheduledDeletionAt}. Sign in cancels at any time during
        the grace window.
      </p>
      {confirming ? (
        <div className="grace-banner-confirm">
          <p>
            Cancelling the scheduled deletion restores full access — your data,
            agents, and prompt templates stay exactly as they are. You can
            re-schedule from settings any time.
          </p>
          <div className="button-row">
            <button
              type="button"
              className="button button-primary"
              disabled={cancel.isPending}
              onClick={() => {
                cancel.mutate();
              }}
            >
              Keep account active
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                setConfirming(false);
              }}
            >
              Dismiss
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          className="button button-primary"
          onClick={() => {
            setConfirming(true);
          }}
        >
          Cancel scheduled deletion
        </button>
      )}
    </section>
  );
}
