import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before importing the store, so zustand's
// `createJSONStorage(() => window.localStorage)` captures the stand-in.
stubLocalStorage();

const { useVoicePrefsStore } = await import('./voicePrefsStore');
const { CleanupToggle } = await import('./CleanupToggle');

const LABEL =
  'Clean up dictated text with the session’s AI agent: remove filler words, fix punctuation and technical terms';

let changes: boolean[];

function mount(checked = false): void {
  render(
    <CleanupToggle
      agentName="laptop"
      checked={checked}
      className="voice-toggle"
      onChange={(on) => {
        changes.push(on);
      }}
    />,
  );
}

const toggle = (): HTMLElement => screen.getByRole('checkbox', { name: LABEL });
const notice = (): HTMLElement | null => screen.queryByRole('dialog', { name: 'Clean up dictated text' });

beforeEach(() => {
  changes = [];
  useVoicePrefsStore.setState({ cleanupNoticeSeen: false });
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
});

describe('CleanupToggle', () => {
  it('says where the text goes before the first opt-in, and turns on only after Got it', () => {
    mount();

    fireEvent.click(toggle());

    expect(changes).toEqual([]);
    expect(notice()).toHaveTextContent('The session’s AI agent on laptop cleans up each transcript');
    expect(notice()).toHaveTextContent('cheapest model');
    expect(notice()).toHaveTextContent('under your own account');

    fireEvent.click(screen.getByRole('button', { name: 'Got it' }));

    expect(changes).toEqual([true]);
    expect(notice()).toBeNull();
    expect(useVoicePrefsStore.getState().cleanupNoticeSeen).toBe(true);
  });

  it.each([
    ['Cancel', () => fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))],
    ['Esc', () => fireEvent(notice() as HTMLElement, new Event('cancel', { cancelable: true }))],
  ])('stays off when the notice is left with %s, and asks again next time', (_label, leave) => {
    mount();
    fireEvent.click(toggle());

    leave();

    expect(changes).toEqual([]);
    expect(notice()).toBeNull();
    expect(useVoicePrefsStore.getState().cleanupNoticeSeen).toBe(false);
    fireEvent.click(toggle());
    expect(notice()).not.toBeNull();
  });

  it('turns on at once once the notice was acknowledged', () => {
    useVoicePrefsStore.setState({ cleanupNoticeSeen: true });
    mount();

    fireEvent.click(toggle());

    expect(changes).toEqual([true]);
    expect(notice()).toBeNull();
  });

  it('turns off without asking', () => {
    mount(true);

    fireEvent.click(toggle());

    expect(changes).toEqual([false]);
    expect(notice()).toBeNull();
  });
});
