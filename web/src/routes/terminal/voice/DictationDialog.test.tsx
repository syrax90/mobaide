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
  cancel: number;
  skipCleanup: number;
  continued: CarriedText[];
  inserted: string[];
  skip: boolean[];
  cleanUp: boolean[];
}

/** Counts the callbacks the footer buttons reach. */
const dialog = (
  calls: Calls,
  state: DictationState,
  canRecord: boolean,
  options: {
    carried?: CarriedText | null;
    canSend?: boolean;
    skipReview?: boolean;
    cleanupAvailable?: boolean;
    cleanUp?: boolean;
  } = {},
) => (
  <DictationDialog
    state={state}
    carried={options.carried ?? null}
    agentName="laptop"
    canSend={options.canSend ?? false}
    canRecord={canRecord}
    onStop={() => undefined}
    onCancel={() => {
      calls.cancel += 1;
    }}
    onRetry={() => {
      calls.retry += 1;
    }}
    onSkipCleanup={() => {
      calls.skipCleanup += 1;
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
    cleanupAvailable={options.cleanupAvailable ?? false}
    cleanUp={options.cleanUp ?? false}
    onCleanUpChange={(on) => {
      calls.cleanUp.push(on);
    }}
  />
);

const newCalls = (): Calls => ({
  recordAgain: 0,
  retry: 0,
  dismiss: 0,
  discard: 0,
  cancel: 0,
  skipCleanup: 0,
  continued: [],
  inserted: [],
  skip: [],
  cleanUp: [],
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

describe('DictationDialog cleanup', () => {
  const textField = (): HTMLTextAreaElement => screen.getByRole('textbox');
  const cleaned = (text: string, original: string): DialogState => ({ kind: 'result', text, original });

  it('shows the transcript as heard while cleaning up, and offers Cancel and Skip cleanup', () => {
    const calls = renderDialog({ kind: 'cleaning', text: 'um fix the uh bug' }, true);

    expect(screen.getByRole('heading', { name: 'Cleaning up' })).toBeInTheDocument();
    expect(screen.getByText('um fix the uh bug')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Skip cleanup' }));
    expect(calls.skipCleanup).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(calls.cancel).toBe(1);
  });

  it('cancels the cleanup on Esc, as in the other live steps', () => {
    const calls = renderDialog({ kind: 'cleaning', text: 'hello' }, true);

    fireEvent(screen.getByRole('dialog'), new Event('cancel', { cancelable: true }));

    expect(calls.cancel).toBe(1);
    expect(calls.discard).toBe(0);
  });

  it('swaps between the cleaned-up text and the text as heard, and inserts what is shown', () => {
    const calls = newCalls();
    render(dialog(calls, cleaned('Fix the bug.', 'um fix the uh bug'), true, { canSend: true }));
    expect(textField().value).toBe('Fix the bug.');
    expect(screen.getByText(/Cleaned up by Claude Haiku/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show original' }));

    expect(textField().value).toBe('um fix the uh bug');
    expect(screen.getByText(/Showing the text as heard/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Insert' }));
    expect(calls.inserted).toEqual(['um fix the uh bug']);

    fireEvent.click(screen.getByRole('button', { name: 'Show cleaned up' }));

    expect(textField().value).toBe('Fix the bug.');
  });

  it('swaps the dictated part only, inside the text carried around it', () => {
    const calls = newCalls();
    const carried = { before: 'Then', after: ' Done.' };
    const { rerender } = render(dialog(calls, { kind: 'result', text: 'first' }, true, { canSend: true }));
    rerender(dialog(calls, { kind: 'starting' }, true, { canSend: true, carried }));
    rerender(
      dialog(calls, cleaned('run the tests.', 'uh run the tests'), true, { canSend: true, carried }),
    );
    expect(textField().value).toBe('Then run the tests. Done.');

    fireEvent.click(screen.getByRole('button', { name: 'Show original' }));

    expect(textField().value).toBe('Then uh run the tests Done.');
  });

  it('keeps the edits made to each version across Show original and Show cleaned up', () => {
    const calls = newCalls();
    render(dialog(calls, cleaned('Fix the bug.', 'um fix the uh bug'), true, { canSend: true }));
    fireEvent.change(textField(), { target: { value: 'Fix the login bug.' } });

    fireEvent.click(screen.getByRole('button', { name: 'Show original' }));
    expect(textField().value).toBe('um fix the uh bug');
    fireEvent.change(textField(), { target: { value: 'fix the uh bug' } });

    fireEvent.click(screen.getByRole('button', { name: 'Show cleaned up' }));
    expect(textField().value).toBe('Fix the login bug.');

    fireEvent.click(screen.getByRole('button', { name: 'Show original' }));
    expect(textField().value).toBe('fix the uh bug');
    fireEvent.click(screen.getByRole('button', { name: 'Insert' }));
    expect(calls.inserted).toEqual(['fix the uh bug']);
  });

  it('leaves the edits of the previous result behind when a new cleaned-up result lands', () => {
    const calls = newCalls();
    const { rerender } = render(dialog(calls, cleaned('One.', 'um one'), true, { canSend: true }));
    fireEvent.change(textField(), { target: { value: 'One, edited.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Show original' }));

    rerender(dialog(calls, cleaned('Two.', 'uh two'), true, { canSend: true }));
    fireEvent.click(screen.getByRole('button', { name: 'Show original' }));

    expect(textField().value).toBe('uh two');
  });

  it('starts every new cleaned-up result on the cleaned-up text', () => {
    const calls = newCalls();
    const { rerender } = render(dialog(calls, cleaned('One.', 'um one'), true, { canSend: true }));
    fireEvent.click(screen.getByRole('button', { name: 'Show original' }));

    rerender(dialog(calls, cleaned('Two.', 'uh two'), true, { canSend: true }));

    expect(textField().value).toBe('Two.');
    expect(screen.getByRole('button', { name: 'Show original' })).toBeInTheDocument();
  });

  it('says why a failed cleanup left the text as heard, with the reported error under Details', () => {
    renderDialog(
      {
        kind: 'result',
        text: 'um fix the bug',
        cleanupError: { code: VOICE_CODE.cleanupOutdated, message: 'error: unknown option' },
      },
      true,
    );

    expect(textField().value).toBe('um fix the bug');
    expect(screen.getByRole('status')).toHaveTextContent(
      'Couldn’t clean up the text, so it’s shown as heard. Claude Code on laptop is too old. Run "claude update" there.',
    );
    expect(screen.getByText('Details')).toBeInTheDocument();
    expect(screen.getByText('error: unknown option')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Show original' })).toBeNull();
  });

  it('leaves Details out of a failure the agent never explained', () => {
    renderDialog(
      { kind: 'result', text: 'hello', cleanupError: { code: VOICE_CODE.cleanupTimeout, message: null } },
      true,
    );

    expect(screen.getByRole('status')).toHaveTextContent('Claude Code took too long.');
    expect(screen.queryByText('Details')).toBeNull();
  });

  it('offers the cleanup checkbox only where cleanup is available', () => {
    const calls = newCalls();
    const { rerender } = render(dialog(calls, { kind: 'result', text: 'hello' }, true, { canSend: true }));
    expect(screen.queryByRole('checkbox', { name: /Clean up dictated text/ })).toBeNull();

    rerender(
      dialog(calls, { kind: 'result', text: 'hello' }, true, { canSend: true, cleanupAvailable: true }),
    );
    const cleanUp = screen.getByRole('checkbox', {
      name: 'Clean up dictated text with Claude Haiku from now on',
    });
    expect(cleanUp).not.toBeChecked();
    fireEvent.click(cleanUp);

    expect(calls.cleanUp).toEqual([true]);
  });

  it('says where the text would go while cleanup is available but off, and stops once it is on', () => {
    const calls = newCalls();
    const hello: DialogState = { kind: 'result', text: 'hello' };
    const { rerender } = render(dialog(calls, hello, true, { canSend: true }));
    expect(screen.queryByText(/sent to Anthropic/)).toBeNull();

    rerender(dialog(calls, hello, true, { canSend: true, cleanupAvailable: true }));
    expect(
      screen.getByText(
        'Each transcript is then sent to Anthropic through Claude Code on laptop and counts toward that Claude account’s usage.',
      ),
    ).toBeInTheDocument();

    rerender(dialog(calls, hello, true, { canSend: true, cleanupAvailable: true, cleanUp: true }));
    expect(screen.queryByText(/sent to Anthropic/)).toBeNull();
  });

  it('describes the cleanup checkbox with where the text would go while it is off, and not once it is on', () => {
    const calls = newCalls();
    const hello: DialogState = { kind: 'result', text: 'hello' };
    const cleanUpBox = (): HTMLElement =>
      screen.getByRole('checkbox', { name: 'Clean up dictated text with Claude Haiku from now on' });
    const { rerender } = render(dialog(calls, hello, true, { canSend: true, cleanupAvailable: true }));

    expect(cleanUpBox()).toHaveAccessibleDescription(
      'Each transcript is then sent to Anthropic through Claude Code on laptop and counts toward that Claude account’s usage.',
    );

    rerender(dialog(calls, hello, true, { canSend: true, cleanupAvailable: true, cleanUp: true }));
    expect(cleanUpBox()).not.toHaveAccessibleDescription();
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
