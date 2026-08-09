import { describe, expect, it } from 'vitest';
import { controlKeysFor, type ControlKey } from './controlKeys';

const ids = (keys: readonly ControlKey[]): string[] => keys.map((key) => key.id);

function byId(keys: readonly ControlKey[], id: string): ControlKey {
  const found = keys.find((key) => key.id === id);
  if (found === undefined) throw new Error(`${id} is not in this set`);
  return found;
}

const DEFAULT_IDS = [
  'enter',
  'up',
  'down',
  'left',
  'right',
  'ctrlC',
  'esc',
  'tab',
  'backspace',
];

describe('controlKeysFor — per-kind membership', () => {
  it('gives Claude the plan-mode and rewind keys', () => {
    const claude = ids(controlKeysFor('claude'));

    expect(claude).toHaveLength(14);
    expect(claude).toEqual(expect.arrayContaining(['mode', 'rewind', 'ctrlL', 'ctrlO', 'ctrlR']));
    expect(claude).not.toEqual(expect.arrayContaining(['approve', 'reject']));
  });

  it('gives auto-scan the same set as Claude, which is what it runs', () => {
    expect(ids(controlKeysFor('auto_scan'))).toEqual(ids(controlKeysFor('claude')));
  });

  it('gives Codex the Claude set plus approve and reject', () => {
    const codex = ids(controlKeysFor('codex'));

    expect(codex).toHaveLength(16);
    expect(codex).toEqual(expect.arrayContaining([...ids(controlKeysFor('claude')), 'approve', 'reject']));
  });

  it('gives Antigravity its own three keys and neither Mode nor Rewind', () => {
    const antigravity = ids(controlKeysFor('antigravity'));

    expect(antigravity).toHaveLength(15);
    expect(antigravity).toEqual(expect.arrayContaining(['ctrlK', 'ctrlG', 'ctrlD']));
    expect(antigravity).not.toEqual(expect.arrayContaining(['mode', 'rewind', 'approve']));
  });
});

describe('controlKeysFor — the neutral set', () => {
  // CLA-158: a plain shell used to inherit Claude's bar, which put `CSI Z` and
  // `ESC ESC` — Claude-TUI input — one tap away from a bash prompt, and offered
  // Ctrl+O / Ctrl+R / Ctrl+L as if they meant what they mean in Claude Code.
  it('gives a shell the neutral nine, not the Claude set', () => {
    expect(ids(controlKeysFor('shell'))).toEqual(DEFAULT_IDS);
  });

  it('gives a custom agent type the neutral nine', () => {
    expect(ids(controlKeysFor('custom_a1b2c3'))).toEqual(DEFAULT_IDS);
  });

  it('gives an unresolved kind the neutral nine rather than guessing Claude', () => {
    expect(ids(controlKeysFor(null))).toEqual(DEFAULT_IDS);
  });

  it('gives a kind from a newer server the neutral nine', () => {
    // An unknown kind means this build is older than the agent, not that the
    // session is Claude.
    expect(ids(controlKeysFor('some_future_cli'))).toEqual(DEFAULT_IDS);
  });

  it('withholds the readline keys a foreign CLI may have remapped', () => {
    expect(ids(controlKeysFor('shell'))).not.toEqual(
      expect.arrayContaining(['ctrlO', 'ctrlR', 'ctrlL', 'mode', 'rewind']),
    );
  });
});

describe('controlKeysFor — ordering', () => {
  // Membership is what differs between kinds, never position: a button that
  // appears in two bars has to sit in the same place in both, or muscle memory
  // breaks every time the session kind changes.
  it('keeps shared keys in the same relative order across kinds', () => {
    const claude = ids(controlKeysFor('claude'));

    for (const kind of ['codex', 'antigravity', 'shell']) {
      const shared = ids(controlKeysFor(kind)).filter((id) => claude.includes(id));

      expect(shared).toEqual(claude.filter((id) => shared.includes(id)));
    }
  });

  it('renders no key twice', () => {
    for (const kind of ['claude', 'codex', 'antigravity', 'shell']) {
      const rendered = ids(controlKeysFor(kind));

      expect(new Set(rendered).size).toBe(rendered.length);
    }
  });
});

describe('controlKeysFor — bytes', () => {
  const claude = controlKeysFor('claude');

  it('erases with DEL, which is what readline and the CLIs expect', () => {
    // 0x08 (BS) is the trap here: it moves the cursor rather than erasing.
    expect(byId(claude, 'backspace').bytes).toEqual(Uint8Array.of(0x7f));
  });

  it('sends Mode as CSI Z and Rewind as a double Escape', () => {
    expect(byId(claude, 'mode').bytes).toEqual(Uint8Array.of(0x1b, 0x5b, 0x5a));
    expect(byId(claude, 'rewind').bytes).toEqual(Uint8Array.of(0x1b, 0x1b));
  });

  it('answers Codex prompts with a bare letter and no carriage return', () => {
    // Enter stays a separate button: the confirm prompts read a single
    // keystroke, and appending CR would submit the next line too.
    const codex = controlKeysFor('codex');

    expect(byId(codex, 'approve').bytes).toEqual(Uint8Array.of(0x79));
    expect(byId(codex, 'reject').bytes).toEqual(Uint8Array.of(0x6e));
  });

  it('sends the control keys as their C0 bytes', () => {
    expect(byId(claude, 'ctrlC').bytes).toEqual(Uint8Array.of(0x03));
    expect(byId(claude, 'ctrlL').bytes).toEqual(Uint8Array.of(0x0c));
    expect(byId(claude, 'ctrlO').bytes).toEqual(Uint8Array.of(0x0f));
    expect(byId(claude, 'ctrlR').bytes).toEqual(Uint8Array.of(0x12));
    expect(byId(controlKeysFor('antigravity'), 'ctrlK').bytes).toEqual(Uint8Array.of(0x0b));
  });

  it('flags exactly Ctrl+L to clear the local view, in every set that has it', () => {
    for (const kind of ['claude', 'codex', 'antigravity']) {
      for (const key of controlKeysFor(kind)) {
        expect(key.clearsView ?? false).toBe(key.id === 'ctrlL');
      }
    }
  });

  it('labels the glyph-only keys for a screen reader', () => {
    // '⌫' and the arrows are announced as their glyph name or not at all.
    for (const id of ['backspace', 'up', 'down', 'left', 'right']) {
      expect(byId(claude, id).ariaLabel).toBeTypeOf('string');
    }
  });
});
