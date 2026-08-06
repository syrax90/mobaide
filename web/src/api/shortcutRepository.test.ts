import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SavedShortcut } from './shortcutRepository';

// Hand-written stand-in for the typed client, as in `templateRepository.test.ts`:
// `relayClient` is built with the SPA's relative base URL, which the undici
// Request under vitest refuses to parse. What is under test is the repository's
// own mapping and its status handling, so the queue below plays the wire.
const relay = vi.hoisted(() => ({
  results: [] as { data?: unknown; error?: unknown; response: Response }[],
  deletes: [] as string[],
}));

vi.mock('./clients', () => ({
  relayClient: {
    DELETE: (_path: string, init: { params: { path: { shortcut_id: string } } }) => {
      relay.deletes.push(init.params.path.shortcut_id);
      const next = relay.results.shift();
      if (next === undefined) return Promise.reject(new Error('unexpected DELETE'));
      return Promise.resolve(next);
    },
  },
}));

const { clampShortcutName, deleteShortcut, shortcutsForKind } = await import(
  './shortcutRepository'
);
const { ApiError } = await import('./errors');

afterEach(() => {
  relay.results.length = 0;
  relay.deletes.length = 0;
});

function saved(over: Partial<SavedShortcut> = {}): SavedShortcut {
  return {
    shortcutId: 'sc-1',
    kind: 'claude',
    chordToken: 'ctrl+shift+i',
    name: null,
    position: 0,
    createdAt: '2026-08-01T10:00:00Z',
    ...over,
  };
}

describe('shortcutsForKind — bucketing', () => {
  it('takes only the rows filed under this exact kind', () => {
    const all = [
      saved({ shortcutId: 'a', kind: 'claude' }),
      saved({ shortcutId: 'b', kind: 'codex' }),
    ];

    expect(shortcutsForKind(all, 'claude').map((entry) => entry.id)).toEqual(['a']);
  });

  it('compares the kind verbatim, so a custom type keeps its own bucket', () => {
    // `custom_<shortid>` is an open wire string; matching it by prefix would
    // pour every custom agent's shortcuts into one bar.
    const all = [
      saved({ shortcutId: 'a', kind: 'custom_a1b2c3' }),
      saved({ shortcutId: 'b', kind: 'custom_d4e5f6' }),
    ];

    expect(shortcutsForKind(all, 'custom_a1b2c3').map((entry) => entry.id)).toEqual(['a']);
  });

  it('shows nothing while the session kind is still unknown', () => {
    // Guessing would render a bar under a kind the user never chose.
    expect(shortcutsForKind([saved()], null)).toEqual([]);
  });
});

describe('shortcutsForKind — ordering', () => {
  it('sorts by position first', () => {
    const all = [
      saved({ shortcutId: 'b', position: 2, chordToken: 'ctrl+b' }),
      saved({ shortcutId: 'a', position: 1, chordToken: 'ctrl+a' }),
    ];

    expect(shortcutsForKind(all, 'claude').map((entry) => entry.id)).toEqual(['a', 'b']);
  });

  it('breaks a tied position on creation time', () => {
    const all = [
      saved({ shortcutId: 'b', createdAt: '2026-08-02T10:00:00Z', chordToken: 'ctrl+b' }),
      saved({ shortcutId: 'a', createdAt: '2026-08-01T10:00:00Z', chordToken: 'ctrl+a' }),
    ];

    expect(shortcutsForKind(all, 'claude').map((entry) => entry.id)).toEqual(['a', 'b']);
  });

  it('breaks a tied creation time on the id, so the order is total', () => {
    // Two devices saving in the same second must not render in a different
    // order on each of them.
    const all = [
      saved({ shortcutId: 'sc-b', chordToken: 'ctrl+b' }),
      saved({ shortcutId: 'sc-a', chordToken: 'ctrl+a' }),
    ];

    expect(shortcutsForKind(all, 'claude').map((entry) => entry.id)).toEqual(['sc-a', 'sc-b']);
  });
});

