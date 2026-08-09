/**
 * The control-key strip: per-kind built-in keys, the "…" composer trigger, then
 * the kind's synced saved shortcuts.
 *
 * Mounted as a flex sibling **below** the terminal stage, never inside it —
 * xterm's `SelectionService` clears the selection on any row-count change, so
 * chrome that appears inside the stage destroys the selection that summoned it.
 * Collapsing is the same deal as the composer: an explicit user toggle, so the
 * row count only moves when someone asks for it.
 *
 * Saved chips send only. Renaming and removing live in the composer, which is
 * where both mobile clients also put them — the phones additionally offer a
 * long-press menu on the bar, which has no desktop equivalent worth inventing.
 */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  listShortcuts,
  shortcutDisclosure,
  shortcutsForKind,
} from '../../api/shortcutRepository';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import { controlKeysFor } from '../../terminal/controlKeys';
import type { TerminalSessionModel } from '../../terminal/sessionModel';
import { useTerminalPrefsStore } from '../../stores/terminalPrefsStore';

interface ShortcutBarProps {
  model: TerminalSessionModel;
  /** Null while the agent has not told us the kind yet. */
  kind: string | null;
  canSend: boolean;
  onOpenComposer: () => void;
}

export function ShortcutBar({ model, kind, canSend, onOpenComposer }: ShortcutBarProps) {
  /**
   * After a **mouse** click the caret has to go back to the grid, or the user
   * types their next command into a focused button and nothing reaches the PTY
   * (worse: Space or Enter re-fires the chip, sending the chord twice).
   *
   * After a **keyboard** activation it must not: a keyboard user tabbing along
   * the bar would be thrown out of it on every press. `event.detail` is the
   * standard discriminator — 0 for keyboard activation, ≥1 for pointer.
   */
  const restoreFocusAfterClick = (event: { detail: number }): void => {
    if (event.detail > 0) model.focus();
  };

  const expanded = useTerminalPrefsStore((state) => state.shortcutBarExpanded);
  const setExpanded = useTerminalPrefsStore((state) => state.setShortcutBarExpanded);
  const noteRecentChord = useTerminalPrefsStore((state) => state.noteRecentChord);

  const shortcuts = useQuery({
    queryKey: queryKeys.shortcuts(),
    queryFn: listShortcuts,
    ...wsBackedQueryOptions,
  });

  const builtIns = controlKeysFor(kind);
  const saved = useMemo(
    () => shortcutsForKind(shortcuts.data ?? [], kind),
    [shortcuts.data, kind],
  );

  return (
    <div className="shortcut-bar">
      <button
        type="button"
        className="shortcut-bar-toggle"
        aria-expanded={expanded}
        onClick={() => {
          setExpanded(!expanded);
        }}
      >
        {expanded ? 'Hide keys' : 'Show keys'}
      </button>

      {expanded && (
        <div className="shortcut-bar-keys" role="toolbar" aria-label="Terminal keys">
          {builtIns.map((builtIn) => (
            <button
              key={builtIn.id}
              type="button"
              className="shortcut-key"
              title={builtIn.title}
              aria-label={builtIn.ariaLabel ?? builtIn.label}
              disabled={!canSend}
              onClick={(event) => {
                model.sendControlKey(builtIn);
                restoreFocusAfterClick(event);
              }}
            >
              {builtIn.label}
            </button>
          ))}

          <button
            type="button"
            className="shortcut-key shortcut-key-more"
            aria-label="Custom shortcut"
            title="Record or pick any Ctrl / Alt / Shift chord"
            onClick={onOpenComposer}
          >
            …
          </button>

          {saved.map((shortcut) => (
            <button
              key={shortcut.id}
              type="button"
              className={`shortcut-key shortcut-key-saved${shortcut.risky ? ' shortcut-key-risky' : ''}`}
              title={shortcutDisclosure(shortcut)}
              disabled={!canSend}
              onClick={(event) => {
                model.sendChordBytes(shortcut.bytes);
                if (kind !== null) noteRecentChord(kind, shortcut.token);
                restoreFocusAfterClick(event);
              }}
            >
              {shortcut.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
