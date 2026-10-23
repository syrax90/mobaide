import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { DictationState } from '../../../voice/dictationController';
import { spliceTranscript, type CarriedText } from '../../../voice/useDictation';
import { VOICE_CODE } from '../../../voice/voiceApi';
import { DictationDialog } from './DictationDialog';

type DialogState = Exclude<DictationState, { kind: 'idle' }>;

const failed = (patch: Partial<Extract<DialogState, { kind: 'failed' }>>): DialogState => ({
  kind: 'failed',
  code: VOICE_CODE.transcriptionFailed,
  retryable: false,
  message: null,
  hasAudio: false,
  ...patch,
});

interface Calls {
  recordAgain: number;
  retry: number;
  dismiss: number;
  discard: number;
  continued: CarriedText[];
  inserted: string[];
  skip: boolean[];
}

/** Counts the callbacks the footer buttons reach. */
const dialog = (
  calls: Calls,
  state: DictationState,
  canRecord: boolean,
  options: { carried?: CarriedText | null; canSend?: boolean; skipReview?: boolean } = {},
) => (
  <DictationDialog
    state={state}
    carried={options.carried ?? null}
    agentName="laptop"
    canSend={options.canSend ?? false}
    canRecord={canRecord}
    onStop={() => undefined}
    onCancel={() => undefined}
    onRetry={() => {
      calls.retry += 1;
    }}
    onRecordAgain={() => {
      calls.recordAgain += 1;
    }}
    onDismiss={() => {
      calls.dismiss += 1;
    }}
    onContinue={(text) => {
      calls.continued.push(text);
    }}
    onDiscard={() => {
      calls.discard += 1;
    }}
    onInsert={(text) => {
      calls.inserted.push(text);
      return true;
    }}
    skipReview={options.skipReview ?? false}
    onSkipReviewChange={(skip) => {
      calls.skip.push(skip);
    }}
  />
);

const newCalls = (): Calls => ({
  recordAgain: 0,
  retry: 0,
  dismiss: 0,
  discard: 0,
  continued: [],
  inserted: [],
  skip: [],
});

const renderDialog = (state: DialogState, canRecord: boolean) => {
  const calls = newCalls();
  render(dialog(calls, state, canRecord));
  return calls;
};

const recordAgain = (): HTMLElement => screen.getByRole('button', { name: 'Record again' });

afterEach(() => {
  cleanup();
});

describe('DictationDialog Record again', () => {
  it('is refused after nothing was heard once the session has ended', () => {
    const calls = renderDialog({ kind: 'empty' }, false);

    expect(recordAgain()).toBeDisabled();
    fireEvent.click(recordAgain());
    expect(calls.recordAgain).toBe(0);
    // The way out of the dialog is never gated.
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(calls.dismiss).toBe(1);
  });

  it('starts a new recording after nothing was heard while the session can take one', () => {
    const calls = renderDialog({ kind: 'empty' }, true);

    expect(recordAgain()).toBeEnabled();
    fireEvent.click(recordAgain());
    expect(calls.recordAgain).toBe(1);
  });

  it.each([
    ['a non-retryable failure', failed({ retryable: false, hasAudio: true })],
    ['a retryable failure with no audio left', failed({ retryable: true, hasAudio: false })],
  ])('is refused after %s once the session has ended', (_label, state) => {
    const calls = renderDialog(state, false);

    expect(recordAgain()).toBeDisabled();
    fireEvent.click(recordAgain());
    expect(calls.recordAgain).toBe(0);
  });

  it('is offered after a non-retryable failure while the session can take one', () => {
    const calls = renderDialog(failed({ retryable: false }), true);

    expect(recordAgain()).toBeEnabled();
    fireEvent.click(recordAgain());
    expect(calls.recordAgain).toBe(1);
  });

  it('leaves Retry available after the session ended: it resends audio already captured', () => {
    const calls = renderDialog(failed({ retryable: true, hasAudio: true }), false);

    expect(screen.queryByRole('button', { name: 'Record again' })).toBeNull();
    const retry = screen.getByRole('button', { name: 'Retry' });
    expect(retry).toBeEnabled();
    fireEvent.click(retry);
    expect(calls.retry).toBe(1);
  });
});

