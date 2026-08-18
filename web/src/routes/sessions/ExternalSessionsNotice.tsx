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
 * sessions list. The agent spawns every session itself and has no attach path,
 * so a process the user started by hand on the agent machine can never show up
 * here.
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
        Mobaide can only open sessions its own agent started. To control a session from
        here, start it from Mobaide instead of launching it yourself on the agent machine.
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
