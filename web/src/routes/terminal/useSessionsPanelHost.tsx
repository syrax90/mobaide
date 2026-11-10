import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { SessionResponse } from '../../protocol/dto/session';
import { useSessionsPanelStore } from '../../stores/sessionsPanelStore';
import {
  peekStopHandOff,
  useStopHandOffStore,
  type StopHandOff,
} from '../../stores/stopHandOffStore';
import { SessionsPanel } from './SessionsPanel';
import { SessionsPanelOverlay } from './SessionsPanelOverlay';
import { useWideViewport } from './useWideViewport';

interface SessionsPanelHostOptions {
  agentId: string;
  sessionId: string;
  /** Warms the next screen's model before the panel navigates to it. */
  onPrepareSession: (row: SessionResponse) => void;
}

interface SessionsPanelHost {
  /** Set when this visit took over from a session stopped in the panel. */
  handOff: StopHandOff | null;
  /** The header's ☰ button. */
  toggle: ReactNode;
  /** The docked rail or the slide-over, whichever is showing. */
  rail: ReactNode;
}

/**
 * The sessions panel of a session screen: `open` is per visit, `pinned`
 * persists. Docked beside the screen only when pinned on a wide viewport;
 * otherwise it slides over.
 */
export function useSessionsPanelHost({
  agentId,
  sessionId,
  onPrepareSession,
}: SessionsPanelHostOptions): SessionsPanelHost {
  // Read once, then cleared: this visit took over from a session stopped in the
  // panel, which stays open and finishes the switch's motion.
  const [handOff] = useState(() => peekStopHandOff(agentId, sessionId));
  useEffect(() => {
    if (handOff !== null) useStopHandOffStore.getState().clearHandOff(handOff);
  }, [handOff]);
  const [sessionsOpen, setSessionsOpen] = useState(handOff?.overlayOpen === true);
  // An overlay carried over from the previous screen is already in place and
  // must not slide in again; any later opening does.
  const [overlayCarried, setOverlayCarried] = useState(handOff?.overlayOpen === true);
  useEffect(() => {
    if (!sessionsOpen) setOverlayCarried(false);
  }, [sessionsOpen]);
  const pinned = useSessionsPanelStore((state) => state.pinned);
  const setPinned = useSessionsPanelStore((state) => state.setPinned);
  const wide = useWideViewport();
  const docked = pinned && wide;
  const panelButtonRef = useRef<HTMLButtonElement>(null);

  const closeSessions = () => {
    setSessionsOpen(false);
    panelButtonRef.current?.focus();
  };

  const sessionsPanel = (
    <SessionsPanel
      agentId={agentId}
      sessionId={sessionId}
      docked={docked}
      pinned={pinned}
      onPinnedChange={(next) => {
        setPinned(next);
        // Unpinning on a wide screen turns the rail into an overlay; keep it
        // on screen rather than making it vanish under the click.
        if (!next && wide) setSessionsOpen(true);
      }}
      onClose={closeSessions}
      handOff={handOff}
      onPrepareSession={onPrepareSession}
    />
  );

  const toggle = (
    <button
      type="button"
      className="button button-icon"
      ref={panelButtonRef}
      aria-label="Sessions"
      aria-expanded={docked || sessionsOpen}
      title="Switch session"
      onClick={() => {
        if (docked) {
          setPinned(false);
        } else {
          setSessionsOpen((open) => !open);
        }
      }}
    >
      ☰
    </button>
  );

  const rail = (
    <>
      {docked && <aside className="sessions-panel sessions-panel-docked">{sessionsPanel}</aside>}
      {sessionsOpen && !docked && (
        <SessionsPanelOverlay onClose={closeSessions} carried={overlayCarried}>
          {sessionsPanel}
        </SessionsPanelOverlay>
      )}
    </>
  );

  return { handOff, toggle, rail };
}
