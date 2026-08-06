import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SavedShortcut } from '../../api/shortcutRepository';
import type { TerminalSessionModel } from '../../terminal/sessionModel';
import { stubLocalStorage } from '../../test/localStorageStub';

stubLocalStorage();

// Only the wire calls are replaced. `shortcutsForKind`, the encoder, the
// recorder and the conflict classifier all stay real: what this suite is about
// is the picker wiring them together.
const repo = vi.hoisted(() => ({
  shortcuts: [] as SavedShortcut[],
  created: [] as { kind: string; token: string }[],
  removed: [] as string[],
}));

vi.mock('../../api/shortcutRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/shortcutRepository')>();
  return {
    ...actual,
    listShortcuts: () => Promise.resolve(repo.shortcuts),
    createShortcut: (kind: string, chordToken: string) => {
      repo.created.push({ kind, token: chordToken });
      repo.shortcuts = [
        ...repo.shortcuts,
        {
          shortcutId: `sc-${String(repo.created.length)}`,
          kind,
          chordToken,
          name: null,
          position: 0,
          createdAt: '2026-08-01T10:00:00Z',
        },
      ];
      return Promise.resolve(repo.shortcuts.at(-1));
    },
    deleteShortcut: (shortcutId: string) => {
      repo.removed.push(shortcutId);
      repo.shortcuts = repo.shortcuts.filter((entry) => entry.shortcutId !== shortcutId);
      return Promise.resolve();
    },
  };
});

const { ShortcutPicker } = await import('./ShortcutPicker');
const { useTerminalPrefsStore } = await import('../../stores/terminalPrefsStore');

const sent: Uint8Array[] = [];
let focused = 0;

const model = {
  sendChordBytes: (payload: Uint8Array) => sent.push(payload),
  // Sending closes the dialog, which unmounts the control that had focus; the
  // picker hands the caret back to the grid rather than leaving it on `body`.
  focus: () => {
    focused += 1;
  },
} as unknown as TerminalSessionModel;

let closed = 0;

function renderPicker(over: { kind?: string | null; canSend?: boolean } = {}) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ShortcutPicker
        model={model}
        kind={over.kind === undefined ? 'claude' : over.kind}
        canSend={over.canSend ?? true}
        onClose={() => {
          closed += 1;
        }}
      />
    </QueryClientProvider>,
  );
  return screen.getByRole('dialog', { name: 'Keyboard shortcut' });
}

/** Press a chord where the recorder listens: capture phase on the document. */
function press(code: string, held: Partial<KeyboardEventInit> = {}): void {
  fireEvent.keyDown(document, { code, key: code, ...held });
}

const saveButton = (): HTMLElement => screen.getByRole('button', { name: /^Saved?$/ });

beforeEach(() => {
  repo.shortcuts = [];
  repo.created.length = 0;
  repo.removed.length = 0;
  sent.length = 0;
  focused = 0;
  closed = 0;
  useTerminalPrefsStore.setState({ recentChords: {} });
});

afterEach(cleanup);

