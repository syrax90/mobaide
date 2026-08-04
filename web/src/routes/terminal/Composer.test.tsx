import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { TerminalSessionModel } from '../../terminal/sessionModel';
import { Composer } from './Composer';

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

  render(
    <Composer
      model={model}
      value="にほん"
      canSend
      onOpenTemplates={() => undefined}
    />,
  );

  return { field: screen.getByLabelText('Message'), calls };
}

describe('Composer', () => {
  afterEach(cleanup);

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
