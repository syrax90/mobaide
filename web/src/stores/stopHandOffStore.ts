import { create } from 'zustand';
import type { SessionResponse } from '../protocol/dto/session';

/** The session just stopped from the sessions panel, and where its row stood. */
export interface DepartingSession {
  session: SessionResponse;
  index: number;
}

/**
 * Stopping the session on screen from the sessions panel replaces the terminal
 * with the nearest open one. The terminal view is keyed per session, so the
 * panel remounts with it; this carries what the next one needs to continue the
 * same motion: the overlay stays open, the stopped row fades out of the list,
 * and the selection settles on the new row.
 */
export interface StopHandOff {
  agentId: string;
  /** The session taking over the screen. */
  sessionId: string;
  /** Null when the stopped row was not in the list as the stop was confirmed. */
  departing: DepartingSession | null;
  /** Whether the panel was the overlay, which must stay open across the swap. */
  overlayOpen: boolean;
}

interface StopHandOffState {
  handOff: StopHandOff | null;
  setHandOff: (handOff: StopHandOff) => void;
  /** Clears `handOff` only if it is still the one the caller read. */
  clearHandOff: (handOff: StopHandOff) => void;
}

/** Not persisted: a hand-off belongs to one route swap in this tab. */
export const useStopHandOffStore = create<StopHandOffState>()((set, get) => ({
  handOff: null,

  setHandOff: (handOff) => {
    set({ handOff });
  },

  clearHandOff: (handOff) => {
    if (get().handOff === handOff) set({ handOff: null });
  },
}));

/** The pending hand-off when it targets this session, else `null`. */
export function peekStopHandOff(agentId: string, sessionId: string): StopHandOff | null {
  const { handOff } = useStopHandOffStore.getState();
  return handOff !== null && handOff.agentId === agentId && handOff.sessionId === sessionId
    ? handOff
    : null;
}
