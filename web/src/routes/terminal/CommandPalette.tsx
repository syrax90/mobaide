/**
 * One filterable list over everything the terminal screen can do without the
 * mouse: the kind's built-in control keys, its saved shortcuts, and the prompt
 * templates.
 *
 * Opened by the app-owned chord in `keyboard/appShortcuts.ts` (Ctrl+Shift+P, or
 * ⌘⇧P on a Mac) and by the toolbar button — the button matters, because on
 * Firefox for Windows and Linux that chord opens a private window instead.
 */
import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listShortcuts, shortcutsForKind } from '../../api/shortcutRepository';
import { listTemplates } from '../../api/templateRepository';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import { chordDisplay } from '../../protocol/keyChord';
import { controlKeysFor } from '../../terminal/controlKeys';
import type { TerminalSessionModel } from '../../terminal/sessionModel';
import { useTerminalPrefsStore } from '../../stores/terminalPrefsStore';

interface CommandPaletteProps {
  model: TerminalSessionModel;
  kind: string | null;
  canSend: boolean;
  onInsertTemplate: (body: string) => void;
  onClose: () => void;
}

interface PaletteCommand {
  id: string;
  group: string;
  label: string;
  hint: string;
  disabled: boolean;
  /**
   * False only for the template rows, which hand focus to the composer. Every
   * other row sends bytes and should leave the caret back in the grid.
   */
  restoresTerminalFocus: boolean;
  run: () => void;
}

export function CommandPalette({
  model,
  kind,
  canSend,
  onInsertTemplate,
  onClose,
}: CommandPaletteProps) {
  const [filter, setFilter] = useState('');
  const [active, setActive] = useState(0);
  const noteRecentChord = useTerminalPrefsStore((state) => state.noteRecentChord);

  const shortcuts = useQuery({
    queryKey: queryKeys.shortcuts(),
    queryFn: listShortcuts,
    refetchOnMount: 'always',
    ...wsBackedQueryOptions,
  });
  const templates = useQuery({
    queryKey: queryKeys.templates(),
    queryFn: listTemplates,
    refetchOnMount: 'always',
    ...wsBackedQueryOptions,
  });

  const commands = useMemo<PaletteCommand[]>(() => {
    const saved = shortcutsForKind(shortcuts.data ?? [], kind);
    return [
      ...saved.map((shortcut) => ({
        id: `shortcut:${shortcut.id}`,
        group: 'Saved shortcuts',
        label: shortcut.label,
        // The chord, not just the name: a saved shortcut's label is entirely
        // user-supplied, so one named "Reject" could carry `y`. The bar's
        // tooltip discloses the same thing for the same reason.
        hint: shortcut.risky
          ? `${chordDisplay(shortcut.chord)} · CSI-u`
          : chordDisplay(shortcut.chord),
        disabled: !canSend,
        restoresTerminalFocus: true,
        run: () => {
          model.sendChordBytes(shortcut.bytes);
          // Mobile records an MRU entry on every chord send (Android
          // `TerminalViewModel.sendChord`, iOS `sendChord`); built-in control
          // keys are deliberately excluded there too.
          if (kind !== null) noteRecentChord(kind, shortcut.token);
        },
      })),
      ...controlKeysFor(kind).map((builtIn) => ({
        id: `key:${builtIn.id}`,
        group: 'Keys',
        label: builtIn.ariaLabel ?? builtIn.label,
        hint: builtIn.title,
        disabled: !canSend,
        restoresTerminalFocus: true,
        run: () => {
          model.sendControlKey(builtIn);
        },
      })),
      ...(templates.data ?? []).map((template) => ({
        id: `template:${template.templateId}`,
        group: 'Templates',
        label: template.name,
        hint: template.body.slice(0, 60),
        disabled: false,
        restoresTerminalFocus: false,
        run: () => {
          onInsertTemplate(template.body);
        },
      })),
    ];
  }, [shortcuts.data, templates.data, kind, canSend, model, onInsertTemplate, noteRecentChord]);

  const needle = filter.trim().toLowerCase();
  const matches =
    needle === ''
      ? commands
      : commands.filter(
          (command) =>
            command.label.toLowerCase().includes(needle) ||
            command.group.toLowerCase().includes(needle),
        );

  // Clamped rather than reset, so typing a narrower filter keeps the highlight
  // on screen instead of jumping back to the top on every keystroke.
  const selected = Math.min(active, Math.max(matches.length - 1, 0));

  const runCommand = (command: PaletteCommand | undefined): void => {
    if (command === undefined || command.disabled) return;
    command.run();
    onClose();
    // Closing unmounts the row that had focus, so it would otherwise fall to
    // `document.body` and the next keystroke would reach nothing.
    if (command.restoresTerminalFocus) model.focus();
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        onClose();
        model.focus();
        return;
      }
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        setActive((index) => Math.min(index + 1, Math.max(matches.length - 1, 0)));
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        setActive((index) => Math.max(index - 1, 0));
      }
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [matches.length, onClose, model]);

  return (
    <div
      className="command-palette"
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          runCommand(matches[selected]);
        }}
      >
        <input
          type="search"
          className="field-input"
          value={filter}
          autoFocus
          placeholder="Search keys, shortcuts and templates"
          aria-label="Filter commands"
          onChange={(event) => {
            setFilter(event.target.value);
            setActive(0);
          }}
        />
      </form>

      {matches.length === 0 ? (
        <p className="empty-state">Nothing matches.</p>
      ) : (
        <ul className="command-palette-list">
          {matches.map((command, index) => (
            <li key={command.id}>
              <button
                type="button"
                className="command-palette-row"
                aria-current={index === selected}
                disabled={command.disabled}
                onClick={() => {
                  runCommand(command);
                }}
              >
                <span className="command-palette-group">{command.group}</span>
                <span className="command-palette-label">{command.label}</span>
                <span className="command-palette-hint">{command.hint}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
