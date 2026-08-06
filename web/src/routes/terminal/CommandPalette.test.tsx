import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SavedShortcut } from '../../api/shortcutRepository';
import type { PromptTemplate } from '../../api/templateRepository';
import type { TerminalSessionModel } from '../../terminal/sessionModel';
import { stubLocalStorage } from '../../test/localStorageStub';

// Before importing anything that pulls in the persisted prefs store.
stubLocalStorage();

// Only the two wire calls are replaced. `shortcutsForKind`, `controlKeysFor`
// and the encoder stay real: which rows the palette offers, and what each one
// sends, is the behaviour under test.
const repo = vi.hoisted(() => ({
  shortcuts: [] as SavedShortcut[],
  templates: [] as PromptTemplate[],
}));

vi.mock('../../api/shortcutRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/shortcutRepository')>();
  return { ...actual, listShortcuts: () => Promise.resolve(repo.shortcuts) };
});

vi.mock('../../api/templateRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/templateRepository')>();
  return { ...actual, listTemplates: () => Promise.resolve(repo.templates) };
});

const { CommandPalette } = await import('./CommandPalette');
const { useTerminalPrefsStore } = await import('../../stores/terminalPrefsStore');

const sent: Uint8Array[] = [];
let focused = 0;
let inserted: string[] = [];
let closed = 0;

/** The two things the palette reaches outside the DOM for. */
const model = {
  sendChordBytes: (payload: Uint8Array) => sent.push(payload),
  focus: () => {
    focused += 1;
  },
} as unknown as TerminalSessionModel;

function saved(over: Partial<SavedShortcut> = {}): SavedShortcut {
  return {
    shortcutId: 'sc-1',
    kind: 'claude',
    chordToken: 'ctrl+a',
    name: null,
    position: 0,
    createdAt: '2026-08-01T10:00:00Z',
    ...over,
  };
}

function template(over: Partial<PromptTemplate> = {}): PromptTemplate {
  return {
    templateId: 'tpl-1',
    name: 'Ship it',
    body: 'ship the thing',
    isFavorite: false,
    lastUsedAt: null,
    ...over,
  };
}

function renderPalette(over: { kind?: string | null; canSend?: boolean } = {}): void {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <CommandPalette
        model={model}
        kind={over.kind === undefined ? 'claude' : over.kind}
        canSend={over.canSend ?? true}
        onInsertTemplate={(body) => {
          inserted.push(body);
        }}
        onClose={() => {
          closed += 1;
        }}
      />
    </QueryClientProvider>,
  );
}

/** The built-in rows are there on mount; saved and template rows land with their queries. */
async function row(label: string): Promise<HTMLButtonElement> {
  const found = await screen.findByText(label, { selector: '.command-palette-label' });
  const button = found.closest('button');
  if (button === null) throw new Error(`palette row "${label}" is not a button`);
  return button;
}

beforeEach(() => {
  repo.shortcuts = [];
  repo.templates = [];
  sent.length = 0;
  inserted = [];
  focused = 0;
  closed = 0;
  useTerminalPrefsStore.setState({ recentChords: {} });
});

afterEach(cleanup);

describe('CommandPalette saved shortcuts', () => {
  it('discloses the chord behind a row the user named', async () => {
    // A label is entirely user-supplied, so a shortcut named "Reject" could
    // carry `y` and answer an approval prompt. The name is never the whole story.
    repo.shortcuts = [saved({ name: 'Reject', chordToken: 'ctrl+a' })];

    renderPalette();

    expect(within(await row('Reject')).getByText('Ctrl+A')).toBeInTheDocument();
  });

  it('marks a chord that only reaches CLIs which understand CSI-u', async () => {
    repo.shortcuts = [saved({ chordToken: 'ctrl+shift+i' })];

    renderPalette();

    expect(within(await row('Ctrl+Shift+I')).getByText(/Ctrl\+Shift\+I · CSI-u/)).toBeInTheDocument();
  });

  it('sends the chord, records it as recent and puts the caret back in the grid', async () => {
    repo.shortcuts = [saved({ chordToken: 'ctrl+a' })];
    renderPalette({ kind: 'claude' });

    fireEvent.click(await row('Ctrl+A'));

    expect(sent).toEqual([Uint8Array.of(0x01)]);
    // Both mobile clients record an MRU entry on every chord send.
    expect(useTerminalPrefsStore.getState().recentChords.claude).toEqual(['ctrl+a']);
    expect(closed).toBe(1);
    // Closing unmounts the row that had focus, which would otherwise fall to
    // `document.body` and leave the next keystroke reaching nothing.
    expect(focused).toBe(1);
  });
});

describe('CommandPalette built-in keys', () => {
  it('sends the key and restores focus without recording a recent', async () => {
    // Recents are for chords the user chose; the fixed keys are already on the
    // bar, and mobile excludes them for the same reason.
    renderPalette({ kind: 'claude' });

    fireEvent.click(await row('Ctrl+C'));

    expect(sent).toEqual([Uint8Array.of(0x03)]);
    expect(useTerminalPrefsStore.getState().recentChords).toEqual({});
    expect(focused).toBe(1);
  });
});

describe('CommandPalette templates', () => {
  it('hands focus to the composer instead of the grid', async () => {
    // A template lands in the composer as text to edit, so pulling focus back
    // into the terminal would drop the caret out of what was just inserted.
    repo.templates = [template()];
    renderPalette();

    fireEvent.click(await row('Ship it'));

    expect(inserted).toEqual(['ship the thing']);
    expect(closed).toBe(1);
    expect(focused).toBe(0);
  });
});

describe('CommandPalette dismissal', () => {
  it('puts the caret back in the grid on Escape', async () => {
    renderPalette();
    await row('Ctrl+C');

    fireEvent.keyDown(document, { key: 'Escape', code: 'Escape' });

    expect(closed).toBe(1);
    expect(focused).toBe(1);
  });
});
