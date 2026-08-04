import { describe, expect, it } from 'vitest';
import {
  detectIsMac,
  resolveKeyAction,
  type KeyChord,
  type KeyPolicyContext,
  type TerminalKeyAction,
} from './terminalKeys';

const chord = (key: string, held: Partial<KeyChord> = {}): KeyChord => ({
  type: 'keydown',
  key,
  ctrlKey: false,
  shiftKey: false,
  altKey: false,
  metaKey: false,
  ...held,
});

const context = (over: Partial<KeyPolicyContext> = {}): KeyPolicyContext => ({
  hasSelection: false,
  passCtrlVToTerminal: false,
  isMac: false,
  ...over,
});

interface Case {
  name: string;
  chord: KeyChord;
  context: KeyPolicyContext;
  expected: TerminalKeyAction;
}

const cases: Case[] = [
  {
    name: 'Ctrl+C with a selection copies',
    chord: chord('c', { ctrlKey: true }),
    context: context({ hasSelection: true }),
    expected: 'copy',
  },
  {
    name: 'Ctrl+C without a selection still reaches the program as SIGINT',
    chord: chord('c', { ctrlKey: true }),
    context: context(),
    expected: 'terminal',
  },
  {
    name: 'Ctrl+Shift+C copies even with nothing selected',
    chord: chord('C', { ctrlKey: true, shiftKey: true }),
    context: context(),
    expected: 'copy',
  },
  {
    name: 'Ctrl+Shift+V hands off to the browser paste',
    chord: chord('V', { ctrlKey: true, shiftKey: true }),
    context: context(),
    expected: 'browserPaste',
  },
  {
    name: 'Ctrl+V pastes by default',
    chord: chord('v', { ctrlKey: true }),
    context: context(),
    expected: 'browserPaste',
  },
  {
    name: 'Ctrl+V reaches the program once the toggle is on',
    chord: chord('v', { ctrlKey: true }),
    context: context({ passCtrlVToTerminal: true }),
    expected: 'terminal',
  },
  {
    name: 'Cmd+C with a selection copies on a Mac',
    chord: chord('c', { metaKey: true }),
    context: context({ isMac: true, hasSelection: true }),
    expected: 'copy',
  },
  {
    name: 'Cmd+C without a selection is left to the terminal',
    chord: chord('c', { metaKey: true }),
    context: context({ isMac: true }),
    expected: 'terminal',
  },
  {
    name: 'Cmd+V pastes on a Mac',
    chord: chord('v', { metaKey: true }),
    context: context({ isMac: true }),
    expected: 'browserPaste',
  },
  {
    name: 'the Windows key is not a clipboard modifier off a Mac',
    chord: chord('v', { metaKey: true }),
    context: context({ hasSelection: true }),
    expected: 'terminal',
  },
  {
    name: 'Ctrl+Alt+C is left alone for the composing layouts that use it',
    chord: chord('c', { ctrlKey: true, altKey: true }),
    context: context({ hasSelection: true }),
    expected: 'terminal',
  },
  {
    name: 'an unmodified letter is ordinary terminal input',
    chord: chord('c'),
    context: context({ hasSelection: true }),
    expected: 'terminal',
  },
];

describe('resolveKeyAction', () => {
  it.each(cases)('$name', ({ chord: keyChord, context: policy, expected }) => {
    expect(resolveKeyAction(keyChord, policy)).toBe(expected);
  });

  it.each(['keyup', 'keypress'])('ignores a %s of the same chord', (type) => {
    // xterm hands the handler every event type; acting on more than the
    // keydown would copy twice per press.
    expect(
      resolveKeyAction(chord('c', { type, ctrlKey: true, shiftKey: true }), context()),
    ).toBe('terminal');
  });
});

describe('detectIsMac', () => {
  it('covers the Apple platform strings and nothing else', () => {
    expect(detectIsMac('MacIntel')).toBe(true);
    expect(detectIsMac('iPhone')).toBe(true);
    expect(detectIsMac('iPad')).toBe(true);
    expect(detectIsMac('Win32')).toBe(false);
    expect(detectIsMac('Linux x86_64')).toBe(false);
  });
});