describe('shortcutsForKind — projection', () => {
  it('falls back to the chord itself when the shortcut was never named', () => {
    expect(shortcutsForKind([saved({ chordToken: 'ctrl+shift+i' })], 'claude')[0]?.label).toBe(
      'Ctrl+Shift+I',
    );
  });

  it('treats a blank name as no name at all', () => {
    // The wire sentinel for clearing a name is `""`, and a whitespace-only
    // rename would otherwise render an invisible button.
    const cleared = shortcutsForKind([saved({ name: '   ' })], 'claude')[0];

    expect(cleared?.name).toBeNull();
    expect(cleared?.label).toBe('Ctrl+Shift+I');
  });

  it('keeps a real name over the chord', () => {
    const named = shortcutsForKind([saved({ name: 'Inspect' })], 'claude')[0];

    expect(named?.name).toBe('Inspect');
    expect(named?.label).toBe('Inspect');
  });

  it('marks a CSI-u chord risky and a legacy one not', () => {
    const [risky] = shortcutsForKind([saved({ chordToken: 'ctrl+shift+i' })], 'claude');
    const [plain] = shortcutsForKind([saved({ chordToken: 'ctrl+c' })], 'claude');

    expect(risky?.risky).toBe(true);
    expect(plain?.risky).toBe(false);
  });

  it('carries the encoded bytes the button will send', () => {
    expect(shortcutsForKind([saved({ chordToken: 'ctrl+c' })], 'claude')[0]?.bytes).toEqual(
      Uint8Array.of(0x03),
    );
  });

  it('drops a token this build cannot parse instead of rendering a dead button', () => {
    // A newer client could sync a token from a grammar this build predates;
    // failing safe means the rest of the bucket still renders.
    const all = [
      saved({ shortcutId: 'good', chordToken: 'ctrl+a' }),
      saved({ shortcutId: 'future', chordToken: 'hyper+meta+a' }),
    ];

    expect(shortcutsForKind(all, 'claude').map((entry) => entry.id)).toEqual(['good']);
  });
});

describe('clampShortcutName', () => {
  it('leaves a name inside the limit alone', () => {
    expect(clampShortcutName('Inspect element')).toBe('Inspect element');
  });

  it('counts code points, not UTF-16 units', () => {
    // Pydantic's `max_length` counts code points. A `slice(0, 100)` of astral
    // characters keeps only 50 of them and still 422s on the way back.
    const astral = '𝔘'.repeat(120);

    const clamped = clampShortcutName(astral);

    expect([...clamped]).toHaveLength(100);
    expect(clamped.length).toBe(200);
  });

  it('clamps a plain name at the same code-point limit', () => {
    expect(clampShortcutName('a'.repeat(120))).toHaveLength(100);
  });
});

describe('deleteShortcut', () => {
  const refusal = (status: number): { error: unknown; response: Response } => ({
    error: { detail: 'Shortcut not found' },
    response: new Response(null, { status }),
  });

  it('absorbs the 403 a repeat delete answers with', () => {
    // DELETE is deliberately not idempotent server-side — not-found maps to 403
    // so a probe cannot enumerate other accounts' ids. For an id that came out
    // of our own list, gone is gone, which is what the user asked for.
    relay.results.push(refusal(403));

    return expect(deleteShortcut('sc-1')).resolves.toBeUndefined();
  });

  it('absorbs a 404 the same way', () => {
    relay.results.push(refusal(404));

    return expect(deleteShortcut('sc-1')).resolves.toBeUndefined();
  });

  it('still raises a real failure', () => {
    relay.results.push(refusal(500));

    return expect(deleteShortcut('sc-1')).rejects.toBeInstanceOf(ApiError);
  });

  it('addresses the shortcut it was asked to delete', async () => {
    relay.results.push({ error: undefined, response: new Response(null, { status: 204 }) });

    await deleteShortcut('sc-42');

    expect(relay.deletes).toEqual(['sc-42']);
  });
});
