import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { parseChordToken } from '../protocol/keyChord';

/** Android `PreferencesStore.MAX_RECENT_SHORTCUT_CHORDS`. */
export const MAX_RECENT_CHORDS = 10;

interface TerminalPrefsState {
  /**
   * Send `Ctrl+V` to the program as `0x16` instead of pasting.
   *
   * Off by default — pasting is what the chord means everywhere else on a
   * desktop — but Claude Code binds `Ctrl+V` for image paste, so the escape
   * hatch has to exist. No mobile counterpart: neither client has a physical
   * keyboard to steal the chord from.
   */
  passCtrlVToTerminal: boolean;
  setPassCtrlVToTerminal: (value: boolean) => void;
  /**
   * Whether the control-key strip is open. Expanded by default: on a desktop the
   * bar is not the only way to send these keys, it is how anyone finds out that
   * Mode, Rewind and saved shortcuts exist — and on Windows and Linux it is the
   * only way to send Ctrl+L and Ctrl+O, which the browser takes.
   */
  shortcutBarExpanded: boolean;
  setShortcutBarExpanded: (value: boolean) => void;
  /**
   * Recently sent chord tokens per session kind, newest first. Device-local and
   * never synced, exactly like both mobile clients — a recent is a "what was I
   * just doing", not a saved button.
   */
  recentChords: Record<string, string[]>;
  noteRecentChord: (kind: string, token: string) => void;
}

/**
 * `Object.entries`/`hasOwn` everywhere, and `__proto__` dropped on the way in:
 * a session kind is an open wire string matching `^[a-z0-9_]{1,32}$`, so
 * `constructor` is a legal kind, and a plain `recents[kind]` lookup would hand
 * back `Object.prototype.constructor` instead of `undefined`.
 */
function recentsFor(recents: Record<string, string[]>, kind: string): string[] {
  return Object.hasOwn(recents, kind) ? (recents[kind] ?? []) : [];
}

export function recentChordsForKind(
  recents: Record<string, string[]>,
  kind: string | null,
): string[] {
  return kind === null ? [] : recentsFor(recents, kind);
}

function sanitizeRecents(value: unknown): Record<string, string[]> {
  const clean: Record<string, string[]> = {};
  if (typeof value !== 'object' || value === null) return clean;
  for (const [kind, tokens] of Object.entries(value)) {
    if (kind === '__proto__' || !Array.isArray(tokens)) continue;
    // Tokens are re-parsed rather than trusted: a stale entry from an older
    // build, or anything hand-edited into localStorage, must not reach the
    // encoder.
    const valid = tokens
      .filter((token): token is string => typeof token === 'string')
      .filter((token) => parseChordToken(token) !== null)
      .slice(0, MAX_RECENT_CHORDS);
    if (valid.length > 0) clean[kind] = valid;
  }
  return clean;
}

export const useTerminalPrefsStore = create<TerminalPrefsState>()(
  persist(
    (set) => ({
      passCtrlVToTerminal: false,
      setPassCtrlVToTerminal: (passCtrlVToTerminal) => {
        set({ passCtrlVToTerminal });
      },
      shortcutBarExpanded: true,
      setShortcutBarExpanded: (shortcutBarExpanded) => {
        set({ shortcutBarExpanded });
      },
      recentChords: {},
      noteRecentChord: (kind, token) => {
        if (kind === '__proto__' || parseChordToken(token) === null) return;
        set((state) => {
          const existing = recentsFor(state.recentChords, kind);
          const next = [token, ...existing.filter((entry) => entry !== token)].slice(
            0,
            MAX_RECENT_CHORDS,
          );
          return { recentChords: { ...state.recentChords, [kind]: next } };
        });
      },
    }),
    {
      name: 'mobaide-terminal',
      // Same untrusted-boundary rule as `themeStore`: anything but a real
      // boolean falls back to the default rather than reaching the key handler
      // as a truthy string.
      merge: (persisted, current) => {
        const saved = persisted as Partial<TerminalPrefsState> | undefined;
        return {
          ...current,
          passCtrlVToTerminal: saved?.passCtrlVToTerminal === true,
          shortcutBarExpanded: saved?.shortcutBarExpanded !== false,
          recentChords: sanitizeRecents(saved?.recentChords),
        };
      },
    },
  ),
);
