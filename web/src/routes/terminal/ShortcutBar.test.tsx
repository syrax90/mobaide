import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SavedShortcut } from '../../api/shortcutRepository';
import type { TerminalSessionModel } from '../../terminal/sessionModel';
import { stubLocalStorage } from '../../test/localStorageStub';

// Before importing anything that pulls in the persisted prefs store.
stubLocalStorage();

// Only the wire call is replaced. `shortcutsForKind` and `controlKeysFor` stay
// real: which buttons the bar renders, and in what order, is the behaviour
// under test.
const repo = vi.hoisted(() => ({ shortcuts: [] as SavedShortcut[] }));

vi.mock('../../api/shortcutRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/shortcutRepository')>();
  return { ...actual, listShortcuts: () => Promise.resolve(repo.shortcuts) };
});

const { ShortcutBar } = await import('./ShortcutBar');
const { useTerminalPrefsStore } = await import('../../stores/terminalPrefsStore');

const sent: Uint8Array[] = [];

/** The bar reaches outside the DOM for exactly one thing. */
const model = {
  sendChordBytes: (payload: Uint8Array) => sent.push(payload),
} as unknown as TerminalSessionModel;

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

let openedComposer = 0;

async function renderBar(
  over: { kind?: string | null; canSend?: boolean } = {},
): Promise<void> {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ShortcutBar
        model={model}
        kind={over.kind === undefined ? 'claude' : over.kind}
        canSend={over.canSend ?? true}
        onOpenComposer={() => {
          openedComposer += 1;
        }}
      />
    </QueryClientProvider>,
  );
  // The saved chips arrive with the query; the built-ins are already there.
  await screen.findByRole('button', { name: 'Custom shortcut' });
}

const keys = (): HTMLElement[] =>
  [...screen.getByRole('toolbar', { name: 'Terminal keys' }).querySelectorAll('button')];

beforeEach(() => {
  repo.shortcuts = [];
  sent.length = 0;
  openedComposer = 0;
  useTerminalPrefsStore.setState({ shortcutBarExpanded: true, recentChords: {} });
});

afterEach(cleanup);

