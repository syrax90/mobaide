import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { stubLocalStorage } from '../../../test/localStorageStub';
import type { CleanupOutcome, DictationState } from '../../../voice/dictationController';
import type { CarriedText } from '../../../voice/useDictation';
import { VOICE_CODE } from '../../../voice/voiceApi';

// Before importing the store, so zustand's
// `createJSONStorage(() => window.localStorage)` captures the stand-in.
stubLocalStorage();

const { useVoicePrefsStore } = await import('../../../voice/voicePrefsStore');
const { spliceTranscript } = await import('../../../voice/useDictation');
const { DictationDialog } = await import('./DictationDialog');

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
  continued: CarriedText[];
  inserted: string[];
  skip: boolean[];
  cleanUp: boolean[];
  /** The review's own cleanups; each settles when the test calls `settle`. */
  cleanUpText: { text: string; abort: AbortController; settle: (outcome: CleanupOutcome) => void }[];
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
    sessionKind?: string;
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
    sessionKind={options.sessionKind ?? 'claude'}
    cleanUp={options.cleanUp ?? false}
    onCleanUpChange={(on) => {
      calls.cleanUp.push(on);
    }}
    onCleanUpText={(text, abort) =>
      new Promise<CleanupOutcome>((settle) => {
        calls.cleanUpText.push({ text, abort, settle });
      })
    }
  />
);

const newCalls = (): Calls => ({
  recordAgain: 0,
  retry: 0,
  dismiss: 0,
  discard: 0,
  cancel: 0,
  continued: [],
  inserted: [],
  skip: [],
  cleanUp: [],
  cleanUpText: [],
});

const renderDialog = (state: DialogState, canRecord: boolean) => {
  const calls = newCalls();
  render(dialog(calls, state, canRecord));
  return calls;
};

const recordAgain = (): HTMLElement => screen.getByRole('button', { name: 'Record again' });

const CLEANUP_LABEL =
  'Clean up dictated text with the session’s AI agent: remove filler words, fix punctuation and technical terms';

beforeEach(() => {
  useVoicePrefsStore.setState({ cleanupNoticeSeen: true });
});

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
});

