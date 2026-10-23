import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { DictationState } from '../../../voice/dictationController';
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
  continued: string[];
  inserted: string[];
  skip: boolean[];
}

/** Counts the callbacks the footer buttons reach. */
const dialog = (
  calls: Calls,
  state: DictationState,
  canRecord: boolean,
  options: { carried?: string | null; canSend?: boolean; skipReview?: boolean } = {},
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
    onContinue={(draft) => {
      calls.continued.push(draft);
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

  it('dictates more from the review, keeping the edited text', () => {
    const calls = newCalls();
    render(dialog(calls, result('hello'), true, { canSend: true }));
    fireEvent.change(textField(), { target: { value: 'hello there' } });

    fireEvent.click(screen.getByRole('button', { name: 'Dictate more: adds to the text' }));

    expect(calls.continued).toEqual(['hello there']);
  });

  it('offers Dictate again once the text is cleared', () => {
    const calls = newCalls();
    render(dialog(calls, result('hello'), true, { canSend: true }));
    fireEvent.change(textField(), { target: { value: '  ' } });

    fireEvent.click(screen.getByRole('button', { name: 'Dictate again' }));

    expect(calls.continued).toEqual(['  ']);
  });

  it('refuses to dictate more once the session has ended', () => {
    render(dialog(newCalls(), result('hello'), false, { canSend: true }));

    expect(screen.getByRole('button', { name: 'Dictate more: adds to the text' })).toBeDisabled();
  });

  it('appends the next transcript to the carried text and inserts both', () => {
    const calls = newCalls();
    const { rerender } = render(dialog(calls, result('hello'), true, { canSend: true }));
    rerender(dialog(calls, { kind: 'starting' }, true, { canSend: true, carried: 'hello' }));

    rerender(dialog(calls, result('world'), true, { canSend: true, carried: 'hello' }));

    expect(textField().value).toBe('hello world');
    fireEvent.click(screen.getByRole('button', { name: 'Insert' }));
    expect(calls.inserted).toEqual(['hello world']);
  });

  it('returns to the carried text when the extra recording is abandoned', () => {
    const calls = newCalls();
    const { rerender } = render(dialog(calls, result('hello'), true, { canSend: true }));
    rerender(dialog(calls, { kind: 'starting' }, true, { canSend: true, carried: 'hello' }));

    rerender(dialog(calls, { kind: 'idle' }, true, { canSend: true, carried: 'hello' }));

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
