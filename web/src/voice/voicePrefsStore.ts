import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface VoicePrefsState {
  /**
   * Agents whose mic button the user hid ("Hide mic for this agent" in the
   * enable dialog); undone from the agent's Voice input settings. Mirrors the
   * per-agent `voice_mic_hidden::<agent>` keys on Android and iOS.
   */
  hiddenAgents: Record<string, true>;
  setMicHidden: (agentId: string, hidden: boolean) => void;
  forgetAgent: (agentId: string) => void;
}

function withoutAgent(map: Record<string, true>, agentId: string): Record<string, true> {
  const next: Record<string, true> = {};
  for (const [id, value] of Object.entries(map)) {
    if (id !== agentId) Object.defineProperty(next, id, { value, enumerable: true, writable: true, configurable: true });
  }
  return next;
}

// `persist` writes localStorage on every `set` with no guard of its own; a
// refused write (private mode, full quota) must not break the click that
// caused it. The in-memory change has landed by then.
function safely(apply: () => void): void {
  try {
    apply();
  } catch {
    // Storage refused.
  }
}

export const useVoicePrefsStore = create<VoicePrefsState>()(
  persist(
    (set) => ({
      hiddenAgents: {},
      setMicHidden: (agentId, hidden) => {
        safely(() => {
          set((state) => {
            const next = withoutAgent(state.hiddenAgents, agentId);
            if (hidden) {
              Object.defineProperty(next, agentId, { value: true, enumerable: true, writable: true, configurable: true });
            }
            return { hiddenAgents: next };
          });
        });
      },
      forgetAgent: (agentId) => {
        safely(() => {
          set((state) =>
            Object.hasOwn(state.hiddenAgents, agentId)
              ? { hiddenAgents: withoutAgent(state.hiddenAgents, agentId) }
              : state,
          );
        });
      },
    }),
    {
      name: 'mobaide-voice-prefs',
      // Untrusted storage: keep only non-empty ids mapped to `true`.
      merge: (persisted, current) => {
        const stored = (persisted as Partial<VoicePrefsState> | undefined)?.hiddenAgents;
        const clean: Record<string, true> = {};
        if (typeof stored === 'object' && stored !== null) {
          for (const [agentId, value] of Object.entries(stored)) {
            if (agentId !== '' && value === true) {
              Object.defineProperty(clean, agentId, { value: true, enumerable: true, writable: true, configurable: true });
            }
          }
        }
        return { ...current, hiddenAgents: clean };
      },
      partialize: (state) => ({ hiddenAgents: state.hiddenAgents }),
    },
  ),
);

export function useMicHidden(agentId: string): boolean {
  return useVoicePrefsStore((state) => Object.hasOwn(state.hiddenAgents, agentId));
}
