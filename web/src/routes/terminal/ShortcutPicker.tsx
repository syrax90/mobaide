/**
 * Compose, send and save an arbitrary chord.
 *
 * Two ways in, because neither alone is enough on a desktop. **Record** is the
 * VS Code affordance — press the chord you mean and the app reads it back — but
 * a browser eats some chords before the page ever sees them (Firefox's Ctrl+W is
 * unsuppressable), so **Choose** keeps mobile's modifier-toggles-plus-key-grid
 * as the fallback for exactly those.
 *
 * Saving is never session-gated: create and delete are server mutations, not PTY
 * writes, and both mobile clients allow them while the terminal is disconnected.
 * Sending is.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createShortcut,
  deleteShortcut,
  listShortcuts,
  renameShortcut,
  shortcutDisclosure,
  shortcutsForKind,
  type ShortcutButton,
} from '../../api/shortcutRepository';
import { currentKeyboardEnv } from '../../keyboard/browserEnv';
import { appShortcutTokens } from '../../keyboard/appShortcuts';
import { classifyChordConflicts } from '../../keyboard/chordConflicts';
import { recordChordFromEvent, unsupportedMessage } from '../../keyboard/recordChord';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import {
  chordDisplay,
  chordToken,
  encodeChord,
  keyDisplayName,
  KEY_CHORD_KEYS,
  parseChordToken,
  type KeyChord,
} from '../../protocol/keyChord';
import { controlKeysFor } from '../../terminal/controlKeys';
import type { TerminalSessionModel } from '../../terminal/sessionModel';
import { useTerminalPrefsStore, recentChordsForKind } from '../../stores/terminalPrefsStore';
import { useToastStore } from '../../stores/toastStore';
import { RenameShortcutDialog } from './RenameShortcutDialog';

interface ShortcutPickerProps {
  model: TerminalSessionModel;
  kind: string | null;
  canSend: boolean;
  onClose: () => void;
}

type Mode = 'record' | 'choose';

export function ShortcutPicker({ model, kind, canSend, onClose }: ShortcutPickerProps) {
  const queryClient = useQueryClient();
  const showToast = useToastStore((state) => state.show);
  const env = useMemo(currentKeyboardEnv, []);

  const [mode, setMode] = useState<Mode>('record');
  const [ctrl, setCtrl] = useState(false);
  const [alt, setAlt] = useState(false);
  const [shift, setShift] = useState(false);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [recordMessage, setRecordMessage] = useState<string | null>(null);
  const [renameTarget, setRenameTarget] = useState<ShortcutButton | null>(null);
  const surfaceRef = useRef<HTMLButtonElement>(null);

  const recents = useTerminalPrefsStore((state) => state.recentChords);
  const noteRecentChord = useTerminalPrefsStore((state) => state.noteRecentChord);

  const shortcuts = useQuery({
    queryKey: queryKeys.shortcuts(),
    queryFn: listShortcuts,
    // The list is `staleTime: Infinity` (the user-events bridge invalidates it),
    // so without a deliberate fetch a cache warmed an hour ago renders as truth.
    refetchOnMount: 'always',
    ...wsBackedQueryOptions,
  });

  const saved = shortcutsForKind(shortcuts.data ?? [], kind);
  const chord: KeyChord | null =
    selectedKey === null ? null : { ctrl, alt, shift, key: selectedKey };
  const encoding = chord === null ? null : encodeChord(chord);
  const token = chord === null ? null : chordToken(chord);
  const savedMatch = token === null ? undefined : saved.find((entry) => entry.token === token);

  const conflicts =
    chord === null || token === null
      ? []
      : classifyChordConflicts({
          chord,
          token,
          savedTokens: saved.map((entry) => entry.token),
          builtInKeys: controlKeysFor(kind),
          appTokens: appShortcutTokens(env),
          env,
        });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: queryKeys.shortcuts() });

  const save = useMutation({
    mutationFn: (input: { kind: string; token: string }) =>
      createShortcut(input.kind, input.token),
    onSuccess: invalidate,
    onError: () => {
      showToast('Failed to save shortcut', 'error');
    },
  });
  const remove = useMutation({
    mutationFn: (shortcutId: string) => deleteShortcut(shortcutId),
    onSuccess: invalidate,
    onError: () => {
      showToast('Failed to remove shortcut', 'error');
    },
  });
  const rename = useMutation({
    mutationFn: (input: { id: string; name: string }) => renameShortcut(input.id, input.name),
    onSuccess: async () => {
      setRenameTarget(null);
      await invalidate();
    },
    onError: () => {
      showToast('Failed to rename shortcut', 'error');
    },
  });

  // Capture phase on the document, so a chord is read before anything in the
  // page acts on it — but only while the recording *surface* itself has focus,
  // not merely the dialog. Gating on the whole dialog still traps the keyboard:
  // Tab and Enter are contract base keys, so they would record instead of moving
  // focus, and the Choose tab and Send button would be mouse-only.
  // Paused while the rename dialog is up — that dialog wants its own keystrokes.
  useEffect(() => {
    if (mode !== 'record' || renameTarget !== null) return undefined;

    const onKeyDown = (event: KeyboardEvent): void => {
      // Esc is not part of the chord contract, so it stays the close key.
      if (event.code === 'Escape') return;
      if (document.activeElement !== surfaceRef.current) return;

      const outcome = recordChordFromEvent(event);
      if (outcome.status === 'ignored') return;
      // Unmodified Tab and Shift+Tab are the way out of the recorder, so they
      // navigate rather than record. Neither is a loss: plain Tab is already a
      // built-in bar key on every kind, and both stay composable in Choose,
      // which is exactly what Choose is for.
      if (
        outcome.status === 'chord' &&
        outcome.chord.key === 'tab' &&
        !outcome.chord.ctrl &&
        !outcome.chord.alt
      ) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();

      if (outcome.status === 'unsupported') {
        setRecordMessage(unsupportedMessage(outcome));
        return;
      }
      setCtrl(outcome.chord.ctrl);
      setAlt(outcome.chord.alt);
      setShift(outcome.chord.shift);
      setSelectedKey(outcome.chord.key);
      setRecordMessage(null);
    };

    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [mode, renameTarget]);

  // Every exit restores the caret to the grid, for the reason `send()` and
  // `CommandPalette` both spell out: closing unmounts the focused control, so
  // focus would fall to `document.body` and the next keystroke would reach
  // nothing.
  const closeAndFocus = useCallback(() => {
    onClose();
    model.focus();
  }, [onClose, model]);

  useEffect(() => {
    const onEscape = (event: KeyboardEvent): void => {
      if (event.key === 'Escape' && renameTarget === null) closeAndFocus();
    };
    document.addEventListener('keydown', onEscape);
    return () => {
      document.removeEventListener('keydown', onEscape);
    };
  }, [closeAndFocus, renameTarget]);

  const applyChord = (next: KeyChord): void => {
    setCtrl(next.ctrl);
    setAlt(next.alt);
    setShift(next.shift);
    setSelectedKey(next.key);
    setRecordMessage(null);
  };

  const send = (target: KeyChord): void => {
    const bytes = encodeChord(target)?.bytes;
    if (bytes === undefined) return;
    model.sendChordBytes(bytes);
    if (kind !== null) noteRecentChord(kind, chordToken(target));
    closeAndFocus();
  };

  const modifierCount = [ctrl, alt, shift].filter(Boolean).length;
  const modifierEnabled = (selected: boolean): boolean => selected || modifierCount < 2;

  const toggleSave = (): void => {
    if (savedMatch !== undefined) {
      remove.mutate(savedMatch.id);
      return;
    }
    if (kind === null || token === null) return;
    save.mutate({ kind, token });
  };

  return (
    <div
      className="shortcut-picker"
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcut"
    >
      <header className="shortcut-picker-head">
        <h2>Keyboard shortcut</h2>
        <div className="shortcut-mode-switch" role="tablist" aria-label="How to enter a chord">
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'record'}
            className="button"
            onClick={() => {
              setMode('record');
            }}
          >
            Record
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'choose'}
            className="button"
            onClick={() => {
              setMode('choose');
            }}
          >
            Choose
          </button>
        </div>
        <button type="button" className="button" onClick={closeAndFocus}>
          Close
        </button>
      </header>

      <div className="shortcut-picker-body">
        {mode === 'record' ? (
          <section className="shortcut-record" aria-live="polite">
            {/*
              A real focusable element, not a bare panel: recording is gated on
              *this button* holding focus, so Tab off it disarms the recorder
              entirely and the rest of the dialog becomes ordinary keyboard
              navigation. That is what makes Choose and Send reachable without a
              mouse.
            */}
            <button
              ref={surfaceRef}
              type="button"
              className="shortcut-record-surface"
              autoFocus
              onClick={() => {
                setRecordMessage(null);
              }}
            >
              Press the shortcut you want. Some chords never reach this page —
              Tab away and use <strong>Choose</strong> for those.
            </button>
            {recordMessage !== null && <p className="shortcut-record-error">{recordMessage}</p>}
          </section>
        ) : (
          <>
            <section className="shortcut-section">
              <h3>Modifiers</h3>
              <div className="shortcut-chips">
                <ModifierChip label="Ctrl" on={ctrl} enabled={modifierEnabled(ctrl)} onToggle={setCtrl} />
                <ModifierChip label="Alt" on={alt} enabled={modifierEnabled(alt)} onToggle={setAlt} />
                <ModifierChip label="Shift" on={shift} enabled={modifierEnabled(shift)} onToggle={setShift} />
              </div>
            </section>
            <KeySection title="Letters" keys={KEY_CHORD_KEYS.letters} selected={selectedKey} onPick={setSelectedKey} />
            <KeySection title="Numbers" keys={KEY_CHORD_KEYS.digits} selected={selectedKey} onPick={setSelectedKey} />
            <KeySection
              title="Function & special"
              keys={[...KEY_CHORD_KEYS.functionKeys, ...KEY_CHORD_KEYS.specials]}
              selected={selectedKey}
              onPick={setSelectedKey}
            />
          </>
        )}

        <ChordChips
          title="Recent"
          tokens={recentChordsForKind(recents, kind)}
          canSend={canSend}
          onSend={send}
          onPick={applyChord}
        />

        {saved.length > 0 && (
          <section className="shortcut-section">
            <h3>Saved</h3>
            <div className="shortcut-chips">
              {saved.map((shortcut) => (
                <span key={shortcut.id} className="shortcut-saved-chip">
                  <button
                    type="button"
                    className={`shortcut-chip${shortcut.risky ? ' shortcut-chip-risky' : ''}`}
                    // Same disclosure the bar and palette carry: this is the
                    // surface people use to audit shortcuts that arrived from
                    // another device, so it must show the chord, not just the
                    // name someone else chose.
                    title={shortcutDisclosure(shortcut)}
                    aria-label={shortcutDisclosure(shortcut)}
                    disabled={!canSend}
                    onClick={() => {
                      send(shortcut.chord);
                    }}
                  >
                    {shortcut.label}
                  </button>
                  <button
                    type="button"
                    className="shortcut-chip-action"
                    aria-label={`Rename ${shortcut.label}`}
                    onClick={() => {
                      setRenameTarget(shortcut);
                    }}
                  >
                    ✎
                  </button>
                  <button
                    type="button"
                    className="shortcut-chip-action"
                    aria-label={`Remove ${shortcut.label}`}
                    disabled={remove.isPending}
                    onClick={() => {
                      remove.mutate(shortcut.id);
                    }}
                  >
                    ✕
                  </button>
                </span>
              ))}
            </div>
          </section>
        )}
      </div>

      <footer className="shortcut-picker-foot">
        <div className="shortcut-readout">
          {chord !== null && encoding !== null ? (
            <>
              <span className="shortcut-readout-chord">{chordDisplay(chord)}</span>
              <span className="shortcut-readout-bytes">
                Sends: {encoding.summary}
                {encoding.sameAsPlainKey !== null &&
                  ` — same byte as ${encoding.sameAsPlainKey}`}
              </span>
            </>
          ) : (
            <span className="shortcut-readout-bytes">
              {mode === 'record' ? 'Waiting for a key…' : 'Pick a key'}
            </span>
          )}
        </div>

        {encoding?.usesCsiU === true && (
          <p className="shortcut-hint-risky">CSI-u sequence — may not work in every CLI</p>
        )}
        {conflicts.map((conflict) => (
          <p
            key={conflict.kind}
            className={
              conflict.severity === 'warning' ? 'shortcut-conflict-warn' : 'shortcut-conflict-info'
            }
          >
            {conflict.message}
          </p>
        ))}
        {kind === null && (
          <p className="shortcut-conflict-warn">
            Saving is unavailable until this session&apos;s type is known.
          </p>
        )}

        <div className="button-row">
          <button
            type="button"
            className="button button-primary"
            disabled={!canSend || chord === null}
            onClick={() => {
              if (chord !== null) send(chord);
            }}
          >
            Send
          </button>
          <button
            type="button"
            className="button"
            aria-pressed={savedMatch !== undefined}
            disabled={chord === null || kind === null || save.isPending || remove.isPending}
            onClick={toggleSave}
          >
            {savedMatch === undefined ? 'Save' : 'Saved'}
          </button>
          {savedMatch !== undefined && (
            <button
              type="button"
              className="button"
              onClick={() => {
                setRenameTarget(savedMatch);
              }}
            >
              {savedMatch.name === null ? 'Name' : 'Rename'}
            </button>
          )}
        </div>
      </footer>

      {renameTarget !== null && (
        <RenameShortcutDialog
          key={renameTarget.id}
          shortcut={renameTarget}
          busy={rename.isPending}
          onSave={(name) => {
            rename.mutate({ id: renameTarget.id, name });
          }}
          onClose={() => {
            setRenameTarget(null);
          }}
        />
      )}
    </div>
  );
}

