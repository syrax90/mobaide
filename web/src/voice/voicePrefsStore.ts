import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface VoicePrefsState {
  /**
   * Agents whose mic button the user hid ("Hide mic for this agent" in the
   * enable dialog); undone from the agent's Voice input settings. Mirrors the
   * per-agent `voice_mic_hidden::<agent>` keys on Android and iOS.
   */
  hiddenAgents: Record<string, true>;
  /**
   * Agents whose transcripts go straight to the session's prompt, skipping
   * the review dialog ("Insert directly from now on" in the review); undone
   * from the agent's Voice input settings. Web only: the mobile apps put the
   * text in the composer, which is the review.
   */
  skipReviewAgents: Record<string, true>;
  /**
   * Agents whose transcripts the session's AI agent cleans up before the
   * review, in sessions that support it. Off by default: the text then leaves
   * the computer for the AI agent's provider.
   */
  cleanupAgents: Record<string, true>;
  /**
   * The user pressed "Got it" on the cleanup notice, which comes before the
   * first opt-in on this browser. Mirrors `voice_cleanup_notice_seen` on
   * Android and iOS.
   */
  cleanupNoticeSeen: boolean;
  setMicHidden: (agentId: string, hidden: boolean) => void;
  setSkipReview: (agentId: string, skip: boolean) => void;
  setCleanup: (agentId: string, on: boolean) => void;
  acknowledgeCleanupNotice: () => void;
  forgetAgent: (agentId: string) => void;
}

function withAgent(map: Record<string, true>, agentId: string, on: boolean): Record<string, true> {
  const next = withoutAgent(map, agentId);
  if (on) {
    Object.defineProperty(next, agentId, { value: true, enumerable: true, writable: true, configurable: true });
  }
  return next;
}

/** Untrusted storage: keep only non-empty ids mapped to `true`. */
function cleanAgentMap(stored: unknown): Record<string, true> {
  const clean: Record<string, true> = {};
  if (typeof stored === 'object' && stored !== null) {
    for (const [agentId, value] of Object.entries(stored)) {
      if (agentId !== '' && value === true) {
        Object.defineProperty(clean, agentId, { value: true, enumerable: true, writable: true, configurable: true });
      }
    }
  }
  return clean;
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
      skipReviewAgents: {},
      cleanupAgents: {},
      cleanupNoticeSeen: false,
      setMicHidden: (agentId, hidden) => {
        safely(() => {
          set((state) => ({ hiddenAgents: withAgent(state.hiddenAgents, agentId, hidden) }));
        });
      },
      setSkipReview: (agentId, skip) => {
        safely(() => {
          set((state) => ({ skipReviewAgents: withAgent(state.skipReviewAgents, agentId, skip) }));
        });
      },
      setCleanup: (agentId, on) => {
        safely(() => {
          set((state) => ({ cleanupAgents: withAgent(state.cleanupAgents, agentId, on) }));
        });
      },
      acknowledgeCleanupNotice: () => {
        safely(() => {
          set({ cleanupNoticeSeen: true });
        });
      },
      forgetAgent: (agentId) => {
        safely(() => {
          set((state) =>
            Object.hasOwn(state.hiddenAgents, agentId) ||
            Object.hasOwn(state.skipReviewAgents, agentId) ||
            Object.hasOwn(state.cleanupAgents, agentId)
              ? {
                  hiddenAgents: withoutAgent(state.hiddenAgents, agentId),
                  skipReviewAgents: withoutAgent(state.skipReviewAgents, agentId),
                  cleanupAgents: withoutAgent(state.cleanupAgents, agentId),
                }
              : state,
          );
        });
      },
    }),
    {
      name: 'mobaide-voice-prefs',
      merge: (persisted, current) => {
        const stored = persisted as Partial<VoicePrefsState> | undefined;
        return {
          ...current,
          hiddenAgents: cleanAgentMap(stored?.hiddenAgents),
          skipReviewAgents: cleanAgentMap(stored?.skipReviewAgents),
          cleanupAgents: cleanAgentMap(stored?.cleanupAgents),
          cleanupNoticeSeen: stored?.cleanupNoticeSeen === true,
        };
      },
      partialize: (state) => ({
        hiddenAgents: state.hiddenAgents,
        skipReviewAgents: state.skipReviewAgents,
        cleanupAgents: state.cleanupAgents,
        cleanupNoticeSeen: state.cleanupNoticeSeen,
      }),
    },
  ),
);

export function useMicHidden(agentId: string): boolean {
  return useVoicePrefsStore((state) => Object.hasOwn(state.hiddenAgents, agentId));
}

export function useSkipReview(agentId: string): boolean {
  return useVoicePrefsStore((state) => Object.hasOwn(state.skipReviewAgents, agentId));
}

export function useCleanup(agentId: string): boolean {
  return useVoicePrefsStore((state) => Object.hasOwn(state.cleanupAgents, agentId));
}
