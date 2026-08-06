/**
 * Per-user saved keyboard shortcuts — the central-server axis.
 *
 * The server stores `chord_token` opaquely (`min_length=1, max_length=64`); its
 * meaning is the shared grammar in `protocol/keyChord.ts`, which is what makes a
 * shortcut saved from a phone work here and the other way round.
 */
import { relayClient } from './clients';
import { toApiError } from './errors';
import type { components } from './generated/central-server';
import {
  chordDisplay,
  chordToken,
  encodeChord,
  parseChordToken,
  type KeyChord,
} from '../protocol/keyChord';
import {
  clampShortcutName,
  createShortcutRequestSchema,
  MAX_SHORTCUT_NAME_LENGTH,
  updateShortcutRequestSchema,
} from '../protocol/dto/shortcuts';

export { clampShortcutName, MAX_SHORTCUT_NAME_LENGTH };

export interface SavedShortcut {
  shortcutId: string;
  /** The verbatim wire kind, never narrowed — `custom_<id>` buckets rely on it. */
  kind: string;
  chordToken: string;
  name: string | null;
  position: number;
  createdAt: string;
}

/** A saved shortcut projected for the bar and the picker. */
export interface ShortcutButton {
  id: string;
  chord: KeyChord;
  token: string;
  name: string | null;
  /** Falls back to the chord itself when the user never named it. */
  label: string;
  /** Encoded as a CSI-u sequence, which not every CLI understands. */
  risky: boolean;
  bytes: Uint8Array;
}

/**
 * The kind's bucket, in the order every client renders it:
 * `position → created_at → shortcut_id`, matching the server's own
 * `ORDER BY` and both mobile clients.
 *
 * `kind` is compared verbatim, so a shortcut saved under a kind this build does
 * not know stays invisible rather than being re-bucketed — and a chord token
 * this build cannot parse is dropped silently, which is how a forward-compatible
 * token from a newer client fails safe.
 */
export function shortcutsForKind(
  all: readonly SavedShortcut[],
  kind: string | null,
): ShortcutButton[] {
  if (kind === null) return [];
  return all
    .filter((shortcut) => shortcut.kind === kind)
    .sort(
      (a, b) =>
        a.position - b.position ||
        a.createdAt.localeCompare(b.createdAt) ||
        a.shortcutId.localeCompare(b.shortcutId),
    )
    .flatMap((shortcut) => {
      const chord = parseChordToken(shortcut.chordToken);
      const encoding = chord === null ? null : encodeChord(chord);
      if (chord === null || encoding === null) return [];
      const name = displayableName(shortcut.name);
      return [
        {
          id: shortcut.shortcutId,
          chord,
          // Re-canonicalised, not the stored string: `parseChordToken` accepts
          // modifiers in any order, so a `shift+ctrl+a` row would otherwise
          // never match the picker's canonical lookup — leaving the Save toggle
          // reading "Save" and a re-save creating a second server row, since the
          // upsert key is `(owner, kind, chord_token)`.
          token: chordToken(chord),
          name,
          label: name ?? chordDisplay(chord),
          risky: encoding.usesCsiU,
          bytes: encoding.bytes,
        },
      ];
    });
}

/**
 * What to render next to a saved shortcut's send button.
 *
 * A label is entirely server-supplied, so one named "Reject" can carry `y` and
 * silently answer an approval prompt. Every surface with a one-click send — the
 * bar, the palette, the picker's Saved chips — discloses the chord alongside it,
 * and they share this helper so they cannot drift apart again.
 */
export function shortcutDisclosure(shortcut: ShortcutButton): string {
  const chord = chordDisplay(shortcut.chord);
  return [
    shortcut.name === null ? chord : `${chord} — ${shortcut.name}`,
    shortcut.risky ? 'CSI-u sequence, may not work in every CLI' : null,
  ]
    .filter((part) => part !== null)
    .join(' — ');
}

/**
 * Blank-name normalisation. `trim()` alone is not enough: it strips `Zs` and
 * line terminators but **not** category `Cf`, so a name of U+200B (or a bidi
 * override) would survive and render a button whose only visible content is
 * empty space — sitting in the same strip as Esc and Ctrl+C, one click from the
 * PTY. Format characters are also what would let a name reorder the disclosure
 * string above.
 */
function displayableName(raw: string | null): string | null {
  if (raw === null) return null;
  const cleaned = raw.replace(/[\p{Cf}\p{Cc}]/gu, '').trim();
  return cleaned === '' ? null : cleaned;
}

/**
 * Mapped off the generated `ShortcutSummary`, like `listTemplates` and
 * `listDevices`. A hand-written wire interface would keep type-checking against
 * a stale shape after a schema change, which is exactly what the type-drift CI
 * gate exists to catch.
 */
function toSavedShortcut(wire: components['schemas']['ShortcutSummary']): SavedShortcut {
  return {
    shortcutId: wire.shortcut_id,
    kind: wire.kind,
    chordToken: wire.chord_token,
    name: wire.name,
    position: wire.position,
    createdAt: wire.created_at,
  };
}

export async function listShortcuts(): Promise<SavedShortcut[]> {
  const { data, error, response } = await relayClient.GET('/api/shortcuts');
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return data.map(toSavedShortcut);
}

/**
 * Save a chord for a session kind.
 *
 * The server upserts on `(owner, kind, chord_token)` and answers 201 either way,
 * so re-saving an existing chord returns that row rather than creating a second
 * button. Saving with no name is a deliberate no-op when the chord already
 * exists — it broadcasts nothing, so a stale cache cannot wipe a name another
 * device just set. Naming is always a separate rename.
 */
export async function createShortcut(
  kind: string,
  chordToken: string,
  name?: string,
): Promise<SavedShortcut> {
  const body = createShortcutRequestSchema.parse({
    kind,
    chord_token: chordToken,
    ...(name === undefined ? {} : { name: clampShortcutName(name) }),
  });
  const { data, error, response } = await relayClient.POST('/api/shortcuts', { body });
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return toSavedShortcut(data);
}

/** Rename, or clear the name with `""` — never `null`, which is a 422 body shape. */
export async function renameShortcut(shortcutId: string, name: string): Promise<void> {
  const body = updateShortcutRequestSchema.parse({ name: clampShortcutName(name.trim()) });
  const { error, response } = await relayClient.PATCH('/api/shortcuts/{shortcut_id}', {
    params: { path: { shortcut_id: shortcutId } },
    body,
  });
  if (error !== undefined) throw toApiError(response, error);
}

/**
 * Delete a shortcut.
 *
 * **Not idempotent**: a repeat DELETE answers 403, because the server maps
 * not-found to 403 rather than 404 so a probe cannot enumerate other accounts'
 * ids. For an id that came out of our own list either status means the same
 * thing — it is gone — which is what the user asked for.
 */
export async function deleteShortcut(shortcutId: string): Promise<void> {
  const { error, response } = await relayClient.DELETE('/api/shortcuts/{shortcut_id}', {
    params: { path: { shortcut_id: shortcutId } },
  });
  if (error !== undefined && response.status !== 403 && response.status !== 404) {
    throw toApiError(response, error);
  }
}
