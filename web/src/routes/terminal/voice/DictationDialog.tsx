import { useEffect, useId, useState, type ReactNode } from 'react';
import { Modal } from '../../../components/Modal';
import type { DictationState, InterruptReason } from '../../../voice/dictationController';
import { VOICE_CODE } from '../../../voice/voiceApi';
import { dictationErrorCopy } from '../../../voice/voiceErrorCopy';
import { formatDuration } from '../../../voice/voiceStatusView';

/** Claude Code shows a paste over 800 characters as `[Pasted text #n]`. */
const PASTE_COLLAPSE_CHARS = 800;

interface DictationDialogProps {
  state: Exclude<DictationState, { kind: 'idle' }>;
  agentName: string;
  canSend: boolean;
  onStop: () => void;
  onCancel: () => void;
  onRetry: () => void;
  onRecordAgain: () => void;
  onDismiss: () => void;
  /** Returns false when the terminal refused the text. */
  onInsert: (text: string) => boolean;
}

function interruptionNote(reason: InterruptReason | null): string | null {
  switch (reason) {
    case 'background':
      return 'Recording stopped because the tab was hidden.';
    case 'device':
      return 'Recording stopped because the microphone went away.';
    case 'limit':
      return 'Recording stopped at the time limit.';
    default:
      return null;
  }
}

const PERMISSION_HELP =
  'Allow the microphone for this site, then try again. Chrome and Edge: the site ' +
  'settings icon left of the address bar. Firefox: the microphone icon in the ' +
  'address bar. Safari: Safari ▸ Settings for This Website.';

/**
 * One dictation, start to finish, as a top-layer dialog: recording (timer,
 * level, countdown near the limit), transcribing, then an editable review
 * field. **Insert** pastes the text into the session the way Templates does
 * — never with Enter — so the user always sends it themselves.
 *
 * Esc cancels a recording or a transcription, and discards a result.
 */
export function DictationDialog({
  state,
  agentName,
  canSend,
  onStop,
  onCancel,
  onRetry,
  onRecordAgain,
  onDismiss,
  onInsert,
}: DictationDialogProps) {
  const titleId = useId();
  const [draft, setDraft] = useState(state.kind === 'result' ? state.text : '');
  const [insertRefused, setInsertRefused] = useState(false);
  const resultText = state.kind === 'result' ? state.text : null;

  useEffect(() => {
    if (resultText !== null) {
      setDraft(resultText);
      setInsertRefused(false);
    }
  }, [resultText]);

  const live = state.kind === 'starting' || state.kind === 'recording' || state.kind === 'transcribing';

  let title: string;
  let body: ReactNode;
  let actions: ReactNode;
  switch (state.kind) {
    case 'starting':
      title = 'Dictation';
      body = <p role="status">Opening the microphone…</p>;
      actions = (
        <button type="button" className="button" onClick={onCancel}>
          Cancel
        </button>
      );
      break;
    case 'recording': {
      const levelPercent = Math.round(state.level * 100);
      title = 'Recording';
      body = (
        <>
          <p className="voice-timer" aria-hidden="true">
            <span className="voice-recording-dot" />
            {formatDuration(state.elapsedMs)}
          </p>
          <div
            className="voice-meter"
            role="meter"
            aria-label="Microphone level"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={levelPercent}
          >
            <span style={{ width: `${String(levelPercent)}%` }} />
          </div>
          {state.remainingSeconds !== null && (
            <p className="voice-countdown">Stops in {state.remainingSeconds} s</p>
          )}
          {state.lagging && (
            <p className="page-note">The connection is slow. Recording continues.</p>
          )}
        </>
      );
      actions = (
        <>
          <button type="button" className="button" onClick={onCancel}>
            Cancel
          </button>
          {/* Focused when recording starts: one key press stops it. */}
          <button type="button" className="button button-primary" autoFocus onClick={onStop}>
            Stop
          </button>
        </>
      );
      break;
    }
    case 'transcribing': {
      const note = interruptionNote(state.interruptedBy);
      title = 'Transcribing';
      body = (
        <>
          <p role="status">Transcribing on {agentName}…</p>
          {note !== null && <p className="page-note">{note}</p>}
          {state.lagging && <p className="page-note">Waiting for the connection…</p>}
        </>
      );
      actions = (
        <button type="button" className="button" onClick={onCancel}>
          Cancel
        </button>
      );
      break;
    }
    case 'result':
      title = 'Review';
      body = (
        <>
          <label className="voice-review-label" htmlFor={`${titleId}-text`}>
            Edit the text, then insert it at the session&rsquo;s prompt. Nothing is sent until you
            press Enter there.
          </label>
          <textarea
            id={`${titleId}-text`}
            className="field-input field-textarea"
            rows={5}
            value={draft}
            autoFocus
            onChange={(event) => {
              setDraft(event.target.value);
            }}
          />
          {(draft.length > PASTE_COLLAPSE_CHARS || draft.includes('\n')) && (
            <p className="page-note">
              Claude Code may show long text as [Pasted text]; it still receives all of it.
            </p>
          )}
          {(!canSend || insertRefused) && (
            <p className="voice-error" role="alert">
              The session isn&rsquo;t accepting input right now.
            </p>
          )}
        </>
      );
      actions = (
        <>
          <button type="button" className="button" onClick={onDismiss}>
            Discard
          </button>
          <button
            type="button"
            className="button button-primary"
            disabled={!canSend || draft.trim() === ''}
            onClick={() => {
              if (!onInsert(draft)) setInsertRefused(true);
            }}
          >
            Insert
          </button>
        </>
      );
      break;
    case 'empty':
      title = 'Nothing heard';
      body = <p role="status">No speech was recognised. Check the microphone and try again.</p>;
      actions = (
        <>
          <button type="button" className="button" onClick={onDismiss}>
            Close
          </button>
          <button type="button" className="button button-primary" onClick={onRecordAgain}>
            Record again
          </button>
        </>
      );
      break;
    case 'failed':
      title = 'Dictation failed';
      body = (
        <>
          <p role="alert">{dictationErrorCopy(state.code, agentName)}</p>
          {state.code === VOICE_CODE.micPermissionDenied && <p className="page-note">{PERMISSION_HELP}</p>}
          {state.message !== null && state.message !== '' && (
            <details className="voice-details">
              <summary>Details</summary>
              <pre>{state.message}</pre>
            </details>
          )}
        </>
      );
      actions = (
        <>
          <button type="button" className="button" onClick={onDismiss}>
            Close
          </button>
          {state.retryable && state.hasAudio ? (
            <button type="button" className="button button-primary" onClick={onRetry}>
              Retry
            </button>
          ) : (
            <button type="button" className="button button-primary" onClick={onRecordAgain}>
              Record again
            </button>
          )}
        </>
      );
      break;
  }

  return (
    <Modal
      labelledBy={titleId}
      onClose={live ? onCancel : onDismiss}
      className="voice-dialog"
    >
      <h2 id={titleId}>{title}</h2>
      {/* Announces the transitions a screen reader would otherwise miss. */}
      <p className="visually-hidden" aria-live="polite">
        {state.kind === 'recording'
          ? 'Recording'
          : state.kind === 'transcribing'
            ? 'Recording stopped. Transcribing'
            : ''}
      </p>
      {body}
      <div className="button-row voice-dialog-footer">{actions}</div>
    </Modal>
  );
}
