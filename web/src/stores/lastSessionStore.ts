import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface LastSessionState {
  /** agentId → the session auto-resume should return to. */
  lastUsedSessionIds: Record<string, string>;
  setLastUsedSession: (agentId: string, sessionId: string) => void;
  clearLastUsedSession: (agentId: string) => void;
}

/**
 * The session auto-resume returns to, per agent.
 *
 * **Keyed by agent, unlike mobile's flat `last_used_session_id`.** One mobile
 * install is bound to one agent, so a single slot is unambiguous there. A flat
 * key on web would hand agent A's session id to a list showing agent B, where it
 * lands in the "not in the list" branch — which deliberately does *not* clear —
 * and the id would be stuck forever.
 *
 * **Written on open as well as on create**, which is a deliberate divergence
 * from both mobile clients (they write only after a successful create). The web
 * client's main job is attaching to sessions started elsewhere, so a create-only
 * write would leave auto-resume dormant for most users. Android and iOS are
 * tracked to match in CLA-229 / CLA-230.
 */
export const useLastSessionStore = create<LastSessionState>()(
  persist(
    (set) => ({
      lastUsedSessionIds: {},
      setLastUsedSession: (agentId, sessionId) => {
        set((state) => ({
          lastUsedSessionIds: { ...state.lastUsedSessionIds, [agentId]: sessionId },
        }));
      },
      clearLastUsedSession: (agentId) => {
        set((state) => {
          const { [agentId]: removed, ...rest } = state.lastUsedSessionIds;
          return removed === undefined ? state : { lastUsedSessionIds: rest };
        });
      },
    }),
    {
      name: 'mobaide-sessions',
      merge: (persisted, current) => {
        const stored = (persisted as Partial<LastSessionState> | undefined)
          ?.lastUsedSessionIds;
        const clean: Record<string, string> = {};
        if (typeof stored === 'object' && stored !== null) {
          // `Object.entries` surfaces a `__proto__` key that `JSON.parse`
          // produced as an own property, and assigning it would invoke the
          // prototype setter. `fromEntries`-style construction on a fresh
          // object avoids that; the type guards do the rest.
          for (const [agentId, sessionId] of Object.entries(stored)) {
            if (agentId !== '' && typeof sessionId === 'string' && sessionId !== '') {
              Object.defineProperty(clean, agentId, {
                value: sessionId,
                enumerable: true,
                writable: true,
                configurable: true,
              });
            }
          }
        }
        return { ...current, lastUsedSessionIds: clean };
      },
    },
  ),
);

/** Non-reactive read, for the auto-resume effect. */
export function readLastUsedSession(agentId: string): string | null {
  const stored = useLastSessionStore.getState().lastUsedSessionIds;
  return Object.hasOwn(stored, agentId) ? (stored[agentId] ?? null) : null;
}
