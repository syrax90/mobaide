import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { TerminalSessionModel } from '../../terminal/sessionModel';
import { Composer } from './Composer';

/** Non-empty on purpose: the selection cases below assert against its length. */
const body = 'にほん';

/**
 * The composer only ever calls back into the model, so a recorder is enough —
 * a real model would open a socket and an emulator to answer one method.
 */
function renderComposer() {
  const calls = { submits: 0 };
  const model = {
    submitComposer: () => {
      calls.submits += 1;
    },
    setComposer: () => undefined,
  } as unknown as TerminalSessionModel;

  const composer = (insertToken: number) => (
    <Composer
      model={model}
      value={body}
      canSend
      onOpenTemplates={() => undefined}
      insertToken={insertToken}
    />
  );

  const { rerender } = render(composer(0));

  return {
    field: screen.getByLabelText<HTMLTextAreaElement>('Message'),
    calls,
    /** What `TerminalScreen` does when a template pick lands in the composer. */
    insert: (token: number): void => {
      rerender(composer(token));
    },
  };
}

describe('Composer', () => {
  afterEach(cleanup);

  it('takes focus on mount', () => {
    // The field only ever mounts because the user asked for it, and one of the
    // two ways in — "Send to input" — clears the selection, which unmounts the
    // toolbar button that was just clicked. Focus would fall to `document.body`
    // and the first thing typed would go nowhere.
    const { field } = renderComposer();

    expect(field).toHaveFocus();
  });

  it('takes focus back and selects the whole body when a token bump inserts text', () => {
    // The picker's only way in is the composer's own Templates button, so the
    // composer is necessarily already mounted when a pick lands: `autoFocus`
    // cannot fire a second time, and unmounting the picker drops focus to
    // `document.body`. Selecting the insert is the rest of the contract — the
    // body is a starting point the user is expected to edit or replace.
    const { field, insert } = renderComposer();
    field.blur();

    insert(1);

    expect(field).toHaveFocus();
    expect(field.selectionStart).toBe(0);
    expect(field.selectionEnd).toBe(body.length);
  });

  it('leaves the value the composer mounts with unselected', () => {
    // The initial token is skipped because `autoFocus` already covers mount —
    // and mounting with text is a real path: "Send to input" opens the composer
    // with the terminal selection already in it. Selecting that on arrival
    // would let the next keystroke wipe what the user just sent over.
    const { field } = renderComposer();

    expect(field).toHaveFocus();
    expect(field.selectionStart).toBe(field.selectionEnd);
  });

  it('sends on a plain Enter and keeps the newline out of the field', () => {
    const { field, calls } = renderComposer();

    // `dispatchEvent` reports false once the handler has cancelled the key,
    // which is what stops the textarea from also inserting a newline.
    const delivered = fireEvent.keyDown(field, { key: 'Enter' });

    expect(calls.submits).toBe(1);
    expect(delivered).toBe(false);
  });

  it('leaves the Enter that commits an IME composition to the IME', () => {
    // Every composition-based IME — Japanese, Chinese, Korean — confirms its
    // candidate with Enter. Taking that key submits the raw, uncommitted
    // composition buffer as an `m` frame and strands the candidate window.
    const { field, calls } = renderComposer();

    const delivered = fireEvent.keyDown(field, { key: 'Enter', isComposing: true });

    expect(calls.submits).toBe(0);
    expect(delivered).toBe(true);
  });

  it('leaves the keyCode 229 form of that Enter alone as well', () => {
    // Safari and older engines announce the composition Enter only as
    // keyCode 229; `isComposing` is false there, so the flag alone would still
    // steal the key on those browsers.
    const { field, calls } = renderComposer();

    const delivered = fireEvent.keyDown(field, { key: 'Enter', keyCode: 229 });

    expect(calls.submits).toBe(0);
    expect(delivered).toBe(true);
  });
});
