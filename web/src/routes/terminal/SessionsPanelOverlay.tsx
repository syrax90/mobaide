import { useLayoutEffect, useRef, type ReactNode } from 'react';

interface SessionsPanelOverlayProps {
  onClose: () => void;
  /** Already on screen before this mount (a stop hand-off): skip the slide-in. */
  carried: boolean;
  children: ReactNode;
}

/**
 * The slide-over form of the panel. `popover="auto"`, unlike the Changes
 * viewer's `manual`: a click into the terminal or Escape should dismiss it,
 * and the UA `::backdrop` is the scrim. Mounted only while open, the way that
 * viewer and `Modal` are, so the top-layer entry comes and goes with it.
 */
export function SessionsPanelOverlay({ onClose, carried, children }: SessionsPanelOverlayProps) {
  const ref = useRef<HTMLElement>(null);
  // Set while this component closes the popover itself (unmount: a switch, or
  // a pin that turns it into the docked rail). The toggle event that follows
  // is not a dismissal, and acting on it would pull focus out of the rail.
  const unmounting = useRef(false);

  useLayoutEffect(() => {
    // Re-armed on every mount: StrictMode replays mount → cleanup → mount on
    // one instance, and a latch left set would swallow every real dismissal.
    unmounting.current = false;
    const panel = ref.current;
    panel?.showPopover();
    return () => {
      unmounting.current = true;
      panel?.hidePopover();
    };
  }, []);

  return (
    <aside
      ref={ref}
      className={`sessions-panel${carried ? ' sessions-panel-carried' : ''}`}
      popover="auto"
      aria-label="Sessions panel"
      onToggle={(event) => {
        // Light dismiss and Escape close the popover without going through
        // React; mirror that into state so the next click opens it again.
        if (event.newState === 'closed' && !unmounting.current) onClose();
      }}
    >
      {children}
    </aside>
  );
}