describe('ShortcutBar built-in keys', () => {
  it('renders the set for the session kind', async () => {
    await renderBar({ kind: 'claude' });

    expect(screen.getByRole('button', { name: 'Mode' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
  });

  it('renders the neutral set for a shell, without the Claude-TUI keys', async () => {
    // CLA-158: a bash prompt has no plan mode to cycle and no message to rewind.
    await renderBar({ kind: 'shell' });

    expect(screen.queryByRole('button', { name: 'Mode' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Rewind' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ctrl+C' })).toBeInTheDocument();
  });

  it('sends the key bytes when a built-in is pressed', async () => {
    await renderBar();

    fireEvent.click(screen.getByRole('button', { name: 'Mode' }));

    expect(sent).toEqual([Uint8Array.of(0x1b, 0x5b, 0x5a)]);
  });

  it('disables the keys while the send gate is shut', async () => {
    await renderBar({ canSend: false });

    expect(screen.getByRole('button', { name: 'Ctrl+C' })).toBeDisabled();
  });
});

describe('ShortcutBar saved shortcuts', () => {
  it('renders the kind bucket after the list lands, labelled by the chord', async () => {
    repo.shortcuts = [saved(), saved({ shortcutId: 'sc-2', kind: 'codex', chordToken: 'ctrl+a' })];

    await renderBar({ kind: 'claude' });

    expect(await screen.findByRole('button', { name: 'Ctrl+Shift+I' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Ctrl+A' })).not.toBeInTheDocument();
  });

  it('prefers the user name over the chord', async () => {
    repo.shortcuts = [saved({ name: 'Inspect' })];

    await renderBar();

    expect(await screen.findByRole('button', { name: 'Inspect' })).toBeInTheDocument();
  });

  it('sends the saved chord bytes when pressed', async () => {
    repo.shortcuts = [saved({ chordToken: 'ctrl+a' })];

    await renderBar();
    fireEvent.click(await screen.findByRole('button', { name: 'Ctrl+A' }));

    expect(sent).toEqual([Uint8Array.of(0x01)]);
  });

  it('discloses the chord first in the tooltip of a chip the user named', async () => {
    // A label is entirely user-supplied, so a chip named "Reject" could carry
    // `y` and answer an approval prompt. The name alone is never enough — and
    // the chord leads, so a name like "Approve — Ctrl+Y" cannot pose as the
    // authoritative half by supplying its own separator.
    repo.shortcuts = [saved({ name: 'Reject', chordToken: 'ctrl+a' })];

    await renderBar();

    expect(await screen.findByRole('button', { name: 'Reject' })).toHaveAttribute(
      'title',
      'Ctrl+A — Reject',
    );
  });

  it('records a sent chord as recent, so the picker can offer it back', async () => {
    repo.shortcuts = [saved({ chordToken: 'ctrl+a' })];

    await renderBar({ kind: 'claude' });
    fireEvent.click(await screen.findByRole('button', { name: 'Ctrl+A' }));

    expect(useTerminalPrefsStore.getState().recentChords.claude).toEqual(['ctrl+a']);
  });

  it('leaves the built-in keys out of the recents', async () => {
    // Recents are for chords the user went and found; the fixed keys are
    // already on the bar. Both mobile clients exclude them for the same reason.
    await renderBar({ kind: 'claude' });

    fireEvent.click(screen.getByRole('button', { name: 'Mode' }));

    expect(useTerminalPrefsStore.getState().recentChords).toEqual({});
  });

  it('warns on the CSI-u chords not every CLI understands', async () => {
    repo.shortcuts = [saved({ chordToken: 'ctrl+shift+i' }), saved({ shortcutId: 'sc-2', chordToken: 'ctrl+a' })];

    await renderBar();

    expect(await screen.findByRole('button', { name: 'Ctrl+Shift+I' })).toHaveAttribute(
      'title',
      expect.stringContaining('may not work in every CLI'),
    );
    expect(screen.getByRole('button', { name: 'Ctrl+A' })).toHaveAttribute('title', 'Ctrl+A');
  });

  it('renders saved chips after the built-ins and the composer trigger', async () => {
    // The bar is the discovery surface: the fixed keys stay where muscle memory
    // left them, and anything the user added lands at the end.
    repo.shortcuts = [saved()];

    await renderBar();
    await screen.findByRole('button', { name: 'Ctrl+Shift+I' });

    const names = keys().map((button) => button.getAttribute('aria-label') ?? button.textContent);
    expect(names.at(-1)).toBe('Ctrl+Shift+I');
    expect(names.at(-2)).toBe('Custom shortcut');
  });
});

describe('ShortcutBar collapsing', () => {
  it('starts open and hides every key when toggled shut', async () => {
    await renderBar();
    const toggle = screen.getByRole('button', { name: 'Hide keys' });
    expect(toggle).toHaveAttribute('aria-expanded', 'true');

    fireEvent.click(toggle);

    expect(screen.queryByRole('toolbar', { name: 'Terminal keys' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show keys' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });

  it('follows the persisted preference on mount', () => {
    useTerminalPrefsStore.setState({ shortcutBarExpanded: false });

    render(
      <QueryClientProvider client={new QueryClient()}>
        <ShortcutBar model={model} kind="claude" canSend onOpenComposer={() => undefined} />
      </QueryClientProvider>,
    );

    expect(screen.getByRole('button', { name: 'Show keys' })).toBeInTheDocument();
    expect(screen.queryByRole('toolbar', { name: 'Terminal keys' })).not.toBeInTheDocument();
  });
});

describe('ShortcutBar composer trigger', () => {
  it('opens the composer', async () => {
    await renderBar();

    fireEvent.click(screen.getByRole('button', { name: 'Custom shortcut' }));

    expect(openedComposer).toBe(1);
  });

  it('stays usable while the send gate is shut', async () => {
    // Recording and saving a chord are server mutations, not PTY sends, so a
    // dead session is no reason to lock the user out of composing one.
    await renderBar({ canSend: false });

    expect(screen.getByRole('button', { name: 'Custom shortcut' })).toBeEnabled();
  });
});
