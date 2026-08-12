import type { TerminalViewState } from '../../terminal/sessionModel';

/**
 * The reconnect / replay pill, mirroring Android `ConnectionOverlay` and iOS
 * `TerminalScreen`'s overlay.
 *
 * It is driven by the socket's own state, not by the first frame to arrive —
 * ttyd's long-standing stuck-overlay bug is exactly the "wait for data" shape.
 * `replayPainting` keeps it up past the `'e'` marker until the parser has
 * actually consumed the replay, so the overlay never lifts on a blank screen.
 */
export function overlayMessage(
  view: Pick<
    TerminalViewState,
    'channelStopped' | 'connection' | 'replayPainting' | 'suspended'
  >,
): string | null {
  // Not "Session ended": nothing in the close policy reports a session's death
  // this way. A stopped channel means the reconnect loop gave up — a terminal
  // close code, or five failed agent-token mints during a network outage — and
  // nothing will re-arm it on its own, which is why this state carries a
  // button rather than just a label.
  if (view.channelStopped) return 'Disconnected';
  // Ahead of the reconnect label, which a suspend would otherwise win: the
  // model leaves `connection` at `'connecting'` when it detaches. Rarely on
  // screen at all — coming back to the terminal resumes the stream in the same
  // pass that renders this — but it must not read as a failure for the frame in
  // between. Android `ConnectionOverlay` and iOS `connectionOverlay` say the
  // same word for the same state.
  if (view.suspended) return 'Paused';
  if (view.connection === 'connecting') return 'Reconnecting…';
  if (view.connection === 'replaying' || view.replayPainting) {
    return 'Replaying buffer…';
  }
  return null;
}

interface ConnectionOverlayProps {
  message: string | null;
  /** Present only when the channel gave up and the user can re-arm it. */
  onRetry?: (() => void) | undefined;
}

export function ConnectionOverlay({ message, onRetry }: ConnectionOverlayProps) {
  if (message === null) return null;
  return (
    <div className="terminal-overlay" role="status" aria-live="polite">
      {message}
      {onRetry !== undefined && (
        <button type="button" className="terminal-overlay-retry" onClick={onRetry}>
          Reconnect
        </button>
      )}
    </div>
  );
}