describe('DictationDialog review', () => {
  const result = (text: string): DialogState => ({ kind: 'result', text });
  const textField = (): HTMLTextAreaElement => screen.getByRole('textbox');

  const dictateMore = (): HTMLElement =>
    screen.getByRole('button', { name: 'Dictate more at the cursor' });

  it('dictates more from the review, splitting the edited text at the cursor', () => {
    const calls = newCalls();
    render(dialog(calls, result('hello'), true, { canSend: true }));
    fireEvent.change(textField(), { target: { value: 'hello there' } });
    textField().setSelectionRange(5, 5);

    fireEvent.click(dictateMore());

    expect(calls.continued).toEqual([{ before: 'hello', after: ' there' }]);
  });

  it('leaves a selection out of the carried text, so the transcript replaces it', () => {
    const calls = newCalls();
    render(dialog(calls, result('hello big world'), true, { canSend: true }));
    textField().setSelectionRange(6, 9);

    fireEvent.click(dictateMore());

    expect(calls.continued).toEqual([{ before: 'hello ', after: ' world' }]);
  });

  it('offers Dictate again once the text is cleared', () => {
    const calls = newCalls();
    render(dialog(calls, result('hello'), true, { canSend: true }));
    fireEvent.change(textField(), { target: { value: '  ' } });

    fireEvent.click(screen.getByRole('button', { name: 'Dictate again' }));

    expect(calls.continued).toHaveLength(1);
  });

  it('refuses to dictate more once the session has ended', () => {
    render(dialog(newCalls(), result('hello'), false, { canSend: true }));

    expect(dictateMore()).toBeDisabled();
  });

  it('puts the next transcript at the cursor, leaves the cursor after it and inserts it all', () => {
    const calls = newCalls();
    const carried = { before: 'hello', after: ' world' };
    const { rerender } = render(dialog(calls, result('hello world'), true, { canSend: true }));
    rerender(dialog(calls, { kind: 'starting' }, true, { canSend: true, carried }));

    rerender(dialog(calls, result('big'), true, { canSend: true, carried }));

    expect(textField().value).toBe('hello big world');
    expect(textField().selectionStart).toBe('hello big'.length);
    expect(textField().selectionEnd).toBe('hello big'.length);
    fireEvent.click(screen.getByRole('button', { name: 'Insert' }));
    expect(calls.inserted).toEqual(['hello big world']);
  });

  it('returns to the carried text when the extra recording is abandoned', () => {
    const calls = newCalls();
    const { rerender } = render(dialog(calls, result('hello'), true, { canSend: true }));
    const carried = { before: 'hel', after: 'lo' };
    rerender(dialog(calls, { kind: 'starting' }, true, { canSend: true, carried }));

    rerender(dialog(calls, { kind: 'idle' }, true, { canSend: true, carried }));

    expect(textField().value).toBe('hello');
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    expect(calls.discard).toBe(1);
  });

  it('turns the review off from its checkbox and says where to turn it back on', () => {
    const calls = newCalls();
    const { rerender } = render(dialog(calls, result('hello'), true, { canSend: true }));
    const skip = screen.getByRole('checkbox', { name: /without this review/ });
    expect(skip).not.toBeChecked();
    expect(screen.queryByText(/Voice input settings/)).toBeNull();

    fireEvent.click(skip);
    expect(calls.skip).toEqual([true]);
    rerender(dialog(calls, result('hello'), true, { canSend: true, skipReview: true }));

    expect(screen.getByRole('checkbox', { name: /without this review/ })).toBeChecked();
    expect(screen.getByText(/laptop.s Voice input settings/)).toBeInTheDocument();
  });
});

describe('spliceTranscript', () => {
  it.each([
    [{ before: '', after: '' }, 'hi', 'hi', 2],
    [{ before: 'one', after: '' }, 'two', 'one two', 7],
    [{ before: 'one ', after: '' }, 'two', 'one two', 7],
    [{ before: '', after: 'three' }, 'two', 'two three', 3],
    [{ before: 'one', after: 'three' }, 'two', 'one two three', 7],
    [{ before: 'one ', after: ' three' }, 'two', 'one two three', 7],
    [{ before: 'one', after: '. Next' }, 'two', 'one two. Next', 7],
  ])('splices %o with %s', (around, transcript, text, caret) => {
    expect(spliceTranscript(around, transcript)).toEqual({ text, caret });
  });
});