interface ModifierChipProps {
  label: string;
  on: boolean;
  enabled: boolean;
  onToggle: (value: boolean) => void;
}

/** The third, unselected modifier disables once two are on — the contract cap. */
function ModifierChip({ label, on, enabled, onToggle }: ModifierChipProps) {
  return (
    <button
      type="button"
      className="shortcut-chip"
      aria-pressed={on}
      disabled={!enabled}
      onClick={() => {
        onToggle(!on);
      }}
    >
      {label}
    </button>
  );
}

interface KeySectionProps {
  title: string;
  keys: readonly string[];
  selected: string | null;
  onPick: (key: string | null) => void;
}

function KeySection({ title, keys, selected, onPick }: KeySectionProps) {
  return (
    <section className="shortcut-section">
      <h3>{title}</h3>
      <div className="shortcut-chips">
        {keys.map((key) => (
          <button
            key={key}
            type="button"
            className="shortcut-chip"
            aria-pressed={key === selected}
            onClick={() => {
              onPick(key === selected ? null : key);
            }}
          >
            {keyDisplayName(key)}
          </button>
        ))}
      </div>
    </section>
  );
}

interface ChordChipsProps {
  title: string;
  tokens: readonly string[];
  canSend: boolean;
  onSend: (chord: KeyChord) => void;
  onPick: (chord: KeyChord) => void;
}

/** Recents: tap sends, the pencil loads the chord into the composer instead. */
function ChordChips({ title, tokens, canSend, onSend, onPick }: ChordChipsProps) {
  const chords = tokens.flatMap((token) => {
    const chord = parseChordToken(token);
    return chord === null ? [] : [{ token, chord }];
  });
  if (chords.length === 0) return null;

  return (
    <section className="shortcut-section">
      <h3>{title}</h3>
      <div className="shortcut-chips">
        {chords.map(({ token, chord }) => (
          <span key={token} className="shortcut-saved-chip">
            <button
              type="button"
              className="shortcut-chip"
              disabled={!canSend}
              onClick={() => {
                onSend(chord);
              }}
            >
              {chordDisplay(chord)}
            </button>
            <button
              type="button"
              className="shortcut-chip-action"
              aria-label={`Edit ${chordDisplay(chord)}`}
              onClick={() => {
                onPick(chord);
              }}
            >
              ✎
            </button>
          </span>
        ))}
      </div>
    </section>
  );
}
