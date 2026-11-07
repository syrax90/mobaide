import { useExternalSessionsNoticeStore } from '../../stores/externalSessionsNoticeStore';

const HEADING_ID = 'external-sessions-notice-heading';

interface ExternalSessionsNoticeProps {
  /**
   * Called after the flag is persisted. Dismissing unmounts the button that
   * held focus, which would otherwise drop focus to `<body>`; the host moves
   * it somewhere sensible instead.
   */
  onDismiss?: () => void;
}

/**
 * Mirror of Android/iOS `ExternalSessionsNotice`: one-time explainer above the
 * sessions list. The list shows only sessions the agent spawned itself; a
 * Claude Code session started by hand on the PC is reached through the create
 * wizard's "Continue a session" instead.
 *
 * A labelled landmark rather than a live region: `GraceBanner`'s
 * `aria-live="polite"` is right for a banner that appears mid-session, but this
 * one is present at first paint, where a live region announces nothing.
 */
export function ExternalSessionsNotice({ onDismiss }: ExternalSessionsNoticeProps) {
  const dismissed = useExternalSessionsNoticeStore((state) => state.dismissed);
  const dismiss = useExternalSessionsNoticeStore((state) => state.dismiss);

  if (dismissed) return null;

  return (
    <section className="notice-banner" aria-labelledby={HEADING_ID}>
      <h2 className="notice-banner-title" id={HEADING_ID}>
        <span aria-hidden="true">ⓘ </span>
        Sessions started outside Mobaide won&apos;t appear here
      </h2>
      <p className="notice-banner-body">
        Mobaide lists only sessions its own agent started. To continue a Claude Code session
        from your PC, start a new Claude Code session and choose Continue a session.
      </p>
      <button
        type="button"
        className="button"
        onClick={() => {
          dismiss();
          onDismiss?.();
        }}
      >
        Got it
      </button>
    </section>
  );
}
