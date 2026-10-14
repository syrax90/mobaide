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

/** Counts the callbacks the footer buttons reach. */
const renderDialog = (state: DialogState, canRecord: boolean) => {
  const calls = { recordAgain: 0, retry: 0, dismiss: 0 };
  render(
    <DictationDialog
      state={state}
      agentName="laptop"
      canSend={false}
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
      onInsert={() => true}
    />,
  );
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