describe('DictationDialog cleanup', () => {
  const textField = (): HTMLTextAreaElement => screen.getByRole('textbox');
  const cleaned = (text: string, original: string): DialogState => ({ kind: 'result', text, original });

  it('stays the Transcribing dialog while cleaning up, saying so, with Cancel only', () => {
    const calls = renderDialog({ kind: 'cleaning', text: 'um fix the uh bug' }, true);

    expect(screen.getByRole('heading', { name: 'Transcribing' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Cleaning up the text…');
    expect(screen.queryByText('um fix the uh bug')).toBeNull();
    expect(screen.getAllByRole('button').map((button) => button.textContent)).toEqual(['Cancel']);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(calls.cancel).toBe(1);
  });

  it('swaps between the cleaned-up text and the text as heard, and inserts what is shown', () => {
    const calls = newCalls();
    render(dialog(calls, cleaned('Fix the bug.', 'um fix the uh bug'), true, { canSend: true }));
    expect(textField().value).toBe('Fix the bug.');
    expect(screen.getByText(/Cleaned up by the session’s AI agent\./)).toBeInTheDocument();

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

  it('offers the cleanup checkbox only where cleanup is available', () => {
    const calls = newCalls();
    const { rerender } = render(dialog(calls, { kind: 'result', text: 'hello' }, true, { canSend: true }));
    expect(screen.queryByRole('checkbox', { name: /Clean up dictated text/ })).toBeNull();

    rerender(
      dialog(calls, { kind: 'result', text: 'hello' }, true, { canSend: true, cleanupAvailable: true }),
    );
    const cleanUp = screen.getByRole('checkbox', { name: CLEANUP_LABEL });
    expect(cleanUp).not.toBeChecked();
    fireEvent.click(cleanUp);

    expect(calls.cleanUp).toEqual([true]);
  });

  it('turns cleanup on from the review only after the notice, then cleans up the text in it', () => {
    useVoicePrefsStore.setState({ cleanupNoticeSeen: false });
    const calls = newCalls();
    render(dialog(calls, { kind: 'result', text: 'hello' }, true, { canSend: true, cleanupAvailable: true }));

    fireEvent.click(screen.getByRole('checkbox', { name: CLEANUP_LABEL }));
    expect(screen.getByRole('dialog', { name: 'Clean up dictated text' })).toBeInTheDocument();
    expect(calls.cleanUp).toEqual([]);
    expect(calls.cleanUpText).toEqual([]);

    fireEvent.click(screen.getByRole('button', { name: 'Got it' }));

    expect(calls.cleanUp).toEqual([true]);
    expect(calls.cleanUpText.map((call) => call.text)).toEqual(['hello']);
  });

  it('closes only the notice on Esc there, keeping the review text and cleanup off', () => {
    useVoicePrefsStore.setState({ cleanupNoticeSeen: false });
    const calls = newCalls();
    render(dialog(calls, { kind: 'result', text: 'hello' }, true, { canSend: true, cleanupAvailable: true }));
    fireEvent.click(screen.getByRole('checkbox', { name: CLEANUP_LABEL }));

    fireEvent(
      screen.getByRole('dialog', { name: 'Clean up dictated text' }),
      new Event('cancel', { cancelable: true }),
    );

    expect(screen.queryByRole('dialog', { name: 'Clean up dictated text' })).toBeNull();
    expect(calls.discard).toBe(0);
    expect(textField().value).toBe('hello');
    expect(calls.cleanUp).toEqual([]);
    expect(calls.cleanUpText).toEqual([]);
  });
});

describe('DictationDialog cleanup from the review', () => {
  const textField = (): HTMLTextAreaElement => screen.getByRole('textbox');

  /** A review of `text` with cleanup available and off, then turned on from the review. */
  function turnOnFromReview(state: DialogState, edited?: string): Calls {
    const calls = newCalls();
    const { rerender } = render(dialog(calls, state, true, { canSend: true, cleanupAvailable: true }));
    if (edited !== undefined) fireEvent.change(textField(), { target: { value: edited } });
    fireEvent.click(screen.getByRole('checkbox', { name: CLEANUP_LABEL }));
    rerender(dialog(calls, state, true, { canSend: true, cleanupAvailable: true, cleanUp: true }));
    return calls;
  }

  async function settle(calls: Calls, outcome: CleanupOutcome): Promise<void> {
    await act(async () => {
      calls.cleanUpText[0]?.settle(outcome);
      await Promise.resolve();
    });
  }

  it('cleans up the text as edited, holding the review still until it is done', () => {
    const calls = turnOnFromReview({ kind: 'result', text: 'um fix the uh bug' }, 'um fix the uh login bug');

    expect(calls.cleanUpText.map((call) => call.text)).toEqual(['um fix the uh login bug']);
    expect(screen.getByRole('status')).toHaveTextContent('Cleaning up the text…');
    expect(textField()).toHaveAttribute('readonly');
    expect(screen.getByRole('button', { name: 'Insert' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Dictate more at the cursor' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: /without this review/ })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: CLEANUP_LABEL })).toBeDisabled();
  });

  it('swaps in the cleaned-up text, with the text as edited one tap away', async () => {
    const calls = turnOnFromReview({ kind: 'result', text: 'um fix the uh bug' }, 'um fix the uh login bug');

    await settle(calls, { kind: 'cleaned', text: 'Fix the login bug.' });

    expect(textField().value).toBe('Fix the login bug.');
    expect(textField()).not.toHaveAttribute('readonly');
    expect(screen.getByText(/Cleaned up by the session’s AI agent\./)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show original' }));
    expect(textField().value).toBe('um fix the uh login bug');
    fireEvent.click(screen.getByRole('button', { name: 'Show cleaned up' }));
    expect(textField().value).toBe('Fix the login bug.');
    fireEvent.click(screen.getByRole('button', { name: 'Insert' }));
    expect(calls.inserted).toEqual(['Fix the login bug.']);
  });

  it('leaves the text and says nothing when there was nothing to clean up', async () => {
    const calls = turnOnFromReview({ kind: 'result', text: 'Fix the bug.' });

    await settle(calls, { kind: 'unchanged' });

    expect(textField().value).toBe('Fix the bug.');
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Show original' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Insert' })).toBeEnabled();
  });

  it('leaves a cleaned-up result alone: it is not cleaned up twice', () => {
    const calls = turnOnFromReview({ kind: 'result', text: 'Fix the bug.', original: 'um fix the uh bug' });

    expect(calls.cleanUp).toEqual([true]);
    expect(calls.cleanUpText).toEqual([]);
  });

  it('sends nothing when the review is empty', () => {
    const calls = turnOnFromReview({ kind: 'result', text: 'hello' }, '  ');

    expect(calls.cleanUp).toEqual([true]);
    expect(calls.cleanUpText).toEqual([]);
  });

  it('drops a cleanup still running when the dialog closes', () => {
    const calls = turnOnFromReview({ kind: 'result', text: 'um fix the bug' });

    cleanup();

    expect(calls.cleanUpText[0]?.abort.signal.aborted).toBe(true);
  });
});

describe('spliceTranscript', () => {
  it.each([
    [{ before: '', after: '' }, 'hi', 'hi', 2],
    [{ before: 'one', after: '' }, 'two', 'one two', 7],
    [{ before: 'one ', after: ' three' }, 'two', 'one two three', 7],
    [{ before: 'one', after: '. Next' }, 'two', 'one two. Next', 7],
  ])('splices %o with %s', (around, transcript, text, caret) => {
    expect(spliceTranscript(around, transcript)).toEqual({ text, caret });
  });
});
