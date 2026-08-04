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
  view: Pick<TerminalViewState, 'channelStopped' | 'connection' | 'replayPainting'>,
): string | null {
  if (view.channelStopped) return 'Session ended';
  if (view.connection === 'connecting') return 'Reconnecting…';
  if (view.connection === 'replaying' || view.replayPainting) {
    return 'Replaying buffer…';
  }
  return null;
}

interface ConnectionOverlayProps {
  message: string | null;
}

export function ConnectionOverlay({ message }: ConnectionOverlayProps) {
  if (message === null) return null;
  return (
    <div className="terminal-overlay" role="status" aria-live="polite">
      {message}
    </div>
  );
}