describe('ShortcutPicker record mode', () => {
  it('reads back the chord and the bytes it will send', async () => {
    renderPicker();

    press('KeyI', { ctrlKey: true, shiftKey: true });

    expect(await screen.findByText('Ctrl+Shift+I')).toBeInTheDocument();
    expect(screen.getByText(/ESC \[ 105 ; 6 u/)).toBeInTheDocument();
  });

  it('warns that a CSI-u chord may not reach every CLI', () => {
    renderPicker();

    press('KeyI', { ctrlKey: true, shiftKey: true });

    expect(screen.getByText(/CSI-u sequence/)).toBeInTheDocument();
  });

  it('discloses a chord that collapses onto a plain key byte', () => {
    // Ctrl+I is 0x09 — the same byte as Tab — and a user who saves it expecting
    // something else has no way to find that out afterwards.
    renderPicker();

    press('KeyI', { ctrlKey: true });

    expect(screen.getByText(/same byte as Tab/)).toBeInTheDocument();
  });

  it('explains a press the contract cannot carry and composes nothing', () => {
    renderPicker();

    press('KeyP', { metaKey: true, shiftKey: true });

    expect(screen.getByText(/cannot use Cmd/)).toBeInTheDocument();
    expect(screen.getByText('Waiting for a key…')).toBeInTheDocument();
  });

  it('leaves Escape to close the dialog rather than recording it', () => {
    // Esc is outside the chord grammar, so the recorder must not consume the
    // one key that gets the user out.
    renderPicker();

    fireEvent.keyDown(document, { key: 'Escape', code: 'Escape' });

    expect(closed).toBe(1);
    expect(screen.queryByText(/Esc already has its own button/)).not.toBeInTheDocument();
  });

  it('surfaces the conflict with a built-in the chord would shadow', () => {
    renderPicker({ kind: 'claude' });

    press('KeyC', { ctrlKey: true });

    expect(screen.getByText(/same bytes as the built-in Ctrl\+C button/)).toBeInTheDocument();
  });
});

describe('ShortcutPicker record focus gate', () => {
  it('starts with focus inside the dialog, where recording listens', () => {
    // The capture handler only records while focus is in here, so something in
    // the dialog has to hold it — focus starts on the "…" trigger outside.
    const dialog = renderPicker();

    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it('records nothing from a keystroke aimed somewhere else on the page', () => {
    renderPicker();
    (document.activeElement as HTMLElement | null)?.blur();

    press('KeyI', { ctrlKey: true, shiftKey: true });

    expect(screen.getByText('Waiting for a key…')).toBeInTheDocument();
  });

  it('releases Tab back to the page once focus leaves the dialog', () => {
    // Recording deliberately swallows Tab, Enter and Space — they are contract
    // base keys — so without the gate a keyboard-only user could never reach
    // the Choose tab or the Send button from the moment the panel opened.
    renderPicker();
    (document.activeElement as HTMLElement | null)?.blur();

    const tab = new KeyboardEvent('keydown', {
      code: 'Tab',
      key: 'Tab',
      bubbles: true,
      cancelable: true,
    });
    document.body.dispatchEvent(tab);

    expect(tab.defaultPrevented).toBe(false);
  });
});

describe('ShortcutPicker choose mode', () => {
  it('caps a chord at two modifiers by disabling the third', () => {
    renderPicker();
    fireEvent.click(screen.getByRole('tab', { name: 'Choose' }));

    fireEvent.click(screen.getByRole('button', { name: 'Ctrl' }));
    fireEvent.click(screen.getByRole('button', { name: 'Alt' }));

    expect(screen.getByRole('button', { name: 'Shift' })).toBeDisabled();
    // The two that are on stay clickable, so the choice is reversible.
    expect(screen.getByRole('button', { name: 'Ctrl' })).toBeEnabled();
  });

  it('composes a chord from the grid', () => {
    renderPicker();
    fireEvent.click(screen.getByRole('tab', { name: 'Choose' }));

    fireEvent.click(screen.getByRole('button', { name: 'Ctrl' }));
    fireEvent.click(screen.getByRole('button', { name: 'Tab' }));

    expect(screen.getByText(/Sends: ESC \[ 9 ; 5 u/)).toBeInTheDocument();
  });
});

describe('ShortcutPicker sending', () => {
  it('sends the composed chord and records it as recent', () => {
    renderPicker({ kind: 'claude' });
    press('KeyC', { ctrlKey: true });

    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(sent).toEqual([Uint8Array.of(0x03)]);
    expect(useTerminalPrefsStore.getState().recentChords.claude).toEqual(['ctrl+c']);
    expect(closed).toBe(1);
  });

  it('puts the caret back in the grid on the way out', () => {
    // Closing unmounts the button that had focus; without this the caret lands
    // on `document.body` and the next keystroke reaches nothing.
    renderPicker();
    press('KeyC', { ctrlKey: true });

    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(focused).toBe(1);
  });

  it('holds Send shut while the terminal cannot take input', () => {
    renderPicker({ canSend: false });
    press('KeyC', { ctrlKey: true });

    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
  });

  it('holds Send shut until a chord exists', () => {
    renderPicker();

    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
  });
});

describe('ShortcutPicker saving', () => {
  it('saves the chord under the session kind and flips to Saved', async () => {
    renderPicker({ kind: 'codex' });
    press('KeyI', { ctrlKey: true, shiftKey: true });

    fireEvent.click(saveButton());

    await waitFor(() => {
      expect(saveButton()).toHaveAttribute('aria-pressed', 'true');
    });
    expect(repo.created).toEqual([{ kind: 'codex', token: 'ctrl+shift+i' }]);
  });

  it('unsaves a chord already in the bucket instead of saving it twice', async () => {
    repo.shortcuts = [
      {
        shortcutId: 'sc-existing',
        kind: 'claude',
        chordToken: 'ctrl+shift+i',
        name: null,
        position: 0,
        createdAt: '2026-08-01T10:00:00Z',
      },
    ];
    renderPicker({ kind: 'claude' });
    await screen.findByRole('button', { name: 'Rename Ctrl+Shift+I' });
    press('KeyI', { ctrlKey: true, shiftKey: true });

    await waitFor(() => {
      expect(saveButton()).toHaveAttribute('aria-pressed', 'true');
    });
    fireEvent.click(saveButton());

    await waitFor(() => {
      expect(repo.removed).toEqual(['sc-existing']);
    });
    expect(repo.created).toEqual([]);
  });

  // Both directions of the toggle are server mutations, not PTY writes, so a
  // dead session is no reason to stop someone curating their shortcuts.
  it('stays open while the terminal cannot take input', () => {
    renderPicker({ kind: 'claude', canSend: false });
    press('KeyI', { ctrlKey: true, shiftKey: true });

    expect(saveButton()).toBeEnabled();
  });

  it('refuses to save under a kind it has not learned yet', () => {
    // Filing a shortcut under a guessed kind puts it in the wrong bucket
    // permanently — there is no re-bucketing later.
    renderPicker({ kind: null });
    press('KeyI', { ctrlKey: true, shiftKey: true });

    expect(saveButton()).toBeDisabled();
    expect(screen.getByText(/Saving is unavailable until/)).toBeInTheDocument();
  });
});
